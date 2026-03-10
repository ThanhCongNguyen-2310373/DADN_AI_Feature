"""
core/mqtt_client.py - Singleton MQTT Client cho YoloHome Gateway

Đảm bảo toàn bộ hệ thống chỉ tồn tại DUY NHẤT một kết nối MQTT,
tránh xung đột tài nguyên khi nhiều thread cùng truy cập.

Tính năng:
- Singleton Pattern (thread-safe với Lock)
- Auto-reconnect khi mất kết nối (NFR 2.2)
- Publish / Subscribe helper methods
"""

import time
import threading
import logging
from Adafruit_IO import MQTTClient
import sys
import os

# Thêm thư mục gốc vào sys.path để import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

logger = logging.getLogger(__name__)


class MQTTSingleton:
    """
    Singleton MQTT Client sử dụng Adafruit IO MQTT.

    Cách dùng:
        mqtt = MQTTSingleton.get_instance()
        mqtt.publish(config.FEED_LED, "1")
    """

    _instance = None          # Biến lưu instance duy nhất
    _lock = threading.Lock()  # Lock đảm bảo thread-safe khi khởi tạo

    def __init__(self):
        """Khởi tạo nội bộ - KHÔNG gọi trực tiếp, dùng get_instance()."""
        self._client: MQTTClient = None
        self._connected = False
        self._reconnect_delay = 5      # Giây chờ trước khi reconnect
        self._message_callbacks = {}   # {feed_name: callback_function}
        self._connect()

    # ------------------------------------------------------------------
    # Singleton accessor
    # ------------------------------------------------------------------
    @classmethod
    def get_instance(cls) -> "MQTTSingleton":
        """
        Trả về instance duy nhất của MQTTSingleton.
        Thread-safe: dùng double-checked locking.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    logger.info("[MQTT] Khởi tạo Singleton MQTT Client...")
                    cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Kết nối & xử lý sự kiện Adafruit MQTT
    # ------------------------------------------------------------------
    def _connect(self):
        """Tạo đối tượng MQTTClient và bắt đầu kết nối đến Adafruit IO."""
        try:
            self._client = MQTTClient(
                username=config.ADAFRUIT_USERNAME,
                key=config.ADAFRUIT_AIO_KEY
            )
            # Gán các hàm xử lý sự kiện
            self._client.on_connect    = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message    = self._on_message

            logger.info(f"[MQTT] Đang kết nối đến {config.MQTT_BROKER}...")
            self._client.connect()
            self._client.loop_background()  # Chạy vòng lặp MQTT ở background thread
        except Exception as e:
            logger.error(f"[MQTT] Lỗi kết nối: {e}")
            self._schedule_reconnect()

    def _on_connect(self, client):
        """Callback khi kết nối thành công."""
        self._connected = True
        logger.info("[MQTT] ✅ Kết nối Adafruit IO thành công!")
        # Subscribe lại tất cả các feed đã đăng ký sau khi reconnect
        for feed_name in self._message_callbacks:
            self._client.subscribe(feed_name)
            logger.debug(f"[MQTT] Subscribed lại feed: {feed_name}")

    def _on_disconnect(self, client):
        """Callback khi mất kết nối - kích hoạt auto-reconnect."""
        self._connected = False
        logger.warning("[MQTT] ⚠️  Mất kết nối MQTT! Đang lên lịch reconnect...")
        self._schedule_reconnect()

    def _on_message(self, client, feed_id, payload):
        """
        Callback khi nhận tin nhắn từ Adafruit IO.
        Điều hướng payload đến đúng callback đã đăng ký theo feed.
        """
        logger.debug(f"[MQTT] Nhận tin nhắn | Feed: {feed_id} | Payload: {payload}")
        # Lấy tên feed không có prefix username
        feed_name = feed_id.split("/")[-1] if "/" in feed_id else feed_id
        if feed_name in self._message_callbacks:
            try:
                self._message_callbacks[feed_name](payload)
            except Exception as e:
                logger.error(f"[MQTT] Lỗi trong callback của feed '{feed_name}': {e}")

    def _schedule_reconnect(self):
        """Lên lịch reconnect sau _reconnect_delay giây trong một thread riêng."""
        def reconnect_task():
            logger.info(f"[MQTT] Thử reconnect sau {self._reconnect_delay}s...")
            time.sleep(self._reconnect_delay)
            self._connect()

        thread = threading.Thread(target=reconnect_task, daemon=True, name="MQTT-Reconnect")
        thread.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def publish(self, feed_name: str, value, retain: bool = False):
        """
        Gửi dữ liệu lên Adafruit IO feed.

        Args:
            feed_name: Tên feed (ví dụ: config.FEED_LED)
            value    : Giá trị cần gửi (str, int, float)
            retain   : Giữ lại tin nhắn trên broker (mặc định False)
        """
        if not self._connected:
            logger.warning(f"[MQTT] Chưa kết nối, không thể publish lên '{feed_name}'")
            return False
        try:
            self._client.publish(feed_name, value)
            logger.info(f"[MQTT] 📤 Publish | Feed: {feed_name} | Value: {value}")
            return True
        except Exception as e:
            logger.error(f"[MQTT] Lỗi publish lên '{feed_name}': {e}")
            return False

    def subscribe(self, feed_name: str, callback):
        """
        Đăng ký nhận tin nhắn từ một feed và gán callback xử lý.

        Args:
            feed_name: Tên feed cần subscribe
            callback : Hàm xử lý nhận được payload (fn(payload: str))
        """
        self._message_callbacks[feed_name] = callback
        if self._connected:
            try:
                self._client.subscribe(feed_name)
                logger.info(f"[MQTT] 📥 Subscribed feed: {feed_name}")
            except Exception as e:
                logger.error(f"[MQTT] Lỗi subscribe feed '{feed_name}': {e}")

    @property
    def is_connected(self) -> bool:
        """Trả về trạng thái kết nối hiện tại."""
        return self._connected
