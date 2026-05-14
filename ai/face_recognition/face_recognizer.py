"""
ai/face_recognition/face_recognizer.py - Module nhận diện khuôn mặt thời gian thực

Thực hiện REQ-08 và REQ-09:
  - REQ-08: Nhận diện khuôn mặt hợp lệ → gửi MQTT mở cửa
  - REQ-09: Phát hiện người lạ > 10s liên tục → gửi cảnh báo

Thiết kế:
  - Chạy trong thread riêng (không block luồng đọc cảm biến)
  - Dùng LBPH Recognizer chạy local (Edge Processing - NFR 2.2)
  - Tích hợp MQTTSingleton để gửi lệnh
"""

import cv2
import os
import sys
import time
import json
import pickle
import threading
import logging
from collections import deque, Counter
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import config
from core.mqtt_client import MQTTSingleton
from core.telegram_notifier import TelegramNotifier
from core.database import DatabaseSingleton

logger = logging.getLogger(__name__)


class FaceRecognizer:
    """
    Module nhận diện khuôn mặt thời gian thực cho YoloHome.

    Vòng lặp chính:
      1. Đọc frame từ webcam
      2. Phát hiện khuôn mặt bằng Haar Cascade
      3. Nhận diện bằng LBPH Recognizer
      4. Nếu nhận diện thành công → MQTT mở cửa
      5. Nếu người lạ > 10s → MQTT cảnh báo + chụp ảnh lưu log

    Cách dùng:
        recognizer = FaceRecognizer()
        recognizer.start()   # Chạy trong thread riêng
        ...
        recognizer.stop()
    """

    def __init__(self):
        self._running = False
        self._thread: threading.Thread = None
        self._mqtt     = MQTTSingleton.get_instance()
        self._telegram = TelegramNotifier.get_instance()
        self._db       = DatabaseSingleton.get_instance()

        # Load Haar Cascade detector
        self._face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )

        # Load LBPH model và label map đã huấn luyện
        self._recognizer = None
        self._label_map: dict = {}
        self._thresholds: dict = {}
        self._model_lock = threading.Lock()
        self._load_model()

        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)) if config.FACE_CLAHE_ENABLE else None

        # Tối ưu CPU: giới hạn số thread OpenCV sử dụng
        cv2.setNumThreads(2)

        # Trạng thái theo dõi người lạ (REQ-09)
        self._stranger_first_seen: float = None  # Thời điểm phát hiện lần đầu
        self._stranger_alerted = False           # Đã gửi cảnh báo chưa?

        # Trạng thái cửa (chống spam lệnh mở cửa)
        self._door_last_opened: float = 0
        self._door_cooldown = 10  # Giây giữa 2 lần mở cửa liên tiếp

        # Tối ưu CPU: bỏ qua frame (chỉ xử lý 1 trong SKIP_N frame)
        self._FRAME_SKIP = config.FACE_FRAME_SKIP
        self._frame_counter = 0

        # Temporal smoothing
        self._recent_predictions = deque(maxlen=config.FACE_SMOOTHING_WINDOW)

        # Face tracker
        self._tracker = None
        self._tracker_frame_count = 0

        # Tham chiếu đến VideoCapture để WebApp dùng lại stream
        self._cap: cv2.VideoCapture = None
        self._frame_lock = threading.Lock()
        self._latest_frame = None
        self._last_read_warning_ts = 0.0
        self._status_lock = threading.Lock()
        self._face_status = {
            "state": "idle",
            "message": "Chờ nhận diện...",
            "person_name": "",
            "similarity": 0.0,
            "event_type": "none",
            "timestamp": None,
        }

    # ------------------------------------------------------------------
    # Load model
    # ------------------------------------------------------------------
    def _load_model(self):
        """
        Nạp LBPH model (.yml) và ánh xạ nhãn (.pkl) từ thư mục trained_model.
        Nếu model chưa tồn tại, in hướng dẫn chạy face_register.py trước.
        """
        model_path     = config.FACE_MODEL_FILE
        label_map_path = os.path.join(config.FACE_MODEL_DIR, "label_map.pkl")

        if not os.path.exists(model_path) or not os.path.exists(label_map_path):
            logger.error("[FaceAI] ❌ Model chưa được huấn luyện!")
            logger.error("[FaceAI] Hãy chạy: python ai/face_recognition/face_register.py")
            return False

        try:
            recognizer = cv2.face.LBPHFaceRecognizer_create()
            recognizer.read(model_path)

            with open(label_map_path, "rb") as f:
                label_map = pickle.load(f)

            thresholds: dict = {}
            thresholds_path = os.path.join(config.FACE_MODEL_DIR, "face_thresholds.json")
            if os.path.exists(thresholds_path):
                try:
                    with open(thresholds_path, "r", encoding="utf-8") as f:
                        thresholds = json.load(f)
                except Exception as e:
                    logger.warning(f"[FaceAI] Không thể đọc ngưỡng động: {e}")

            with self._model_lock:
                self._recognizer = recognizer
                self._label_map = label_map
                self._thresholds = thresholds

            logger.info(f"[FaceAI] ✅ Đã nạp model. Nhận diện {len(self._label_map)} người: {list(self._label_map.values())}")
            return True
        except Exception as e:
            logger.error(f"[FaceAI] Lỗi nạp model: {e}")
            with self._model_lock:
                self._recognizer = None
            return False

    def reload_model(self):
        """Nạp lại model huấn luyện mới từ đĩa."""
        ok = self._load_model()
        if ok:
            self._reset_smoothing()
            self._reset_stranger_timer()
        return ok

    # ------------------------------------------------------------------
    # Thread control
    # ------------------------------------------------------------------
    def start(self):
        """Khởi động thread nhận diện khuôn mặt chạy nền."""
        if self._running:
            logger.warning("[FaceAI] Thread đã đang chạy.")
            return

        if self._recognizer is None:
            logger.error("[FaceAI] Không thể khởi động: model chưa được nạp.")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._recognition_loop,
            daemon=True,
            name="FaceAI-Thread"
        )
        self._thread.start()
        logger.info("[FaceAI] 🚀 Thread nhận diện khuôn mặt đã khởi động.")

    def stop(self):
        """Dừng thread nhận diện và giải phóng webcam."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[FaceAI] 🛑 Thread nhận diện khuôn mặt đã dừng.")

    def _create_tracker(self):
        tracker_type = str(config.FACE_TRACKER_TYPE).upper()
        if tracker_type == "CSRT" and hasattr(cv2, "TrackerCSRT_create"):
            return cv2.TrackerCSRT_create()
        if tracker_type == "KCF" and hasattr(cv2, "TrackerKCF_create"):
            return cv2.TrackerKCF_create()
        if hasattr(cv2, "TrackerCSRT_create"):
            return cv2.TrackerCSRT_create()
        if hasattr(cv2, "TrackerKCF_create"):
            return cv2.TrackerKCF_create()
        return None

    def _init_tracker(self, frame, box):
        tracker = self._create_tracker()
        if tracker is None:
            self._tracker = None
            return
        self._tracker = tracker
        self._tracker.init(frame, tuple(box))
        self._tracker_frame_count = 0

    def _get_tracked_face(self, frame):
        if self._tracker is None:
            return None
        ok, box = self._tracker.update(frame)
        if not ok:
            self._tracker = None
            return None
        x, y, w, h = [int(v) for v in box]
        return (x, y, w, h)

    def _get_dynamic_threshold(self, person_name: str) -> float:
        threshold = config.FACE_CONFIDENCE_THRESHOLD
        stats = self._thresholds.get(person_name)
        if isinstance(stats, dict):
            threshold = float(stats.get("recommended_threshold", threshold))
        threshold = max(config.FACE_THRESHOLD_FLOOR, min(config.FACE_CONFIDENCE_THRESHOLD, threshold))
        return threshold

    def _get_candidate_threshold(self, person_name: str) -> float:
        threshold = self._get_dynamic_threshold(person_name)
        return max(config.FACE_THRESHOLD_FLOOR, threshold - config.FACE_THRESHOLD_MARGIN)

    def _update_smoothing(self, person_name: str, similarity: float):
        self._recent_predictions.append((person_name, similarity))

    def _reset_smoothing(self):
        self._recent_predictions.clear()

    def _get_stable_person(self):
        if len(self._recent_predictions) < config.FACE_SMOOTHING_MIN_COUNT:
            return None, None
        names = [name for name, _ in self._recent_predictions]
        counts = Counter(names)
        person, count = counts.most_common(1)[0]
        if person == "Unknown":
            return None, None
        ratio = count / len(self._recent_predictions)
        if ratio < config.FACE_SMOOTHING_MIN_RATIO:
            return None, None
        sims = [sim for name, sim in self._recent_predictions if name == person]
        avg_sim = sum(sims) / len(sims) if sims else 0.0
        return person, avg_sim

    def _set_face_status(self, state: str, message: str, person_name: str = "", similarity: float = 0.0, event_type: str = "none"):
        with self._status_lock:
            self._face_status = {
                "state": state,
                "message": message,
                "person_name": person_name,
                "similarity": float(similarity),
                "event_type": event_type,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }

    def get_status(self):
        with self._status_lock:
            return dict(self._face_status)

    # ------------------------------------------------------------------
    # Vòng lặp nhận diện chính
    # ------------------------------------------------------------------
    def _recognition_loop(self):
        """
        Vòng lặp chính chạy trong thread riêng:
          - Mở webcam
          - Liên tục phân tích frame
          - Nhận diện khuôn mặt và xử lý logic cửa
        """
        # ── Mở webcam theo cấu hình hệ thống ──
        self._cap = cv2.VideoCapture(config.CAMERA_INDEX)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  config.FACE_FRAME_WIDTH)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FACE_FRAME_HEIGHT)
        self._cap.set(cv2.CAP_PROP_FPS, config.CAMERA_FPS)

        cap = self._cap
        if not cap.isOpened():
            logger.error("[FaceAI] ❌ Không thể mở webcam!")
            self._running = False
            return

        logger.info(
            f"[FaceAI] 📷 Webcam đã mở ({config.FACE_FRAME_WIDTH}x{config.FACE_FRAME_HEIGHT}@{config.CAMERA_FPS}fps). "
            "Bắt đầu giám sát cửa..."
        )

        while self._running:
            ret, frame = cap.read()
            if not ret:
                now = time.time()
                if now - self._last_read_warning_ts >= 5:
                    logger.warning("[FaceAI] Không đọc được frame, bỏ qua...")
                    self._last_read_warning_ts = now
                time.sleep(0.1)
                continue

            with self._frame_lock:
                self._latest_frame = frame.copy()

            # ── Frame-skip: chỉ xử lý nhận diện mỗi FRAME_SKIP frame ──
            self._frame_counter += 1
            if self._frame_counter % self._FRAME_SKIP != 0:
                # Vẫn hiển thị frame (nếu có cửa sổ) nhưng không xử lý AI
                time.sleep(0.05)
                continue

            # ── Xử lý AI trên frame ──
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (3, 3), 0)
            if self._clahe:
                gray = self._clahe.apply(gray)
            else:
                gray = cv2.equalizeHist(gray)

            face_box = None
            self._tracker_frame_count += 1
            if self._tracker and self._tracker_frame_count % config.FACE_TRACKER_REFRESH != 0:
                face_box = self._get_tracked_face(frame)

            if face_box is None:
                faces = self._face_cascade.detectMultiScale(
                    gray,
                    scaleFactor=1.1,
                    minNeighbors=4,
                    minSize=(config.FACE_MIN_SIZE, config.FACE_MIN_SIZE),
                    flags=cv2.CASCADE_SCALE_IMAGE,
                )
                if len(faces) > 0:
                    face_box = max(faces, key=lambda b: b[2] * b[3])
                    self._init_tracker(frame, face_box)

            if face_box is None:
                self._reset_stranger_timer()
                self._reset_smoothing()
                self._set_face_status("idle", "Chờ nhận diện...", event_type="idle")
            else:
                x, y, w, h = face_box
                face_roi = gray[y:y + h, x:x + w]
                face_roi = cv2.resize(face_roi, (160, 160))

                with self._model_lock:
                    recognizer = self._recognizer
                    label_map = dict(self._label_map)

                if recognizer is None:
                    self._set_face_status("idle", "Model chưa sẵn sàng...", event_type="idle")
                    time.sleep(0.2)
                    continue

                label_id, confidence = recognizer.predict(face_roi)
                similarity = max(0.0, 1.0 - (confidence / 100.0))
                person_name = label_map.get(label_id, "Unknown")
                threshold = self._get_dynamic_threshold(person_name)
                candidate_threshold = self._get_candidate_threshold(person_name)
                is_known = similarity >= threshold
                is_candidate = similarity >= candidate_threshold
                candidate_name = person_name if is_candidate else "Unknown"

                self._update_smoothing(candidate_name, similarity)
                stable_person, stable_similarity = self._get_stable_person()

                display_name = stable_person or candidate_name
                color = (0, 255, 0) if display_name != "Unknown" else (0, 0, 255)
                cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
                label_text = f"{display_name} ({similarity:.0%})"
                cv2.putText(frame, label_text,
                            (x, y - 8), cv2.FONT_HERSHEY_SIMPLEX,
                            0.55, color, 1)

                if stable_person:
                    self._set_face_status(
                        "known",
                        f"Đã nhận diện: {stable_person}",
                        person_name=stable_person,
                        similarity=stable_similarity,
                        event_type="known",
                    )
                    self._handle_known_person(stable_person, stable_similarity, frame)
                elif candidate_name != "Unknown":
                    self._reset_stranger_timer()
                    self._set_face_status(
                        "candidate",
                        f"Đang theo dõi: {candidate_name}",
                        person_name=candidate_name,
                        similarity=similarity,
                        event_type="candidate",
                    )
                else:
                    self._set_face_status(
                        "stranger",
                        "Phát hiện người lạ",
                        person_name="Unknown",
                        similarity=similarity,
                        event_type="stranger",
                    )
                    self._handle_stranger(frame, x, y, w, h)

            # ── Tăng sleep để giảm CPU usage (~5 FPS thực tế) ──
            time.sleep(0.2)

        cap.release()
        self._cap = None
        logger.info("[FaceAI] Webcam đã giải phóng.")

    def get_latest_frame(self):
        with self._frame_lock:
            if self._latest_frame is None:
                return None
            return self._latest_frame.copy()

    # ------------------------------------------------------------------
    # Xử lý khuôn mặt hợp lệ (REQ-08)
    # ------------------------------------------------------------------
    def _handle_known_person(self, person_name: str, similarity: float, frame):
        """
        Xử lý khi nhận diện thành công chủ nhà.
        Gửi MQTT mở cửa (có cooldown để tránh spam lệnh).

        Args:
            person_name: Tên người được nhận diện
            similarity : Độ tương đồng (0.0 - 1.0)
            frame      : Frame hiện tại để lưu log ảnh
        """
        # Reset bộ đếm người lạ
        self._reset_stranger_timer()

        self._set_face_status(
            "known",
            f"Đã nhận diện: {person_name}",
            person_name=person_name,
            similarity=similarity,
            event_type="known",
        )

        now = time.time()
        # Kiểm tra cooldown: không gửi lệnh mở cửa liên tục trong 10s
        if now - self._door_last_opened < self._door_cooldown:
            return

        # Gửi lệnh MQTT mở cửa (REQ-08)
        self._mqtt.publish(config.FEED_DOOR, "ON")
        self._door_last_opened = now

        # Ghi log sự kiện
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_message = f"[{timestamp}] Cửa mở: {person_name} (similarity={similarity:.1%})"
        logger.info(f"[FaceAI] ✅ {log_message}")
        self._mqtt.publish(config.FEED_LOG, log_message)

        # Lưu ảnh log khi mở cửa thành công
        img_path = self._save_log_image(frame, f"open_{person_name}")

        # Ghi vào DB
        try:
            self._db.insert_face_event("known", person=person_name,
                                        confidence=similarity, img_path=img_path)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Xử lý người lạ (REQ-09)
    # ------------------------------------------------------------------
    def _handle_stranger(self, frame, x: int, y: int, w: int, h: int):
        """
        Xử lý khi phát hiện khuôn mặt không nhận diện được.
        Nếu người lạ xuất hiện liên tục > FACE_STRANGER_TIMEOUT giây → cảnh báo.

        Args:
            frame: Frame hiện tại
            x, y, w, h: Vị trí khuôn mặt trong frame
        """
        now = time.time()

        # Bắt đầu đếm thời gian nếu chưa thấy người lạ
        if self._stranger_first_seen is None:
            self._stranger_first_seen = now
            self._stranger_alerted = False
            logger.warning("[FaceAI] ⚠️  Phát hiện người lạ! Bắt đầu đếm thời gian...")

        self._set_face_status(
            "stranger",
            f"Phát hiện người lạ ({now - self._stranger_first_seen:.1f}s)",
            person_name="Unknown",
            similarity=0.0,
            event_type="stranger",
        )

        elapsed = now - self._stranger_first_seen

        # Hiển thị bộ đếm thời gian trên màn hình
        cv2.putText(frame, f"NGUOI LA: {elapsed:.1f}s/{config.FACE_STRANGER_TIMEOUT}s",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

        # Nếu vượt ngưỡng timeout và chưa gửi cảnh báo → gửi ALERT
        if elapsed >= config.FACE_STRANGER_TIMEOUT and not self._stranger_alerted:
            self._send_stranger_alert(frame)
            self._stranger_alerted = True

    def _send_stranger_alert(self, frame):
        """
        Gửi cảnh báo người lạ lên Adafruit IO Dashboard.
        Lưu ảnh người lạ để chủ nhà xem lại.

        Args:
            frame: Frame chứa hình ảnh người lạ
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        alert_msg = f"[{timestamp}] CẢNH BÁO: Người lạ xuất hiện trước cửa trên {config.FACE_STRANGER_TIMEOUT}s!"

        logger.warning(f"[FaceAI] 🚨 {alert_msg}")

        # Publish cảnh báo lên Dashboard
        self._mqtt.publish(config.FEED_ALERT, alert_msg)
        self._mqtt.publish(config.FEED_LOG,   alert_msg)

        # Lưu ảnh bằng chứng
        img_path = self._save_log_image(frame, "stranger_alert")

        # Gửi Telegram kèm ảnh
        elapsed = time.time() - (self._stranger_first_seen or time.time())
        self._telegram.stranger_alert(elapsed, img_path)

        # Ghi vào DB
        try:
            self._db.insert_face_event("stranger", img_path=img_path)
        except Exception:
            pass

    def _reset_stranger_timer(self):
        """Reset bộ đếm theo dõi người lạ khi không còn khuôn mặt lạ."""
        if self._stranger_first_seen is not None:
            logger.debug("[FaceAI] Reset bộ đếm người lạ.")
        self._stranger_first_seen = None
        self._stranger_alerted = False

    # ------------------------------------------------------------------
    # Lưu ảnh log
    # ------------------------------------------------------------------
    def _save_log_image(self, frame, event_type: str):
        """
        Lưu ảnh sự kiện vào thư mục logs/ để xem lại sau.

        Args:
            frame     : Frame ảnh cần lưu
            event_type: Loại sự kiện (vd: "open_Cong", "stranger_alert")
        """
        log_img_dir = os.path.join(config.LOG_DIR, "face_events")
        os.makedirs(log_img_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        img_path = os.path.join(log_img_dir, f"{event_type}_{timestamp}.jpg")

        cv2.imwrite(img_path, frame)
        logger.debug(f"[FaceAI] 📸 Đã lưu ảnh log: {img_path}")
        return img_path

    @property
    def is_running(self) -> bool:
        """Trả về trạng thái thread nhận diện."""
        return self._running
