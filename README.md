# YoloHome — Hệ thống Nhà thông minh IoT + AI

> **Môn học:** Đồ án Đa Ngành  
> **Phần cứng:** Yolo:Bit | **Gateway:** Python 3.13 | **Cloud:** Adafruit IO

---

## Tính năng

| Nhóm | Tính năng | Công nghệ |
| --- | --- | --- |
| 📡 IoT | Đọc cảm biến, điều khiển thiết bị | Yolo:Bit Serial + Adafruit IO MQTT |
| 📷 AI – Vision | Nhận diện khuôn mặt (edge) | OpenCV LBPH |
| 🎤 AI – Voice | Điều khiển giọng nói + hỏi đáp multi-turn | Google STT · Gemini 1.5 Flash · gTTS |
| 💾 Storage | Lưu lịch sử cảm biến & sự kiện thiết bị | SQLite (WAL mode) |
| 📊 Dashboard | Giao diện web real-time + biểu đồ 24h | FastAPI · WebSocket · Chart.js 4 |
| 🔔 Alert | Cảnh báo nhiệt độ / khí gas / người lạ | Telegram Bot |
| 🔐 Security | Đăng nhập, Rate Limiting (brute-force protection) | Session Cookie · SHA-256 · Sliding Window |
| 🤖 Automation | Quy tắc If-Then tự động bật/tắt thiết bị | Rule Engine Singleton |
| 🌤 Weather | Thời tiết thực tế tích hợp vào Voice + API | OpenWeatherMap REST API |
| 📝 API Docs | Swagger UI phân nhóm, ví dụ đầy đủ | FastAPI OpenAPI 4.0 |

---

## Kiến trúc

```text
[Yolo:Bit] --Serial--> [Gateway Python]
                              |
              +-----------+---+-------+-----------+
              v           v           v           v
       [SensorReader] [FaceAI]   [VoiceAI]  [RuleEngine]
       MQTT publish   LBPH Cam   STT->Gemini  If-Then rules
              |           |           |           |
              v           v           v           v
         [SQLite DB] [Telegram]  [WeatherSvc] [MQTT fire]
              |
              v
    [FastAPI Web :8000]
    WebSocket · Chart.js · Auth · Swagger UI
```

---

## Cấu trúc thư mục

```text
gateway/
├── main.py                    <- Entry point
├── config.py                  <- Cấu hình (+ Phase 4: OpenWeatherMap)
├── .env                       <- API keys (không commit)
├── requirements.txt
├── core/
│   ├── mqtt_client.py         <- Singleton MQTT (Adafruit IO)
│   ├── serial_client.py       <- Singleton Serial (Yolo:Bit)
│   ├── database.py            <- SQLite Singleton (+ automation_rules, rule_logs)
│   ├── telegram_notifier.py   <- Telegram Bot alerts
│   ├── rule_engine.py         <- (Phase 4) Rule Engine Singleton
│   └── weather_service.py     <- (Phase 4) OpenWeatherMap Singleton + cache
├── sensors/
│   └── sensor_reader.py       <- Đọc cảm biến + Rule Engine call
├── ai/
│   ├── face_recognition/
│   │   ├── face_recognizer.py
│   │   ├── face_register.py
│   │   ├── dataset/
│   │   └── model/
│   └── voice_control/
│       └── voice_assistant.py <- STT + RAG + TTS + Weather query
├── web_app/
│   ├── app.py                 <- FastAPI + Auth + Rate Limiting + Rule/Weather API
│   ├── templates/
│   │   ├── index.html
│   │   ├── login.html
│   │   └── members.html
│   └── static/
│       ├── css/style.css
│       └── js/dashboard.js
├── data/
│   └── yolohome.db
├── logs/
└── document/
    ├── PROGRESS_v3.md
    ├── PROGRESS_v4.md         <- (Phase 4) Nhật ký đầy đủ Phase 4
    └── guide.md               <- Hướng dẫn chạy và test (Phase 3 + 4)
```

---

## Cài đặt & chạy

### 1. Tạo virtual environment

```powershell
cd D:\HK252\DADN
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 2. Cài dependencies

```powershell
cd gateway
pip install -r requirements.txt
```

### 3. Cấu hình `.env`

```env
ADAFRUIT_USERNAME=your_username
ADAFRUIT_AIO_KEY=your_aio_key
GEMINI_API_KEY=your_gemini_key

TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

WEB_USERNAME=admin
WEB_PASSWORD=yolohome2025
WEB_PORT=8000

# Phase 4 — OpenWeatherMap
OPENWEATHER_API_KEY=your_openweather_key
OPENWEATHER_CITY=Ho Chi Minh City
```

### 4. Chạy hệ thống

```powershell
python main.py
python main.py --no-face --no-voice   # Chỉ Sensor + Web
```

### 5. Truy cập

| URL | Mô tả |
| --- | --- |
| `http://localhost:8000` | Dashboard chính |
| `http://localhost:8000/docs` | Swagger UI |
| `http://localhost:8000/login` | Đăng nhập |

---

## Web API

| Endpoint | Method | Mô tả |
| --- | --- | --- |
| `GET /` | GET | Dashboard chính |
| `GET/POST /login` | — | Đăng nhập (rate-limited 5/5min) |
| `GET /api/sensors` | GET | Dữ liệu cảm biến hiện tại |
| `POST /api/control` | POST | Điều khiển thiết bị |
| `GET /api/history` | GET | Lịch sử cảm biến |
| `GET /api/energy` | GET | Báo cáo năng lượng |
| `GET /api/weather` | GET | **(Phase 4)** Thời tiết OpenWeatherMap |
| `GET /api/rules` | GET | **(Phase 4)** Danh sách rules |
| `POST /api/rules` | POST | **(Phase 4)** Tạo rule mới |
| `DELETE /api/rules/{id}` | DELETE | **(Phase 4)** Xoá rule |
| `PATCH /api/rules/{id}/toggle` | PATCH | **(Phase 4)** Bật/tắt rule |
| `POST /api/face/enroll` | POST | Đăng ký khuôn mặt |
| `WS /ws/sensors` | WS | Real-time sensor stream |

---

## Rule Engine — Ví dụ

Tạo quy tắc tự động qua `POST /api/rules`:

```json
{
  "name": "Bật quạt khi nhiệt độ > 35°C",
  "condition_field": "temp",
  "condition_op": ">",
  "condition_value": 35,
  "action_device": "fan",
  "action_state": 1,
  "notify_telegram": true,
  "enabled": true
}
```

Hệ thống sẽ **tự động bật quạt** và **gửi Telegram** mỗi khi nhiệt độ vượt 35°C (cooldown 60 giây).

---

## Lệnh giọng nói

Nói **"yolo"** để kích hoạt:

```
"bật đèn"  /  "tắt quạt"  /  "mở cửa"
"nhiệt độ hiện tại bao nhiêu?"
"hôm nay trời có mưa không?"       <- (Phase 4) gọi OpenWeatherMap
"thời tiết TP.HCM như thế nào?"    <- (Phase 4) gọi OpenWeatherMap
```

---

## Tài liệu

- [`document/PROGRESS_v4.md`](document/PROGRESS_v4.md) — Nhật ký Phase 4
- [`document/PROGRESS_v3.md`](document/PROGRESS_v3.md) — Nhật ký Phase 1–3
- [`document/guide.md`](document/guide.md) — Hướng dẫn chạy và test chi tiết
