# 📋 Nhật ký tiến độ phát triển - YoloHome Gateway

> **Môn học:** Đồ án Đa Ngành  
> **Ngày cập nhật:** 10/03/2026

---

## Tổng quan dự án

**YoloHome** là hệ thống Nhà thông minh tích hợp IoT và AI, với thành phần trung tâm là **Gateway Python** chạy trên máy tính, đóng vai trò cầu nối giữa phần cứng Yolo:Bit và nền tảng đám mây Adafruit IO.

---

## ✅ Những gì đã hoàn thành

### Giai đoạn 1 — Kiến trúc & Cấu trúc dự án

**Ngày hoàn thành:** 10/03/2026

Đã thiết kế và khởi tạo toàn bộ cấu trúc thư mục cho module Gateway Python:

```
gateway/
├── config.py                    ✅ Tập trung cấu hình, đọc .env
├── main.py                      ✅ Entry point + multi-threading orchestration
├── requirements.txt             ✅ Danh sách dependency
├── .env                         ✅ Biến môi trường bảo mật (git-ignored)
├── .gitignore                   ✅ Bảo vệ key, dataset, logs
├── core/
│   ├── mqtt_client.py           ✅ Singleton MQTT + auto-reconnect
│   └── serial_client.py         ✅ Singleton Serial + read loop
├── ai/
│   ├── face_recognition/
│   │   ├── face_register.py     ✅ Thu thập ảnh + train LBPH model
│   │   └── face_recognizer.py   ✅ Nhận diện realtime + xử lý logic cửa
│   └── voice_control/
│       └── voice_assistant.py   ✅ STT → NLP → MQTT → TTS + Gemini RAG
├── sensors/
│   └── sensor_reader.py         ✅ Đọc Serial + publish MQTT + threshold
└── logs/                        ✅ Lưu log hệ thống + ảnh sự kiện
```

---

### Giai đoạn 2 — Lớp Core (Singleton Pattern)

**Ngày hoàn thành:** 10/03/2026

#### `core/mqtt_client.py` — MQTTSingleton
- **Pattern:** Singleton thread-safe (double-checked locking với `threading.Lock`)
- **Kết nối:** Adafruit IO MQTT Broker (`io.adafruit.com:1883`)
- **Auto-reconnect:** Khi mất kết nối, tự động lên lịch reconnect sau 5 giây trong thread riêng → đáp ứng **NFR 2.2**
- **Subscribe routing:** Ánh xạ feed → callback function, tự động re-subscribe sau reconnect
- **API công khai:**
  - `MQTTSingleton.get_instance()` — Lấy instance duy nhất
  - `publish(feed, value)` — Gửi dữ liệu lên Adafruit
  - `subscribe(feed, callback)` — Đăng ký lắng nghe feed
  - `is_connected` — Trạng thái kết nối

#### `core/serial_client.py` — SerialSingleton
- **Pattern:** Singleton thread-safe
- **Giao thức:** JSON qua UART với Yolo:Bit (baudrate 115200)
  - Gửi xuống: `{"cmd": "set", "device": "fan", "value": 1}`
  - Nhận lên: `{"sensor": "temp", "value": 28.5}`
- **Read loop:** Thread nền liên tục đọc dữ liệu Serial, điều hướng đến callback
- **Write lock:** `threading.Lock` riêng cho thao tác ghi, tránh race condition
- **Simulation mode:** Hoạt động bình thường khi không có phần cứng (log giả lập)
- **API công khai:**
  - `SerialSingleton.get_instance()` — Lấy instance duy nhất
  - `send_command(device, value)` — Gửi lệnh xuống Yolo:Bit
  - `register_sensor_callback(sensor, callback)` — Đăng ký nhận dữ liệu cảm biến

---

### Giai đoạn 3 — Module FaceAI (Nhận diện khuôn mặt)

**Ngày hoàn thành:** 10/03/2026  
**Yêu cầu thực hiện:** REQ-08, REQ-09

#### `ai/face_recognition/face_register.py`
- **Mục đích:** Thu thập ảnh khuôn mặt và huấn luyện model (chạy một lần trước khi deploy)
- **Detector:** Haar Cascade (`haarcascade_frontalface_default.xml`) — OpenCV built-in
- **Thu thập:** Tự động chụp 50 ảnh/người, resize về 160×160 grayscale
- **Model AI:** LBPH Face Recognizer (`cv2.face.LBPHFaceRecognizer`) — chạy hoàn toàn local (Edge Processing - **NFR 2.2**)
- **Lý do chọn LBPH:** Nhẹ, không cần GPU, phù hợp IoT/embedded, không phụ thuộc internet
- **Output:** `trained_model/face_model.yml` + `trained_model/label_map.pkl`

