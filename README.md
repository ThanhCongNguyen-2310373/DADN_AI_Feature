# YoloHome — Hệ thống Nhà thông minh IoT + AI

> **Môn học:** Đồ án Đa Ngành  
> **Phần cứng:** Yolo:Bit | **Gateway:** Python 3.13 | **Cloud:** Adafruit IO

---

## Tính năng

| Nhóm | Tính năng | Công nghệ |
| --- | --- | --- |
| 📡 IoT | Đọc cảm biến, điều khiển thiết bị | Yolo:Bit Serial + Adafruit IO MQTT |
| 📷 AI – Vision | Nhận diện khuôn mặt (edge) | OpenCV LBPH |
| 🎤 AI – Voice | Điều khiển giọng nói + hỏi đáp multi-turn | Google STT · Gemini 2.5 Flash · gTTS |
| 💾 Storage | Lưu lịch sử cảm biến & sự kiện thiết bị | SQLite (WAL) / PostgreSQL |
| 📊 Dashboard | Giao diện web real-time + biểu đồ 24h | FastAPI · WebSocket · Chart.js 4 |
| 🔔 Alert | Cảnh báo nhiệt độ / khí gas / người lạ | Telegram Bot |
| 🔐 Security | Đăng nhập, Profile + RBAC, Rate Limiting | Session DB · SHA-256 · memory/redis |
| 🤖 Automation | Quy tắc If-Then tự động bật/tắt thiết bị | Rule Engine Singleton |
| 🌤 Weather | Thời tiết thực tế tích hợp vào Voice + API | OpenWeatherMap REST API |
| 📈 ML Analytics | Dự báo năng lượng và phát hiện bất thường | Numpy · Linear trend · Z-score |
| 🔭 Observability | Metrics, structured logging, tracing tùy chọn | Prometheus client · OpenTelemetry |
| 📝 API Docs | Swagger UI phân nhóm, ví dụ đầy đủ | FastAPI OpenAPI 4.0 |

---

## Kiến trúc

```text
[Yolo:Bit] --Serial--> [Gateway Python]
                              |
  +------------+------+---------+---------+------------+
  v            v      v         v         v            v
[SensorReader] [FaceAI] [VoiceAI] [RuleEngine] [Auth/RBAC] [RateLimit]
 MQTT publish  LBPH Cam STT->RAG   If-Then      Session DB  memory/redis
  |            |      |            |             |            |
  +------------+------+------------+-------------+------------+
            |
            v
         [SQLite / PostgreSQL]
            |
        [Observability Layer]
        Structured logs · /metrics · tracing
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
├── config.py                  <- Cấu hình hệ thống (Phase 5)
├── .env                       <- API keys (không commit)
├── requirements.txt
├── requirements.in            <- Top-level dependencies cho lock
├── requirements.lock.txt      <- Lock file dùng cho CI/release
├── core/
│   ├── mqtt_client.py         <- Singleton MQTT (Adafruit IO)
│   ├── serial_client.py       <- Singleton Serial (Yolo:Bit)
│   ├── database.py            <- DB abstraction + SQLite backend
│   ├── database_postgres.py   <- PostgreSQL backend (Phase 5)
│   ├── auth_service.py        <- Auth + session + RBAC service
│   ├── rate_limiter.py        <- memory/redis rate limiter
│   ├── observability.py       <- structured log, metrics, tracing
│   ├── ml_analytics.py        <- forecast + anomaly detection
│   ├── telegram_notifier.py   <- Telegram Bot alerts
│   ├── rule_engine.py         <- Rule Engine Singleton
│   └── weather_service.py     <- OpenWeatherMap Singleton + cache
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
│   ├── app.py                 <- FastAPI + Auth/RBAC + ML + Observability API
│   ├── templates/
│   │   ├── index.html
│   │   ├── login.html
│   │   └── members.html
│   └── static/
│       ├── css/style.css
│       └── js/dashboard.js
├── data/
│   └── yolohome.db
├── scripts/
│   └── lock_dependencies.ps1  <- Script tạo lock dependencies
├── tests/
│   ├── test_smoke.py
│   └── test_e2e_phase5.py
├── logs/
├── .github/workflows/
│   ├── smoke-tests.yml
│   ├── e2e-tests.yml
│   └── release.yml
└── document/
    ├── PROGRESS_v3.md
    ├── PROGRESS_v4.md         <- (Phase 4) Nhật ký đầy đủ Phase 4
    ├── PROGRESS_v5.md         <- (Phase 5) Báo cáo triển khai production scale
    ├── TONG_HOP_BAO_CAO_TIENDO.md
    └── guide.md               <- Hướng dẫn chạy và test
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
WEB_SESSION_TTL=28800

# Phase 5 — DB backend
DATABASE_BACKEND=sqlite
DATABASE_PATH=data/yolohome.db

# PostgreSQL (nếu DATABASE_BACKEND=postgresql)
POSTGRES_DSN=
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=yolohome
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres

# Phase 5 — Rate limiting backend
RATE_LIMIT_BACKEND=memory
RATE_LIMIT_MAX_ATTEMPTS=5
RATE_LIMIT_WINDOW_SECS=300
REDIS_URL=redis://localhost:6379/0

# Phase 5 — Observability
LOG_STRUCTURED=1
METRICS_ENABLED=1
TRACING_ENABLED=0
OTLP_ENDPOINT=http://localhost:4318/v1/traces

# Phase 4 — OpenWeatherMap
OPENWEATHER_API_KEY=your_openweather_key
OPENWEATHER_CITY=Ho Chi Minh City
```

