"""
core/telegram_notifier.py - Gửi thông báo Telegram khi có sự kiện nguy hiểm

Tích hợp:
  - SensorReader._check_gas_threshold()  → gas_alert
  - FaceRecognizer._send_stranger_alert() → stranger_alert
  - SensorReader._check_temp_threshold() → temp_alert (tuỳ chọn)

Cài đặt:
    pip install python-telegram-bot --trusted-host pypi.org --trusted-host files.pythonhosted.org

Cấu hình .env:
    TELEGRAM_BOT_TOKEN=<token>
    TELEGRAM_CHAT_ID=<chat_id>
"""

import os
import sys
import time
import logging
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """
    Singleton gửi tin nhắn và ảnh qua Telegram Bot API.

    Dùng python-telegram-bot (sync wrapper) trong thread riêng
    để không block luồng chính.
    """

    _instance: "TelegramNotifier" = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._init()
                    cls._instance = inst
        return cls._instance

    @classmethod
    def get_instance(cls) -> "TelegramNotifier":
        return cls()

    # ─────────────────────────── Init ───────────────────────────────────
    def _init(self):
        self._token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = os.getenv("TELEGRAM_CHAT_ID",   "")
        self._bot     = None
        self._enabled = False
        self._queue   = []
        self._q_lock  = threading.Lock()

        if not self._token or not self._chat_id:
            logger.warning("[Telegram] Token hoặc Chat ID chưa cấu hình → thông báo bị tắt.")
            return

        self._enabled = True
        # Worker thread gửi tin không đồng bộ
        threading.Thread(target=self._worker, daemon=True, name="Telegram-Worker").start()
        logger.info("[Telegram] ✅ Notifier đã sẵn sàng.")

    # ─────────────────────────── Public API ─────────────────────────────
    def send_text(self, message: str):
        """Đưa tin nhắn text vào hàng đợi gửi."""
        if not self._enabled:
            return
        with self._q_lock:
            self._queue.append(("text", message, None))

    def send_photo(self, image_path: str, caption: str = ""):
        """Đưa ảnh + caption vào hàng đợi gửi."""
        if not self._enabled:
            return
        with self._q_lock:
            self._queue.append(("photo", caption, image_path))

    def gas_alert(self, ppm: float):
        """Gửi cảnh báo rò rỉ khí gas (REQ-03)."""
        msg = (
            "🚨 *CẢNH BÁO KHÍ GAS* 🚨\n\n"
            f"📍 Nồng độ: *{ppm:.0f} ppm*\n"
            f"⚠️ Ngưỡng an toàn: 300 ppm\n\n"
            "🔴 *Hành động ngay:*\n"
            "1. Tắt bếp gas\n"
            "2. Mở cửa thông gió\n"
            "3. Không bật công tắc điện\n"
            "4. Rời khỏi khu vực ngay lập tức"
        )
        self.send_text(msg)

    def stranger_alert(self, duration_s: float, image_path: Optional[str] = None):
        """Gửi cảnh báo người lạ kèm ảnh (REQ-09)."""
        msg = (
            "🚨 *PHÁT HIỆN NGƯỜI LẠ* 🚨\n\n"
            f"⏱️ Xuất hiện liên tục: *{duration_s:.0f} giây*\n"
            f"🕒 Thời gian: {time.strftime('%H:%M:%S %d/%m/%Y')}\n\n"
            "📸 Xem ảnh đính kèm bên dưới."
        )
        if image_path and os.path.exists(image_path):
            self.send_photo(image_path, caption=msg)
        else:
            self.send_text(msg)

    def temp_alert(self, temp: float):
        """Gửi cảnh báo nhiệt độ cao."""
        msg = (
            "🌡️ *CẢNH BÁO NHIỆT ĐỘ CAO*\n\n"
            f"🌡️ Nhiệt độ hiện tại: *{temp:.1f}°C*\n"
            "⚠️ Ngưỡng an toàn: 35°C\n"
            "✅ Quạt tự động đã được bật."
        )
        self.send_text(msg)

    # ─────────────────────────── Worker ─────────────────────────────────
    def _worker(self):
        """Background thread: xử lý hàng đợi gửi Telegram."""
        # Lazy import để không crash khi chưa cài
        try:
            import requests as _req
            self._req = _req
        except ImportError:
            logger.error("[Telegram] Thiếu 'requests'. pip install requests")
            self._enabled = False
            return

        while True:
            time.sleep(0.5)
            with self._q_lock:
                items = list(self._queue)
                self._queue.clear()

            for kind, content, extra in items:
                try:
                    if kind == "text":
                        self._send_text_sync(content)
                    elif kind == "photo":
                        self._send_photo_sync(extra, content)
                except Exception as e:
                    logger.error(f"[Telegram] Gửi thất bại: {e}")

    def _send_text_sync(self, text: str):
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        self._req.post(url, json={
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }, timeout=10)

    def _send_photo_sync(self, image_path: str, caption: str):
        url = f"https://api.telegram.org/bot{self._token}/sendPhoto"
        with open(image_path, "rb") as f:
            self._req.post(url, data={
                "chat_id": self._chat_id,
                "caption": caption,
                "parse_mode": "Markdown",
            }, files={"photo": f}, timeout=15)