#### `ai/face_recognition/face_recognizer.py`
- **Mục đích:** Nhận diện khuôn mặt thời gian thực từ webcam
- **Threading:** Chạy trong thread daemon riêng, không block luồng đọc cảm biến
- **Ngưỡng nhận diện:** `FACE_CONFIDENCE_THRESHOLD = 0.55` (configurable)
- **Logic REQ-08 — Chủ nhà:**
  - Nhận diện thành công → Publish MQTT `yolohome-door: ON`
  - Cooldown 10s giữa 2 lần mở cửa (chống spam lệnh)
  - Lưu ảnh log khi mở cửa
- **Logic REQ-09 — Người lạ:**
  - Bắt đầu đếm thời gian khi phát hiện khuôn mặt lạ
  - Sau `FACE_STRANGER_TIMEOUT = 10s` liên tục → gửi cảnh báo MQTT
  - Publish lên `yolohome-alert` và `yolohome-log`
  - Lưu ảnh bằng chứng vào `logs/face_events/`
- **FPS:** Giới hạn ~10 FPS (`sleep(0.1)`) để giảm tải CPU

---

### Giai đoạn 4 — Module Voice Control

**Ngày hoàn thành:** 10/03/2026  
**Yêu cầu thực hiện:** REQ-05, REQ-06

#### `ai/voice_control/voice_assistant.py`
- **Pipeline:** Wake Word → Ghi âm → STT → NLP → MQTT → TTS
- **Wake Word:** `"yolo"` (configurable trong `config.py`)
- **STT:** Google Web Speech API qua thư viện `SpeechRecognition`, ngôn ngữ `vi-VN`
- **NLP:** Regex + từ điển tiếng Việt (không cần model AI nặng)
  - Nhận diện hành động: `bật/mở/khởi động` → `ON`, `tắt/đóng/ngắt` → `OFF`
  - Nhận diện thiết bị: `đèn`, `quạt`, `máy bơm`, `cửa`
  - Độ chính xác mục tiêu: >95% với lệnh cơ bản (**NFR 2.3**)
- **MQTT mapping:** Action + Device → Feed tương ứng trên Adafruit
- **TTS:** gTTS + pygame phát âm phản hồi tiếng Việt trong thread riêng
- **Fault tolerance:** Try/except cho từng loại lỗi (timeout, unknown, API error) → **NFR 2.2** không crash hệ thống
- **Bonus — GeminiRAGAssistant:** Class tùy chọn tích hợp LangChain + Gemini Pro + FAISS vector store cho các câu hỏi phức tạp ngoài tập lệnh điều khiển cơ bản

---

### Giai đoạn 5 — Module Sensor & Dashboard Sync

**Ngày hoàn thành:** 10/03/2026  
**Yêu cầu thực hiện:** REQ-01, REQ-02, REQ-04, REQ-07

#### `sensors/sensor_reader.py`
- **Đọc cảm biến:** Nhận callback từ SerialSingleton (temp, humi, gas) theo sự kiện
- **Publish định kỳ:** Thread riêng, chu kỳ 5 giây (**REQ-01**), publish lên 3 feed Adafruit
- **Thread-safety:** `threading.Lock` bảo vệ `_sensor_data` dict
- **Threshold (REQ-07):**
  - Nhiệt độ > 35°C → Alert MQTT + **tự động bật quạt** qua Serial
  - Khí gas > 300 ppm → Alert khẩn cấp MQTT
  - Cooldown 30s giữa 2 cảnh báo cùng loại (chống spam)
- **Nhận lệnh từ Dashboard (REQ-04):**
  - Subscribe `yolohome-led`, `yolohome-fan`, `yolohome-pump`
  - Forward lệnh xuống Yolo:Bit qua Serial
  - Ghi log mỗi lần điều khiển

---

### Giai đoạn 6 — Main Gateway & Multi-threading

**Ngày hoàn thành:** 10/03/2026

#### `main.py` — YoloHomeGateway
- **Orchestration:** Khởi động tuần tự, an toàn, có xử lý ngoại lệ từng module
- **Thread architecture:**

