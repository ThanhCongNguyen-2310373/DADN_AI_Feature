"""
core/rule_engine.py - Bộ quy tắc tự động hoá (Rule Engine) cho YoloHome

Thực thi các quy tắc "Nếu-Thì" được định nghĩa bởi người dùng:
  Nếu <sensor_field> <op> <value> → bật/tắt <device> [+ gửi Telegram]

Thiết kế:
  - Singleton pattern, thread-safe
  - Tải quy tắc từ SQLite mỗi 30 giây (tự động nhận rules mới)
  - Được gọi bởi SensorReader sau mỗi chu kỳ đọc cảm biến
  - Cooldown 60s mỗi rule để tránh kích hoạt liên tục
"""

import threading
import logging
import time
import operator
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Ánh xạ toán tử chuỗi → hàm so sánh
_OPS = {
    ">":  operator.gt,
    "<":  operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
}

# Cooldown tránh kích hoạt lặp (giây)
_RULE_COOLDOWN_SECS = 60


class RuleEngine:
    """
    Singleton Rule Engine.

    Cách dùng:
        engine = RuleEngine.get_instance()
        engine.evaluate({"temp": 36.0, "humi": 70.0, "gas": 250.0})
    """

    _instance: "RuleEngine" = None
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
    def get_instance(cls) -> "RuleEngine":
        return cls()

    # ─────────────────────────── Init ────────────────────────────────────
    def _init(self):
        self._rules: List[Dict] = []
        self._rules_lock = threading.Lock()
        self._cooldowns: Dict[int, float] = {}   # {rule_id: last_fired_ts}
        self._last_load_ts: float = 0
        self._load_interval: float = 30.0        # Tải lại rules mỗi 30 giây
        logger.info("[RuleEngine] ✅ Rule Engine đã khởi tạo.")

    # ─────────────────────────── Load rules ──────────────────────────────
    def _load_rules(self):
        """Tải (hoặc reload) danh sách quy tắc từ SQLite."""
        now = time.time()
        if now - self._last_load_ts < self._load_interval:
            return  # Chưa đến lúc reload
        try:
            from core.database import DatabaseSingleton
            db = DatabaseSingleton.get_instance()
            rules = db.get_rules(enabled_only=True)
            with self._rules_lock:
                self._rules = rules
            self._last_load_ts = now
            logger.debug(f"[RuleEngine] Đã tải {len(rules)} quy tắc từ DB.")
        except Exception as e:
            logger.warning(f"[RuleEngine] Không thể tải rules: {e}")

    # ─────────────────────────── Evaluate ────────────────────────────────
    def evaluate(self, sensor_data: Dict[str, Any]):
        """
        Kiểm tra tất cả quy tắc đang bật với dữ liệu cảm biến hiện tại.
        Kích hoạt hành động nếu điều kiện đúng và hết cooldown.

        Args:
            sensor_data: Dict với các key 'temp', 'humi', 'gas'
        """
        self._load_rules()

        with self._rules_lock:
            rules_snapshot = list(self._rules)

        for rule in rules_snapshot:
            try:
                self._check_rule(rule, sensor_data)
            except Exception as e:
                logger.debug(f"[RuleEngine] Lỗi kiểm tra rule #{rule.get('id')}: {e}")

    def _check_rule(self, rule: Dict, sensor_data: Dict[str, Any]):
        """Kiểm tra và kích hoạt một quy tắc nếu điều kiện thoả mãn."""
        rule_id     = rule["id"]
        field       = rule["condition_field"]   # "temp" | "humi" | "gas"
        op_str      = rule["condition_op"]      # ">" | "<" | ">=" | "<=" | "=="
        threshold   = float(rule["condition_value"])
        device      = rule["action_device"]
        state       = int(rule["action_state"])
        notify      = bool(rule.get("notify_telegram", 0))
        name        = rule.get("name", f"Rule#{rule_id}")

        # Lấy giá trị cảm biến hiện tại
        current_val = sensor_data.get(field)
        if current_val is None:
            return

        # Kiểm tra điều kiện
        compare_fn = _OPS.get(op_str)
        if compare_fn is None:
            return
        if not compare_fn(float(current_val), threshold):
            return

        # Kiểm tra cooldown
        now = time.time()
        last_fired = self._cooldowns.get(rule_id, 0)
        if now - last_fired < _RULE_COOLDOWN_SECS:
            return

        # ── Kích hoạt hành động ──
        self._cooldowns[rule_id] = now
        logger.info(
            f"[RuleEngine] 🔥 Rule '{name}': {field}={current_val} {op_str} {threshold} "
            f"→ {device}={'BẬT' if state else 'TẮT'}"
        )
        self._fire_action(rule_id, name, device, state, field, current_val, notify)

    def _fire_action(self, rule_id: int, name: str, device: str, state: int,
                     field: str, current_val: float, notify: bool):
        """Thực thi hành động: publish MQTT + ghi DB + Telegram (nếu cần)."""
        try:
            import config
            from core.mqtt_client import MQTTSingleton
            from core.database import DatabaseSingleton

            feed_map = {
                "led":  f"{config.ADAFRUIT_USERNAME}/feeds/{config.FEED_LED}",
                "fan":  f"{config.ADAFRUIT_USERNAME}/feeds/{config.FEED_FAN}",
                "pump": f"{config.ADAFRUIT_USERNAME}/feeds/{config.FEED_PUMP}",
                "door": f"{config.ADAFRUIT_USERNAME}/feeds/{config.FEED_DOOR}",
            }
            feed = feed_map.get(device)
            if feed:
                MQTTSingleton.get_instance().publish(feed, str(state))

            # Ghi sự kiện vào DB với source="auto"
            DatabaseSingleton.get_instance().insert_device_event(device, state, source="auto")

            # Ghi log rule được kích hoạt
            DatabaseSingleton.get_instance().insert_rule_log(rule_id, field, current_val)

        except Exception as e:
            logger.error(f"[RuleEngine] Lỗi fire action: {e}")

        # Gửi Telegram nếu được bật
        if notify:
            try:
                from core.telegram_notifier import TelegramNotifier
                field_label = {"temp": "Nhiệt độ", "humi": "Độ ẩm", "gas": "Khí gas"}.get(field, field)
                unit = {"temp": "°C", "humi": "%", "gas": "ppm"}.get(field, "")
                action_label = "BẬT" if state else "TẮT"
                msg = (
                    f"⚙️ *Rule Engine kích hoạt*\n"
                    f"📋 Quy tắc: _{name}_\n"
                    f"📊 {field_label}: *{current_val:.1f}{unit}*\n"
                    f"💡 Hành động: *{device.upper()} → {action_label}*"
                )
                TelegramNotifier.get_instance().send_text(msg)
            except Exception as e:
                logger.debug(f"[RuleEngine] Telegram notify error: {e}")
