# YoloHome — Hệ thống Nhà thông minh IoT + AI

> **Môn học:** Đồ án Đa Ngành  
> **Phần cứng:** Yolo:Bit | **Gateway:** Python 3.13 | **Cloud:** Adafruit IO

---

## Tính năng

| Nhóm | Tính năng | Công nghệ |
| --- | --- | --- |
| 📡 IoT | Đọc cảm biến, điều khiển thiết bị | Yolo:Bit Serial + Adafruit IO MQTT |
| 📷 AI – Vision | Nhận diện khuôn mặt real-time | OpenCV LBPH + Web Dashboard |
| 🎤 AI – Voice | Điều khiển giọng nói + hỏi đáp multi-turn | Google STT · gemini-3-flash-preview · gTTS |
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
├── config.py                  <- Cấu hình hệ thống
├── .env                       <- API keys (không commit)
├── requirements.txt
├── requirements.in            <- Top-level dependencies
├── requirements.lock.txt      <- Lock file cho CI/release
├── core/
│   ├── mqtt_client.py         <- Singleton MQTT (Adafruit IO)
│   ├── serial_client.py       <- Singleton Serial (Yolo:Bit)
│   ├── database.py            <- DB abstraction + SQLite backend
│   ├── database_postgres.py   <- PostgreSQL backend
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
│   │   ├── face_recognizer.py <- Nhận diện khuôn mặt real-time
│   │   ├── face_register.py   <- Web enroll + train LBPH model
│   │   ├── dataset/           <- Ảnh mẫu khuôn mặt
│   │   └── trained_model/     <- face_model.yml + label_map.pkl
│   └── voice_control/
│       ├── voice_assistant.py <- STT + NLP + RAG + TTS
│       ├── intent_nlp.py      <- Intent classification (fastText/BoW)
│       ├── audio_preprocess.py <- VAD (webrtcvad) + RMS trim
│       ├── intent_lexicon.json <- Từ điển action/device/question
│       ├── intent_corpus.txt  <- Training corpus cho fastText/BoW
│       ├── knowledge_base.txt <- RAG knowledge base
│       └── device_manual_vi.txt <- Tài liệu thiết bị cho RAG
├── web_app/
│   ├── app.py                 <- FastAPI + Auth/RBAC + ML + Observability API
│   ├── templates/
│   │   ├── index.html         <- Dashboard chính
│   │   ├── login.html         <- Trang đăng nhập
│   │   └── members.html       <- Quản lý khuôn mặt
│   └── static/
│       ├── css/style.css
│       └── js/dashboard.js
├── data/
│   └── yolohome.db            <- SQLite database
├── scripts/
│   └── lock_dependencies.ps1  <- Script tạo lock dependencies
├── tests/
│   ├── test_smoke.py          <- Smoke test
│   ├── test_e2e_phase5.py     <- E2E test RBAC + IoT + AI
│   └── test_intent_nlp.py     <- Unit test intent classification
├── logs/
├── .github/workflows/
│   ├── smoke-tests.yml
│   ├── e2e-tests.yml
│   └── release.yml
└── document/
    ├── PROGRESS_v8.md          <- Nhật ký Voice AI v8 (STT/NLP/TTS/RAG)
    ├── PROGRESS_v7.md
    ├── PROGRESS_v6.md
    ├── PROGRESS_v5.md
    ├── PROGRESS_v4.md
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

# DB backend
DATABASE_BACKEND=sqlite
DATABASE_PATH=data/yolohome.db

# PostgreSQL (nếu DATABASE_BACKEND=postgresql)
POSTGRES_DSN=
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=yolohome
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres

# Rate limiting backend
RATE_LIMIT_BACKEND=memory
RATE_LIMIT_MAX_ATTEMPTS=5
RATE_LIMIT_WINDOW_SECS=300
REDIS_URL=redis://localhost:6379/0

# Observability
LOG_STRUCTURED=1
METRICS_ENABLED=1
TRACING_ENABLED=0
OTLP_ENDPOINT=http://localhost:4318/v1/traces