| Thread | Module | Daemon | Vai trò |
|---|---|---|---|
| Main Thread | `YoloHomeGateway` | No | Heartbeat loop, signal handler |
| Serial-ReadLoop | `SerialSingleton` | Yes | Đọc liên tục dữ liệu từ Yolo:Bit |
| MQTT-Reconnect | `MQTTSingleton` | Yes | Auto-reconnect khi mất mạng |
| SensorReader-Thread | `SensorReader` | Yes | Publish cảm biến 5s/lần |
| FaceAI-Thread | `FaceRecognizer` | Yes | Nhận diện khuôn mặt realtime |
| VoiceAI-Thread | `VoiceAssistant` | Yes | Lắng nghe giọng nói liên tục |
| TTS-Thread | `VoiceAssistant._speak()` | Yes | Phát âm thanh phản hồi |

- **Graceful shutdown:** `signal.SIGINT/SIGTERM` → gọi `stop()` tuần tự từng module → đóng Serial
- **CLI flags:** `--no-face`, `--no-voice`, `--sim` để linh hoạt khi test
- **Heartbeat:** Log trạng thái đầy đủ mỗi 30 giây

---

### Giai đoạn 7 — Môi trường & Dependency

**Ngày hoàn thành:** 10/03/2026

#### Thư viện đã cài đặt (Python 3.13, venv)

| Nhóm | Thư viện | Phiên bản | Mục đích |
|---|---|---|---|
| IoT/MQTT | `Adafruit-IO` | 2.7.2 | MQTT client Adafruit |
| IoT/Serial | `pyserial` | 3.5 | Giao tiếp Yolo:Bit |
| Computer Vision | `opencv-python` | 4.13.0 | Xử lý hình ảnh |
| Computer Vision | `opencv-contrib-python` | 4.13.0 | LBPH FaceRecognizer |
| Voice | `SpeechRecognition` | 3.10.1 | Google STT |
| Voice | `gTTS` | 2.5.1 | Text-to-Speech tiếng Việt |
| Voice | `pygame` | 2.5.2 | Phát âm thanh |
| AI/RAG | `langchain` | 0.2.0 | LLM orchestration |
| AI/RAG | `langchain-google-genai` | 1.0.3 | Gemini Pro |
| AI/RAG | `faiss-cpu` | 1.8.0 | Vector store RAG |
| Config | `python-dotenv` | 1.0.1 | Đọc .env |
| Math | `numpy` | 2.4.3 | Mảng số |

#### Bảo mật credentials
- API keys lưu trong `.env` (không commit lên git)
- `.gitignore` loại trừ `.env`, dataset ảnh, model files, log files

---

## 🔄 Việc còn lại (TODO)

| ID | Nhiệm vụ | Mức độ ưu tiên |
|---|---|---|
| T-01 | Viết code Yolo:Bit (MicroPython) đọc cảm biến và gửi Serial JSON | 🔴 Cao |
| T-02 | Thu thập ảnh khuôn mặt thực tế (`face_register.py`) | 🔴 Cao |
| T-03 | Tạo Dashboard trên Adafruit IO (feeds, blocks, gauges) | 🔴 Cao |
| T-04 | Test end-to-end toàn bộ luồng | 🔴 Cao |
| T-05 | Tích hợp PyAudio cho microphone thực (cần cài PortAudio) | 🟡 Trung bình |
| T-06 | Xây dựng `knowledge_base.txt` cho Gemini RAG | 🟡 Trung bình |
| T-07 | Viết unit test cho NLP intent extraction | 🟢 Thấp |
| T-08 | Tối ưu FPS webcam cho máy yếu | 🟢 Thấp |

---

## 📐 Kiến trúc luồng dữ liệu

```
[Yolo:Bit]
    │  Serial JSON (baudrate 115200)
    ▼
[SerialSingleton] ─── callback ──► [SensorReader]
                                         │
                                    publish mỗi 5s
                                         │
                                         ▼
                                  [MQTTSingleton]
                                         │
                                  MQTT to Adafruit IO
                                         │
                            ┌────────────┴────────────┐
                            ▼                         ▼
                     [Adafruit Dashboard]      [Subscribe lệnh]
                     (hiển thị realtime)             │
                                                      ▼
                                              [SensorReader]
                                                      │
                                               Serial command
                                                      ▼
                                               [Yolo:Bit]

[FaceRecognizer Thread] ──────────────────────────────────►
    Webcam → Haar Cascade → LBPH → MQTT door/alert

[VoiceAssistant Thread] ──────────────────────────────────►
    Mic → Wake Word → STT → NLP → MQTT device → TTS
```
