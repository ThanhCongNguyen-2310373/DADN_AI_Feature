"""
config.py - Tập trung toàn bộ cấu hình hệ thống YoloHome Gateway
Các giá trị nhạy cảm (API key) được đọc từ file .env.
"""

import os
from dotenv import load_dotenv

# Tải biến môi trường từ file .env (nằm cùng thư mục với config.py)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

# ============================================================
# ADAFRUIT IO / MQTT CONFIGURATION
# ============================================================
ADAFRUIT_USERNAME   = os.getenv("ADAFRUIT_USERNAME", "your_adafruit_username")
ADAFRUIT_AIO_KEY    = os.getenv("ADAFRUIT_AIO_KEY",  "your_adafruit_aio_key")
MQTT_BROKER         = "io.adafruit.com"
MQTT_PORT           = 1883
MQTT_KEEPALIVE      = 60                          # Giây

# --- Feed names (prefix tự động ghép: USERNAME/feeds/FEED_NAME) ---
FEED_TEMPERATURE    = "yolohome-temperature"
FEED_HUMIDITY       = "yolohome-humidity"
FEED_GAS            = "yolohome-gas"
FEED_LED            = "yolohome-led"
FEED_FAN            = "yolohome-fan"
FEED_PUMP           = "yolohome-pump"
FEED_DOOR           = "yolohome-door"
FEED_ALERT          = "yolohome-alert"
FEED_LOG            = "yolohome-log"

# ============================================================
# SERIAL (Yolo:Bit) CONFIGURATION
# ============================================================
SERIAL_PORT         = "COM3"       # Thay đổi theo cổng kết nối thực tế
SERIAL_BAUDRATE     = 115200
SERIAL_TIMEOUT      = 1            # Giây

# Giao thức Serial với Yolo:Bit (chuỗi JSON ví dụ):
# Gửi xuống   : {"cmd": "set", "device": "led",  "value": 1}
# Nhận lên    : {"sensor": "temp", "value": 28.5}
#               {"sensor": "humi", "value": 65.0}
#               {"sensor": "gas",  "value": 120}

# ============================================================
# SENSOR CONFIGURATION
# ============================================================
SENSOR_READ_INTERVAL    = 5        # Chu kỳ đọc cảm biến (giây) - REQ-01
TEMP_THRESHOLD          = 35.0     # Ngưỡng nhiệt độ cảnh báo (°C) - REQ-07
GAS_THRESHOLD           = 300      # Ngưỡng khí gas cảnh báo (ppm)

# ============================================================
# FACE RECOGNITION CONFIGURATION
# ============================================================
FACE_DATASET_DIR        = "ai/face_recognition/dataset"
FACE_MODEL_DIR          = "ai/face_recognition/trained_model"
FACE_MODEL_FILE         = os.path.join(FACE_MODEL_DIR, "face_model.yml")
FACE_CONFIDENCE_THRESHOLD = 0.42   # Ngưỡng tin cậy (>= là nhận diện thành công)
FACE_STRANGER_TIMEOUT   = 10       # Giây liên tục thấy người lạ → cảnh báo REQ-09
CAMERA_INDEX            = 0        # Index webcam (0 = webcam mặc định)
CAMERA_FPS              = 30
FACE_FRAME_WIDTH        = 1280
FACE_FRAME_HEIGHT       = 720
FACE_FRAME_SKIP         = 2
FACE_MIN_SIZE           = 60
FACE_BLUR_THRESHOLD     = 30.0
FACE_BRIGHTNESS_MIN     = 35
FACE_BRIGHTNESS_MAX     = 230
FACE_CAPTURE_COOLDOWN   = 0.2
FACE_ALIGN_ENABLE       = True
FACE_CLAHE_ENABLE       = True
FACE_SMOOTHING_WINDOW   = 9
FACE_SMOOTHING_MIN_RATIO = 0.45
FACE_SMOOTHING_MIN_COUNT = 4
FACE_THRESHOLD_FLOOR     = 0.30
FACE_THRESHOLD_MARGIN    = 0.08
FACE_THRESHOLD_STD_MULTIPLIER = 1.5
FACE_TRACKER_TYPE       = "CSRT"
FACE_TRACKER_REFRESH    = 15
FACE_VAL_SPLIT          = 0.2
FACE_MIN_IMAGES_PER_PERSON = 80
FACE_AUGMENT_ENABLED    = True
FACE_AUGMENT_MAX_PER_IMAGE = 3
FACE_AUGMENT_ROTATE_DEG = 10
FACE_AUGMENT_BRIGHTNESS_RANGE = (0.8, 1.2)
FACE_AUGMENT_CONTRAST_RANGE   = (0.8, 1.2)
FACE_AUGMENT_NOISE_STD = 5.0
FACE_AUGMENT_ZOOM_RANGE = (0.9, 1.1)
FACE_AUGMENT_OCCLUSION_PROB = 0.3
FACE_AUGMENT_OCCLUSION_AREA = (0.15, 0.3)

