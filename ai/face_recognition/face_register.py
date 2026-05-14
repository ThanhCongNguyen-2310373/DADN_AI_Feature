"""
ai/face_recognition/face_register.py - Thu thập và đăng ký khuôn mặt

Bước 1 trong workflow FaceAI:
  1. Nhập tên người dùng cần đăng ký
  2. Mở webcam, chụp N ảnh khuôn mặt (mặc định 50 ảnh)
  3. Lưu vào thư mục dataset/<tên_người_dùng>/
  4. Huấn luyện lại model sau khi thu thập xong

Chạy độc lập:
    python ai/face_recognition/face_register.py
"""

import cv2
import os
import sys
import time
import random
import logging
from typing import List, Tuple, Dict

import numpy as np

# Thêm thư mục gốc để import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import config

logger = logging.getLogger(__name__)

# =====================================================================
# Tải Haar Cascade detector (phát hiện khuôn mặt từ OpenCV built-in)
# =====================================================================
HAAR_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
face_cascade = cv2.CascadeClassifier(HAAR_CASCADE_PATH)
EYE_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_eye_tree_eyeglasses.xml"
eye_cascade = cv2.CascadeClassifier(EYE_CASCADE_PATH)
_CLAHE = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

_GUIDED_STAGES: List[Tuple[str, bool]] = [
    ("Nhìn thẳng, biểu cảm bình thường", False),
    ("Quay mặt trái 15-30 độ", False),
    ("Quay mặt phải 15-30 độ", False),
    ("Nhìn lên/nhìn xuống nhẹ", False),
    ("Thay đổi khoảng cách gần/xa", False),
    ("Cười nhẹ hoặc đổi biểu cảm", False),
    ("Che mặt nhẹ (tay/khẩu trang/kính/tóc)", True),
]


