"""
core/auth_service.py - Auth + Session + RBAC service (Phase 5)
"""

import hmac
import hashlib
import secrets
import time
import threading
import logging
from typing import Optional, Dict, Any

import config
from core.database import DatabaseSingleton

logger = logging.getLogger(__name__)


class AuthService:
    _instance: "AuthService" = None
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
    def get_instance(cls) -> "AuthService":
        return cls()

    def _init(self):
        self._db = DatabaseSingleton.get_instance()
        self._session_ttl = getattr(config, "WEB_SESSION_TTL", 8 * 3600)

    @staticmethod
    def hash_password(password: str) -> str:
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        current_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(current_hash, password_hash)

    def bootstrap_default_admin(self):
        username = getattr(config, "WEB_USERNAME", "admin")
        password = getattr(config, "WEB_PASSWORD", "yolohome2025")
        full_name = "System Admin"

        user = self._db.get_user_by_username(username)
        if user:
            return

        self._db.create_user(
            username=username,
            password_hash=self.hash_password(password),
            role="admin",
            full_name=full_name,
            is_active=1,
        )
        logger.info("[Auth] Default admin user bootstrapped")

    def authenticate(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        user = self._db.get_user_by_username(username)
        if not user:
            return None
        if int(user.get("is_active", 0)) != 1:
            return None
        if not self.verify_password(password, str(user.get("password_hash", ""))):
            return None
        return user

    def create_session(self, user_id: int) -> str:
        token = secrets.token_hex(32)
        expires_ts = time.time() + self._session_ttl
        self._db.create_session(token, user_id, expires_ts)
        return token

    def get_session_user(self, token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        data = self._db.get_session(token)
        if not data:
            return None
        if time.time() > float(data.get("expires_ts", 0)):
            self._db.delete_session(token)
            return None
        if int(data.get("is_active", 0)) != 1:
            return None
        return data

    def delete_session(self, token: str):
        if token:
            self._db.delete_session(token)

    def create_user(
        self,
        username: str,
        password: str,
        role: str,
        full_name: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        department: Optional[str] = None,
    ) -> int:
        return self._db.create_user(
            username=username,
            password_hash=self.hash_password(password),
            role=role,
            full_name=full_name,
            email=email,
            phone=phone,
            department=department,
            is_active=1,
        )

    def list_users(self):
        return self._db.list_users()

    def update_user_role(self, user_id: int, role: str) -> bool:
        return self._db.update_user_role(user_id, role)
