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
import logging

# Thêm thư mục gốc để import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import config

logger = logging.getLogger(__name__)

# =====================================================================
# Tải Haar Cascade detector (phát hiện khuôn mặt từ OpenCV built-in)
# =====================================================================
HAAR_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
face_cascade = cv2.CascadeClassifier(HAAR_CASCADE_PATH)


def collect_face_samples(person_name: str, num_samples: int = 50) -> bool:
    """
    Mở webcam và chụp num_samples ảnh khuôn mặt của người dùng.

    Quy trình:
      - Phát hiện khuôn mặt bằng Haar Cascade
      - Cắt ROI khuôn mặt, resize về 160x160 pixel
      - Lưu ảnh grayscale vào dataset/<person_name>/

    Args:
        person_name: Tên người dùng (sử dụng làm tên thư mục và nhãn)
        num_samples: Số lượng ảnh cần chụp (khuyến nghị >= 50)

    Returns:
        True nếu thu thập đủ ảnh, False nếu thất bại.
    """
    # Tạo thư mục lưu ảnh cho người dùng này
    person_dir = os.path.join(config.FACE_DATASET_DIR, person_name)
    os.makedirs(person_dir, exist_ok=True)
    logger.info(f"[FaceRegister] Thư mục lưu ảnh: {person_dir}")

    # Mở webcam
    cap = cv2.VideoCapture(config.CAMERA_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  config.FACE_FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FACE_FRAME_HEIGHT)

    if not cap.isOpened():
        logger.error("[FaceRegister] ❌ Không thể mở webcam!")
        return False

    print(f"\n{'='*50}")
    print(f"  Đang thu thập khuôn mặt của: {person_name}")
    print(f"  Cần chụp: {num_samples} ảnh")
    print(f"  Nhấn [Q] để huỷ | [SPACE] để chụp thủ công")
    print(f"{'='*50}\n")

    count = 0          # Số ảnh đã chụp
    auto_capture = True  # Chụp tự động khi phát hiện khuôn mặt

    while count < num_samples:
        ret, frame = cap.read()
        if not ret:
            logger.warning("[FaceRegister] Không đọc được frame từ webcam.")
            continue

        # Chuyển sang grayscale để phát hiện khuôn mặt nhanh hơn
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Phát hiện khuôn mặt trong frame
        faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(80, 80)   # Kích thước tối thiểu của khuôn mặt
        )

        for (x, y, w, h) in faces:
            # Vẽ khung xanh quanh khuôn mặt
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

            if auto_capture:
                # Cắt vùng khuôn mặt và resize về kích thước chuẩn
                face_roi = gray[y:y + h, x:x + w]
                face_resized = cv2.resize(face_roi, (160, 160))

                # Lưu ảnh: dataset/<person_name>/<person_name>_<count>.jpg
                img_path = os.path.join(person_dir, f"{person_name}_{count:03d}.jpg")
                cv2.imwrite(img_path, face_resized)
                count += 1
                logger.debug(f"[FaceRegister] Đã lưu ảnh {count}/{num_samples}: {img_path}")

        # Hiển thị thông tin lên màn hình preview
        cv2.putText(frame, f"Da chup: {count}/{num_samples}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        cv2.putText(frame, f"Nguoi dung: {person_name}",
                    (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 1)
        cv2.imshow(f"Thu thap khuon mat - {person_name}", frame)

        key = cv2.waitKey(100) & 0xFF  # Chờ 100ms giữa các frame
        if key == ord("q"):
            logger.info("[FaceRegister] Người dùng huỷ thu thập.")
            break

    cap.release()
    cv2.destroyAllWindows()

    if count >= num_samples:
        print(f"\n✅ Thu thập xong {count} ảnh cho '{person_name}'!")
        logger.info(f"[FaceRegister] ✅ Hoàn thành thu thập {count} ảnh cho '{person_name}'")
        return True
    else:
        print(f"\n⚠️  Chỉ thu thập được {count}/{num_samples} ảnh.")
        logger.warning(f"[FaceRegister] Chỉ thu thập được {count}/{num_samples} ảnh.")
        return count > 10  # Chấp nhận nếu có ít nhất 10 ảnh


def train_face_model():
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
    import numpy as np

    dataset_dir = config.FACE_DATASET_DIR
    model_dir   = config.FACE_MODEL_DIR
    os.makedirs(model_dir, exist_ok=True)

    faces_data   = []   # Danh sách mảng numpy của ảnh khuôn mặt
    labels       = []   # Danh sách nhãn số tương ứng
    label_map    = {}   # {label_id (int): person_name (str)}
    label_id     = 0

    print("\n[Training] Bắt đầu đọc dataset...")

    # Duyệt qua từng thư mục người dùng trong dataset/
    for person_name in sorted(os.listdir(dataset_dir)):
        person_dir = os.path.join(dataset_dir, person_name)
        if not os.path.isdir(person_dir):
            continue

        label_map[label_id] = person_name
        count = 0

        for img_file in os.listdir(person_dir):
            if not img_file.lower().endswith((".jpg", ".png", ".jpeg")):
                continue

            img_path = os.path.join(person_dir, img_file)
            img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)

            if img is None:
                logger.warning(f"[Training] Không đọc được ảnh: {img_path}")
                continue

            # Đảm bảo tất cả ảnh có cùng kích thước 160x160
            img = cv2.resize(img, (160, 160))
            faces_data.append(img)
            labels.append(label_id)
            count += 1

        print(f"  ✔ {person_name}: {count} ảnh (label_id={label_id})")
        logger.info(f"[Training] Nạp {count} ảnh cho '{person_name}' (id={label_id})")
        label_id += 1

    if len(faces_data) == 0:
        print("\n❌ Không tìm thấy ảnh trong dataset. Hãy chạy face_register trước.")
        logger.error("[Training] Dataset rỗng. Huấn luyện thất bại.")
        return False

    print(f"\n[Training] Tổng: {len(faces_data)} ảnh, {len(label_map)} người.")
    print("[Training] Đang huấn luyện LBPH model...")

    # Khởi tạo và huấn luyện LBPH Face Recognizer
    recognizer = cv2.face.LBPHFaceRecognizer_create(
        radius=1,
        neighbors=8,
        grid_x=8,
        grid_y=8
    )
    recognizer.train(faces_data, np.array(labels))

    # Lưu model .yml
    model_path = os.path.join(model_dir, "face_model.yml")
    recognizer.save(model_path)
    print(f"  ✅ Đã lưu model: {model_path}")

    # Lưu ánh xạ nhãn số → tên người dùng
    label_map_path = os.path.join(model_dir, "label_map.pkl")
    with open(label_map_path, "wb") as f:
        import pickle
        pickle.dump(label_map, f)
    print(f"  ✅ Đã lưu label map: {label_map_path}")

    logger.info(f"[Training] ✅ Huấn luyện xong. Model: {model_path} | Labels: {label_map}")
    return True


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
    success = collect_face_samples(person_name, num_samples=50)

    if success:
        # Bước 2: Huấn luyện lại model ngay sau khi thu thập
        retrain = input("\nHuấn luyện lại model ngay bây giờ? (y/n): ").strip().lower()
        if retrain == "y":
            train_face_model()
    else:
        print("\n❌ Thu thập ảnh thất bại. Vui lòng thử lại.")
