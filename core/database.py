"""
core/database.py - SQLite Persistent Storage cho YoloHome

Singleton pattern, thread-safe.
Lưu trữ:
  - sensor_readings  : lịch sử cảm biến (temp, humi, gas)
  - device_events    : nhật ký bật/tắt thiết bị (led, fan, pump, door)
  - face_events      : sự kiện nhận diện khuôn mặt
  - automation_rules : quy tắc tự động hóa If-Then (Phase 4)
  - rule_logs        : nhật ký kích hoạt quy tắc (Phase 4)

Dùng bởi:
  - SensorReader   → ghi sensor_readings + device_events
  - FaceRecognizer → ghi face_events
  - RuleEngine     → đọc/ghi automation_rules + rule_logs
  - WebApp /api/history → đọc lịch sử
  - WebApp /api/energy  → tính điện năng tiêu thụ
  - WebApp /api/rules   → CRUD automation_rules
"""

import os
import sqlite3
import threading
import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Đường dẫn mặc định – có thể ghi đè qua config.DATABASE_PATH
_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "yolohome.db"
)


class DatabaseSingleton:
    """
    Thread-safe SQLite Singleton.
    Dùng check-lock-check pattern để đảm bảo chỉ tạo một instance.
    """

    _instance: "DatabaseSingleton" = None
    _lock = threading.Lock()

    def __new__(cls, db_path: str = _DEFAULT_DB_PATH):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._init(db_path)
                    cls._instance = inst
        return cls._instance

    @classmethod
    def get_instance(cls, db_path: str = _DEFAULT_DB_PATH) -> "DatabaseSingleton":
        return cls(db_path)

    # ─────────────────────────── Init ───────────────────────────────────
    def _init(self, db_path: str):
        self._db_path = db_path
        self._write_lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._create_tables()
        logger.info(f"[DB] ✅ SQLite khởi tạo tại: {db_path}")

    def _get_conn(self) -> sqlite3.Connection:
        """Mỗi thread dùng connection riêng (check_same_thread=False)."""
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row          # Trả về dict-like rows
        conn.execute("PRAGMA journal_mode=WAL") # Write-Ahead Log → không block read
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _create_tables(self):
        """Tạo schema nếu chưa tồn tại."""
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sensor_readings (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        REAL    NOT NULL,          -- Unix timestamp
                    temp      REAL,
                    humi      REAL,
                    gas       REAL,
                    created   TEXT    DEFAULT (datetime('now','localtime'))
                );
                CREATE INDEX IF NOT EXISTS idx_sr_ts ON sensor_readings(ts);

                CREATE TABLE IF NOT EXISTS device_events (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        REAL    NOT NULL,
                    device    TEXT    NOT NULL,          -- led|fan|pump|door
                    state     INTEGER NOT NULL,          -- 1=ON, 0=OFF
                    source    TEXT    DEFAULT 'mqtt',    -- mqtt|voice|web|auto
                    created   TEXT    DEFAULT (datetime('now','localtime'))
                );
                CREATE INDEX IF NOT EXISTS idx_de_ts ON device_events(ts);
                CREATE INDEX IF NOT EXISTS idx_de_dev ON device_events(device);

                CREATE TABLE IF NOT EXISTS face_events (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts         REAL    NOT NULL,
                    event_type TEXT    NOT NULL,         -- known|stranger
                    person     TEXT,                     -- tên người (nếu known)
                    confidence REAL,
                    img_path   TEXT,
                    created    TEXT    DEFAULT (datetime('now','localtime'))
                );
                CREATE INDEX IF NOT EXISTS idx_fe_ts ON face_events(ts);

                CREATE TABLE IF NOT EXISTS automation_rules (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    name             TEXT    NOT NULL,
                    condition_field  TEXT    NOT NULL,   -- temp|humi|gas
                    condition_op     TEXT    NOT NULL,   -- >|<|>=|<=|==
                    condition_value  REAL    NOT NULL,
                    action_device    TEXT    NOT NULL,   -- led|fan|pump|door
                    action_state     INTEGER NOT NULL,   -- 1=ON, 0=OFF
                    notify_telegram  INTEGER DEFAULT 0,
                    enabled          INTEGER DEFAULT 1,
                    created          TEXT    DEFAULT (datetime('now','localtime'))
                );

                CREATE TABLE IF NOT EXISTS rule_logs (
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id  INTEGER NOT NULL,
                    ts       REAL    NOT NULL,
                    field    TEXT,
                    value    REAL,
                    created  TEXT    DEFAULT (datetime('now','localtime'))
                );
                CREATE INDEX IF NOT EXISTS idx_rl_ts      ON rule_logs(ts);
                CREATE INDEX IF NOT EXISTS idx_rl_rule_id ON rule_logs(rule_id);
            """)

    # ─────────────────────────── Write ──────────────────────────────────
    def insert_sensor(self, temp: float, humi: float, gas: float):
        """Ghi một bản đọc cảm biến."""
        with self._write_lock:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT INTO sensor_readings (ts, temp, humi, gas) VALUES (?,?,?,?)",
                    (time.time(), temp, humi, gas)
                )

    def insert_device_event(self, device: str, state: int, source: str = "mqtt"):
        """Ghi sự kiện bật/tắt thiết bị."""
        with self._write_lock:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT INTO device_events (ts, device, state, source) VALUES (?,?,?,?)",
                    (time.time(), device, state, source)
                )

    def insert_face_event(self, event_type: str, person: Optional[str] = None,
                          confidence: Optional[float] = None, img_path: Optional[str] = None):
        """Ghi sự kiện nhận diện khuôn mặt."""
        with self._write_lock:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT INTO face_events (ts, event_type, person, confidence, img_path) "
                    "VALUES (?,?,?,?,?)",
                    (time.time(), event_type, person, confidence, img_path)
                )

    # ─────────────────────────── Read ───────────────────────────────────
    def get_sensor_history(self, hours: int = 24, limit: int = 500) -> List[Dict]:
        """
        Lấy lịch sử cảm biến trong N giờ gần nhất.
        Trả về danh sách dict {ts, temp, humi, gas, created}.
        """
        since = time.time() - hours * 3600
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT ts, temp, humi, gas, created FROM sensor_readings "
                "WHERE ts >= ? ORDER BY ts ASC LIMIT ?",
                (since, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_device_events(self, device: Optional[str] = None,
                          hours: int = 24) -> List[Dict]:
        """Lấy lịch sử bật/tắt thiết bị."""
        since = time.time() - hours * 3600
        if device:
            sql = ("SELECT ts, device, state, source, created FROM device_events "
                   "WHERE ts >= ? AND device = ? ORDER BY ts DESC LIMIT 200")
            params = (since, device)
        else:
            sql = ("SELECT ts, device, state, source, created FROM device_events "
                   "WHERE ts >= ? ORDER BY ts DESC LIMIT 200")
            params = (since,)
        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_energy_report(self, hours: int = 24) -> Dict[str, Any]:
        """
        Tính thời gian bật (giây) cho mỗi thiết bị trong N giờ qua.
        Dùng để ước tính điện năng tiêu thụ.

        Trả về:
            {
              "led":  {"on_seconds": 3600, "on_hours": 1.0, "est_kwh": 0.006},
              "fan":  {...},
              ...
              "period_hours": 24
            }
        """
        since = time.time() - hours * 3600
        # Công suất ước tính (Watt) mỗi thiết bị
        POWER_W = {"led": 6, "fan": 40, "pump": 30, "door": 5}
        report: Dict[str, Any] = {"period_hours": hours}

        with self._get_conn() as conn:
            for dev, watt in POWER_W.items():
                rows = conn.execute(
                    "SELECT ts, state FROM device_events "
                    "WHERE ts >= ? AND device = ? ORDER BY ts ASC",
                    (since, dev)
                ).fetchall()

                on_secs = 0.0
                last_on_ts = None
                for row in rows:
                    if row["state"] == 1:
                        last_on_ts = row["ts"]
                    elif row["state"] == 0 and last_on_ts is not None:
                        on_secs += row["ts"] - last_on_ts
                        last_on_ts = None
                # Nếu vẫn đang bật đến hiện tại
                if last_on_ts is not None:
                    on_secs += time.time() - last_on_ts

                kwh = (watt * on_secs / 3600) / 1000
                report[dev] = {
                    "on_seconds": round(on_secs),
                    "on_hours":   round(on_secs / 3600, 2),
                    "est_kwh":    round(kwh, 4),
                    "power_w":    watt,
                }

        return report

    # ─────────────────────── Rule Engine CRUD ───────────────────────────
    def get_rules(self, enabled_only: bool = False) -> List[Dict]:
        """
        Lấy danh sách tất cả quy tắc tự động hóa.

        Args:
            enabled_only: Nếu True, chỉ trả về các rule đang bật.

        Returns:
            Danh sách dict với các key: id, name, condition_field,
            condition_op, condition_value, action_device, action_state,
            notify_telegram, enabled, created.
        """
        sql = "SELECT * FROM automation_rules"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY id ASC"
        with self._get_conn() as conn:
            rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]

    def insert_rule(self, name: str, condition_field: str, condition_op: str,
                    condition_value: float, action_device: str, action_state: int,
                    notify_telegram: int = 0, enabled: int = 1) -> int:
        """
        Tạo một quy tắc tự động hóa mới.

        Returns:
            ID của rule vừa tạo.
        """
        with self._write_lock:
            with self._get_conn() as conn:
                cur = conn.execute(
                    """INSERT INTO automation_rules
                       (name, condition_field, condition_op, condition_value,
                        action_device, action_state, notify_telegram, enabled)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (name, condition_field, condition_op, condition_value,
                     action_device, action_state, notify_telegram, enabled)
                )
                return cur.lastrowid

    def delete_rule(self, rule_id: int) -> bool:
        """
        Xoá một quy tắc theo ID.

        Returns:
            True nếu xoá thành công, False nếu không tìm thấy.
        """
        with self._write_lock:
            with self._get_conn() as conn:
                cur = conn.execute(
                    "DELETE FROM automation_rules WHERE id = ?", (rule_id,)
                )
                return cur.rowcount > 0

    def toggle_rule(self, rule_id: int) -> Optional[int]:
        """
        Đổi trạng thái enabled/disabled của một rule.

        Returns:
            Giá trị enabled mới (0 hoặc 1), hoặc None nếu rule không tồn tại.
        """
        with self._write_lock:
            with self._get_conn() as conn:
                row = conn.execute(
                    "SELECT enabled FROM automation_rules WHERE id = ?", (rule_id,)
                ).fetchone()
                if row is None:
                    return None
                new_state = 0 if row["enabled"] else 1
                conn.execute(
                    "UPDATE automation_rules SET enabled = ? WHERE id = ?",
                    (new_state, rule_id)
                )
                return new_state

    def insert_rule_log(self, rule_id: int, field: str, value: float):
        """Ghi nhật ký mỗi khi một quy tắc được kích hoạt."""
        with self._write_lock:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT INTO rule_logs (rule_id, ts, field, value) VALUES (?,?,?,?)",
                    (rule_id, time.time(), field, value)
                )

    def get_rule_logs(self, rule_id: Optional[int] = None,
                      hours: int = 24, limit: int = 100) -> List[Dict]:
        """
        Lấy nhật ký kích hoạt quy tắc.

        Args:
            rule_id : Lọc theo rule cụ thể (None = tất cả)
            hours   : Khoảng thời gian lấy log
            limit   : Số lượng tối đa
        """
        since = time.time() - hours * 3600
        if rule_id is not None:
            sql = ("SELECT rl.*, ar.name as rule_name FROM rule_logs rl "
                   "LEFT JOIN automation_rules ar ON rl.rule_id = ar.id "
                   "WHERE rl.ts >= ? AND rl.rule_id = ? ORDER BY rl.ts DESC LIMIT ?")
            params = (since, rule_id, limit)
        else:
            sql = ("SELECT rl.*, ar.name as rule_name FROM rule_logs rl "
                   "LEFT JOIN automation_rules ar ON rl.rule_id = ar.id "
                   "WHERE rl.ts >= ? ORDER BY rl.ts DESC LIMIT ?")
            params = (since, limit)
        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def cleanup_old_data(self, keep_days: int = 7):
        """Xóa dữ liệu cũ hơn keep_days ngày để tránh DB phình to."""
        cutoff = time.time() - keep_days * 86400
        with self._write_lock:
            with self._get_conn() as conn:
                conn.execute("DELETE FROM sensor_readings WHERE ts < ?", (cutoff,))
                conn.execute("DELETE FROM device_events   WHERE ts < ?", (cutoff,))
                conn.execute("DELETE FROM face_events     WHERE ts < ?", (cutoff,))
                conn.execute("DELETE FROM rule_logs       WHERE ts < ?", (cutoff,))
                conn.execute("VACUUM")
        logger.info(f"[DB] 🧹 Đã xóa dữ liệu cũ hơn {keep_days} ngày.")
