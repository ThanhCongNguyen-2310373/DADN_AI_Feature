"""
core/database_postgres.py - PostgreSQL backend cho YoloHome (Phase 5)

Mục tiêu:
- Hỗ trợ multi-node bằng DB server (PostgreSQL)
- Giữ API method tương thích với DatabaseSingleton (SQLite)
"""

import os
import time
import threading
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class PostgreSQLDatabase:
    """Thread-safe singleton cho PostgreSQL backend."""

    _instance: "PostgreSQLDatabase" = None
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
    def get_instance(cls) -> "PostgreSQLDatabase":
        return cls()

    def _init(self):
        import config

        self._write_lock = threading.Lock()
        self._dsn = getattr(config, "POSTGRES_DSN", "")
        if not self._dsn:
            user = getattr(config, "POSTGRES_USER", "postgres")
            password = getattr(config, "POSTGRES_PASSWORD", "postgres")
            host = getattr(config, "POSTGRES_HOST", "localhost")
            port = getattr(config, "POSTGRES_PORT", "5432")
            db = getattr(config, "POSTGRES_DB", "yolohome")
            self._dsn = f"postgresql://{user}:{password}@{host}:{port}/{db}"

        self._connect_test()
        self._create_tables()
        logger.info("[DB] PostgreSQL backend initialized")

    def _connect_test(self):
        conn = self._get_conn()
        conn.close()

    def _get_conn(self):
        try:
            import psycopg2
            import psycopg2.extras
        except Exception as exc:
            raise RuntimeError(
                "psycopg2-binary chưa được cài. Hãy cài requirements trước khi dùng PostgreSQL backend"
            ) from exc

        conn = psycopg2.connect(self._dsn)
        return conn

    def _query(self, sql: str, params: tuple = (), fetch: str = "none"):
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if fetch == "one":
                    row = cur.fetchone()
                    if not row:
                        return None
                    cols = [d[0] for d in cur.description]
                    return dict(zip(cols, row))
                if fetch == "all":
                    rows = cur.fetchall()
                    cols = [d[0] for d in cur.description]
                    return [dict(zip(cols, r)) for r in rows]
                return None

    def _create_tables(self):
        sql = """
        CREATE TABLE IF NOT EXISTS sensor_readings (
            id        BIGSERIAL PRIMARY KEY,
            ts        DOUBLE PRECISION NOT NULL,
            temp      DOUBLE PRECISION,
            humi      DOUBLE PRECISION,
            gas       DOUBLE PRECISION,
            created   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_sr_ts ON sensor_readings(ts);

        CREATE TABLE IF NOT EXISTS device_events (
            id        BIGSERIAL PRIMARY KEY,
            ts        DOUBLE PRECISION NOT NULL,
            device    TEXT NOT NULL,
            state     INTEGER NOT NULL,
            source    TEXT DEFAULT 'mqtt',
            created   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_de_ts ON device_events(ts);
        CREATE INDEX IF NOT EXISTS idx_de_dev ON device_events(device);

        CREATE TABLE IF NOT EXISTS face_events (
            id         BIGSERIAL PRIMARY KEY,
            ts         DOUBLE PRECISION NOT NULL,
            event_type TEXT NOT NULL,
            person     TEXT,
            confidence DOUBLE PRECISION,
            img_path   TEXT,
            created    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_fe_ts ON face_events(ts);

        CREATE TABLE IF NOT EXISTS automation_rules (
            id               BIGSERIAL PRIMARY KEY,
            name             TEXT NOT NULL,
            condition_field  TEXT NOT NULL,
            condition_op     TEXT NOT NULL,
            condition_value  DOUBLE PRECISION NOT NULL,
            action_device    TEXT NOT NULL,
            action_state     INTEGER NOT NULL,
            notify_telegram  INTEGER DEFAULT 0,
            enabled          INTEGER DEFAULT 1,
            created          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS rule_logs (
            id       BIGSERIAL PRIMARY KEY,
            rule_id  BIGINT NOT NULL,
            ts       DOUBLE PRECISION NOT NULL,
            field    TEXT,
            value    DOUBLE PRECISION,
            created  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_rl_ts ON rule_logs(ts);
        CREATE INDEX IF NOT EXISTS idx_rl_rule_id ON rule_logs(rule_id);

        CREATE TABLE IF NOT EXISTS users (
            id             BIGSERIAL PRIMARY KEY,
            username       TEXT NOT NULL UNIQUE,
            password_hash  TEXT NOT NULL,
            role           TEXT NOT NULL DEFAULT 'viewer',
            is_active      INTEGER NOT NULL DEFAULT 1,
            created_ts     DOUBLE PRECISION NOT NULL,
            updated_ts     DOUBLE PRECISION
        );

        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id      BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            full_name    TEXT,
            email        TEXT,
            phone        TEXT,
            department   TEXT,
            updated_ts   DOUBLE PRECISION
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token         TEXT PRIMARY KEY,
            user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_ts    DOUBLE PRECISION NOT NULL,
            expires_ts    DOUBLE PRECISION NOT NULL,
            last_seen_ts  DOUBLE PRECISION NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_ts);
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)

    # ---------------------------- sensors/events ----------------------------
    def insert_sensor(self, temp: float, humi: float, gas: float):
        self._query(
            "INSERT INTO sensor_readings (ts, temp, humi, gas) VALUES (%s,%s,%s,%s)",
            (time.time(), temp, humi, gas),
        )

    def insert_device_event(self, device: str, state: int, source: str = "mqtt"):
        self._query(
            "INSERT INTO device_events (ts, device, state, source) VALUES (%s,%s,%s,%s)",
            (time.time(), device, state, source),
        )

    def insert_face_event(
        self,
        event_type: str,
        person: Optional[str] = None,
        confidence: Optional[float] = None,
        img_path: Optional[str] = None,
    ):
        self._query(
            "INSERT INTO face_events (ts, event_type, person, confidence, img_path) VALUES (%s,%s,%s,%s,%s)",
            (time.time(), event_type, person, confidence, img_path),
        )

    def get_sensor_history(self, hours: int = 24, limit: int = 500) -> List[Dict[str, Any]]:
        since = time.time() - hours * 3600
        return self._query(
            "SELECT ts, temp, humi, gas, created FROM sensor_readings WHERE ts >= %s ORDER BY ts ASC LIMIT %s",
            (since, limit),
            fetch="all",
        )

    def get_device_events(self, device: Optional[str] = None, hours: int = 24) -> List[Dict[str, Any]]:
        since = time.time() - hours * 3600
        if device:
            sql = (
                "SELECT ts, device, state, source, created FROM device_events "
                "WHERE ts >= %s AND device = %s ORDER BY ts DESC LIMIT 500"
            )
            params = (since, device)
        else:
            sql = (
                "SELECT ts, device, state, source, created FROM device_events "
                "WHERE ts >= %s ORDER BY ts DESC LIMIT 500"
            )
            params = (since,)
        return self._query(sql, params, fetch="all")

    def get_energy_report(self, hours: int = 24) -> Dict[str, Any]:
        since = time.time() - hours * 3600
        power_w = {"led": 6, "fan": 40, "pump": 30, "door": 5}
        report: Dict[str, Any] = {"period_hours": hours}

        for dev, watt in power_w.items():
            rows = self._query(
                "SELECT ts, state FROM device_events WHERE ts >= %s AND device = %s ORDER BY ts ASC",
                (since, dev),
                fetch="all",
            )
            on_secs = 0.0
            last_on_ts = None
            for row in rows:
                if row["state"] == 1:
                    last_on_ts = row["ts"]
                elif row["state"] == 0 and last_on_ts is not None:
                    on_secs += row["ts"] - last_on_ts
                    last_on_ts = None
            if last_on_ts is not None:
                on_secs += time.time() - last_on_ts

            kwh = (watt * on_secs / 3600) / 1000
            report[dev] = {
                "on_seconds": round(on_secs),
                "on_hours": round(on_secs / 3600, 2),
                "est_kwh": round(kwh, 4),
                "power_w": watt,
            }
        return report

    # ---------------------------- rules ----------------------------
    def get_rules(self, enabled_only: bool = False) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM automation_rules"
        params: tuple = ()
        if enabled_only:
            sql += " WHERE enabled = %s"
            params = (1,)
        sql += " ORDER BY id ASC"
        return self._query(sql, params, fetch="all")

    def insert_rule(
        self,
        name: str,
        condition_field: str,
        condition_op: str,
        condition_value: float,
        action_device: str,
        action_state: int,
        notify_telegram: int = 0,
        enabled: int = 1,
    ) -> int:
        row = self._query(
            "INSERT INTO automation_rules "
            "(name, condition_field, condition_op, condition_value, action_device, action_state, notify_telegram, enabled) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (
                name,
                condition_field,
                condition_op,
                condition_value,
                action_device,
                action_state,
                notify_telegram,
                enabled,
            ),
            fetch="one",
        )
        return int(row["id"])

    def delete_rule(self, rule_id: int) -> bool:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM automation_rules WHERE id = %s", (rule_id,))
                return cur.rowcount > 0

    def toggle_rule(self, rule_id: int) -> Optional[int]:
        row = self._query("SELECT enabled FROM automation_rules WHERE id = %s", (rule_id,), fetch="one")
        if not row:
            return None
        new_state = 0 if row["enabled"] else 1
        self._query("UPDATE automation_rules SET enabled = %s WHERE id = %s", (new_state, rule_id))
        return new_state

    def insert_rule_log(self, rule_id: int, field: str, value: float):
        self._query(
            "INSERT INTO rule_logs (rule_id, ts, field, value) VALUES (%s,%s,%s,%s)",
            (rule_id, time.time(), field, value),
        )

    def get_rule_logs(self, rule_id: Optional[int] = None, hours: int = 24, limit: int = 100) -> List[Dict[str, Any]]:
        since = time.time() - hours * 3600
        if rule_id is not None:
            sql = (
                "SELECT rl.*, ar.name as rule_name FROM rule_logs rl "
                "LEFT JOIN automation_rules ar ON rl.rule_id = ar.id "
                "WHERE rl.ts >= %s AND rl.rule_id = %s ORDER BY rl.ts DESC LIMIT %s"
            )
            params = (since, rule_id, limit)
        else:
            sql = (
                "SELECT rl.*, ar.name as rule_name FROM rule_logs rl "
                "LEFT JOIN automation_rules ar ON rl.rule_id = ar.id "
                "WHERE rl.ts >= %s ORDER BY rl.ts DESC LIMIT %s"
            )
            params = (since, limit)
        return self._query(sql, params, fetch="all")

    # ---------------------------- auth / users ----------------------------
    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        sql = (
            "SELECT u.*, p.full_name, p.email, p.phone, p.department "
            "FROM users u LEFT JOIN user_profiles p ON p.user_id = u.id "
            "WHERE u.username = %s"
        )
        return self._query(sql, (username,), fetch="one")

    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        sql = (
            "SELECT u.*, p.full_name, p.email, p.phone, p.department "
            "FROM users u LEFT JOIN user_profiles p ON p.user_id = u.id "
            "WHERE u.id = %s"
        )
        return self._query(sql, (user_id,), fetch="one")

    def list_users(self) -> List[Dict[str, Any]]:
        sql = (
            "SELECT u.id, u.username, u.role, u.is_active, u.created_ts, "
            "p.full_name, p.email, p.phone, p.department "
            "FROM users u LEFT JOIN user_profiles p ON p.user_id = u.id "
            "ORDER BY u.id ASC"
        )
        return self._query(sql, fetch="all")

    def create_user(
        self,
        username: str,
        password_hash: str,
        role: str = "viewer",
        full_name: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        department: Optional[str] = None,
        is_active: int = 1,
    ) -> int:
        now = time.time()
        row = self._query(
            "INSERT INTO users (username, password_hash, role, is_active, created_ts, updated_ts) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (username, password_hash, role, is_active, now, now),
            fetch="one",
        )
        user_id = int(row["id"])
        self._query(
            "INSERT INTO user_profiles (user_id, full_name, email, phone, department, updated_ts) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (user_id, full_name, email, phone, department, now),
        )
        return user_id

    def update_user_role(self, user_id: int, role: str) -> bool:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET role = %s, updated_ts = %s WHERE id = %s",
                    (role, time.time(), user_id),
                )
                return cur.rowcount > 0

    def create_session(self, token: str, user_id: int, expires_ts: float):
        now = time.time()
        self._query(
            "INSERT INTO sessions (token, user_id, created_ts, expires_ts, last_seen_ts) "
            "VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (token) DO UPDATE SET user_id=EXCLUDED.user_id, expires_ts=EXCLUDED.expires_ts, last_seen_ts=EXCLUDED.last_seen_ts",
            (token, user_id, now, expires_ts, now),
        )

    def get_session(self, token: str) -> Optional[Dict[str, Any]]:
        row = self._query(
            "SELECT s.token, s.user_id, s.created_ts, s.expires_ts, s.last_seen_ts, "
            "u.username, u.role, u.is_active, p.full_name, p.email, p.phone, p.department "
            "FROM sessions s "
            "JOIN users u ON u.id = s.user_id "
            "LEFT JOIN user_profiles p ON p.user_id = u.id "
            "WHERE s.token = %s",
            (token,),
            fetch="one",
        )
        if row:
            self._query("UPDATE sessions SET last_seen_ts = %s WHERE token = %s", (time.time(), token))
        return row

    def delete_session(self, token: str):
        self._query("DELETE FROM sessions WHERE token = %s", (token,))

    def cleanup_expired_sessions(self):
        self._query("DELETE FROM sessions WHERE expires_ts < %s", (time.time(),))

    # ---------------------------- maintenance ----------------------------
    def cleanup_old_data(self, keep_days: int = 7):
        cutoff = time.time() - keep_days * 86400
        self._query("DELETE FROM sensor_readings WHERE ts < %s", (cutoff,))
        self._query("DELETE FROM device_events WHERE ts < %s", (cutoff,))
        self._query("DELETE FROM face_events WHERE ts < %s", (cutoff,))
        self._query("DELETE FROM rule_logs WHERE ts < %s", (cutoff,))
        self.cleanup_expired_sessions()
