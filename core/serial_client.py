"""
core/serial_client.py - Singleton Serial Client cho YoloHome Gateway

Quản lý giao tiếp Serial UART với mạch Yolo:Bit.
Đảm bảo chỉ có MỘT kết nối Serial duy nhất, thread-safe.

Giao thức Serial với Yolo:Bit (JSON-based):
  PC → Yolo:Bit : {"cmd": "set", "device": "led",  "value": 1}
                   {"cmd": "set", "device": "fan",  "value": 0}
                   {"cmd": "set", "device": "pump", "value": 1}
                   {"cmd": "set", "device": "door", "value": 1}
  Yolo:Bit → PC : {"sensor": "temp", "value": 28.5}
                   {"sensor": "humi", "value": 65.0}
                   {"sensor": "gas",  "value": 120}
"""

import json
import time
import threading
import logging
import serial
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

logger = logging.getLogger(__name__)


class SerialSingleton:
    """
    Singleton Serial Client giao tiếp với Yolo:Bit qua UART.

    Cách dùng:
        serial_client = SerialSingleton.get_instance()
        serial_client.send_command("led", 1)
    """

    _instance = None
    _lock = threading.Lock()          # Lock bảo vệ Singleton
    _write_lock = threading.Lock()    # Lock riêng cho thao tác ghi Serial

    def __init__(self):
        """Khởi tạo nội bộ - KHÔNG gọi trực tiếp."""
        self._serial: serial.Serial = None
        self._connected = False
        self._sensor_callbacks = {}    # {sensor_name: callback_function}
        self._read_thread = None
        self._running = False
        self._connect()

    # ------------------------------------------------------------------
    # Singleton accessor
    # ------------------------------------------------------------------
    @classmethod
    def get_instance(cls) -> "SerialSingleton":
        """
        Trả về instance duy nhất của SerialSingleton.
        Thread-safe: double-checked locking.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    logger.info("[Serial] Khởi tạo Singleton Serial Client...")
                    cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Kết nối Serial
    # ------------------------------------------------------------------
    def _connect(self):
        """Mở cổng Serial kết nối Yolo:Bit và khởi động thread đọc dữ liệu."""
        try:
            self._serial = serial.Serial(
                port=config.SERIAL_PORT,
                baudrate=config.SERIAL_BAUDRATE,
                timeout=config.SERIAL_TIMEOUT
            )
            self._connected = True
            self._running = True
            logger.info(f"[Serial] ✅ Kết nối thành công cổng {config.SERIAL_PORT} @ {config.SERIAL_BAUDRATE} baud")

            # Khởi động thread đọc liên tục dữ liệu từ Yolo:Bit
            self._read_thread = threading.Thread(
                target=self._read_loop,
                daemon=True,
                name="Serial-ReadLoop"
            )
            self._read_thread.start()

        except serial.SerialException as e:
            self._connected = False
            logger.error(f"[Serial] ❌ Không thể mở cổng {config.SERIAL_PORT}: {e}")
            logger.warning("[Serial] Chạy ở chế độ SIMULATION (không có phần cứng).")

    # ------------------------------------------------------------------
    # Vòng lặp đọc dữ liệu từ Yolo:Bit
    # ------------------------------------------------------------------
    def _read_loop(self):
        """
        Thread liên tục đọc dữ liệu JSON từ Yolo:Bit qua Serial.
        Dữ liệu nhận về có dạng: {"sensor": "temp", "value": 28.5}
        Điều hướng đến callback đã đăng ký tương ứng.
        """
        logger.info("[Serial] 🔄 Bắt đầu vòng lặp đọc Serial...")
        buffer = ""

        while self._running:
            try:
                if self._serial and self._serial.in_waiting > 0:
                    raw_data = self._serial.readline().decode("utf-8").strip()
                    if not raw_data:
                        continue

                    logger.debug(f"[Serial] Raw nhận: {raw_data}")

                    # Parse JSON từ Yolo:Bit
                    try:
                        data = json.loads(raw_data)
                        sensor_name = data.get("sensor")
                        value = data.get("value")

                        if sensor_name and value is not None:
                            logger.debug(f"[Serial] 📨 Cảm biến: {sensor_name} = {value}")
                            # Gọi callback đã đăng ký cho cảm biến này
                            if sensor_name in self._sensor_callbacks:
                                self._sensor_callbacks[sensor_name](value)
                    except json.JSONDecodeError:
                        logger.warning(f"[Serial] Dữ liệu không hợp lệ (JSON parse lỗi): {raw_data}")

                else:
                    time.sleep(0.05)  # Tránh busy-wait, nghỉ 50ms

            except serial.SerialException as e:
                logger.error(f"[Serial] Lỗi đọc Serial: {e}")
                self._connected = False
                time.sleep(2)  # Chờ rồi thử lại

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def send_command(self, device: str, value: int) -> bool:
        """
        Gửi lệnh điều khiển thiết bị xuống Yolo:Bit qua Serial.

        Args:
            device: Tên thiết bị ("led", "fan", "pump", "door")
            value : Giá trị điều khiển (1 = BẬT, 0 = TẮT)

        Returns:
            True nếu gửi thành công, False nếu thất bại.

        Ví dụ:
            serial_client.send_command("fan", 1)
            → Gửi: {"cmd": "set", "device": "fan", "value": 1}
        """
        command = json.dumps({"cmd": "set", "device": device, "value": value})

        if not self._connected or self._serial is None:
            # Chế độ simulation khi không có phần cứng thực
            logger.info(f"[Serial][SIM] Gửi lệnh giả lập: {command}")
            return True

        with self._write_lock:  # Đảm bảo chỉ một thread ghi tại một thời điểm
            try:
                self._serial.write((command + "\n").encode("utf-8"))
                logger.info(f"[Serial] 📤 Gửi lệnh: {command}")
                return True
            except serial.SerialException as e:
                logger.error(f"[Serial] Lỗi gửi lệnh '{command}': {e}")
                return False

    def register_sensor_callback(self, sensor_name: str, callback):
        """
        Đăng ký callback nhận dữ liệu từ một cảm biến cụ thể.

        Args:
            sensor_name: Tên cảm biến ("temp", "humi", "gas")
            callback   : Hàm xử lý (fn(value: float))

        Ví dụ:
            serial_client.register_sensor_callback("temp", lambda v: print(f"Nhiệt độ: {v}°C"))
        """
        self._sensor_callbacks[sensor_name] = callback
        logger.debug(f"[Serial] Đã đăng ký callback cho cảm biến: {sensor_name}")

    def stop(self):
        """Dừng vòng lặp đọc và đóng cổng Serial."""
        self._running = False
        if self._serial and self._serial.is_open:
            self._serial.close()
            logger.info("[Serial] 🔌 Đã đóng cổng Serial.")

    @property
    def is_connected(self) -> bool:
        """Trả về trạng thái kết nối Serial hiện tại."""
        return self._connected
