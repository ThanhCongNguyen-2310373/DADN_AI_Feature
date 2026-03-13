"""
main.py - Điểm khởi động chính của YoloHome Gateway

Khởi tạo và điều phối tất cả các module chạy song song:
  - Thread 1: SensorReader  → Đọc cảm biến + publish MQTT + xử lý ngưỡng
  - Thread 2: FaceRecognizer → Giám sát camera + nhận diện khuôn mặt
  - Thread 3: VoiceAssistant → Lắng nghe giọng nói + điều khiển thiết bị

Tất cả các thread là daemon thread → tự động dừng khi main thread kết thúc.
Kết nối MQTT và Serial là Singleton → không bao giờ bị tạo trùng.

Chạy hệ thống:
    python main.py
    python main.py --no-face   (tắt Face Recognition)
    python main.py --no-voice  (tắt Voice Control)
    python main.py --sim       (chế độ simulation, không cần phần cứng)
"""

import os
import sys
import time
import signal
import logging
import logging.handlers
import argparse
import threading

# Đảm bảo import được các module con
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import config

# =====================================================================
# Thiết lập logging cho toàn hệ thống
# =====================================================================
def setup_logging():
    """
    Cấu hình hệ thống logging:
      - File log: logs/yolohome.log (rotating, max 5MB, giữ 3 bản)
      - Console: hiển thị INFO trở lên
    """
    os.makedirs(config.LOG_DIR, exist_ok=True)

    # Format chung cho tất cả handler
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] [%(threadName)-20s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # --- Handler ghi ra file (RotatingFileHandler) ---
    file_handler = logging.handlers.RotatingFileHandler(
        filename=config.LOG_FILE,
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # --- Handler hiển thị console ---
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    return logging.getLogger(__name__)


# =====================================================================
# Lớp điều phối chính
# =====================================================================
class YoloHomeGateway:
    """
    Gateway trung tâm điều phối toàn bộ hệ thống YoloHome.

    Quản lý vòng đời (lifecycle) của các module:
      - Khởi tạo Singleton connections (MQTT, Serial)
      - Khởi động các AI module trong thread riêng
      - Xử lý graceful shutdown khi nhận tín hiệu Ctrl+C (SIGINT)
    """

    def __init__(self, enable_face: bool = True, enable_voice: bool = True, enable_web: bool = True):
        """
        Args:
            enable_face : Có bật module Face Recognition không
            enable_voice: Có bật module Voice Control không
            enable_web  : Có bật Dashboard Web App không
        """
        self._logger = logging.getLogger(self.__class__.__name__)
        self._enable_face  = enable_face
        self._enable_voice = enable_voice
        self._enable_web   = enable_web
        self._running = False

        self._sensor_reader   = None
        self._face_recognizer = None
        self._voice_assistant = None
        self._web_thread: threading.Thread = None

        # Đăng ký xử lý tín hiệu Ctrl+C
        signal.signal(signal.SIGINT,  self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Xử lý graceful shutdown khi nhận SIGINT/SIGTERM."""
        self._logger.info(f"\n[Gateway] Nhận tín hiệu dừng ({signum}). Đang shutdown an toàn...")
        self.stop()

    # ------------------------------------------------------------------
    # Khởi động hệ thống
    # ------------------------------------------------------------------
    def start(self):
        """
        Khởi động toàn bộ hệ thống theo thứ tự:
          1. Kết nối Singleton MQTT
          2. Kết nối Singleton Serial
          3. Khởi động SensorReader thread
          4. Khởi động FaceRecognizer thread (nếu bật)
          5. Khởi động VoiceAssistant thread (nếu bật)
          6. Vào vòng lặp giữ main thread sống
        """
        self._running = True
        self._logger.info("=" * 60)
        self._logger.info("  🏠 YoloHome Gateway đang khởi động...")
        self._logger.info("=" * 60)

        # --- Bước 1 & 2: Khởi tạo Singleton connections ---
        self._logger.info("[Gateway] Khởi tạo kết nối MQTT và Serial...")
        from core.mqtt_client import MQTTSingleton
        from core.serial_client import SerialSingleton

        mqtt   = MQTTSingleton.get_instance()
        serial = SerialSingleton.get_instance()

        # Chờ MQTT kết nối (tối đa 10 giây)
        self._logger.info("[Gateway] Chờ kết nối MQTT...")
        for _ in range(10):
            if mqtt.is_connected:
                break
            time.sleep(1)

        if not mqtt.is_connected:
            self._logger.warning("[Gateway] ⚠️  MQTT chưa kết nối. Hệ thống vẫn tiếp tục (auto-reconnect).")

        # --- Bước 3: Khởi động SensorReader ---
        self._logger.info("[Gateway] Khởi động module đọc cảm biến...")
        from sensors.sensor_reader import SensorReader
        self._sensor_reader = SensorReader()
        self._sensor_reader.start()

        # --- Bước 4: Khởi động FaceRecognizer ---
        if self._enable_face:
            self._logger.info("[Gateway] Khởi động module FaceAI...")
            try:
                from ai.face_recognition.face_recognizer import FaceRecognizer
                self._face_recognizer = FaceRecognizer()
                self._face_recognizer.start()
            except Exception as e:
                self._logger.error(f"[Gateway] Lỗi khởi động FaceAI: {e}")
        else:
            self._logger.info("[Gateway] ⏭️  FaceAI đã bị tắt (--no-face).")

        # --- Bước 5: Khởi động VoiceAssistant ---
        if self._enable_voice:
            self._logger.info("[Gateway] Khởi động module Voice Control...")
            try:
                from ai.voice_control.voice_assistant import VoiceAssistant
                self._voice_assistant = VoiceAssistant()
                self._voice_assistant.start()
            except Exception as e:
                self._logger.error(f"[Gateway] Lỗi khởi động VoiceAI: {e}")
        else:
            self._logger.info("[Gateway] ⏭️  VoiceAI đã bị tắt (--no-voice).")

        # --- Bước 6: Khởi động Web Dashboard ---
        if self._enable_web:
            self._logger.info("[Gateway] Khởi động Web Dashboard tại http://localhost:8000 ...")
            try:
                self._start_web_app()
            except Exception as e:
                self._logger.error(f"[Gateway] Lỗi khởi động WebApp: {e}")
        else:
            self._logger.info("[Gateway] ⏭️  Web Dashboard đã bị tắt (--no-web).")

        # --- Bước 7: Báo cáo trạng thái ---
        self._print_status()

        # --- Giữ main thread sống, in heartbeat mỗi 30 giây ---
        self._logger.info("[Gateway] ✅ Hệ thống đã khởi động hoàn tất. Nhấn Ctrl+C để dừng.\n")
        self._heartbeat_loop()

    def _heartbeat_loop(self):
        """
        Vòng lặp chính của main thread.
        In báo cáo trạng thái hệ thống mỗi 30 giây.
        """
        heartbeat_interval = 30
        last_heartbeat = time.time()

        while self._running:
            time.sleep(1)

            # In heartbeat định kỳ
            if time.time() - last_heartbeat >= heartbeat_interval:
                self._print_heartbeat()
                last_heartbeat = time.time()

    def _print_heartbeat(self):
        """In báo cáo trạng thái ngắn gọn."""
        from core.mqtt_client import MQTTSingleton
        from core.serial_client import SerialSingleton

        mqtt_status   = "🟢 Online" if MQTTSingleton.get_instance().is_connected   else "🔴 Offline"
        serial_status = "🟢 Online" if SerialSingleton.get_instance().is_connected  else "🔴 Offline (Sim)"
        face_status   = "🟢 Running" if (self._face_recognizer and self._face_recognizer.is_running) else "⚫ Off"
        voice_status  = "🟢 Running" if (self._voice_assistant and self._voice_assistant._running) else "⚫ Off"

        self._logger.info(
            f"[Heartbeat] MQTT: {mqtt_status} | Serial: {serial_status} | "
            f"FaceAI: {face_status} | VoiceAI: {voice_status}"
        )

        # Hiển thị dữ liệu cảm biến mới nhất
        if self._sensor_reader:
            data = self._sensor_reader.get_latest_data()
            self._logger.info(
                f"[Heartbeat] Cảm biến: T={data['temperature']}°C | H={data['humidity']}% | Gas={data['gas']}ppm"
            )

    def _start_web_app(self):
        """
        Khởi động FastAPI Web App trong thread riêng.
        Inject các module runtime vào WebApp trước khi chạy.
        """
        import uvicorn
        from web_app.app import app as fastapi_app, inject_modules

        # Inject các module để WebApp có thể đọc dữ liệu thực tế
        inject_modules(
            sensor_reader   = self._sensor_reader,
            voice_assistant = self._voice_assistant,
            face_recognizer = self._face_recognizer,
        )

        def _run():
            uvicorn.run(
                fastapi_app,
                host="0.0.0.0",
                port=8000,
                log_level="warning",  # Im lặng hơn để không spam console
            )

        self._web_thread = threading.Thread(
            target=_run,
            daemon=True,
            name="WebApp-Thread",
        )
        self._web_thread.start()
        self._logger.info("[Gateway] 🌐 Web Dashboard: http://localhost:8000")

    def _print_status(self):
        """In bảng trạng thái khởi động."""
        print("\n" + "=" * 60)
        print("  🏠 YoloHome Gateway - Trạng thái hệ thống")
        print("=" * 60)
        print(f"  📡 MQTT Broker  : {config.MQTT_BROKER}")
        print(f"  🔌 Serial Port  : {config.SERIAL_PORT}")
        print(f"  ⏱️  Chu kỳ cảm biến: {config.SENSOR_READ_INTERVAL}s")
        print(f"  🌡️  Ngưỡng nhiệt độ: {config.TEMP_THRESHOLD}°C")
        print(f"  💨 Ngưỡng khí gas : {config.GAS_THRESHOLD} ppm")
        print(f"  📷 FaceAI       : {'✅ Bật' if self._enable_face  else '❌ Tắt'}")
        print(f"  🎤 VoiceAI      : {'✅ Bật' if self._enable_voice else '❌ Tắt'}")
        print(f"  🌐 Web Dashboard: {'✅ http://localhost:8000' if self._enable_web else '❌ Tắt'}")
        print("=" * 60 + "\n")

    # ------------------------------------------------------------------
    # Dừng hệ thống
    # ------------------------------------------------------------------
    def stop(self):
        """Dừng tất cả các module một cách an toàn (graceful shutdown)."""
        self._running = False
        self._logger.info("[Gateway] 🛑 Đang dừng các module...")

        if self._face_recognizer:
            self._face_recognizer.stop()

        if self._voice_assistant:
            self._voice_assistant.stop()

        if self._sensor_reader:
            self._sensor_reader.stop()

        # Đóng kết nối Serial
        from core.serial_client import SerialSingleton
        SerialSingleton.get_instance().stop()

        self._logger.info("[Gateway] 👋 YoloHome Gateway đã dừng hoàn toàn.")
        sys.exit(0)


# =====================================================================
# Entry point
# =====================================================================
def parse_args():
    """Phân tích các tham số dòng lệnh."""
    parser = argparse.ArgumentParser(
        description="YoloHome Gateway - Hệ thống Nhà thông minh IoT + AI"
    )
    parser.add_argument(
        "--no-face",
        action="store_true",
        help="Tắt module Face Recognition (dùng khi không có webcam)"
    )
    parser.add_argument(
        "--no-voice",
        action="store_true",
        help="Tắt module Voice Control (dùng khi không có microphone)"
    )
    parser.add_argument(
        "--no-web",
        action="store_true",
        help="Tắt Web Dashboard (mặc định: bật tại http://localhost:8000)"
    )
    parser.add_argument(
        "--sim",
        action="store_true",
        help="Chế độ simulation: giả lập cảm biến, không cần Yolo:Bit"
    )
    return parser.parse_args()


if __name__ == "__main__":
    # 1. Thiết lập logging
    logger = setup_logging()

    # 2. Parse arguments
    args = parse_args()

    if args.sim:
        logger.info("[Main] Chế độ SIMULATION: Không cần phần cứng thực.")

    # 3. Khởi động Gateway
    gateway = YoloHomeGateway(
        enable_face  = not args.no_face,
        enable_voice = not args.no_voice,
        enable_web   = not args.no_web,
    )
    gateway.start()
