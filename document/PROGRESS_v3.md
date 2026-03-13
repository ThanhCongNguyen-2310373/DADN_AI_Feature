# PROGRESS v3 – YoloHome Phase 3

> **Ngày cập nhật:** 11/03/2026  
> **Giai đoạn:** Phase 3 – Persistent Storage, Notification, Auth, Face Enrollment Web, Multi-turn RAG

---

## 1. Tổng quan thay đổi

Phase 3 bổ sung 4 nhóm tính năng lớn vào hệ thống YoloHome:

| # | Tính năng | Trạng thái |
|---|-----------|-----------|
| 1 | **SQLite + Biểu đồ lịch sử (Chart.js) + Báo cáo năng lượng** | ✅ Hoàn thành |
| 2 | **Telegram Bot – cảnh báo tự động kèm ảnh** | ✅ Hoàn thành |
| 3 | **Web Authentication – đăng nhập Dashboard** | ✅ Hoàn thành |
| 4 | **Web Face Enrollment – đăng ký khuôn mặt qua UI** | ✅ Hoàn thành |
| 5 | **Multi-turn Voice Assistant với RAG** | ✅ Hoàn thành |

---

## 2. Kiến trúc tổng thể (Phase 3)

```
gateway/
├── core/
│   ├── database.py          ← NEW – SQLite Singleton
│   └── telegram_notifier.py ← NEW – Telegram Bot queue worker
├── sensors/
│   └── sensor_reader.py     ← UPDATED – ghi DB + gửi Telegram
├── ai/
│   ├── face_recognition/
│   │   └── face_recognizer.py ← UPDATED – ghi DB + gửi Telegram ảnh
│   └── voice_control/
│       └── voice_assistant.py ← UPDATED – multi-turn history
├── web_app/
│   ├── app.py               ← UPDATED – Auth, History/Energy API, Face enroll
│   ├── templates/
│   │   ├── index.html       ← UPDATED – Chart.js, Energy section, nav
│   │   ├── login.html       ← NEW
│   │   └── members.html     ← NEW – Face enrollment UI
│   └── static/
│       ├── js/dashboard.js  ← UPDATED – initSensorChart(), pollEnergy()
│       └── css/style.css    ← UPDATED – .nav-link, .energy-grid
├── data/
│   └── yolohome.db          ← AUTO-CREATED khi chạy lần đầu
├── config.py                ← UPDATED – Telegram, Auth, DB config
├── .env                     ← UPDATED – Telegram credentials
└── requirements.txt         ← UPDATED – thêm fastapi, requests, ...
```

---

## 3. Chi tiết từng tính năng

### 3.1 SQLite – Lưu trữ dữ liệu lịch sử

**File:** `gateway/core/database.py`  
**Class:** `DatabaseSingleton` (Thread-safe Singleton)

#### Tables

| Bảng | Mô tả | Các cột quan trọng |
|------|-------|-------------------|
| `sensor_readings` | Dữ liệu cảm biến 5 giây/lần | `ts`, `temp`, `humi`, `gas` |
| `device_events` | Lịch sử bật/tắt thiết bị | `ts`, `device`, `state`, `source` |
| `face_events` | Nhận diện khuôn mặt | `ts`, `event_type`, `person`, `confidence`, `img_path` |

#### Cấu hình
- **WAL mode** + `synchronous=NORMAL` → tối ưu cho ghi đồng thời
- Tự động tạo thư mục `data/` và DB file nếu chưa có
- `cleanup_old_data(keep_days=7)` dọn dẹp dữ liệu cũ

#### API liên quan
```
GET /api/history?hours=24   → lịch sử cảm biến (tối đa 500 điểm)
GET /api/energy?hours=24    → báo cáo năng lượng
```

**Tính toán năng lượng (công suất định danh):**
| Thiết bị | Công suất |
|----------|----------|
| Đèn LED  | 6 W      |
| Quạt     | 40 W     |
| Máy bơm  | 30 W     |
| Cửa      | 5 W      |

---

### 3.2 Chart.js – Biểu đồ lịch sử

**File cập nhật:**
- `index.html` – thêm `<canvas id="chart-sensor">` + Chart.js CDN
- `dashboard.js` – hàm `initSensorChart()` vẽ line chart nhiệt độ + độ ẩm 24h
- `style.css` – style cho phần chart card

**Thư viện:** Chart.js 4.4.3 via CDN  
**Refresh:** Tự động mỗi 5 phút

---

### 3.3 Telegram Bot – Cảnh báo tự động

**File:** `gateway/core/telegram_notifier.py`  
**Class:** `TelegramNotifier` (Singleton + queue worker thread)

#### Loại cảnh báo
| Method | Trigger | Nội dung |
|--------|---------|---------|
| `temp_alert(temp)` | Nhiệt độ > ngưỡng (config) | Text: ⚠️ nhiệt độ cao |
| `gas_alert(ppm)` | Gas > ngưỡng (config) | Text: 🚨 khí gas nguy hiểm |
| `stranger_alert(secs, img_path)` | Khuôn mặt lạ | Photo + caption |