# ============================================================
# VOICE CONTROL CONFIGURATION
# ============================================================
WAKE_WORD               = "yolo"   # Từ khoá đánh thức hệ thống
VOICE_LANGUAGE          = "vi-VN"  # Ngôn ngữ nhận diện giọng nói
VOICE_ENERGY_THRESHOLD  = 300      # Ngưỡng năng lượng mic (mặc định trước khi auto-calib)
VOICE_TIMEOUT           = 5        # Giây chờ lệnh sau wake word
VOICE_PHRASE_LIMIT      = 8        # Giây tối đa của một câu lệnh

# --- Voice v8: STT (VAD / năng lượng) ---
VOICE_STT_TARGET_HZ         = int(os.getenv("VOICE_STT_TARGET_HZ", "16000"))  # webrtcvad: 8000|16000|32000|48000
VOICE_VAD_ENABLED           = os.getenv("VOICE_VAD_ENABLED", "1") == "1"
VOICE_VAD_AGGRESSIVENESS    = int(os.getenv("VOICE_VAD_AGGRESSIVENESS", "2"))  # 0–3
VOICE_RNNOISE_ENABLED       = os.getenv("VOICE_RNNOISE_ENABLED", "1") == "1"  # cần pyrnnoise; tắt nếu không cài
VOICE_AUTO_ENERGY           = os.getenv("VOICE_AUTO_ENERGY", "1") == "1"
VOICE_ENERGY_THRESHOLD_MIN  = int(os.getenv("VOICE_ENERGY_THRESHOLD_MIN", "120"))
VOICE_ENERGY_THRESHOLD_MAX  = int(os.getenv("VOICE_ENERGY_THRESHOLD_MAX", "1800"))
VOICE_AMBIENT_CALIB_SEC     = float(os.getenv("VOICE_AMBIENT_CALIB_SEC", "2.0"))
VOICE_AMBIENT_RECALIB_SEC   = float(os.getenv("VOICE_AMBIENT_RECALIB_SEC", "0.5"))
VOICE_ENERGY_RECALIB_AFTER_TIMEOUTS = int(os.getenv("VOICE_ENERGY_RECALIB_AFTER_TIMEOUTS", "30"))

# --- Voice v8: ngữ cảnh thiết bị (đại từ "tắt nó") ---
VOICE_CONTEXT_SECS          = int(os.getenv("VOICE_CONTEXT_SECS", "12"))

# --- Voice v8: RAG ---
RAG_CHUNK_SIZE              = int(os.getenv("RAG_CHUNK_SIZE", "360"))
RAG_CHUNK_OVERLAP           = int(os.getenv("RAG_CHUNK_OVERLAP", "55"))
RAG_RETRIEVER_K             = int(os.getenv("RAG_RETRIEVER_K", "2"))
RAG_MAX_CONTEXT_CHARS       = int(os.getenv("RAG_MAX_CONTEXT_CHARS", "1800"))
RAG_MAX_OUTPUT_TOKENS       = int(os.getenv("RAG_MAX_OUTPUT_TOKENS", "256"))