def _get_guidance_stage(count: int, total: int) -> Tuple[str, bool, int]:
    stage_size = max(1, total // len(_GUIDED_STAGES))
    idx = min(len(_GUIDED_STAGES) - 1, count // stage_size)
    text, allow_occlusion = _GUIDED_STAGES[idx]
    return text, allow_occlusion, idx


def _select_largest_face(faces) -> Tuple[int, int, int, int] | None:
    if len(faces) == 0:
        return None
    return max(faces, key=lambda b: b[2] * b[3])


def _compute_blur_score(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _compute_brightness(gray: np.ndarray) -> float:
    return float(gray.mean())


def _apply_clahe(gray: np.ndarray) -> np.ndarray:
    if config.FACE_CLAHE_ENABLE:
        return _CLAHE.apply(gray)
    return gray


def _align_face(face_gray: np.ndarray) -> Tuple[np.ndarray, bool]:
    if not config.FACE_ALIGN_ENABLE:
        return face_gray, True

    eyes = eye_cascade.detectMultiScale(
        face_gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(20, 20),
    )
    if len(eyes) < 2:
        return face_gray, False

    eyes = sorted(eyes, key=lambda e: e[2] * e[3], reverse=True)[:2]
    eyes = sorted(eyes, key=lambda e: e[0])
    (x1, y1, w1, h1), (x2, y2, w2, h2) = eyes
    left_center = (x1 + w1 // 2, y1 + h1 // 2)
    right_center = (x2 + w2 // 2, y2 + h2 // 2)

    dy = right_center[1] - left_center[1]
    dx = right_center[0] - left_center[0]
    angle = np.degrees(np.arctan2(dy, dx))

    h, w = face_gray.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    aligned = cv2.warpAffine(face_gray, matrix, (w, h), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REPLICATE)
    return aligned, True


def _check_quality(
    face_gray: np.ndarray,
    face_w: int,
    face_h: int,
    blur_score: float,
    brightness: float,
    eye_found: bool,
    allow_occlusion: bool,
) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    if face_w < config.FACE_MIN_SIZE or face_h < config.FACE_MIN_SIZE:
        reasons.append("khuon mat qua nho")
    if blur_score < config.FACE_BLUR_THRESHOLD:
        reasons.append("anh bi nhoe")
    if brightness < config.FACE_BRIGHTNESS_MIN or brightness > config.FACE_BRIGHTNESS_MAX:
        reasons.append("anh sang khong phu hop")
    if not eye_found and not allow_occlusion:
        reasons.append("khong tim thay mat")
    return len(reasons) == 0, reasons


def _save_face_sample(person_dir: str, person_name: str, count: int, face_gray: np.ndarray) -> str:
    img_path = os.path.join(person_dir, f"{person_name}_{count:03d}.jpg")
    cv2.imwrite(img_path, face_gray)
    return img_path


def capture_face_samples_from_provider(
    person_name: str,
    num_samples: int,
    frame_provider,
    pause_event=None,
    cancel_event=None,
    progress_callback=None,
) -> bool:
    """
    Thu thập mẫu từ nguồn frame có sẵn (ví dụ camera cache của FaceRecognizer).

    Hàm này không mở webcam mới, nên phù hợp cho web UI để tránh xung đột camera.
    Trạng thái tiến trình có thể được đẩy qua progress_callback.
    """
    person_dir = os.path.join(config.FACE_DATASET_DIR, person_name)
    os.makedirs(person_dir, exist_ok=True)

    count = 0
    last_capture_ts = 0.0
    last_stage_idx = -1
    last_state = {}

    def _emit(state: Dict):
        nonlocal last_state
        last_state = state
        if progress_callback:
            try:
                progress_callback(state)
            except Exception:
                pass

    while count < num_samples:
        if cancel_event is not None and cancel_event.is_set():
            _emit({"status": "cancelled", "captured": count, "remaining": num_samples - count})
            return False

        if pause_event is not None and pause_event.is_set():
            _emit({"status": "paused", "captured": count, "remaining": num_samples - count})
            time.sleep(0.1)
            continue

        frame = frame_provider() if frame_provider else None
        if frame is None:
            _emit({"status": "waiting_frame", "captured": count, "remaining": num_samples - count})
            time.sleep(0.05)
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(config.FACE_MIN_SIZE, config.FACE_MIN_SIZE),
        )

        stage_text, allow_occlusion, stage_idx = _get_guidance_stage(count, num_samples)
        face_box = _select_largest_face(faces)
        quality_ok = False
        reasons: List[str] = []
        blur_score = 0.0
        brightness = 0.0

        if face_box is not None:
            x, y, w, h = face_box
            face_roi = gray[y:y + h, x:x + w]
            face_roi, eye_found = _align_face(face_roi)
            face_roi = _apply_clahe(face_roi)
            face_roi = cv2.resize(face_roi, (160, 160))

            blur_score = _compute_blur_score(face_roi)
            brightness = _compute_brightness(face_roi)
            # compute quality for logging but do not block capture for low-quality cams
            quality_ok, reasons = _check_quality(
                face_roi, w, h, blur_score, brightness, eye_found, allow_occlusion
            )

            capture_ready = (
                (time.time() - last_capture_ts >= config.FACE_CAPTURE_COOLDOWN)
            )
            if capture_ready:
                _save_face_sample(person_dir, person_name, count, face_roi)
                count += 1
                last_capture_ts = time.time()

        if stage_idx != last_stage_idx:
            last_stage_idx = stage_idx

        _emit({
            "status": "capturing",
            "captured": count,
            "remaining": max(0, num_samples - count),
            "stage_text": stage_text,
            "stage_index": stage_idx,
            "quality_ok": quality_ok,
            "reasons": reasons,
            "blur": blur_score,
            "brightness": brightness,
            "auto_mode": True,
            "paused": False,
            "can_pause": True,
            "can_cancel": True,
        })

        time.sleep(0.05)

    _emit({"status": "completed", "captured": count, "remaining": 0, "stage_text": "Hoàn tất"})
    return True


def collect_face_samples(person_name: str, num_samples: int = 80) -> bool:
    """
    Mở webcam và chụp num_samples ảnh khuôn mặt của người dùng.

    Quy trình:
      - Phát hiện khuôn mặt bằng Haar Cascade
      - Cắt ROI khuôn mặt, căn chỉnh theo mắt (nếu có)
      - Cân bằng histogram (CLAHE)
      - Kiểm tra chất lượng (blur/brightness/size)
      - Lưu ảnh grayscale 160x160 vào dataset/<person_name>/

    Args:
        person_name: Tên người dùng (sử dụng làm tên thư mục và nhãn)
        num_samples: Số lượng ảnh cần chụp (khuyến nghị >= 80)

    Returns:
        True nếu thu thập đủ ảnh, False nếu thất bại.
    """
    person_dir = os.path.join(config.FACE_DATASET_DIR, person_name)
    os.makedirs(person_dir, exist_ok=True)
    logger.info(f"[FaceRegister] Thư mục lưu ảnh: {person_dir}")

    cap = cv2.VideoCapture(config.CAMERA_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  config.FACE_FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FACE_FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, config.CAMERA_FPS)

    if not cap.isOpened():
        logger.error("[FaceRegister] ❌ Không thể mở webcam!")
        return False

    print(f"\n{'='*60}")
    print(f"  Đang thu thập khuôn mặt của: {person_name}")
    print(f"  Cần chụp: {num_samples} ảnh")
    print("  Phím: [Q] huỷ | [SPACE] chụp tay | [A] auto | [P] tạm dừng | [S] bỏ qua 1 lần")
    print(f"{'='*60}\n")

    count = 0
    auto_capture = True
    paused = False
    skip_capture = False
    manual_capture = False
    last_capture_ts = 0.0
    last_stage_idx = -1

    while count < num_samples:
        ret, frame = cap.read()
        if not ret:
            logger.warning("[FaceRegister] Không đọc được frame từ webcam.")
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(config.FACE_MIN_SIZE, config.FACE_MIN_SIZE),
        )

        stage_text, allow_occlusion, stage_idx = _get_guidance_stage(count, num_samples)
        if stage_idx != last_stage_idx:
            print(f"👉 Hướng dẫn: {stage_text}")
            last_stage_idx = stage_idx

        face_box = _select_largest_face(faces)
        quality_ok = False
        reasons: List[str] = []
        blur_score = 0.0
        brightness = 0.0

        if face_box is not None:
            x, y, w, h = face_box
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

            face_roi = gray[y:y + h, x:x + w]
            face_roi, eye_found = _align_face(face_roi)
            face_roi = _apply_clahe(face_roi)
            face_roi = cv2.resize(face_roi, (160, 160))

            blur_score = _compute_blur_score(face_roi)
            brightness = _compute_brightness(face_roi)
            # NOTE: For low-quality webcams we capture regardless of quality checks.
            # We still compute metrics for logging, but do not block saving.
            quality_ok, reasons = _check_quality(
                face_roi, w, h, blur_score, brightness, eye_found, allow_occlusion
            )

            capture_ready = (
                (auto_capture and not paused and not skip_capture
                 and time.time() - last_capture_ts >= config.FACE_CAPTURE_COOLDOWN)
                or manual_capture
            )

            if capture_ready:
                img_path = _save_face_sample(person_dir, person_name, count, face_roi)
                count += 1
                last_capture_ts = time.time()
                manual_capture = False
                logger.debug(f"[FaceRegister] Đã lưu ảnh {count}/{num_samples}: {img_path}")

        if manual_capture and not quality_ok:
            manual_capture = False

        if skip_capture:
            skip_capture = False

        remaining = num_samples - count
        status_line = f"Da chup: {count}/{num_samples} | Con lai: {remaining}"
        mode_line = f"Auto: {'ON' if auto_capture else 'OFF'} | Pause: {'ON' if paused else 'OFF'}"
        quality_line = "Chat luong: OK" if quality_ok else f"Chat luong: FAIL ({', '.join(reasons)})"
        metrics_line = f"Blur: {blur_score:.0f} | Bright: {brightness:.0f}"

        cv2.putText(frame, status_line, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.putText(frame, f"Huong dan: {stage_text}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
        cv2.putText(frame, mode_line, (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 255, 200), 1)
        cv2.putText(frame, quality_line, (10, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 1)
        cv2.putText(frame, metrics_line, (10, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 200, 200), 1)
        cv2.imshow(f"Thu thap khuon mat - {person_name}", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            logger.info("[FaceRegister] Người dùng huỷ thu thập.")
            break
        if key == ord("p"):
            paused = not paused
        if key == ord("a"):
            auto_capture = not auto_capture
        if key == ord("s"):
            skip_capture = True
        if key == ord(" "):
            manual_capture = True

    cap.release()
    cv2.destroyAllWindows()

    if count >= num_samples:
        print(f"\n✅ Thu thập xong {count} ảnh cho '{person_name}'!")
        logger.info(f"[FaceRegister] ✅ Hoàn thành thu thập {count} ảnh cho '{person_name}'")
        return True

    print(f"\n⚠️  Chỉ thu thập được {count}/{num_samples} ảnh.")
    logger.warning(f"[FaceRegister] Chỉ thu thập được {count}/{num_samples} ảnh.")
    return count >= 10


def _confidence_to_similarity(confidence: float) -> float:
    return max(0.0, 1.0 - (confidence / 100.0))


def _preprocess_train_image(img: np.ndarray) -> np.ndarray | None:
    if img is None:
        return None
    if len(img.shape) == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    img = cv2.resize(img, (160, 160))
    img = _apply_clahe(img)
    return img


def _is_train_quality_ok(face_gray: np.ndarray) -> bool:
    blur_score = _compute_blur_score(face_gray)
    brightness = _compute_brightness(face_gray)
    if blur_score < config.FACE_BLUR_THRESHOLD:
        return False
    if brightness < config.FACE_BRIGHTNESS_MIN or brightness > config.FACE_BRIGHTNESS_MAX:
        return False
    return True


def _zoom_image(img: np.ndarray, zoom: float) -> np.ndarray:
    h, w = img.shape[:2]
    if abs(zoom - 1.0) < 0.01:
        return img
    new_w = max(1, int(w * zoom))
    new_h = max(1, int(h * zoom))
    resized = cv2.resize(img, (new_w, new_h))
    if zoom > 1.0:
        x = (new_w - w) // 2
        y = (new_h - h) // 2
        return resized[y:y + h, x:x + w]
    pad_w = w - new_w
    pad_h = h - new_h
    top = pad_h // 2
    bottom = pad_h - top
    left = pad_w // 2
    right = pad_w - left
    return cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_REFLECT)


def _apply_random_occlusion(img: np.ndarray, rng: random.Random) -> np.ndarray:
    h, w = img.shape[:2]
    min_ratio, max_ratio = config.FACE_AUGMENT_OCCLUSION_AREA
    target_area = rng.uniform(min_ratio, max_ratio) * (h * w)
    occ_w = rng.randint(max(1, int(w * 0.2)), max(2, int(w * 0.6)))
    occ_h = max(1, int(target_area / max(1, occ_w)))
    occ_w = min(occ_w, w - 1)
    occ_h = min(occ_h, h - 1)
    x = rng.randint(0, w - occ_w)
    y = rng.randint(0, h - occ_h)
    occluded = img.copy()
    cv2.rectangle(occluded, (x, y), (x + occ_w, y + occ_h), (0,), -1)
    return occluded


def _augment_image(img: np.ndarray, rng: random.Random) -> List[np.ndarray]:
    if not config.FACE_AUGMENT_ENABLED:
        return []

    variants: List[np.ndarray] = []
    h, w = img.shape[:2]

    angle = rng.uniform(-config.FACE_AUGMENT_ROTATE_DEG, config.FACE_AUGMENT_ROTATE_DEG)
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    rotated = cv2.warpAffine(img, matrix, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    variants.append(rotated)

    contrast = rng.uniform(*config.FACE_AUGMENT_CONTRAST_RANGE)
    brightness = rng.uniform(*config.FACE_AUGMENT_BRIGHTNESS_RANGE)
    bc = np.clip(img.astype(np.float32) * contrast * brightness, 0, 255).astype(np.uint8)
    variants.append(bc)

    if config.FACE_AUGMENT_NOISE_STD > 0:
        noise = np.random.normal(0, config.FACE_AUGMENT_NOISE_STD, img.shape)
        noisy = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)
        variants.append(noisy)

    zoom = rng.uniform(*config.FACE_AUGMENT_ZOOM_RANGE)
    variants.append(_zoom_image(img, zoom))

    if rng.random() < config.FACE_AUGMENT_OCCLUSION_PROB:
        variants.append(_apply_random_occlusion(img, rng))

    rng.shuffle(variants)
    return variants[:config.FACE_AUGMENT_MAX_PER_IMAGE]


def _save_json(path: str, data: Dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        import json
        json.dump(data, f, ensure_ascii=False, indent=2)


def _evaluate_model(
    recognizer,
    samples: List[Tuple[np.ndarray, int]],
    label_map: Dict[int, str],
) -> Dict:
    report: Dict[str, Dict] = {
        "total": len(samples),
        "correct": 0,
        "accuracy": 0.0,
        "per_person": {},
    }

    for name in label_map.values():
        report["per_person"][name] = {
            "total": 0,
            "correct": 0,
            "accuracy": 0.0,
            "similarity_mean": None,
            "similarity_std": None,
            "_sims": [],
        }

    for img, true_label in samples:
        pred_label, confidence = recognizer.predict(img)
        similarity = _confidence_to_similarity(confidence)
        true_name = label_map.get(true_label, "Unknown")
        pred_name = label_map.get(pred_label, "Unknown")

        entry = report["per_person"].setdefault(true_name, {
            "total": 0,
            "correct": 0,
            "accuracy": 0.0,
            "similarity_mean": None,
            "similarity_std": None,
            "_sims": [],
        })
        entry["total"] += 1

        if pred_name == true_name:
            entry["correct"] += 1
            entry["_sims"].append(similarity)
            report["correct"] += 1

    for entry in report["per_person"].values():
        if entry["total"] > 0:
            entry["accuracy"] = entry["correct"] / entry["total"]
        if entry["_sims"]:
            entry["similarity_mean"] = float(np.mean(entry["_sims"]))
            entry["similarity_std"] = float(np.std(entry["_sims"]))
        entry.pop("_sims", None)

    if report["total"] > 0:
        report["accuracy"] = report["correct"] / report["total"]
    return report


def train_face_model(progress_callback=None, include_members: List[str] | None = None):
    """
    Huấn luyện LBPH Face Recognizer từ toàn bộ ảnh trong thư mục dataset.

    Bước 2 sau khi thu thập xong ảnh:
      - Đọc tất cả ảnh từ dataset/
      - Tạo nhãn số (label_id) tương ứng với từng người
      - Huấn luyện LBPH model
      - Lưu model (.yml) và ánh xạ nhãn (.pkl) vào trained_model/

    LBPH (Local Binary Patterns Histogram) được chọn vì:
      - Chạy hoàn toàn local, không cần GPU (phù hợp IoT - NFR 2.2)
      - Nhẹ và nhanh, đáp ứng yêu cầu độ trễ < 2s
    """
    import pickle

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    def _emit(state: Dict):
        if not progress_callback:
            return
        try:
            progress_callback(state)
        except Exception:
            pass

    dataset_dir = config.FACE_DATASET_DIR
    model_dir = config.FACE_MODEL_DIR
    os.makedirs(model_dir, exist_ok=True)

    selected_members = {name.strip() for name in (include_members or []) if str(name).strip()}
    if selected_members:
        print(f"[Training] Chỉ train các thành viên: {sorted(selected_members)}")

    _emit({
        "status": "starting",
        "stage": "starting",
        "message": "Đang quét dataset...",
        "progress": 0,
        "current_person": "",
        "processed_people": 0,
        "total_people": 0,
        "trained_images": 0,
    })

    rng = random.Random(42)
    faces_data: List[np.ndarray] = []
    labels: List[int] = []
    val_samples: List[Tuple[np.ndarray, int]] = []
    label_map: Dict[int, str] = {}
    label_id = 0
    person_entries: List[Tuple[str, str, List[str]]] = []

    print("\n[Training] Bắt đầu đọc dataset...")

    for person_name in sorted(os.listdir(dataset_dir)):
        person_dir = os.path.join(dataset_dir, person_name)
        if not os.path.isdir(person_dir):
            continue

        if selected_members and person_name not in selected_members:
            continue

        image_files = [
            f for f in os.listdir(person_dir)
            if f.lower().endswith((".jpg", ".png", ".jpeg"))
        ]
        if not image_files:
            continue

        person_entries.append((person_name, person_dir, image_files))

    total_people = len(person_entries)
    if total_people == 0:
        print("\n❌ Không tìm thấy ảnh hợp lệ trong dataset. Hãy chạy face_register trước.")
        logger.error("[Training] Dataset rỗng hoặc không khớp bộ lọc. Huấn luyện thất bại.")
        _emit({
            "status": "error",
            "stage": "error",
            "message": "Không tìm thấy ảnh hợp lệ trong dataset.",
            "error": "dataset_empty",
            "progress": 0,
        })
        return False

    for idx, (person_name, person_dir, image_files) in enumerate(person_entries, start=1):
        if len(image_files) < config.FACE_MIN_IMAGES_PER_PERSON:
            logger.warning(
                f"[Training] '{person_name}' chỉ có {len(image_files)} ảnh, nên >= {config.FACE_MIN_IMAGES_PER_PERSON}."
            )

        rng.shuffle(image_files)
        val_count = int(len(image_files) * config.FACE_VAL_SPLIT)
        if len(image_files) < 10:
            val_count = 0

        val_files = image_files[:val_count]
        train_files = image_files[val_count:]

        label_map[label_id] = person_name
        _emit({
            "status": "loading_person",
            "stage": "loading",
            "message": f"Đang nạp dữ liệu của {person_name} ({idx}/{total_people})...",
            "current_person": person_name,
            "processed_people": idx - 1,
            "total_people": total_people,
            "progress": round(((idx - 1) / total_people) * 60, 1),
        })
        train_count = 0
        val_count_real = 0

        for img_file in train_files:
            img_path = os.path.join(person_dir, img_file)
            img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
            img = _preprocess_train_image(img)
            if img is None:
                logger.warning(f"[Training] Không đọc được ảnh: {img_path}")
                continue
            if not _is_train_quality_ok(img):
                continue

            faces_data.append(img)
            labels.append(label_id)
            train_count += 1

            for aug in _augment_image(img, rng):
                if _is_train_quality_ok(aug):
                    faces_data.append(aug)
                    labels.append(label_id)
                    train_count += 1

        for img_file in val_files:
            img_path = os.path.join(person_dir, img_file)
            img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
            img = _preprocess_train_image(img)
            if img is None:
                continue
            if not _is_train_quality_ok(img):
                continue
            val_samples.append((img, label_id))
            val_count_real += 1

        print(f"  ✔ {person_name}: train={train_count}, val={val_count_real} (label_id={label_id})")
        logger.info(f"[Training] Nạp {train_count} ảnh train cho '{person_name}' (id={label_id})")
        _emit({
            "status": "loading_person",
            "stage": "loading",
            "message": f"Đã nạp {person_name}: train={train_count}, val={val_count_real}",
            "current_person": person_name,
            "processed_people": idx,
            "total_people": total_people,
            "trained_images": len(faces_data),
            "progress": round((idx / total_people) * 60, 1),
        })
        label_id += 1

    if len(faces_data) == 0:
        print("\n❌ Không tìm thấy ảnh hợp lệ trong dataset. Hãy chạy face_register trước.")
        logger.error("[Training] Dataset rỗng. Huấn luyện thất bại.")
        _emit({
            "status": "error",
            "stage": "error",
            "message": "Không có ảnh hợp lệ để train.",
            "error": "no_valid_images",
            "progress": 0,
        })
        return False

    print(f"\n[Training] Tổng train: {len(faces_data)} ảnh, {len(label_map)} người.")
    print("[Training] Đang huấn luyện LBPH model...")
    _emit({
        "status": "training",
        "stage": "training",
        "message": f"Đang huấn luyện LBPH trên {len(faces_data)} ảnh của {len(label_map)} người...",
        "trained_images": len(faces_data),
        "total_people": total_people,
        "processed_people": total_people,
        "progress": 70,
    })

    recognizer = cv2.face.LBPHFaceRecognizer_create(
        radius=1,
        neighbors=8,
        grid_x=8,
        grid_y=8,
    )
    recognizer.train(faces_data, np.array(labels))

    model_path = os.path.join(model_dir, "face_model.yml")
    _emit({
        "status": "saving_model",
        "stage": "saving",
        "message": "Đang lưu model và label map...",
        "progress": 85,
    })
    recognizer.save(model_path)
    print(f"  ✅ Đã lưu model: {model_path}")

    label_map_path = os.path.join(model_dir, "label_map.pkl")
    with open(label_map_path, "wb") as f:
        pickle.dump(label_map, f)
    print(f"  ✅ Đã lưu label map: {label_map_path}")

    eval_samples = val_samples
    if not eval_samples:
        fallback = min(200, len(faces_data))
        eval_samples = list(zip(faces_data[:fallback], labels[:fallback]))

    _emit({
        "status": "evaluating",
        "stage": "evaluating",
        "message": "Đang đánh giá model sau train...",
        "progress": 90,
    })
    report = _evaluate_model(recognizer, eval_samples, label_map)
    report_path = os.path.join(model_dir, "face_train_report.json")
    _emit({
        "status": "saving_report",
        "stage": "saving",
        "message": "Đang lưu báo cáo và ngưỡng động...",
        "progress": 95,
    })
    _save_json(report_path, report)
    print(f"  ✅ Đã lưu báo cáo train: {report_path}")

    thresholds: Dict[str, Dict] = {}
    for name, entry in report["per_person"].items():
        mean_sim = entry.get("similarity_mean")
        std_sim = entry.get("similarity_std") or 0.0
        base = config.FACE_CONFIDENCE_THRESHOLD - config.FACE_THRESHOLD_MARGIN
        if mean_sim is not None:
            base = mean_sim - config.FACE_THRESHOLD_STD_MULTIPLIER * std_sim
            base = min(base, config.FACE_CONFIDENCE_THRESHOLD - config.FACE_THRESHOLD_MARGIN)
        recommended = max(config.FACE_THRESHOLD_FLOOR, base)
        thresholds[name] = {
            "mean_similarity": mean_sim,
            "std_similarity": std_sim,
            "recommended_threshold": recommended,
            "samples": entry.get("total", 0),
        }

    thresholds_path = os.path.join(model_dir, "face_thresholds.json")
    _save_json(thresholds_path, thresholds)
    print(f"  ✅ Đã lưu ngưỡng động: {thresholds_path}")

    if report["total"] > 0:
        print(f"[Training] Validation accuracy: {report['accuracy']:.1%} ({report['correct']}/{report['total']})")
    logger.info(f"[Training] ✅ Huấn luyện xong. Model: {model_path} | Labels: {label_map}")
    _emit({
        "status": "completed",
        "stage": "completed",
        "message": f"Huấn luyện hoàn tất: {len(label_map)} người, {len(faces_data)} ảnh.",
        "progress": 100,
        "model_path": model_path,
        "label_map_path": label_map_path,
        "report_path": report_path,
        "accuracy": report.get("accuracy", 0.0),
        "trained_images": len(faces_data),
        "trained_people": list(label_map.values()),
    })
    return True


def _load_json(path: str) -> Dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        import json
        return json.load(f)


def quick_live_check(sample_frames: int = 10) -> None:
    model_path = config.FACE_MODEL_FILE
    label_map_path = os.path.join(config.FACE_MODEL_DIR, "label_map.pkl")
    thresholds_path = os.path.join(config.FACE_MODEL_DIR, "face_thresholds.json")

    if not os.path.exists(model_path) or not os.path.exists(label_map_path):
        print("❌ Chưa có model hoặc label_map. Hãy train trước.")
        return

    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.read(model_path)

    import pickle
    with open(label_map_path, "rb") as f:
        label_map = pickle.load(f)

    thresholds = _load_json(thresholds_path)

    cap = cv2.VideoCapture(config.CAMERA_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  config.FACE_FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FACE_FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, config.CAMERA_FPS)

    if not cap.isOpened():
        print("❌ Không thể mở webcam để kiểm tra nhanh.")
        return

    print(f"\n[QuickCheck] Bắt đầu kiểm tra nhanh {sample_frames} frame...")
    counts: Dict[str, int] = {}

    for idx in range(sample_frames):
        ret, frame = cap.read()
        if not ret:
            print(f"  Frame {idx + 1}: Không đọc được ảnh")
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(config.FACE_MIN_SIZE, config.FACE_MIN_SIZE),
        )
        face_box = _select_largest_face(faces)
        if not face_box:
            print(f"  Frame {idx + 1}: Không thấy khuôn mặt")
            continue

        x, y, w, h = face_box
        face_roi = gray[y:y + h, x:x + w]
        face_roi, _ = _align_face(face_roi)
        face_roi = _apply_clahe(face_roi)
        face_roi = cv2.resize(face_roi, (160, 160))

        label_id, confidence = recognizer.predict(face_roi)
        similarity = _confidence_to_similarity(confidence)
        person_name = label_map.get(label_id, "Unknown")

        threshold = config.FACE_CONFIDENCE_THRESHOLD
        if person_name in thresholds:
            threshold = float(thresholds[person_name].get("recommended_threshold", threshold))
        threshold = max(config.FACE_THRESHOLD_FLOOR, min(config.FACE_CONFIDENCE_THRESHOLD, threshold))

        result = person_name if similarity >= threshold else "Unknown"
        counts[result] = counts.get(result, 0) + 1
        print(f"  Frame {idx + 1}: {result} ({similarity:.0%})")

    cap.release()
    print("[QuickCheck] Tổng hợp:")
    for name, total in sorted(counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  - {name}: {total}")


# =====================================================================
# Chạy trực tiếp để đăng ký khuôn mặt
# =====================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    print("\n🏠 YoloHome - Đăng ký khuôn mặt")
    print("=" * 40)
    person_name = input("Nhập tên người cần đăng ký (không dấu, không space): ").strip()

    if not person_name:
        print("❌ Tên không được để trống!")
        sys.exit(1)

    # Bước 1: Thu thập ảnh
    num_samples = 80
    raw_input = input("Số lượng ảnh cần chụp (mặc định 80): ").strip()
    if raw_input.isdigit():
        num_samples = max(20, int(raw_input))
    success = collect_face_samples(person_name, num_samples=num_samples)

    if success:
        # Bước 2: Huấn luyện lại model ngay sau khi thu thập
        retrain = input("\nHuấn luyện lại model ngay bây giờ? (y/n): ").strip().lower()
        if retrain == "y":
            if train_face_model():
                quick = input("\nChạy kiểm tra nhanh 10 frame live? (y/n): ").strip().lower()
                if quick == "y":
                    quick_live_check(sample_frames=10)
    else:
        print("\n❌ Thu thập ảnh thất bại. Vui lòng thử lại.")