#### Cấu hình (`.env`)
```
TELEGRAM_BOT_TOKEN=7954908785:AAEg-PADmResbfKaRhX2wcnBtN-niTPtKC4
TELEGRAM_CHAT_ID=6190195686
```

**Implementation:** Dùng `requests` HTTP thuần (không phụ thuộc `python-telegram-bot`) – tránh conflict asyncio với Python 3.13.

---

### 3.4 Web Authentication

**File:** `gateway/web_app/app.py`

#### Cơ chế
- **Session-based:** Token ngẫu nhiên 32 bytes (hex) lưu trong in-memory dict `_SESSIONS`
- **Cookie:** `session_token` (httponly)
- **TTL:** 8 giờ
- **Password:** SHA-256 hash (so sánh constant-time)

#### Routes
| Route | Mô tả |
|-------|-------|
| `GET /login` | Trang đăng nhập |
| `POST /login` | Xử lý form → set cookie |
| `GET /logout` | Xoá cookie + session |

#### FastAPI Dependency
```python
_=Depends(require_auth)   # Bảo vệ tất cả route /api/* và /
```

**Credentials mặc định:** `admin` / `yolohome2025` (thay đổi trong `.env`)

---

### 3.5 Web Face Enrollment

**File:** `gateway/web_app/app.py` + `gateway/web_app/templates/members.html`

#### API
| Endpoint | Method | Mô tả |
|---------|--------|-------|
| `/members` | GET | Trang quản lý thành viên |
| `/api/face/members` | GET | Danh sách thành viên đã đăng ký |
| `/api/face/enroll` | POST | Bắt đầu chụp ảnh đăng ký |
| `/api/face/train` | POST | Retrain LBPH model |

**Request body `/api/face/enroll`:**
```json
{ "person_name": "Nguyen Van A", "num_samples": 30 }
```

**Quy trình:**
1. Mở `/members` → nhập tên + số mẫu
2. Click "Bắt đầu chụp" → chạy trong background thread
3. Sau khi thu thập xong → click "Train Model"
4. Danh sách cập nhật tự động

---

### 3.6 Multi-turn Voice Assistant

**File:** `gateway/ai/voice_control/voice_assistant.py`

**Cơ chế:**
- `VoiceAssistant` lưu `self.chat_history` (list các dict `{role, text}`)
- Trước mỗi câu hỏi mới, lấy **6 lượt cuối** làm context
- `GeminiRAGAssistant.ask(question, history=None)` nhận history, xây `history_str` và augment vào câu hỏi:

```python
augmented_question = (
    f"Lịch sử hội thoại trước đó:\n{history_str}\n\n"
    f"Câu hỏi mới: {question}"
)
```

**Ưu điểm:** Không cần thay đổi RetrievalQA chain, tương thích hoàn toàn với Gemini 1.5 Flash.

---

## 4. Thư viện sử dụng (Phase 3)

| Package | Version | Mục đích |
|---------|---------|---------|
| `fastapi` | 0.135.1 | Web framework |
| `uvicorn[standard]` | 0.41.0 | ASGI server |
| `jinja2` | 3.1.6 | Template engine |
| `python-multipart` | 0.0.22 | Form parsing |
| `requests` | 2.32.5 | Telegram HTTP API |
| `sqlite3` | stdlib | Database (không cần cài) |
| `Chart.js` | 4.4.3 | CDN – không cài pip |

---

## 5. API Endpoint tổng hợp (toàn bộ hệ thống)

| Endpoint | Method | Auth | Mô tả |
|---------|--------|------|-------|
| `/` | GET | ✅ | Dashboard chính |
| `/login` | GET/POST | ❌ | Đăng nhập |
| `/logout` | GET | ❌ | Đăng xuất |
| `/members` | GET | ✅ | Quản lý khuôn mặt |
| `/video_feed` | GET | ✅ | MJPEG camera stream |
| `/ws/sensors` | WS | ✅ | WebSocket sensor realtime |
| `/api/sensors` | GET | ✅ | Sensor hiện tại |
| `/api/control` | POST | ✅ | Điều khiển thiết bị |
| `/api/chat` | GET | ✅ | Lịch sử hội thoại |
| `/api/face/log` | GET | ✅ | Nhật ký nhận diện |
| `/api/history` | GET | ✅ | Lịch sử cảm biến 24h |
| `/api/energy` | GET | ✅ | Báo cáo năng lượng |
| `/api/face/members` | GET | ✅ | Danh sách thành viên |
| `/api/face/enroll` | POST | ✅ | Đăng ký khuôn mặt |
| `/api/face/train` | POST | ✅ | Train lại model |

---

## 6. Những thay đổi đáng chú ý

### `face_recognizer.py`
- `_save_log_image()` **bây giờ trả về `img_path`** (trước đây trả về None) để Telegram có thể gửi ảnh

### `sensor_reader.py`
- `get_latest_data()` trả về dict chuẩn hoá với key: `temperature`, `humidity`, `gas`, `led`, `fan`, `door`, `pump`, `timestamp`

### `app.py`
- `/api/control` bây giờ **ghi event vào DB** với source=`"web"`
- Tất cả route đã được bảo vệ bằng `require_auth`

---

## 7. Tóm tắt tiến độ các Phase

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
