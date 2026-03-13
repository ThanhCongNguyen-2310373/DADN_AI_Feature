# PROGRESS v4 – YoloHome Phase 4

> **Ngày cập nhật:** 11/03/2026  
> **Giai đoạn:** Phase 4 – Swagger UI, Rule Engine, OpenWeatherMap, Rate Limiting

---

## 1. Tổng quan thay đổi

Phase 4 tập trung vào **tối ưu hoá trải nghiệm developer (DX)**, **tự động hoá thông minh**, và **bảo mật nâng cao**:

| # | Tính năng | File chính | Trạng thái |
|---|-----------|-----------|-----------|
| 1 | **Swagger UI tối ưu** – Tags, Pydantic models, docstrings, Security config | `web_app/app.py` | ✅ Hoàn thành |
| 2 | **Rule Engine (If-Then Automation)** – Quy tắc tự động hóa ngưỡng cảm biến | `core/rule_engine.py` | ✅ Hoàn thành |
| 3 | **OpenWeatherMap Integration** – Thông tin thời tiết thực tế | `core/weather_service.py` | ✅ Hoàn thành |
| 4 | **Rate Limiting Login** – Brute-force protection | `web_app/app.py` | ✅ Hoàn thành |
| 5 | **SQLite Rule Tables** – `automation_rules` + `rule_logs` | `core/database.py` | ✅ Hoàn thành |
| 6 | **Voice Weather Query** – Giọng nói hỏi thời tiết | `ai/voice_control/voice_assistant.py` | ✅ Hoàn thành |

---

## 2. Kiến trúc tổng thể (Phase 4)

```
gateway/
├── core/
│   ├── database.py          ← UPDATED – thêm automation_rules, rule_logs tables + 6 methods
│   ├── rule_engine.py       ← NEW – Rule Engine Singleton (If-Then automation)
│   └── weather_service.py   ← NEW – OpenWeatherMap Singleton + 10-min cache
├── sensors/
│   └── sensor_reader.py     ← UPDATED – gọi RuleEngine.evaluate() sau mỗi lần đọc
├── ai/
│   └── voice_control/
│       └── voice_assistant.py ← UPDATED – detect weather keywords → WeatherService → RAG
├── web_app/
│   └── app.py               ← UPDATED – Swagger Tags, Rate Limiting, Rule CRUD API, /api/weather
├── config.py                ← UPDATED – OPENWEATHER_API_KEY, OPENWEATHER_CITY, OPENWEATHER_UNITS
└── .env                     ← UPDATED – OPENWEATHER_API_KEY + OPENWEATHER_CITY
```

---

## 3. Chi tiết từng tính năng

### 3.1 Swagger UI Tối ưu

**File:** `gateway/web_app/app.py`

#### Tags được phân loại

| Tag | Màu | Endpoints |
|-----|-----|---------|
| `IoT Control` | Xanh lá | `/api/sensors`, `/api/control` |
| `Statistics` | Cam | `/api/history`, `/api/energy` |
| `AI Features` | Tím | `/api/chat`, `/api/voice/ask`, `/api/face/*` |
| `Automation` | Xanh dương | `/api/rules/*`, `/api/weather` |
| `Security` | Đỏ | `/login`, `/logout` |

#### Pydantic Models nâng cấp

```python
class ControlCommand(BaseModel):
    device: Literal["led", "fan", "door", "pump"]  # Swagger dropdown
    value:  Literal[0, 1]
    source: str = Field(default="web", description="Nguồn lệnh")

class RuleCreate(BaseModel):                        # NEW
    name:             str   = Field(..., min_length=1, max_length=100)
    condition_field:  Literal["temp", "humi", "gas"]
    condition_op:     Literal[">", "<", ">=", "<=", "=="]
    condition_value:  float = Field(..., ge=-50, le=1000)
    action_device:    Literal["led", "fan", "door", "pump"]
    action_state:     Literal[0, 1]
    notify_telegram:  bool = False
    enabled:          bool = True

class EnrollRequest(BaseModel):
    person_name: str   = Field(..., min_length=2, max_length=50)
    num_samples: int   = Field(default=30, ge=10, le=100)
```

#### FastAPI Security Config

