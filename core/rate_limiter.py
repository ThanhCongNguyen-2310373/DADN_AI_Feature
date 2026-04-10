"""
core/rate_limiter.py - Persistent/Memory rate limiting service (Phase 5)
"""

import time
import uuid
import threading
from typing import Dict, List, Tuple

import config


class InMemoryRateLimiter:
    def __init__(self, max_attempts: int, window_seconds: int):
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._attempts: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def _cleanup(self, ip: str):
        now = time.time()
        attempts = self._attempts.get(ip, [])
        self._attempts[ip] = [t for t in attempts if now - t < self._window_seconds]

    def check(self, ip: str) -> Tuple[bool, int]:
        with self._lock:
            self._cleanup(ip)
            count = len(self._attempts.get(ip, []))
            blocked = count >= self._max_attempts
            remaining = max(0, self._max_attempts - count)
            return blocked, remaining

    def record_failure(self, ip: str):
        with self._lock:
            self._cleanup(ip)
            self._attempts.setdefault(ip, []).append(time.time())

    def reset(self, ip: str):
        with self._lock:
            self._attempts.pop(ip, None)


class RedisRateLimiter:
    def __init__(self, redis_url: str, max_attempts: int, window_seconds: int):
        import redis

        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._redis = redis.Redis.from_url(redis_url, decode_responses=True)

    def _key(self, ip: str) -> str:
        return f"ratelimit:login:{ip}"

    def _cleanup(self, ip: str):
        now = time.time()
        min_score = 0
        max_score = now - self._window_seconds
        self._redis.zremrangebyscore(self._key(ip), min_score, max_score)

    def check(self, ip: str) -> Tuple[bool, int]:
        self._cleanup(ip)
        count = self._redis.zcard(self._key(ip))
        blocked = count >= self._max_attempts
        remaining = max(0, self._max_attempts - int(count))
        return blocked, remaining

    def record_failure(self, ip: str):
        now = time.time()
        member = f"{now}:{uuid.uuid4().hex}"
        key = self._key(ip)
        pipe = self._redis.pipeline()
        pipe.zadd(key, {member: now})
        pipe.expire(key, self._window_seconds + 60)
        pipe.execute()

    def reset(self, ip: str):
        self._redis.delete(self._key(ip))


_rate_limiter_instance = None


def get_rate_limiter():
    global _rate_limiter_instance
    if _rate_limiter_instance is not None:
        return _rate_limiter_instance

    backend = getattr(config, "RATE_LIMIT_BACKEND", "memory").lower()
    max_attempts = int(getattr(config, "RATE_LIMIT_MAX_ATTEMPTS", 5))
    window_seconds = int(getattr(config, "RATE_LIMIT_WINDOW_SECS", 300))

    if backend == "redis":
        redis_url = getattr(config, "REDIS_URL", "redis://localhost:6379/0")
        try:
            _rate_limiter_instance = RedisRateLimiter(
                redis_url=redis_url,
                max_attempts=max_attempts,
                window_seconds=window_seconds,
            )
            return _rate_limiter_instance
        except Exception:
            # Fallback an toàn
            _rate_limiter_instance = InMemoryRateLimiter(max_attempts, window_seconds)
            return _rate_limiter_instance

    _rate_limiter_instance = InMemoryRateLimiter(max_attempts, window_seconds)
    return _rate_limiter_instance
