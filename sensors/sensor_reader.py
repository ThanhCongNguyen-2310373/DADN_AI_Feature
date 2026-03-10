"""
sensors/sensor_reader.py - Module đọc cảm biến và điều khiển thiết bị

Thực hiện REQ-01, REQ-02, REQ-07:
  - REQ-01: Đọc cảm biến mỗi 5 giây
  - REQ-02: Đồng bộ dữ liệu lên Adafruit Dashboard
  - REQ-07: So sánh với ngưỡng, tự động cảnh báo + kích hoạt thiết bị

Tích hợp:
  - SerialSingleton: nhận dữ liệu từ Yolo:Bit
  - MQTTSingleton : publish lên Adafruit + subscribe lệnh điều khiển
"""

import os
import sys
import time
import threading
import logging
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from core.mqtt_client import MQTTSingleton
from core.serial_client import SerialSingleton

logger = logging.getLogger(__name__)


class SensorReader:
    """
    Module đọc cảm biến định kỳ và xử lý ngưỡng cảnh báo.

    Hai luồng chính:
      1. Serial callback: nhận dữ liệu realtime từ Yolo:Bit
      2. Publish loop: gửi dữ liệu lên MQTT mỗi 5 giây

    Cách dùng:
        reader = SensorReader()
        reader.start()
    """

    def __init__(self):
        self._mqtt   = MQTTSingleton.get_instance()
        self._serial = SerialSingleton.get_instance()

        self._running = False
        self._thread: threading.Thread = None

        # Cache giá trị cảm biến mới nhất (thread-safe bằng Lock)
        self._data_lock = threading.Lock()
        self._sensor_data = {
            "temp": None,   # Nhiệt độ (°C)
            "humi": None,   # Độ ẩm (%)
            "gas":  None,   # Nồng độ khí gas (ppm)
        }

        # Trạng thái ngưỡng (tránh spam cảnh báo liên tục)
        self._alert_cooldown = {}   # {alert_type: last_alert_time}
        self._alert_interval = 30   # Giây tối thiểu giữa 2 cảnh báo cùng loại

        # Đăng ký callback nhận dữ liệu từ Serial
        self._serial.register_sensor_callback("temp", self._on_temp)
        self._serial.register_sensor_callback("humi", self._on_humi)
        self._serial.register_sensor_callback("gas",  self._on_gas)

        # Đăng ký MQTT subscribe lệnh điều khiển thiết bị từ Dashboard
        self._mqtt.subscribe(config.FEED_LED,  self._on_led_command)
        self._mqtt.subscribe(config.FEED_FAN,  self._on_fan_command)
        self._mqtt.subscribe(config.FEED_PUMP, self._on_pump_command)

    # ------------------------------------------------------------------
    # Thread control
    # ------------------------------------------------------------------
    def start(self):
        """Khởi động thread publish dữ liệu cảm biến định kỳ."""
        self._running = True
        self._thread = threading.Thread(
            target=self._publish_loop,
            daemon=True,
            name="SensorReader-Thread"
        )
        self._thread.start()
        logger.info("[Sensor] 🚀 SensorReader đã khởi động.")

    def stop(self):
        """Dừng thread publish."""
        self._running = False
        logger.info("[Sensor] 🛑 SensorReader đã dừng.")

    # ------------------------------------------------------------------
    # Callback nhận dữ liệu từ Serial (Yolo:Bit → Gateway)
    # ------------------------------------------------------------------
    def _on_temp(self, value):
        """Callback nhận giá trị nhiệt độ từ Yolo:Bit."""
        try:
            with self._data_lock:
                self._sensor_data["temp"] = float(value)
            logger.debug(f"[Sensor] Nhiệt độ cập nhật: {value}°C")
        except (ValueError, TypeError) as e:
            logger.warning(f"[Sensor] Giá trị nhiệt độ không hợp lệ: {value} | {e}")

    def _on_humi(self, value):
        """Callback nhận giá trị độ ẩm từ Yolo:Bit."""
        try:
            with self._data_lock:
                self._sensor_data["humi"] = float(value)
            logger.debug(f"[Sensor] Độ ẩm cập nhật: {value}%")
        except (ValueError, TypeError) as e:
            logger.warning(f"[Sensor] Giá trị độ ẩm không hợp lệ: {value} | {e}")

    def _on_gas(self, value):
        """Callback nhận giá trị khí gas từ Yolo:Bit."""
        try:
            with self._data_lock:
                self._sensor_data["gas"] = float(value)
            logger.debug(f"[Sensor] Khí gas cập nhật: {value} ppm")
        except (ValueError, TypeError) as e:
            logger.warning(f"[Sensor] Giá trị khí gas không hợp lệ: {value} | {e}")

    # ------------------------------------------------------------------
    # Vòng lặp publish dữ liệu lên MQTT mỗi 5 giây (REQ-01)
    # ------------------------------------------------------------------
    def _publish_loop(self):
        """
        Định kỳ mỗi SENSOR_READ_INTERVAL giây:
          1. Lấy dữ liệu cảm biến mới nhất
          2. Publish lên Adafruit (REQ-02)
          3. So sánh với ngưỡng, gửi cảnh báo nếu cần (REQ-07)
        """
        logger.info(f"[Sensor] 🔄 Bắt đầu publish loop, chu kỳ {config.SENSOR_READ_INTERVAL}s")

        while self._running:
            with self._data_lock:
                temp = self._sensor_data["temp"]
                humi = self._sensor_data["humi"]
                gas  = self._sensor_data["gas"]

            # --- Publish dữ liệu lên Adafruit Dashboard ---
            if temp is not None:
                self._mqtt.publish(config.FEED_TEMPERATURE, round(temp, 1))

            if humi is not None:
                self._mqtt.publish(config.FEED_HUMIDITY, round(humi, 1))

            if gas is not None:
                self._mqtt.publish(config.FEED_GAS, round(gas, 1))

            # --- Kiểm tra ngưỡng cảnh báo (REQ-07) ---
            if temp is not None:
                self._check_temp_threshold(temp)

            if gas is not None:
                self._check_gas_threshold(gas)

            logger.info(f"[Sensor] 📊 T={temp}°C | H={humi}% | Gas={gas}ppm")
            time.sleep(config.SENSOR_READ_INTERVAL)

    # ------------------------------------------------------------------
    # Kiểm tra ngưỡng và cảnh báo (REQ-07)
    # ------------------------------------------------------------------
    def _check_temp_threshold(self, temp: float):
        """
        So sánh nhiệt độ với ngưỡng cài đặt.
        Nếu vượt ngưỡng: gửi cảnh báo MQTT + tự động bật quạt.

        Args:
            temp: Giá trị nhiệt độ hiện tại (°C)
        """
        if temp > config.TEMP_THRESHOLD:
            if self._can_send_alert("temp_high"):
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                alert_msg = f"[{timestamp}] ⚠️ Nhiệt độ cao: {temp}°C (ngưỡng: {config.TEMP_THRESHOLD}°C)"

                logger.warning(f"[Sensor] 🌡️  {alert_msg}")
                self._mqtt.publish(config.FEED_ALERT, alert_msg)
                self._mqtt.publish(config.FEED_LOG,   alert_msg)

                # Tự động bật quạt làm mát (REQ-07)
                logger.info("[Sensor] 🔄 Tự động bật quạt do nhiệt độ cao...")
                self._serial.send_command("fan", 1)
                self._mqtt.publish(config.FEED_FAN, "ON")

    def _check_gas_threshold(self, gas: float):
        """
        So sánh nồng độ khí gas với ngưỡng cài đặt.
        Nếu vượt ngưỡng: gửi cảnh báo khẩn cấp.

        Args:
            gas: Nồng độ khí gas (ppm)
        """
        if gas > config.GAS_THRESHOLD:
            if self._can_send_alert("gas_high"):
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                alert_msg = f"[{timestamp}] 🚨 NGUY HIỂM: Phát hiện khí gas! {gas} ppm (ngưỡng: {config.GAS_THRESHOLD})"

                logger.critical(f"[Sensor] ☠️  {alert_msg}")
                self._mqtt.publish(config.FEED_ALERT, alert_msg)
                self._mqtt.publish(config.FEED_LOG,   alert_msg)

    def _can_send_alert(self, alert_type: str) -> bool:
        """
        Kiểm tra cooldown trước khi gửi cảnh báo.
        Tránh spam MQTT với cảnh báo lặp lại mỗi 5 giây.

        Args:
            alert_type: Loại cảnh báo (vd: "temp_high", "gas_high")

        Returns:
            True nếu có thể gửi, False nếu còn trong cooldown.
        """
        now = time.time()
        last_alert = self._alert_cooldown.get(alert_type, 0)

        if now - last_alert >= self._alert_interval:
            self._alert_cooldown[alert_type] = now
            return True
        return False

    # ------------------------------------------------------------------
    # Callback lệnh điều khiển thiết bị từ Dashboard (REQ-04)
    # ------------------------------------------------------------------
    def _on_led_command(self, payload: str):
        """
        Xử lý lệnh bật/tắt đèn nhận từ Adafruit Dashboard.
        Chuyển tiếp xuống Yolo:Bit qua Serial.

        Args:
            payload: "ON" hoặc "OFF" (hoặc "1"/"0")
        """
        value = 1 if payload.upper() in ("ON", "1") else 0
        logger.info(f"[Sensor] 💡 Lệnh đèn: {'BẬT' if value else 'TẮT'}")
        self._serial.send_command("led", value)

        # Ghi log
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status = "Bật" if value else "Tắt"
        self._mqtt.publish(config.FEED_LOG, f"[{timestamp}] {status} đèn (Dashboard)")

    def _on_fan_command(self, payload: str):
        """
        Xử lý lệnh bật/tắt quạt nhận từ Adafruit Dashboard.

        Args:
            payload: "ON" hoặc "OFF"
        """
        value = 1 if payload.upper() in ("ON", "1") else 0
        logger.info(f"[Sensor] 🌀 Lệnh quạt: {'BẬT' if value else 'TẮT'}")
        self._serial.send_command("fan", value)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status = "Bật" if value else "Tắt"
        self._mqtt.publish(config.FEED_LOG, f"[{timestamp}] {status} quạt (Dashboard)")

    def _on_pump_command(self, payload: str):
        """
        Xử lý lệnh bật/tắt máy bơm nhận từ Adafruit Dashboard.

        Args:
            payload: "ON" hoặc "OFF"
        """
        value = 1 if payload.upper() in ("ON", "1") else 0
        logger.info(f"[Sensor] 💧 Lệnh máy bơm: {'BẬT' if value else 'TẮT'}")
        self._serial.send_command("pump", value)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status = "Bật" if value else "Tắt"
        self._mqtt.publish(config.FEED_LOG, f"[{timestamp}] {status} máy bơm (Dashboard)")

    # ------------------------------------------------------------------
    # Truy xuất dữ liệu
    # ------------------------------------------------------------------
    def get_latest_data(self) -> dict:
        """
        Trả về snapshot dữ liệu cảm biến mới nhất (thread-safe).

        Returns:
            Dict {"temp": float, "humi": float, "gas": float}
        """
        with self._data_lock:
            return dict(self._sensor_data)