```python
app = FastAPI(
    title="YoloHome API",
    version="4.0.0",
    description="...",
    openapi_tags=TAGS_METADATA,
)
cookie_scheme = APIKeyCookie(name="session_token", auto_error=False)
```

---

### 3.2 Rate Limiting – Brute-force Protection

**File:** `gateway/web_app/app.py`

#### Cơ chế

```
Mỗi IP bị theo dõi trong sliding-window 5 phút.
Sau 5 lần sai → trả về 429 Too Many Requests.
Đăng nhập thành công → xoá lịch sử thất bại của IP đó.
```

#### Implementation

```python
_LOGIN_ATTEMPTS: Dict[str, List[float]] = defaultdict(list)
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECS  = 300  # 5 phút

def _check_rate_limit(ip: str) -> tuple[bool, int]:
    """Trả về (is_blocked, remaining_attempts)."""
    now     = time.time()
    window  = [t for t in _LOGIN_ATTEMPTS[ip] if now - t < _LOGIN_WINDOW_SECS]
    _LOGIN_ATTEMPTS[ip] = window
    if len(window) >= _LOGIN_MAX_ATTEMPTS:
        return True, 0
    return False, _LOGIN_MAX_ATTEMPTS - len(window)

def _record_failed_login(ip: str):
    _LOGIN_ATTEMPTS[ip].append(time.time())
```

#### Response khi bị block

```json
{
  "detail": "Quá nhiều lần đăng nhập sai. Thử lại sau 5 phút."
}
```
**HTTP status:** `429 Too Many Requests`

---

### 3.3 Rule Engine – Tự động hóa If-Then

**File:** `gateway/core/rule_engine.py`  
**Class:** `RuleEngine` (Singleton)

#### Schema quy tắc

| Trường | Kiểu | Mô tả |
|--------|------|-------|
| `id` | int | Primary key |
| `name` | str | Tên mô tả quy tắc |
| `condition_field` | `temp\|humi\|gas` | Trường cảm biến kiểm tra |
| `condition_op` | `>\|<\|>=\|<=\|==` | Toán tử so sánh |
| `condition_value` | float | Ngưỡng so sánh |
| `action_device` | `led\|fan\|pump\|door` | Thiết bị kích hoạt |
| `action_state` | `0\|1` | Trạng thái: OFF/ON |
| `notify_telegram` | bool | Có gửi Telegram không |
| `enabled` | bool | Quy tắc đang bật/tắt |

#### Luồng xử lý

```
SensorReader._publish_loop()
  └── RuleEngine.evaluate({"temp": t, "humi": h, "gas": g})
        ├── Reload rules mỗi 30s (DB cache)
        ├── For each enabled rule:
        │     ├── _check_rule(rule, sensor_data)
        │     │     └── operator.gt/lt/ge/le/eq(current_val, threshold)
        │     ├── If TRUE + cooldown(60s) expired:
        │     │     └── _fire_action(rule)
        │     │           ├── MQTT publish(feed, ON/OFF)
        │     │           ├── db.insert_device_event(source="auto")
        │     │           ├── db.insert_rule_log(rule_id, field, val)
        │     │           └── TelegramNotifier.send_text(msg)  ← nếu notify=True
        │     └── Update cooldown timestamp
        └── Return list of fired rule names
```

#### Cooldown

Mỗi rule có cooldown riêng `_last_fired[rule_id] = timestamp`.  
Mặc định **60 giây** (`_RULE_COOLDOWN_SECS = 60`) để tránh spam.

#### Ví dụ quy tắc hữu ích

| Tên | Điều kiện | Hành động |
|-----|----------|----------|
| Bật quạt khi nóng | `temp > 35` | Bật fan |
| Tắt đèn khi mát | `temp <= 25` | Tắt led |
| Cảnh báo gas | `gas >= 300` | Bật pump + Telegram |
| Ẩm cao bật quạt | `humi > 80` | Bật fan |

---

### 3.4 OpenWeatherMap Integration

**File:** `gateway/core/weather_service.py`  
**Class:** `WeatherService` (Singleton)

#### API endpoint gọi

```
GET https://api.openweathermap.org/data/2.5/weather
    ?q=Ho Chi Minh City
    &appid=<OPENWEATHER_API_KEY>
    &units=metric
    &lang=vi
```

#### Response chuẩn hoá