# OpenWeatherMap
OPENWEATHER_API_KEY=your_openweather_key
OPENWEATHER_CITY=Ho Chi Minh City

# Voice AI (tuỳ chọn)
VOICE_RNNOISE_ENABLED=1
VOICE_AUTO_ENERGY=1
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

### 6. Lock dependency

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
| `POST /api/voice/ask` | POST | Hỏi đáp Voice AI qua HTTP |
| `POST /api/voice/trigger-wake` | POST | Kích hoạt Voice AI từ Dashboard |
| `GET /api/chat` | GET | Lịch sử hội thoại Voice AI |
| `GET /api/ml/forecast` | GET | Dự báo năng lượng ngắn hạn |
| `GET /api/ml/anomalies` | GET | Phát hiện bất thường dữ liệu |
| `GET /health` | GET | Health check service |
| `GET /metrics` | GET | Prometheus metrics |
| `POST /api/face/enroll` | POST | Bắt đầu đăng ký khuôn mặt |
| `GET /api/face/enroll/status` | GET | Trạng thái phiên chụp khuôn mặt |
| `POST /api/face/train` | POST | Train LBPH model |
| `GET /api/face/train/status` | GET | Trạng thái train model |
| `GET /api/face/status` | GET | Trạng thái nhận diện real-time |
| `GET /api/face/log` | GET | Log nhận diện gần nhất |
| `GET /api/face/members` | GET | Danh sách thành viên đã đăng ký |
| `WS /ws/sensors` | WS | Real-time sensor stream |

---

## Voice AI — Chi tiết kỹ thuật

### Pipeline xử lý

```
Microphone → Wake Word Detection → VAD (webrtcvad / RMS trim)
  → STT (Google Speech-to-Text)
  → Intent NLP (từ điển JSON → fastText / BoW softmax → ngữ cảnh)
  → Action: MQTT publish HOẶC RAG (gemini-3-flash-preview + FAISS)
  → TTS (gTTS + mp3 cache)
```

### Kích hoạt

| Cách | Mô tả |
| --- | --- |
| **Nói "yolo"** | Wake word → lắng nghe lệnh |
| **Nút Dashboard** | Nhấn "Nói Yolo" trên Dashboard → tương đương nói wake word |
| **HTTP API** | `POST /api/voice/ask` — hỏi đáp không cần microphone |

### Ví dụ lệnh

```
"bật đèn" / "tắt quạt" / "mở cửa"       <- Điều khiển thiết bị
"tắt nó" / "bật lại"                      <- Dùng ngữ cảnh gần nhất
"nhiệt độ hiện tại bao nhiêu?"           <- Hỏi thông tin nhà
"hôm nay trời có mưa không?"               <- OpenWeatherMap
"cách tiết kiệm điện như thế nào?"       <- RAG knowledge base
```

### Fallback chain

```
Từ điển điều khiển
  → Từ điển câu hỏi / thời tiết
  → fastText / BoW-softmax
  → RAG (gemini-3-flash-preview + FAISS)
  → Fallback từ khóa KB
```

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

## Tài liệu

- [`document/PROGRESS_v8.md`](document/PROGRESS_v8.md) — Nhật ký Voice AI v8 (STT/NLP/TTS/RAG)
- [`document/PROGRESS_v7.md`](document/PROGRESS_v7.md) — Nhật ký cải tiến Voice AI v7
- [`document/PROGRESS_v6.md`](document/PROGRESS_v6.md) — Nhật ký cải tiến v6
- [`document/PROGRESS_v5.md`](document/PROGRESS_v5.md) — Nhật ký triển khai Phase 5
- [`document/PROGRESS_v4.md`](document/PROGRESS_v4.md) — Nhật ký Phase 4
- [`document/TONG_HOP_BAO_CAO_TIENDO.md`](document/TONG_HOP_BAO_CAO_TIENDO.md) — Tổng hợp tiến độ
- [`document/guide.md`](document/guide.md) — Hướng dẫn chạy và test chi tiết
