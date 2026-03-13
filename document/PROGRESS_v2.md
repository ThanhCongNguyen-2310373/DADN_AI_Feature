# PROGRESS_v2.md – YoloHome Gateway Development Log (Session 3 – March 11, 2025)

> **Ghi chú:** Tài liệu này ghi lại tiến độ Session 3 (6 tasks mới).  
> Xem lịch sử giai đoạn Phase 1–7 tại: `document/PROGRESS.md`

---

## Tổng quan Session 3

| Task | Nội dung |
|------|----------|
| T1 | Cài PyAudio cho Voice Control |
| T2 | Firmware MicroPython cho Yolo:Bit |
| T3 | knowledge_base.txt + tích hợp RAG vào VoiceAssistant |
| T4 | FastAPI Web Dashboard |
| T5 | Tối ưu FaceAI + tích hợp Web vào main.py |
| T6 | PROGRESS_v2.md |
---

## T1 – Cài PyAudio


```powershell
pip install PyAudio --trusted-host pypi.org --trusted-host files.pythonhosted.org
```


---

## T2 – Firmware MicroPython Yolo:Bit

**File tạo mới:** `yolobit/main.py`

### Phần cứng được hỗ trợ

| Component | Pin / Địa chỉ | Giao thức |
|-----------|--------------|-----------|
| LED | GPIO0 (P0) | Digital OUT |
| Fan | GPIO1 (P1) | Digital OUT |
| Servo (cửa) | P2 | PWM (duty 26–128) |
| Gas sensor | GPIO34 | ADC 12-bit |
| DHT20 (temp+humi) | SDA=GPIO21, SCL=GPIO22, addr=0x38 | I2C |

### Giao thức Serial JSON

**PC → Yolo:Bit:**
```json
{"cmd": "set", "device": "led", "value": 1}
{"cmd": "set", "device": "fan", "value": 0}
{"cmd": "set", "device": "door", "value": 90}
```

**Yolo:Bit → PC (cứ mỗi 5 giây):**
```json
{"sensor": "temp", "value": 28.5}
{"sensor": "humi", "value": 64.2}
{"sensor": "gas",  "value": 145}
{"ack": "led", "value": 1}
{"error": "unknown_device"}
```

### Đặc điểm thiết kế
- **Non-blocking loop:** dùng `ticks_diff()` thay vì `utime.sleep()` để không block nhận lệnh
- **Servo duty formula:** `duty = int(angle / 180 * 102) + 26` → range 26–128 cho 0°–180°
- **DHT20 polling:** đọc I2C raw bytes, parse theo datasheet Sensirion

---

## T3 – knowledge_base.txt + RAG Integration

### File tạo mới: `gateway/ai/voice_control/knowledge_base.txt`

Nội dung gồm 5 phần:
1. **Thông số an toàn** – ngưỡng nhiệt độ (>35°C nguy hiểm), gas (>300 ppm nguy hiểm)
2. **Hướng dẫn xử lý rò rỉ khí gas** – 5 bước hành động
3. **Mẹo tiết kiệm điện** – tắt quạt khi nhiệt độ ổn, lịch trình thiết bị
4. **Danh sách lệnh thoại** – toàn bộ 12 lệnh hỗ trợ
5. **Thông tin hệ thống** – mô tả kiến trúc YoloHome

### Cập nhật `voice_assistant.py`

#### Thêm mới:
```python
QUESTION_KEYWORDS = ["là gì", "như thế nào", "bao nhiêu", "ngưỡng",
                     "an toàn", "tiết kiệm", "rò rỉ", "nguy hiểm", ...]

# Trong __init__:
self.chat_history = []   # [{role, text, time}] - dùng bởi WebApp
self._rag: GeminiRAGAssistant = None

# Luồng xử lý lệnh mới:
_is_question(text) → True → _ask_rag() → TTS
action + device found → MQTT publish → TTS
fallback → _ask_rag()

def _init_rag()     # thread "RAG-Init", không block main
def _is_question()  # keyword matching
def _ask_rag()      # delegate to GeminiRAGAssistant
def _add_to_history() # max 100 entries
```

