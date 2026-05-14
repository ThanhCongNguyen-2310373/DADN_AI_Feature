# PROGRESS v6

> **Ngày cập nhật:** 13/05/2026  
> **Giai đoạn:** chuẩn hóa MQTT, hoàn chỉnh luồng cửa, ổn định auth và lock dependencies

---

## 1. Tổng quan thay đổi

| # | Hạng mục | Trạng thái |
|---|----------|-----------|
| 1 | Chuẩn hóa feed MQTT trong Rule Engine | ✅ Hoàn thành |
| 2 | Hoàn thiện luồng door end-to-end | ✅ Hoàn thành |
| 3 | Đồng bộ cấu hình FaceAI (model file) | ✅ Hoàn thành |
| 4 | Chuẩn hóa auth response cho API | ✅ Hoàn thành |
| 5 | Tạo lock dependency thực sự | ✅ Hoàn thành |

---

## 2. Sửa lỗi ảnh hưởng chức năng

### 2.1 Chuẩn hóa feed MQTT trong Rule Engine
- Rule Engine đã publish bằng cùng chuẩn feed key như các module khác (`config.FEED_*`).
- Loại bỏ dạng feed kèm prefix `username/feeds/` để tránh lệch chuẩn.

### 2.2 Hoàn thiện luồng điều khiển cửa (door)
- **SensorReader** đã subscribe thêm `FEED_DOOR` để nhận lệnh mở/đóng.
- Lệnh door được chuyển xuống Serial với giá trị servo hợp lệ (0/90 hoặc góc tùy payload).
- `get_latest_data()` trả về trạng thái door để UI phản ánh đúng.
- Ghi `device_events` cho door theo logic **giá trị != 0 → ON**.
- UI đồng bộ trạng thái door theo số dương (tránh lệch khi nhận giá trị 90).

---

## 3. Ổn định vận hành và tái lập

### 3.1 Đồng bộ cấu hình FaceAI
- `FACE_MODEL_FILE` được cập nhật trỏ đúng `face_model.yml`.
- README đã thống nhất thư mục `trained_model/` và tên file model.

### 3.2 Chuẩn hóa auth response cho API
- Request **API** không đăng nhập nhận **401 JSON** thay vì 307 redirect.
- Request **UI** vẫn giữ redirect `/login` để trải nghiệm web ổn định.

### 3.3 Dependency lock thực sự
- Chạy `pip-tools` để sinh `requirements.lock.txt` đầy đủ phiên bản phụ thuộc.
- Workflow release tiếp tục cài đặt từ lock file để đảm bảo tái lập bản build.

---

## 4. Danh sách file cập nhật

- `core/rule_engine.py`
- `sensors/sensor_reader.py`
- `web_app/static/js/dashboard.js`
- `config.py`
- `ai/face_recognition/face_recognizer.py`
- `web_app/app.py`
- `README.md`
- `requirements.lock.txt`

---

