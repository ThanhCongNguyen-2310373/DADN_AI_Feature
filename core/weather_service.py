"""
core/weather_service.py - Tích hợp OpenWeatherMap API cho YoloHome

Cung cấp thông tin thời tiết thực tế cho Voice Assistant và Web Dashboard.
Kết quả được cache 10 phút để tránh gọi API quá nhiều.

API endpoint:
    GET /api/weather  → WeatherService.get_instance().get_current_weather()

Cấu hình .env:
    OPENWEATHER_API_KEY=<key>
    OPENWEATHER_CITY=Ho Chi Minh City   (tuỳ chọn, mặc định là TP.HCM)
"""

import time
import logging
import threading
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False
    logger.warning("[Weather] 'requests' chưa được cài đặt → dịch vụ thời tiết bị tắt.")


class WeatherService:
    """
    Singleton lấy thông tin thời tiết từ OpenWeatherMap API.

    Cache kết quả 10 phút để tiết kiệm request.
    Nếu API không khả dụng, trả về dữ liệu lỗi thay vì raise exception.

    Dùng:
        ws = WeatherService.get_instance()
        data = ws.get_current_weather()
        # → {"city": "Ho Chi Minh City", "temp": 32.5, ...}
    """

    _instance: "WeatherService" = None
    _lock = threading.Lock()

    _CACHE_TTL  = 600   # Giây – cache 10 phút
    _API_URL    = "https://api.openweathermap.org/data/2.5/weather"

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._init()
                    cls._instance = inst
        return cls._instance

    @classmethod
    def get_instance(cls) -> "WeatherService":
        return cls()

    # ─────────────────────────── Init ───────────────────────────────────
    def _init(self):
        # Import config tại đây để tránh circular import khi module chưa load
        try:
            import sys, os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            import config as cfg
            self._api_key  = getattr(cfg, "OPENWEATHER_API_KEY", "")
            self._city     = getattr(cfg, "OPENWEATHER_CITY",    "Ho Chi Minh City")
            self._units    = getattr(cfg, "OPENWEATHER_UNITS",   "metric")
        except Exception:
            self._api_key  = ""
            self._city     = "Ho Chi Minh City"
            self._units    = "metric"

        self._cache_ts:   float            = 0.0
        self._cache_data: Optional[Dict]   = None
        self._data_lock = threading.Lock()

        if not self._api_key:
            logger.warning("[Weather] OPENWEATHER_API_KEY chưa cấu hình → thời tiết bị tắt.")
        else:
            logger.info(f"[Weather] ✅ WeatherService sẵn sàng (city={self._city})")

    # ─────────────────────────── Public API ─────────────────────────────
    def get_current_weather(self) -> Dict[str, Any]:
        """
        Lấy thông tin thời tiết hiện tại.

        Returns:
            dict với các key:
              - success     (bool)   : True nếu lấy được dữ liệu
              - city        (str)    : Tên thành phố
              - country     (str)    : Mã quốc gia (VD: "VN")
              - temp        (float)  : Nhiệt độ hiện tại (°C)
              - feels_like  (float)  : Nhiệt độ cảm giác (°C)
              - temp_min    (float)  : Nhiệt độ thấp nhất trong ngày
              - temp_max    (float)  : Nhiệt độ cao nhất trong ngày
              - humidity    (int)    : Độ ẩm (%)
              - pressure    (int)    : Áp suất khí quyển (hPa)
              - description (str)    : Mô tả thời tiết (VD: "mưa nhỏ")
              - description_en (str) : Mô tả tiếng Anh (VD: "light rain")
              - icon        (str)    : Mã icon OpenWeatherMap (VD: "10d")
              - icon_url    (str)    : URL ảnh icon thời tiết
              - wind_speed  (float)  : Tốc độ gió (m/s)
              - wind_deg    (int)    : Hướng gió (độ)
              - visibility  (int)    : Tầm nhìn (m)
              - clouds      (int)    : Độ phủ mây (%)
              - sunrise     (int)    : Unix timestamp bình minh
              - sunset      (int)    : Unix timestamp hoàng hôn
              - timestamp   (float)  : Unix timestamp lần fetch
              - cached      (bool)   : True nếu dữ liệu từ cache
              - error       (str)    : Thông báo lỗi (nếu success=False)
        """
        with self._data_lock:
            now = time.time()
            # Trả về cache nếu còn hợp lệ
            if self._cache_data and (now - self._cache_ts) < self._CACHE_TTL:
                result = dict(self._cache_data)
                result["cached"] = True
                return result

        # Gọi API mới
        data = self._fetch()
        if data:
            with self._data_lock:
                self._cache_ts   = time.time()
                self._cache_data = data
            data["cached"] = False
            return data

        # Fallback: trả về cache cũ dù hết hạn, tốt hơn trả về lỗi
        with self._data_lock:
            if self._cache_data:
                result = dict(self._cache_data)
                result["cached"] = True
                result["stale"]  = True
                return result

        return self._error_response("Không thể lấy dữ liệu thời tiết.")

    def invalidate_cache(self):
        """Xoá cache để buộc fetch mới ở lần gọi tiếp theo."""
        with self._data_lock:
            self._cache_ts   = 0.0
            self._cache_data = None

    def is_available(self) -> bool:
        """Trả về True nếu API key đã được cấu hình."""
        return bool(self._api_key) and _HAS_REQUESTS

    # ─────────────────────────── Private ────────────────────────────────
    def _fetch(self) -> Optional[Dict[str, Any]]:
        """Gọi OpenWeatherMap API, trả về dict đã xử lý hoặc None nếu lỗi."""
        if not self.is_available():
            return None

        params = {
            "q":     self._city,
            "appid": self._api_key,
            "units": self._units,
            "lang":  "vi",          # Mô tả trả về tiếng Việt
        }

        try:
            resp = requests.get(self._API_URL, params=params, timeout=8)
            if resp.status_code == 401:
                logger.error("[Weather] ❌ API key không hợp lệ (401 Unauthorized).")
                return None
            if resp.status_code == 404:
                logger.error(f"[Weather] ❌ Không tìm thấy thành phố: {self._city}")
                return None
            resp.raise_for_status()
            raw = resp.json()
            return self._parse(raw)
        except requests.exceptions.Timeout:
            logger.warning("[Weather] ⏱ API request timeout.")
        except requests.exceptions.ConnectionError:
            logger.warning("[Weather] 🔌 Không có kết nối mạng.")
        except Exception as e:
            logger.warning(f"[Weather] ⚠️ Lỗi không xác định: {e}")
        return None

    @staticmethod
    def _parse(raw: dict) -> Dict[str, Any]:
        """Chuyển raw JSON OpenWeatherMap → dict chuẩn hoá."""
        main    = raw.get("main", {})
        wind    = raw.get("wind", {})
        weather = raw.get("weather", [{}])[0]
        sys_d   = raw.get("sys", {})
        clouds  = raw.get("clouds", {})

        icon_code = weather.get("icon", "01d")
        return {
            "success":        True,
            "city":           raw.get("name", ""),
            "country":        sys_d.get("country", ""),
            "temp":           round(main.get("temp", 0), 1),
            "feels_like":     round(main.get("feels_like", 0), 1),
            "temp_min":       round(main.get("temp_min", 0), 1),
            "temp_max":       round(main.get("temp_max", 0), 1),
            "humidity":       main.get("humidity", 0),
            "pressure":       main.get("pressure", 0),
            "description":    weather.get("description", ""),
            "description_en": weather.get("main", ""),
            "icon":           icon_code,
            "icon_url":       f"https://openweathermap.org/img/wn/{icon_code}@2x.png",
            "wind_speed":     round(wind.get("speed", 0), 1),
            "wind_deg":       wind.get("deg", 0),
            "visibility":     raw.get("visibility", 0),
            "clouds":         clouds.get("all", 0),
            "sunrise":        sys_d.get("sunrise", 0),
            "sunset":         sys_d.get("sunset", 0),
            "timestamp":      time.time(),
        }

    @staticmethod
    def _error_response(msg: str) -> Dict[str, Any]:
        return {
            "success": False,
            "error":   msg,
            "city":    "",
            "temp":    None,
            "humidity": None,
            "description": "",
            "timestamp": time.time(),
        }