#### GeminiRAGAssistant thay đổi:
| Trước | Sau |
|-------|-----|
| `from langchain.vectorstores import FAISS` | `from langchain_community.vectorstores import FAISS` |
| `from langchain.document_loaders import TextLoader` | `from langchain_community.document_loaders import TextLoader` |
| model `gemini-pro` | model `gemini-1.5-flash` |
| Default prompt | Custom Vietnamese PromptTemplate |
| `chunk_size=500` | `chunk_size=300, separator="\n\n"` |

---

## T4 – FastAPI Web Dashboard

### Cài đặt thư viện
```powershell
pip install fastapi "uvicorn[standard]" jinja2 python-multipart langchain-community \
    --trusted-host pypi.org --trusted-host files.pythonhosted.org
```

**Phiên bản đã cài:** FastAPI 0.135.1, Uvicorn 0.41.0, Jinja2 3.1.6, langchain-community 0.4.1

### Cấu trúc thư mục
```
gateway/web_app/
├── __init__.py
├── app.py                  # FastAPI entry point
├── templates/
│   └── index.html          # Dashboard UI
└── static/
    ├── css/
    │   └── style.css       # Dark mode theme
    └── js/
        └── dashboard.js    # Real-time update logic
```

### API Endpoints

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| GET | `/` | Trang dashboard HTML |
| GET | `/api/sensors` | JSON dữ liệu cảm biến mới nhất |
| GET | `/api/chat` | JSON lịch sử chat VoiceAssistant |
| GET | `/api/face/log` | JSON danh sách ảnh log nhận diện |
| POST | `/api/control` | Điều khiển thiết bị (body: `{device, value}`) |
| GET | `/video_feed` | MJPEG stream từ webcam (dùng trong `<img>`) |
| GET | `/face_log/{filename}` | Serve ảnh log nhận diện |
| WS | `/ws/sensors` | WebSocket push sensor data mỗi 3 giây |

### Tính năng Dashboard

- **3 sensor cards** (nhiệt độ, độ ẩm, gas) với progress bar màu động:
  - Xanh lá: bình thường
  - Cam: cảnh báo (>32°C, >80%, >200 ppm)
  - Đỏ: nguy hiểm (>35°C, >90%, >300 ppm)
- **4 toggle điều khiển** (LED, Fan, Door, Pump) gửi POST `/api/control`
- **Camera stream** MJPEG từ `/video_feed`
- **Chat panel** hiển thị `voice_assistant.chat_history`
- **Face Log gallery** grid ảnh sự kiện nhận diện gần nhất
- **WebSocket real-time** tự reconnect sau 5 giây khi mất kết nối
- **Fallback REST polling** khi WebSocket không khả dụng (mỗi 3 giây)
- **Dark mode** với CSS custom properties, responsive grid

### Module Injection Pattern
Web App được thiết kế để nhận dữ liệu runtime qua `inject_modules()`:
```python
from web_app.app import inject_modules
inject_modules(sensor_reader=sr, voice_assistant=va, face_recognizer=fr)
```
Nếu chạy standalone (`python app.py`), sẽ dùng dữ liệu giả lập.

---

## T5 – Tối ưu FaceAI + Tích hợp Web vào main.py

### Tối ưu FaceAI (`face_recognizer.py`)

| Thay đổi | Trước | Sau | Tác động |
|---------|-------|-----|----------|
| Camera resolution | 640×480 | 320×240 | -75% pixel data |
| OpenCV threads | Mặc định (all cores) | `cv2.setNumThreads(2)` | CPU usage giảm ~30% |
| Face ROI size | 160×160 | 100×100 | LBPH predict nhanh hơn |
| Frame skip | Mỗi frame | 1/2 frame (`FRAME_SKIP=2`) | ~5 FPS thực tế |
| Sleep per loop | 0.1s (10 FPS) | 0.2s (5 FPS) | CPU usage giảm thêm |
| `cap.set(CAP_PROP_FPS)` | Không có | 15 FPS | Giới hạn buffer đầu vào |
| Histogram equalizer | Không có | `cv2.equalizeHist()` | Cải thiện ánh sáng yếu |
| `minNeighbors` | 5 | 4 | Giảm miss detection |
| `minSize` | (80,80) | (50,50) | Phát hiện tốt hơn ở 320×240 |
| `_cap` expose | Không có | `self._cap` | WebApp dùng cùng stream |
| `cv2.imshow` | Có | **Bỏ** | Không block headless server |