```python
{
    "success":        True,
    "city":           "Ho Chi Minh City",
    "country":        "VN",
    "temp":           32.5,       # °C
    "feels_like":     36.2,       # °C
    "temp_min":       30.1,
    "temp_max":       34.7,
    "humidity":       78,          # %
    "pressure":       1009,        # hPa
    "description":    "mưa rào",  # tiếng Việt
    "description_en": "Rain",
    "icon":           "10d",
    "icon_url":       "https://openweathermap.org/img/wn/10d@2x.png",
    "wind_speed":     4.5,         # m/s
    "wind_deg":       180,
    "visibility":     8000,        # m
    "clouds":         75,          # %
    "sunrise":        1741123456,
    "sunset":         1741168012,
    "timestamp":      1741150000.0,
    "cached":         False
}
```

#### Cache strategy

```
TTL = 10 phút
Nếu fetch lỗi → trả về cache cũ (dù hết hạn) với flag stale=True
Nếu không có cache → trả về error response (success=False)
```

#### Web API

```
GET /api/weather   → WeatherService.get_instance().get_current_weather()
```

---

### 3.5 Voice Weather Query

**File:** `gateway/ai/voice_control/voice_assistant.py`

#### Từ khoá phát hiện

```python
_WEATHER_KEYWORDS = [
    "thời tiết", "thoi tiet", "nhiệt độ ngoài", "trời",
    "mưa", "nắng", "tuyết", "gió", "bão", "ngoài trời",
    "hôm nay trời", "weather",
]
```

#### Luồng xử lý

```
User nói: "Hôm nay trời có mưa không?"
  └── _process_command()
        ├── Phát hiện keyword "trời" / "mưa"
        ├── Gọi _answer_weather(question)
        │     ├── WeatherService.get_instance().get_current_weather()
        │     ├── Nếu RAG sẵn: inject weather data + hỏi Gemini
        │     └── Nếu không có RAG: tổng hợp câu trả lời text trực tiếp
        └── TTS phản hồi
```

#### Fallback chain

1. `WeatherService` khả dụng + RAG sẵn → **Gemini với weather context**
2. `WeatherService` khả dụng + RAG không có → **text response trực tiếp**
3. `WeatherService` không khả dụng → **fallback sang RAG thuần**

---

### 3.6 Database – Bảng mới Phase 4

**File:** `gateway/core/database.py`

#### Bảng `automation_rules`

```sql
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
```

#### Bảng `rule_logs`

```sql
CREATE TABLE IF NOT EXISTS rule_logs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id  INTEGER NOT NULL,
    ts       REAL    NOT NULL,
    field    TEXT,
    value    REAL,
    created  TEXT    DEFAULT (datetime('now','localtime'))
);
```

#### Methods mới

| Method | Mô tả | Returns |
|--------|-------|---------|
| `get_rules(enabled_only=False)` | Lấy danh sách rules | `List[Dict]` |
| `insert_rule(...)` | Tạo rule mới | `int` (rule_id) |
| `delete_rule(rule_id)` | Xoá rule | `bool` |
| `toggle_rule(rule_id)` | Đổi trạng thái | `int` (new state) |
| `insert_rule_log(rule_id, field, value)` | Ghi log kích hoạt | — |
| `get_rule_logs(rule_id, hours, limit)` | Lấy log | `List[Dict]` |

---

## 4. Rule Engine API

### CRUD Endpoints

| Endpoint | Method | Mô tả |
|---------|--------|-------|
| `/api/rules` | GET | Lấy danh sách tất cả rules |
| `/api/rules` | POST | Tạo rule mới (body: `RuleCreate`) |
| `/api/rules/{id}` | DELETE | Xoá rule theo ID |
| `/api/rules/{id}/toggle` | PATCH | Bật/tắt rule |

### Ví dụ tạo rule via Swagger UI

**POST `/api/rules`** – body:

```json
{
  "name": "Bật quạt khi nhiệt độ > 35°C",
  "condition_field": "temp",
  "condition_op": ">",
  "condition_value": 35,
  "action_device": "fan",
  "action_state": 1,
  "notify_telegram": true,
  "enabled": true
}
```

**Response:**

```json
{
  "id": 1,
  "message": "Tạo quy tắc thành công",
  "name": "Bật quạt khi nhiệt độ > 35°C"
}
```

---

## 5. Cấu hình Phase 4

