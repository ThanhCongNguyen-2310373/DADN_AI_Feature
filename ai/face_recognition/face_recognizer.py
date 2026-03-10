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
import pickle
import threading
import logging
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import config
from core.mqtt_client import MQTTSingleton

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
        self._mqtt = MQTTSingleton.get_instance()

        # Load Haar Cascade detector
        self._face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )

        # Load LBPH model và label map đã huấn luyện
        self._recognizer = None
        self._label_map: dict = {}
        self._load_model()

        # Trạng thái theo dõi người lạ (REQ-09)
        self._stranger_first_seen: float = None  # Thời điểm phát hiện lần đầu
        self._stranger_alerted = False           # Đã gửi cảnh báo chưa?

        # Trạng thái cửa (chống spam lệnh mở cửa)
        self._door_last_opened: float = 0
        self._door_cooldown = 10  # Giây giữa 2 lần mở cửa liên tiếp

    # ------------------------------------------------------------------
    # Load model
    # ------------------------------------------------------------------
    def _load_model(self):
        """
        Nạp LBPH model (.yml) và ánh xạ nhãn (.pkl) từ thư mục trained_model.
        Nếu model chưa tồn tại, in hướng dẫn chạy face_register.py trước.
        """
        model_path     = os.path.join(config.FACE_MODEL_DIR, "face_model.yml")
        label_map_path = os.path.join(config.FACE_MODEL_DIR, "label_map.pkl")

        if not os.path.exists(model_path) or not os.path.exists(label_map_path):
            logger.error("[FaceAI] ❌ Model chưa được huấn luyện!")
            logger.error("[FaceAI] Hãy chạy: python ai/face_recognition/face_register.py")
            return

        try:
            self._recognizer = cv2.face.LBPHFaceRecognizer_create()
            self._recognizer.read(model_path)

            with open(label_map_path, "rb") as f:
                self._label_map = pickle.load(f)

            logger.info(f"[FaceAI] ✅ Đã nạp model. Nhận diện {len(self._label_map)} người: {list(self._label_map.values())}")
        except Exception as e:
            logger.error(f"[FaceAI] Lỗi nạp model: {e}")
            self._recognizer = None

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
        cap = cv2.VideoCapture(config.CAMERA_INDEX)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  config.FACE_FRAME_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FACE_FRAME_HEIGHT)

        if not cap.isOpened():
            logger.error("[FaceAI] ❌ Không thể mở webcam!")
            self._running = False
            return

        logger.info("[FaceAI] 📷 Webcam đã mở. Bắt đầu giám sát cửa...")

        while self._running:
            ret, frame = cap.read()
            if not ret:
                logger.warning("[FaceAI] Không đọc được frame, bỏ qua...")
                time.sleep(0.1)
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # --- Phát hiện khuôn mặt ---
            faces = self._face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(80, 80)
            )

            if len(faces) == 0:
                # Không có khuôn mặt → reset bộ đếm người lạ
                self._reset_stranger_timer()
            else:
                for (x, y, w, h) in faces:
                    face_roi = gray[y:y + h, x:x + w]
                    face_roi = cv2.resize(face_roi, (160, 160))

                    # --- Nhận diện bằng LBPH ---
                    label_id, confidence = self._recognizer.predict(face_roi)

                    # LBPH: confidence THẤP hơn = GIỐNG hơn (0 = hoàn hảo)
                    # Ngưỡng: confidence < threshold → nhận diện thành công
                    # Chuyển đổi sang thang 0-1 để dễ đọc
                    similarity = max(0, 1 - (confidence / 100))
                    is_known = (similarity >= config.FACE_CONFIDENCE_THRESHOLD)

                    person_name = self._label_map.get(label_id, "Unknown") if is_known else "Unknown"

                    # --- Vẽ kết quả lên frame ---
                    color = (0, 255, 0) if is_known else (0, 0, 255)  # Xanh / Đỏ
                    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
                    label_text = f"{person_name} ({similarity:.0%})"
                    cv2.putText(frame, label_text,
                                (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX,
                                0.7, color, 2)

                    # --- Xử lý logic ---
                    if is_known:
                        self._handle_known_person(person_name, similarity, frame)
                    else:
                        self._handle_stranger(frame, x, y, w, h)

            cv2.imshow("YoloHome - FaceAI Door Guard", frame)

            # Nhấn Q để dừng (chỉ dùng khi debug)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

            # Giới hạn FPS (~10 FPS) để giảm tải CPU
            time.sleep(0.1)

        cap.release()
        cv2.destroyAllWindows()
        logger.info("[FaceAI] Webcam đã giải phóng.")

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
        self._save_log_image(frame, f"open_{person_name}")

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
        self._save_log_image(frame, "stranger_alert")

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

    @property
    def is_running(self) -> bool:
        """Trả về trạng thái thread nhận diện."""
        return self._running