**Tổng: CPU usage FaceAI ước giảm 50–60% trên CPU tầm trung.**

### Cập nhật `main.py`

- Thêm flag `--no-web` (mặc định Web App bật)
- Thêm method `_start_web_app()`: khởi động uvicorn trong daemon thread `"WebApp-Thread"`
- Inject modules trước khi start uvicorn
- Cập nhật `_print_status()` hiển thị trạng thái Web Dashboard
- `YoloHomeGateway.__init__` nhận thêm tham số `enable_web: bool = True`

### Luồng khởi động mới
```
main.py
  ├─ Step 1+2: MQTT Singleton + Serial Singleton
  ├─ Step 3: SensorReader (thread)
  ├─ Step 4: FaceRecognizer (thread, tuỳ chọn)
  ├─ Step 5: VoiceAssistant (thread, tuỳ chọn)
  ├─ Step 6: WebApp (uvicorn thread, tuỳ chọn) ← MỚI
  └─ Heartbeat loop (main thread, 30s interval)
```

---

## Phiên bản thư viện (Environment)

| Thư viện | Phiên bản |
|----------|-----------|
| Python | 3.13.7 |
| opencv-python | 4.13.0 |
| PyAudio | 0.2.14 |
| pyserial | 3.5 |
| Adafruit-IO | 2.x |
| SpeechRecognition | latest |
| gTTS | latest |
| pygame | latest |
| langchain | latest |
| langchain-community | 0.4.1 |
| langchain-google-genai | latest |
| faiss-cpu | latest |
| fastapi | 0.135.1 |
| uvicorn | 0.41.0 |
| jinja2 | 3.1.6 |
| python-multipart | 0.0.22 |
| python-dotenv | 1.2.2 |
| pydantic | 2.12.5 |

---

## Quyết định kiến trúc

### 1. WebSocket + REST Fallback
Chọn pattern hybrid: WebSocket cho real-time, REST polling làm fallback. Lý do:
- WebSocket bị block bởi một số browser settings / proxy
- REST polling 3s đủ mượt cho sensor data không thay đổi nhanh

### 2. MJPEG thay vì WebRTC
MJPEG (`multipart/x-mixed-replace`) đơn giản hơn, không cần signaling server, phù hợp LAN. Latency ~200ms chấp nhận được.

### 3. Module Injection thay vì Global State
Web App nhận module qua `inject_modules()` thay vì import trực tiếp. Ưu điểm:
- Có thể chạy Web App standalone (demo/test)
- Không tạo circular import
- Dễ mock trong testing

### 4. Frame Skip thay vì Thread Sleep thuần
`FRAME_SKIP=2` kết hợp với `time.sleep(0.2)` cho phép đọc camera liên tục (giữ buffer sạch) nhưng chỉ chạy AI inference mỗi 2 frame → không bị frame lag.

---

## Vấn đề đã gặp và giải pháp

| Vấn đề | Nguyên nhân | Giải pháp |
|--------|-------------|-----------|
| `pip install` SSL error | Chứng chỉ SSL công ty/trường học | `--trusted-host pypi.org --trusted-host files.pythonhosted.org` |
| `pipwin` không chạy được | Python 3.13 không tương thích js2py bytecode | Dùng pip trực tiếp với trusted-host |
| `from langchain.vectorstores import FAISS` deprecated | langchain refactor sang community package | Đổi sang `langchain_community.vectorstores` |
| `gemini-pro` không còn hoạt động | Google deprecated model cũ | Đổi sang `gemini-1.5-flash` |
| FaceAI block headless server | `cv2.imshow()` cần display | Bỏ `cv2.imshow`, WebApp serve stream riêng |

---

## Hướng phát triển tiếp theo

- [ ] **HTTPS cho Web Dashboard** – dùng `uvicorn --ssl-keyfile`
- [ ] **Authentication** – thêm login page trước dashboard
- [ ] **Mobile notifications** – gửi Telegram bot khi có người lạ
- [ ] **Face enrollment qua Web** – upload ảnh để đăng ký khuôn mặt mới
- [ ] **Historical charts** – lưu sensor data vào SQLite, vẽ biểu đồ với Chart.js
- [ ] **Energy report** – thống kê thời gian thiết bị bật/tắt theo ngày