### `.env` thêm

```env
OPENWEATHER_API_KEY
OPENWEATHER_CITY=Ho Chi Minh City
```

### `config.py` thêm

```python
OPENWEATHER_API_KEY  = os.getenv("OPENWEATHER_API_KEY", "")
OPENWEATHER_CITY     = os.getenv("OPENWEATHER_CITY", "Ho Chi Minh City")
OPENWEATHER_UNITS    = "metric"
```

---

## 6. Thư viện sử dụng (Phase 4)

| Package | Mục đích | Ghi chú |
|---------|---------|--------|
| `requests` | Gọi OpenWeatherMap API | Đã có từ Phase 3 |
| `operator` | So sánh động trong Rule Engine | stdlib – không cài |

> **Không thêm package mới** – Phase 4 tận dụng toàn bộ dependencies đã có.

---

## 7. API Endpoint tổng hợp (Phase 4 – đầy đủ)

| Endpoint | Method | Auth | Tag | Mô tả |
|---------|--------|------|-----|-------|
| `/` | GET | ✅ | — | Dashboard chính |
| `/login` | GET/POST | ❌ | Security | Đăng nhập (rate-limited) |
| `/logout` | GET | ❌ | Security | Đăng xuất |
| `/members` | GET | ✅ | AI Features | Quản lý khuôn mặt |
| `/video_feed` | GET | ✅ | — | MJPEG camera stream |
| `/ws/sensors` | WS | ✅ | — | WebSocket sensor realtime |
| `/api/sensors` | GET | ✅ | IoT Control | Sensor hiện tại |
| `/api/control` | POST | ✅ | IoT Control | Điều khiển thiết bị |
| `/api/history` | GET | ✅ | Statistics | Lịch sử cảm biến |
| `/api/energy` | GET | ✅ | Statistics | Báo cáo năng lượng |
| `/api/chat` | GET | ✅ | AI Features | Lịch sử hội thoại |
| `/api/voice/ask` | POST | ✅ | AI Features | Hỏi qua API (không cần mic) |
| `/api/face/log` | GET | ✅ | AI Features | Nhật ký nhận diện |
| `/api/face/members` | GET | ✅ | AI Features | Danh sách thành viên |
| `/api/face/enroll` | POST | ✅ | AI Features | Đăng ký khuôn mặt |
| `/api/face/train` | POST | ✅ | AI Features | Train lại model |
| `/api/weather` | GET | ✅ | Automation | Thời tiết hiện tại (OpenWeatherMap) |
| `/api/rules` | GET | ✅ | Automation | Danh sách rules |
| `/api/rules` | POST | ✅ | Automation | Tạo rule mới |
| `/api/rules/{id}` | DELETE | ✅ | Automation | Xoá rule |
| `/api/rules/{id}/toggle` | PATCH | ✅ | Automation | Bật/tắt rule |

---

## 8. Tóm tắt tiến độ các Phase

| Phase | Tính năng | Trạng thái |
|-------|-----------|-----------|
| Phase 1 | MQTT + Adafruit + Sensor Reading | ✅ |
| Phase 1 | Face Recognition (LBPH) | ✅ |
| Phase 1 | Voice Control (Gemini + RAG) | ✅ |
| Phase 2 | Web Dashboard (FastAPI + WS) | ✅ |
| Phase 2 | Camera MJPEG Stream | ✅ |
| Phase 2 | Real-time Sensor via WebSocket | ✅ |
| Phase 3 | SQLite Persistent Storage | ✅ |
| Phase 3 | Historical Chart (Chart.js) | ✅ |
| Phase 3 | Energy Report | ✅ |
| Phase 3 | Telegram Bot Alerts | ✅ |
| Phase 3 | Web Authentication | ✅ |
| Phase 3 | Web Face Enrollment | ✅ |
| Phase 3 | Multi-turn RAG Conversation | ✅ |
| Phase 4 | Swagger UI Optimization | ✅ |
| Phase 4 | Rate Limiting (Brute-force protection) | ✅ |
| Phase 4 | Rule Engine (If-Then Automation) | ✅ |
| Phase 4 | OpenWeatherMap Integration | ✅ |
| Phase 4 | Voice Weather Query | ✅ |
| Phase 4 | Rule CRUD API + DB Tables | ✅ |
