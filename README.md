# 🏠 YoloHome — Hệ thống Nhà thông minh IoT + AI

> **Môn học:** Đồ án Đa Ngành  
**Phần cứng:** Yolo:Bit | **Gateway:** Python 3.13 | **Cloud:** Adafruit IO

---

## Giới thiệu

YoloHome là hệ thống nhà thông minh tích hợp **IoT** và **AI**, cho phép giám sát môi trường, điều khiển thiết bị và bảo mật thông minh thông qua ba tính năng chính:

| Tính năng | Công nghệ | Yêu cầu |
|---|---|---|
| 📊 Giám sát & điều khiển | Adafruit IO MQTT + Yolo:Bit Serial | REQ-01 đến REQ-04 |
| 📷 Nhận diện khuôn mặt | OpenCV LBPH (Edge Processing) | REQ-08, REQ-09 |
| 🎙️ Điều khiển giọng nói | Google STT + NLP + gTTS | REQ-05, REQ-06 |

---

## Kiến trúc hệ thống

```
[Yolo:Bit]  ──Serial──►  [Gateway Python]  ──MQTT──►  [Adafruit IO]  ──►  [Dashboard]
                               │
                    ┌──────────┴──────────┐
                    ▼                     ▼
             [FaceAI Thread]      [VoiceAI Thread]
             (Webcam → LBPH)      (Mic → STT → NLP)
```

---

## Cấu trúc thư mục

```
DADN/
├── gateway/               ← Toàn bộ code Gateway Python
│   ├── config.py          ← Cấu hình hệ thống
│   ├── main.py            ← Entry point
│   ├── .env               ← API keys (không commit)
│   ├── requirements.txt
│   ├── core/              ← Singleton MQTT & Serial
│   ├── ai/
│   │   ├── face_recognition/  ← LBPH FaceAI
│   │   └── voice_control/     ← STT + NLP + TTS + Gemini RAG
│   ├── sensors/           ← Đọc cảm biến + ngưỡng cảnh báo
│   └── logs/              ← Log hệ thống + ảnh sự kiện
├── document/              ← Tài liệu dự án
│   └── PROGRESS.md        ← Nhật ký tiến độ
└── README.md
```

---

## Hướng dẫn cài đặt & chạy

### Yêu cầu hệ thống
- Python 3.10+
- Webcam (cho FaceAI)
- Microphone (cho VoiceAI)
- Mạch Yolo:Bit kết nối qua USB Serial
- Tài khoản [Adafruit IO](https://io.adafruit.com)

---

### Bước 1 — Cài đặt thư viện

```bash
cd gateway
pip install -r requirements.txt
```

> ⚠️ **Lưu ý PyAudio (Microphone):** Cần cài PortAudio trước trên Windows:
> ```bash
> pip install PyAudio‑0.2.14‑cp313‑cp313‑win_amd64.whl
> ```

---

### Bước 2 — Cấu hình

Sao chép và điền thông tin vào file `.env`:

```bash
cp .env.example .env
```

```env
ADAFRUIT_USERNAME=your_username
ADAFRUIT_AIO_KEY=your_aio_key
GEMINI_API_KEY=your_gemini_key   # Tùy chọn, cho RAG Assistant
```

Kiểm tra lại `config.py` để chỉnh:
- `SERIAL_PORT` — Cổng COM của Yolo:Bit (mặc định `COM3`)
- `TEMP_THRESHOLD`, `GAS_THRESHOLD` — Ngưỡng cảnh báo

---

### Bước 3 — Đăng ký khuôn mặt (chỉ làm 1 lần)

```bash
python ai/face_recognition/face_register.py
```

- Nhập tên người dùng khi được hỏi
- Hệ thống tự động chụp **50 ảnh** qua webcam
- Sau khi chụp xong, chọn `y` để train model ngay

---

### Bước 4 — Chạy Gateway

```bash
# Chạy đầy đủ (Sensor + FaceAI + VoiceAI)
python main.py

# Chỉ chạy Sensor + MQTT (không cần webcam/mic)
python main.py --no-face --no-voice

# Tắt Face Recognition
python main.py --no-face

# Tắt Voice Control
python main.py --no-voice

# Chế độ simulation (không cần Yolo:Bit)
python main.py --sim
```

---

## Các feeds Adafruit IO cần tạo

| Feed Name | Loại | Mô tả |
|---|---|---|
| `yolohome-temperature` | Gauge/Line chart | Nhiệt độ (°C) |
| `yolohome-humidity` | Gauge/Line chart | Độ ẩm (%) |
| `yolohome-gas` | Gauge | Nồng độ khí gas (ppm) |
| `yolohome-led` | Toggle | Điều khiển đèn |
| `yolohome-fan` | Toggle | Điều khiển quạt |
| `yolohome-pump` | Toggle | Điều khiển máy bơm |
| `yolohome-door` | Toggle | Điều khiển cửa |
| `yolohome-alert` | Text/Notification | Cảnh báo hệ thống |
| `yolohome-log` | Stream | Nhật ký hoạt động |

---

## Giao thức Serial với Yolo:Bit

**PC → Yolo:Bit** (lệnh điều khiển):
```json
{"cmd": "set", "device": "led",  "value": 1}
{"cmd": "set", "device": "fan",  "value": 0}
{"cmd": "set", "device": "pump", "value": 1}
{"cmd": "set", "device": "door", "value": 1}
```

**Yolo:Bit → PC** (dữ liệu cảm biến):
```json
{"sensor": "temp", "value": 28.5}
{"sensor": "humi", "value": 65.0}
{"sensor": "gas",  "value": 120}
```

---

## Lệnh giọng nói được hỗ trợ

Nói **"Yolo ơi"** để đánh thức, sau đó đọc lệnh:

| Lệnh mẫu | Hành động |
|---|---|
| `"Yolo ơi, bật đèn"` | Bật đèn |
| `"Yolo ơi, tắt quạt"` | Tắt quạt |
| `"Yolo ơi, mở cửa"` | Mở cửa |
| `"Yolo ơi, bật máy bơm"` | Bật máy bơm |
| `"Yolo ơi, tắt đèn"` | Tắt đèn |

---

## Tài liệu thêm

- [`document/PROGRESS.md`](document/PROGRESS.md) — Nhật ký tiến độ chi tiết từng module

---

## Yêu cầu phi chức năng đã đáp ứng

| Yêu cầu | Giải pháp |
|---|---|
| Độ trễ < 2s | Singleton MQTT (không tạo lại kết nối), thread riêng cho AI |
| Chu kỳ 5s/lần | `SensorReader` publish loop với `time.sleep(5)` |
| FaceAI chạy local | LBPH Recognizer — OpenCV, không cần internet |
| Auto-reconnect MQTT | `_schedule_reconnect()` với thread daemon |
| Không crash khi lỗi | Try/except riêng cho từng loại lỗi Voice AI |
| Thread-safe | `threading.Lock` cho Singleton, data cache, write Serial |