# --- Voice v8: TTS cache ---
TTS_CACHE_DIR               = os.getenv(
    "TTS_CACHE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tts_cache"),
)

# ============================================================
# LOGGING CONFIGURATION
# ============================================================
LOG_DIR                 = "logs"
LOG_FILE                = "logs/yolohome.log"
LOG_MAX_BYTES           = 5 * 1024 * 1024   # 5 MB
LOG_BACKUP_COUNT        = 3

# ============================================================
# GEMINI / RAG CONFIGURATION
# ============================================================
GEMINI_API_KEY          = os.getenv("GEMINI_API_KEY", "")

# ============================================================
# TELEGRAM BOT CONFIGURATION (REQ-03 / REQ-09 alerts)
# ============================================================
TELEGRAM_BOT_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID        = os.getenv("TELEGRAM_CHAT_ID",   "")

# ============================================================
# WEB DASHBOARD AUTHENTICATION
# ============================================================
WEB_USERNAME            = os.getenv("WEB_USERNAME", "admin")
WEB_PASSWORD            = os.getenv("WEB_PASSWORD", "yolohome2025")
WEB_PORT                = int(os.getenv("WEB_PORT", "8000"))

# ============================================================
# DATABASE CONFIGURATION
# ============================================================
DATABASE_PATH           = os.getenv(
    "DATABASE_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "yolohome.db"),
)
DB_KEEP_DAYS            = 7    # Giữ dữ liệu lịch sử 7 ngày

# ============================================================
# OPENWEATHERMAP CONFIGURATION (Phase 4)
# ============================================================
OPENWEATHER_API_KEY     = os.getenv("OPENWEATHER_API_KEY", "")
OPENWEATHER_CITY        = os.getenv("OPENWEATHER_CITY", "Ho Chi Minh City")
OPENWEATHER_UNITS       = "metric"   # Nhiệt độ °C, tốc độ gió m/s

# ============================================================
# PHASE 5 - DATA PLATFORM & SECURITY SCALE
# ============================================================
# Database backend: "sqlite" (mặc định) hoặc "postgresql"
DATABASE_BACKEND        = os.getenv("DATABASE_BACKEND", "sqlite").lower()

# PostgreSQL (dùng khi DATABASE_BACKEND=postgresql)
POSTGRES_DSN            = os.getenv("POSTGRES_DSN", "")
POSTGRES_HOST           = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT           = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB             = os.getenv("POSTGRES_DB", "yolohome")
POSTGRES_USER           = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD       = os.getenv("POSTGRES_PASSWORD", "postgres")

# Session + RBAC
WEB_SESSION_TTL         = int(os.getenv("WEB_SESSION_TTL", str(8 * 3600)))
ROLE_ADMIN              = "admin"
ROLE_OPERATOR           = "operator"
ROLE_VIEWER             = "viewer"

# Persistent rate limiting
RATE_LIMIT_BACKEND      = os.getenv("RATE_LIMIT_BACKEND", "memory").lower()  # memory|redis
RATE_LIMIT_MAX_ATTEMPTS = int(os.getenv("RATE_LIMIT_MAX_ATTEMPTS", "5"))
RATE_LIMIT_WINDOW_SECS  = int(os.getenv("RATE_LIMIT_WINDOW_SECS", "300"))
REDIS_URL               = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Observability
LOG_STRUCTURED          = os.getenv("LOG_STRUCTURED", "1") == "1"
METRICS_ENABLED         = os.getenv("METRICS_ENABLED", "1") == "1"
TRACING_ENABLED         = os.getenv("TRACING_ENABLED", "0") == "1"
OTLP_ENDPOINT           = os.getenv("OTLP_ENDPOINT", "http://localhost:4318/v1/traces")
