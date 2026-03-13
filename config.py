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
FACE_MODEL_FILE         = "ai/face_recognition/trained_model/face_model.pkl"
FACE_CONFIDENCE_THRESHOLD = 0.55   # Ngưỡng tin cậy (>= là nhận diện thành công)
FACE_STRANGER_TIMEOUT   = 10       # Giây liên tục thấy người lạ → cảnh báo REQ-09
CAMERA_INDEX            = 0        # Index webcam (0 = webcam mặc định)
FACE_FRAME_WIDTH        = 640
FACE_FRAME_HEIGHT       = 480

# ============================================================
# VOICE CONTROL CONFIGURATION
# ============================================================
WAKE_WORD               = "yolo"   # Từ khoá đánh thức hệ thống
VOICE_LANGUAGE          = "vi-VN"  # Ngôn ngữ nhận diện giọng nói
VOICE_ENERGY_THRESHOLD  = 300      # Ngưỡng năng lượng mic để bắt đầu ghi âm
VOICE_TIMEOUT           = 5        # Giây chờ lệnh sau wake word
VOICE_PHRASE_LIMIT      = 8        # Giây tối đa của một câu lệnh

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
DATABASE_PATH           = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "yolohome.db"
)
DB_KEEP_DAYS            = 7    # Giữ dữ liệu lịch sử 7 ngày

# ============================================================
# OPENWEATHERMAP CONFIGURATION (Phase 4)
# ============================================================
OPENWEATHER_API_KEY     = os.getenv("OPENWEATHER_API_KEY", "")
OPENWEATHER_CITY        = os.getenv("OPENWEATHER_CITY", "Ho Chi Minh City")
OPENWEATHER_UNITS       = "metric"   # Nhiệt độ °C, tốc độ gió m/s
