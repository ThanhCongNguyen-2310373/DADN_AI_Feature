# Hướng dẫn chạy và test YoloHome (Phase 3 + Phase 4)

> Hướng dẫn này bao gồm toàn bộ các bước từ cài đặt môi trường đến kiểm thử từng tính năng của hệ thống YoloHome Phase 3 và Phase 4.

---

## Mục lục

1. [Yêu cầu hệ thống](#1-yêu-cầu-hệ-thống)
2. [Cấu hình môi trường (.env)](#2-cấu-hình-môi-trường-env)
3. [Cài đặt dependencies](#3-cài-đặt-dependencies)
4. [Đăng ký khuôn mặt (Offline)](#4-đăng-ký-khuôn-mặt-offline)
5. [Khởi động hệ thống](#5-khởi-động-hệ-thống)
6. [Truy cập Web Dashboard](#6-truy-cập-web-dashboard)
7. [Test từng tính năng](#7-test-từng-tính-năng)
8. [Phase 4 – Tính năng mới](#8-phase-4--tính-năng-mới)
9. [Xử lý lỗi thường gặp](#9-xử-lý-lỗi-thường-gặp)

---

## 1. Yêu cầu hệ thống

| Yêu cầu | Chi tiết |
|---------|---------|
| **Python** | 3.10 – 3.13 (khuyến nghị 3.13.7) |
| **Camera** | Webcam USB hoặc camera laptop |
| **Micro** | Microphone (cho Voice Control) |
| **Adafruit** | Tài khoản Adafruit IO (đã có feeds: `led`, `fan`, `pump`, `door`, `temp`, `humi`, `gas`) |
| **Telegram** | Bot đã tạo qua @BotFather, đã lấy token + chat_id |
| **OS** | Windows 10/11 (đã test) hoặc Ubuntu 22.04 |

---

## 2. Cấu hình môi trường (.env)

Mở file `gateway/.env` và điền đầy đủ thông tin:

```dotenv
# Adafruit IO
ADAFRUIT_USERNAME=<your_adafruit_username>
ADAFRUIT_AIO_KEY=<your_adafruit_aio_key>

# Gemini AI
GEMINI_API_KEY=<your_gemini_api_key>

# Telegram Bot
TELEGRAM_BOT_TOKEN=<your_bot_token>
TELEGRAM_CHAT_ID=<your_chat_id>

# Web Dashboard
WEB_USERNAME=admin
WEB_PASSWORD=yolohome2025
WEB_PORT=8000
```

### Lấy Telegram Chat ID
1. Gửi tin nhắn `/start` cho bot của bạn
2. Truy cập `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Tìm trường `"chat": {"id": ...}` trong kết quả JSON

---

## 3. Cài đặt dependencies

Mở PowerShell tại thư mục `D:\HK252\DADN`:

```powershell
# Tạo venv (nếu chưa có)
python -m venv .venv

# Kích hoạt venv
.venv\Scripts\Activate.ps1

# Cài đặt packages (có SSL bypass nếu cần)
.venv\Scripts\pip.exe install -r gateway\requirements.txt `
    --trusted-host pypi.org `
    --trusted-host files.pythonhosted.org `
    --trusted-host pypi.python.org
```

> **Lưu ý SSL:** Nếu gặp lỗi SSL certificate, luôn thêm 3 flag `--trusted-host` ở trên.

---

## 4. Đăng ký khuôn mặt (Offline)

### Cách 1: Script dòng lệnh (nhanh nhất)
```powershell
cd D:\HK252\DADN\gateway
..\venv\Scripts\python.exe ai\face_recognition\face_register.py
```
- Nhập tên người dùng khi được hỏi
- Nhìn thẳng vào camera, giữ vài giây
- Script tự chụp 30 ảnh mẫu và train model

### Cách 2: Qua Web Dashboard (Phase 3)
1. Khởi động hệ thống (xem Bước 5)
2. Truy cập `http://localhost:8000/members`
3. Nhập tên → nhập số mẫu (khuyến nghị 30) → click **"Bắt đầu chụp"**
4. Sau khi chụp xong → click **"Train Model"**
5. Danh sách thành viên cập nhật tự động

---

## 5. Khởi động hệ thống

```powershell
cd D:\HK252\DADN\gateway
..\venv\Scripts\python.exe main.py
```

### Các flag tuỳ chọn (nếu `main.py` hỗ trợ)
```powershell
# Chỉ chạy web (không cần camera/micro)
..\venv\Scripts\python.exe main.py --no-camera --no-voice

# Chạy debug mode
..\venv\Scripts\python.exe main.py --debug
```

### Dấu hiệu hệ thống đã sẵn sàng
```
[DB] Database initialized at gateway/data/yolohome.db
[MQTT] Connected to Adafruit IO
[WEB] Uvicorn running on http://0.0.0.0:8000
[FACE] LBPH model loaded
[VOICE] Wake word detector ready – say "yolo"
```

---

## 6. Truy cập Web Dashboard

Mở trình duyệt tại: **`http://localhost:8000`**

### Đăng nhập
- **Username:** `admin`
- **Password:** `yolohome2025`
- Sau khi đăng nhập, session tồn tại 8 giờ

### Các trang
| URL | Mô tả |
|-----|-------|
| `http://localhost:8000/` | Dashboard chính |
| `http://localhost:8000/members` | Quản lý khuôn mặt |
| `http://localhost:8000/login` | Trang đăng nhập |
| `http://localhost:8000/logout` | Đăng xuất |

---

## 7. Test từng tính năng

### 7.1 Test điều khiển thiết bị (MQTT)

Trên Dashboard, gạt switch **Đèn LED** → quan sát:
- Switch thay đổi trạng thái
- Feed Adafruit IO `led` nhận giá trị 1/0
- Nhật ký DB: `device_events` có record mới với `source='web'`

**Test bằng API trực tiếp:**
```powershell
# Bật đèn
Invoke-RestMethod -Uri "http://localhost:8000/api/control" `
    -Method POST `
    -ContentType "application/json" `
    -Body '{"device":"led","value":1}' `
    -Headers @{Cookie="session_token=<your_token>"}
```

---

### 7.2 Test Biểu đồ lịch sử (Chart.js)

1. Chờ **5 giây** để sensor đầu tiên được ghi vào DB
2. Mở Dashboard → scroll xuống phần "Lịch sử 24h"
3. Biểu đồ hiển thị đường nhiệt độ (cam) và độ ẩm (xanh)

**Kiểm tra API trực tiếp:**
```
http://localhost:8000/api/history?hours=24
```
Kết quả mẫu:
```json
{
  "data": [
    {"ts": 1710000000, "temp": 28.5, "humi": 65.2, "gas": 120},
    ...
  ],
  "count": 50
}
```

---

### 7.3 Test Báo cáo năng lượng

**API:**
```
http://localhost:8000/api/energy?hours=24
```
Kết quả mẫu:
```json
{
  "led":  {"on_seconds": 3600, "on_hours": 1.0, "est_kwh": 0.006, "power_w": 6},
  "fan":  {"on_seconds": 1800, "on_hours": 0.5, "est_kwh": 0.02,  "power_w": 40},
  "pump": {"on_seconds": 0,    "on_hours": 0.0, "est_kwh": 0.0,   "power_w": 30},
  "door": {"on_seconds": 600,  "on_hours": 0.17,"est_kwh": 0.0008,"power_w": 5}
}
```

Trên Dashboard: phần "Năng lượng tiêu thụ" cập nhật mỗi 30 giây.

---

### 7.4 Test Telegram Bot

#### Test cảnh báo nhiệt độ
Dùng Adafruit IO Dashboard gửi giá trị `temp` > 35°C lên feed → Bot gửi tin nhắn cảnh báo.

#### Test cảnh báo khí gas
Gửi giá trị `gas` > 300 lên feed `gas` → Bot gửi cảnh báo gas.

#### Test cảnh báo khuôn mặt lạ
Đưa khuôn mặt người chưa đăng ký vào camera:
- Sau ~5 giây liên tục không nhận ra → Bot gửi ảnh + caption

#### Test thủ công (PowerShell)
```powershell
# Gửi text test
$token = "TELEGRAM_BOT_TOKEN"
$chat  = "TELEGRAM_CHAT_ID"
Invoke-RestMethod "https://api.telegram.org/bot$token/sendMessage" `
    -Method POST `
    -Body @{chat_id=$chat; text="Test từ YoloHome ✅"}
```

---

### 7.5 Test Xác thực Web (Authentication)

1. Mở `http://localhost:8000` → tự động redirect đến `/login`
2. Nhập sai mật khẩu → thấy thông báo lỗi
3. Nhập đúng `admin` / `yolohome2025` → vào Dashboard
4. Mở tab mới ẩn danh → kiểm tra `/api/sensors` → nhận `401 Unauthorized`
5. Truy cập `/logout` → cookie bị xoá → redirect về `/login`

---

### 7.6 Test Đăng ký khuôn mặt qua Web

1. Truy cập `http://localhost:8000/members`
2. Nhập tên: `Test User`, số mẫu: `10`
3. Click **"Bắt đầu chụp"** → đảm bảo khuôn mặt rõ trong khung camera
4. Đợi ~15 giây để thu thập 10 ảnh
5. Click **"Train Model"** → chờ vài giây
6. Danh sách bên dưới cập nhật, hiển thị `Test User`
7. Kiểm tra thư mục `gateway/ai/face_recognition/dataset/Test_User/`

---

### 7.7 Test Voice Control (Multi-turn)

1. Nói **"yolo"** (wake word)
2. Nói câu hỏi: *"Nhiệt độ hiện tại là bao nhiêu?"*
3. Trợ lý trả lời
4. Nói tiếp (không cần nói "yolo" lại nếu trong session): *"So với lúc nãy thì thế nào?"*
5. Trợ lý nhớ ngữ cảnh và trả lời câu hỏi so sánh

**Lịch sử chat** hiển thị trên Dashboard (cập nhật mỗi 4 giây).

---

### 7.8 Kiểm tra SQLite trực tiếp

```powershell
cd D:\HK252\DADN\gateway
..\venv\Scripts\python.exe -c "
import sqlite3, json
con = sqlite3.connect('data/yolohome.db')
con.row_factory = sqlite3.Row

# 5 sensor readings mới nhất
rows = con.execute('SELECT * FROM sensor_readings ORDER BY ts DESC LIMIT 5').fetchall()
for r in rows: print(dict(r))

# Device events
rows = con.execute('SELECT * FROM device_events ORDER BY ts DESC LIMIT 5').fetchall()
for r in rows: print(dict(r))
con.close()
"
```

---

## 8. Phase 4 – Tính năng mới

---

### 8.1 Swagger UI – Tra cứu API

Sau khi khởi động hệ thống, mở trình duyệt:

```
http://localhost:8000/docs
```

**Tính năng:**
- Tất cả endpoint được phân nhóm bằng màu sắc: `IoT Control`, `Statistics`, `AI Features`, `Automation`, `Security`
- Mỗi endpoint có mô tả, ví dụ request/response
- Có thể test trực tiếp sau khi đăng nhập (cookie tự động được truyền)

**Authorize trong Swagger:**
1. Mở `http://localhost:8000/docs`
2. Click nút **Authorize** (biểu tượng 🔒 góc phải)
3. Nhập `session_token` đã lấy sau khi đăng nhập tại `http://localhost:8000/login`

---

### 8.2 Test Rate Limiting (Brute-force Protection)

Hệ thống chặn IP sau **5 lần đăng nhập sai** trong vòng **5 phút**.

**Cách test:**
1. Vào `http://localhost:8000/login`
2. Đăng nhập với mật khẩu sai 5 lần liên tiếp
3. Lần thứ 6 → nhận thông báo lỗi và HTTP `429 Too Many Requests`
4. Chờ 5 phút hoặc restart server để reset

**Test bằng PowerShell:**

```powershell
# Gửi 6 lần đăng nhập sai liên tiếp
1..6 | ForEach-Object {
    $body = "username=admin&password=wrong_password_$_"
    $response = Invoke-WebRequest "http://localhost:8000/login" -Method POST -Body $body -ContentType "application/x-www-form-urlencoded" -UseBasicParsing -ErrorAction SilentlyContinue
    Write-Host "Lần $_: Status $($response.StatusCode)"
}
```

> Lần 5–6 sẽ trả về `429`.

---

### 8.3 Test Rule Engine (Quy tắc tự động hóa)

#### Tạo rule qua Swagger UI

1. Vào `http://localhost:8000/docs`
2. Tìm section **Automation** → `POST /api/rules`
3. Click **Try it out** → nhập body JSON:

```json
{
  "name": "Bật quạt khi nóng",
  "condition_field": "temp",
  "condition_op": ">",
  "condition_value": 30,
  "action_device": "fan",
  "action_state": 1,
  "notify_telegram": false,
  "enabled": true
}
```

4. Click **Execute** → nhận response `{"id": 1, "message": "Tạo quy tắc thành công", ...}`

#### Kiểm tra rule hoạt động

```powershell
# Xem danh sách rules
Invoke-RestMethod "http://localhost:8000/api/rules" -Headers @{Cookie="session_token=<TOKEN>"}

# Tắt/bật rule (toggle)
Invoke-RestMethod "http://localhost:8000/api/rules/1/toggle" -Method PATCH -Headers @{Cookie="session_token=<TOKEN>"}

# Xoá rule
Invoke-RestMethod "http://localhost:8000/api/rules/1" -Method DELETE -Headers @{Cookie="session_token=<TOKEN>"}
```

#### Xem rule logs trong SQLite

```powershell
D:/HK252/DADN/.venv/Scripts/python.exe -c "
import sqlite3
con = sqlite3.connect('data/yolohome.db')
con.row_factory = sqlite3.Row
rows = con.execute('SELECT * FROM rule_logs ORDER BY ts DESC LIMIT 10').fetchall()
for r in rows: print(dict(r))
con.close()
"
```

---

### 8.4 Test OpenWeatherMap API

#### Kiểm tra qua Swagger

1. Vào `http://localhost:8000/docs` → section **Automation**
2. `GET /api/weather` → **Try it out** → **Execute**
3. Kết quả mẫu:

```json
{
  "success": true,
  "city": "Ho Chi Minh City",
  "country": "VN",
  "temp": 32.5,
  "feels_like": 36.2,
  "humidity": 78,
  "description": "mưa rào",
  "wind_speed": 4.5,
  "cached": false
}
```

#### Test trực tiếp bằng PowerShell

```powershell
Invoke-RestMethod "http://localhost:8000/api/weather" -Headers @{Cookie="session_token=<TOKEN>"} | ConvertTo-Json
```

#### Nếu API key lỗi (401)

Kiểm tra `.env`:

Đảm bảo đã **restart server** sau khi sửa `.env`.

---

### 8.5 Test Voice Weather Query

1. Khởi động hệ thống với module Voice Control
2. Nói wake word: **"yolo"**
3. Nói câu hỏi thời tiết, ví dụ:
   - *"Hôm nay trời có mưa không?"*
   - *"Thời tiết TP.HCM như thế nào?"*
   - *"Nhiệt độ ngoài trời bao nhiêu?"*
4. Hệ thống sẽ:
   - Phát hiện từ khoá thời tiết
   - Gọi `WeatherService.get_current_weather()`
   - Nếu Gemini sẵn: inject dữ liệu thời tiết vào RAG → trả lời thông minh
   - Nếu không có Gemini: đọc thông tin thẳng từ API

---

### 8.6 Kiểm tra automation_rules trong SQLite

```powershell
D:/HK252/DADN/.venv/Scripts/python.exe -c "
import sqlite3
con = sqlite3.connect('data/yolohome.db')
con.row_factory = sqlite3.Row

print('=== automation_rules ===')
rows = con.execute('SELECT * FROM automation_rules').fetchall()
for r in rows: print(dict(r))

print()
print('=== rule_logs (10 gần nhất) ===')
rows = con.execute('SELECT * FROM rule_logs ORDER BY ts DESC LIMIT 10').fetchall()
for r in rows: print(dict(r))
con.close()
"
```

---

## 9. Xử lý lỗi thường gặp

### ❌ `ModuleNotFoundError: No module named 'requests'`
```powershell
.venv\Scripts\pip.exe install requests --trusted-host pypi.org --trusted-host files.pythonhosted.org
```

### ❌ `401 Unauthorized` khi gọi API
- Cookie `session_token` không hợp lệ hoặc đã hết hạn (8h)
- Giải pháp: đăng nhập lại tại `http://localhost:8000/login`

### ❌ Biểu đồ không hiển thị
- Kiểm tra console trình duyệt (F12) → có lỗi JavaScript không?
- Đảm bảo đã có ít nhất 1 bản ghi trong `sensor_readings`
- Thử: `http://localhost:8000/api/history?hours=24` → kiểm tra `count > 0`

### ❌ Telegram không gửi được tin nhắn
- Kiểm tra `TELEGRAM_BOT_TOKEN` và `TELEGRAM_CHAT_ID` trong `.env`
- Đảm bảo đã `/start` bot trước khi nhận tin
- Test thủ công bằng lệnh PowerShell ở mục 7.4

### ❌ Camera không khởi động
```
[FACE] Cannot open camera index 0
```
- Thử đổi index camera: trong `config.py` → `CAMERA_INDEX = 1`
- Kiểm tra camera không bị ứng dụng khác chiếm dụng

### ❌ Lỗi SSL khi cài pip
```
SSLError: [SSL: CERTIFICATE_VERIFY_FAILED]
```
Luôn thêm:
```
--trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host pypi.python.org
```

### ❌ `sqlite3.OperationalError: unable to open database file`
- Đảm bảo thư mục `gateway/data/` tồn tại
- Hoặc chạy: `mkdir gateway\data` trước khi khởi động

### ❌ Wake word "yolo" không nhận ra
- Kiểm tra micro đang hoạt động (Windows Settings → Sound)
- Nói rõ và đủ to: **"Y-O-L-O"**
- Kiểm tra `VOSK_MODEL_PATH` trong `config.py` trỏ đúng model

### ❌ `/api/weather` trả về `success: false`
- Kiểm tra `OPENWEATHER_API_KEY` trong `.env` đã đúng chưa
- Kiểm tra kết nối internet (API cần gọi ra ngoài)
- Xem log server: dòng `[Weather]` để biết lý do lỗi

### ❌ `429 Too Many Requests` khi đăng nhập
- IP bị block do thử đăng nhập sai ≥5 lần
- Chờ 5 phút để cửa sổ sliding-window reset
- Hoặc restart server (xoá in-memory `_LOGIN_ATTEMPTS`)

---

## Phụ lục: Kiểm tra nhanh hệ thống

```powershell
# Kiểm tra web đang chạy
Invoke-WebRequest "http://localhost:8000/login" -UseBasicParsing | Select-Object StatusCode

# Kiểm tra DB size
Get-Item D:\HK252\DADN\gateway\data\yolohome.db | Select-Object Name, Length

# Kiểm tra thời tiết (thay <TOKEN> bằng session token thực)
Invoke-RestMethod "http://localhost:8000/api/weather" -Headers @{Cookie="session_token=<TOKEN>"}

# Kiểm tra rules
Invoke-RestMethod "http://localhost:8000/api/rules" -Headers @{Cookie="session_token=<TOKEN>"}
```