### 4. Chạy hệ thống

```powershell
python main.py
python main.py --no-face --no-voice   # Chỉ Sensor + Web
```

### 5. Chạy test

```powershell
pytest -q
```

### 6. Tùy chọn lock dependency

```powershell
.\scripts\lock_dependencies.ps1
```

### 7. Truy cập

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
| `GET /api/me` | GET | Hồ sơ user hiện tại |
| `GET /api/users` | GET | Danh sách user (admin) |
| `POST /api/users` | POST | Tạo user mới (admin) |
| `PATCH /api/users/{user_id}/role` | PATCH | Cập nhật role (admin) |
| `GET /api/sensors` | GET | Dữ liệu cảm biến hiện tại |
| `POST /api/control` | POST | Điều khiển thiết bị |
| `GET /api/history` | GET | Lịch sử cảm biến |
| `GET /api/energy` | GET | Báo cáo năng lượng |
| `GET /api/weather` | GET | Thời tiết OpenWeatherMap |
| `GET /api/rules` | GET | Danh sách rules |
| `POST /api/rules` | POST | Tạo rule mới |
| `DELETE /api/rules/{rule_id}` | DELETE | Xoá rule |
| `PATCH /api/rules/{rule_id}/toggle` | PATCH | Bật/tắt rule |
| `POST /api/voice/ask` | POST | Hỏi đáp Voice Assistant qua HTTP |
| `GET /api/ml/forecast` | GET | Dự báo năng lượng ngắn hạn |
| `GET /api/ml/anomalies` | GET | Phát hiện bất thường dữ liệu |
| `GET /health` | GET | Health check service |
| `GET /metrics` | GET | Prometheus metrics |
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

```text
"bật đèn"  /  "tắt quạt"  /  "mở cửa"
"nhiệt độ hiện tại bao nhiêu?"
"hôm nay trời có mưa không?"       <- (Phase 4) gọi OpenWeatherMap
"thời tiết TP.HCM như thế nào?"    <- (Phase 4) gọi OpenWeatherMap
```

---

## Tài liệu

- [`document/PROGRESS_v5.md`](document/PROGRESS_v5.md) — Nhật ký triển khai Phase 5
- [`document/TONG_HOP_BAO_CAO_TIENDO.md`](document/TONG_HOP_BAO_CAO_TIENDO.md) — Tổng hợp tiến độ Phase 1 -> 5
- [`document/PROGRESS_v4.md`](document/PROGRESS_v4.md) — Nhật ký Phase 4
- [`document/PROGRESS_v3.md`](document/PROGRESS_v3.md) — Nhật ký Phase 1–3
- [`document/guide.md`](document/guide.md) — Hướng dẫn chạy và test chi tiết
