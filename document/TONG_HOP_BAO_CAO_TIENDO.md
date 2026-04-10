# TỔNG HỢP TIẾN ĐỘ DỰ ÁN YOLOHOME (PHASE 1 → PHASE 5)

> Mục đích: Tài liệu hợp nhất toàn bộ nội dung đã triển khai để dùng trực tiếp cho viết báo cáo tiến độ và làm slide thuyết trình.

---

## 1) Phạm vi các tài liệu

### Nhóm tài liệu tiến độ chính
- `gateway/document/PROGRESS.md`
- `gateway/document/PROGRESS_v2.md`
- `gateway/document/PROGRESS_v3.md`
- `gateway/document/PROGRESS_v4.md`
- `gateway/document/PROGRESS_v5.md`
- `gateway/document/guide.md`

---

## 2) Tổng quan hành trình phát triển

YoloHome đã đi từ một hệ thống IoT cơ bản sang nền tảng nhà thông minh có năng lực tự chủ, theo 5 giai đoạn:

1. **Phase 1**: Lõi kết nối IoT (Serial + MQTT) và điều khiển thiết bị.
2. **Phase 2**: Dashboard Web real-time (FastAPI, WebSocket, MJPEG) và tối ưu vận hành.
3. **Phase 3**: Lưu trữ bền vững + AI nâng cao (RAG multi-turn, Face Enrollment qua Web, Telegram cảnh báo).
4. **Phase 4**: Tự động hóa thông minh + bảo mật nâng cao (Rule Engine, OpenWeatherMap, Rate Limiting, Swagger chuẩn hóa).
5. **Phase 5**: Production scale (PostgreSQL multi-node, Profile + RBAC, ML analytics, Observability, Redis rate limit, CI/CD release strategy).

Thông điệp kỹ thuật xuyên suốt là: **tăng dần năng lực từ Automation sang Autonomous**.

---

## 3) Đối chiếu yêu cầu chức năng (REQ-01 → REQ-09)

Theo tài liệu yêu cầu trong `DADN.md`, trạng thái thực hiện:

| Mã REQ | Nội dung | Trạng thái | Cách hiện thực |
|---|---|---|---|
| REQ-01 | Đọc cảm biến định kỳ 5 giây | Hoàn thành | `SensorReader._publish_loop()` + callback Serial |
| REQ-02 | Giám sát realtime trên dashboard | Hoàn thành | WebSocket `/ws/sensors`, fallback REST polling |
| REQ-03 | Ghi log/lịch sử hoạt động | Hoàn thành | SQLite (`sensor_readings`, `device_events`, `face_events`, `rule_logs`) |
| REQ-04 | Điều khiển thủ công từ web | Hoàn thành | `POST /api/control` + MQTT publish + Serial command |
| REQ-05 | Nhận diện lệnh giọng nói | Hoàn thành | Wake word + STT (Google SpeechRecognition) |
| REQ-06 | Thực thi lệnh bằng giọng nói | Hoàn thành | NLP từ điển/regex + MQTT + TTS |
| REQ-07 | Cảnh báo vượt ngưỡng + tự kích hoạt | Hoàn thành | Threshold check + auto fan + alert + Rule Engine |
| REQ-08 | Mở cửa bằng khuôn mặt chủ nhà | Hoàn thành | FaceAI LBPH + MQTT door control |
| REQ-09 | Cảnh báo người lạ > 10 giây | Hoàn thành | Stranger timeout + ảnh log + Telegram cảnh báo |

---

## 4) Tiến độ theo từng phase

## 4.1 Phase 1 – Lõi IoT và kiến trúc nền

### Thành phần chính
- Thiết kế cấu trúc dự án Gateway đầy đủ (`core`, `sensors`, `ai`, `web_app`, `logs`, `data`).
- Triển khai `config.py` đọc biến môi trường từ `.env`.
- Tạo orchestration trong `main.py` để chạy đa luồng an toàn.

### Điểm kỹ thuật nổi bật
- **Singleton pattern thread-safe** cho các lớp trọng yếu:
  - `MQTTSingleton`
  - `SerialSingleton`
- Double-checked locking + `threading.Lock` giúp tránh tạo nhiều kết nối trùng.
- Tách rõ luồng đọc cảm biến, điều khiển, nhận diện và phản hồi giọng nói.

### Kết quả
- Nền tảng kết nối ổn định giữa Yolo:Bit ↔ Python Gateway ↔ Adafruit IO.

---

## 4.2 Phase 2 – Dashboard Web real-time và tối ưu vận hành

### Thành phần chính
- FastAPI web app + Uvicorn + Jinja2.
- Dashboard hiển thị realtime sensor, camera stream, điều khiển thiết bị.
- WebSocket + fallback polling để tăng độ bền khi mạng không ổn định.

### API/luồng đã có ở giai đoạn này
- `GET /`, `GET /api/sensors`, `POST /api/control`
- `GET /video_feed`, `WS /ws/sensors`
- Khu vực hiển thị chat history và face log.

### Tối ưu hiệu năng
- Giảm tải FaceAI bằng:
  - giảm kích thước frame,
  - frame skipping,
  - giới hạn FPS,
  - tối ưu số luồng OpenCV.
- Báo cáo trong tài liệu cho thấy CPU FaceAI giảm mạnh (50–60%).

---

## 4.3 Phase 3 – AI sâu hơn + lưu trữ bền vững

### 4.3.1 SQLite Persistent Storage
- Thêm `core/database.py` theo Singleton.
- Bảng chính:
  - `sensor_readings`
  - `device_events`
  - `face_events`
- Cấu hình WAL mode + cleanup data cũ.
- Mở API lịch sử và năng lượng:
  - `GET /api/history`
  - `GET /api/energy`

### 4.3.2 Telegram Notification
- `core/telegram_notifier.py` với mô hình queue + worker thread.
- Hỗ trợ cảnh báo nhiệt độ, gas, người lạ kèm ảnh.
- Tránh block luồng đọc cảm biến/nhận diện.

### 4.3.3 Authentication Web
- Cơ chế session cookie (`session_token`), TTL 8 giờ.
- Hash mật khẩu SHA-256.
- Bảo vệ route bằng dependency auth.

### 4.3.4 Face Enrollment qua Web
- Trang `/members` cho phép thêm thành viên khuôn mặt.
- API:
  - `GET /api/face/members`
  - `POST /api/face/enroll`
  - `POST /api/face/train`

### 4.3.5 Voice AI Multi-turn + RAG
- Lưu lịch sử hội thoại (`chat_history`).
- Mỗi câu hỏi mới được augment thêm ngữ cảnh 6 lượt trước.
- Knowledge base + FAISS retrieval + Gemini 1.5 Flash.

---

## 4.4 Phase 4 – Tự động hóa thông minh + bảo mật nâng cao

### 4.4.1 Swagger UI nâng cấp
- Chuẩn hóa tags theo miền chức năng:
  - IoT Control
  - Statistics
  - AI Features
  - Automation
  - Security
- Bổ sung schema chặt với `Literal`, `Field`, mô tả endpoint đầy đủ.

### 4.4.2 Rule Engine (If-Then)
- Thêm `core/rule_engine.py` theo Singleton.
- Cơ chế evaluate rule mỗi chu kỳ sensor.
- Hành động tự động gồm:
  - publish MQTT,
  - ghi `device_events`,
  - ghi `rule_logs`,
  - gửi Telegram (nếu bật).
- Có cooldown chống spam action (60 giây/rule).

### 4.4.3 OpenWeatherMap Integration
- Thêm `core/weather_service.py`:
  - gọi API thời tiết,
  - chuẩn hóa response,
  - cache 10 phút,
  - fallback dữ liệu cache cũ khi lỗi mạng.
- Thêm API `GET /api/weather`.
- Voice Assistant nhận câu hỏi thời tiết, inject dữ liệu thời tiết vào RAG.

### 4.4.4 Rate Limiting chống brute-force
- Áp dụng tại đăng nhập (`/login`).
- Sliding window 5 phút, tối đa 5 lần sai/IP.
- Khi vượt ngưỡng trả HTTP 429.

### 4.4.5 Database mở rộng cho automation
- Thêm bảng:
  - `automation_rules`
  - `rule_logs`
- Thêm CRUD methods tương ứng để phục vụ Rule API.

---

## 4.5 Phase 5 – Production scale và vận hành chuyên nghiệp

### 4.5.1 Data platform cho multi-node
- Mở rộng backend DB theo cấu hình `DATABASE_BACKEND`.
- Hỗ trợ PostgreSQL qua module `core/database_postgres.py`.
- Bổ sung schema người dùng/phiên đăng nhập dùng chung cho triển khai nhiều node.

### 4.5.2 Security nâng cao: Profile + RBAC
- Bổ sung `core/auth_service.py` quản lý hash mật khẩu, xác thực và vòng đời session.
- Role-based access control với 3 vai trò:
  - `admin`
  - `operator`
  - `viewer`
- Bảo vệ các endpoint thay đổi trạng thái thiết bị/rule/face train theo quyền.

### 4.5.3 ML analytics cho vận hành thông minh
- Thêm `core/ml_analytics.py` cho:
  - dự báo năng lượng ngắn hạn,
  - phát hiện bất thường từ sensor theo z-score.
- Mở API:
  - `GET /api/ml/forecast`
  - `GET /api/ml/anomalies`

### 4.5.4 Observability và vận hành
- Structured logging (JSON) theo cờ cấu hình.
- HTTP middleware thu thập metrics request count/latency/active requests.
- Hỗ trợ tracing tùy chọn qua OpenTelemetry + OTLP endpoint.
- Bổ sung endpoint:
  - `GET /health`
  - `GET /metrics`

### 4.5.5 Rate limiting bền vững + kiểm thử E2E
- Trừu tượng hoá rate limiter theo backend `memory|redis`.
- Redis backend cho kịch bản nhiều process/nhiều node.
- Bổ sung test E2E Phase 5 (`tests/test_e2e_phase5.py`) bao phủ luồng:
  - đăng nhập/phiên,
  - RBAC,
  - user/rule APIs,
  - ML endpoints,
  - metrics,
  - brute-force protection.

### 4.5.6 Dependency lock và release strategy
- Bổ sung `requirements.in`, `requirements.lock.txt`, script lock dependency.
- Thêm workflow CI:
  - smoke tests,
  - full E2E tests,
  - release theo tag.

---

## 5) Danh sách API theo trạng thái hiện tại

### Public / Auth
- `GET /login`, `POST /login`, `GET /logout`
- `GET /api/me`

### Dashboard & Realtime
- `GET /`
- `WS /ws/sensors`
- `GET /video_feed`
- `GET /api/sensors`

### Điều khiển & thống kê
- `POST /api/control`
- `GET /api/history`
- `GET /api/energy`

### AI Features
- `GET /api/chat`
- `POST /api/voice/ask`
- `GET /api/face/log`
- `GET /api/face/members`
- `POST /api/face/enroll`
- `POST /api/face/train`
- `GET /api/ml/forecast`
- `GET /api/ml/anomalies`

### Automation & Security nâng cao
- `GET /api/weather`
- `GET /api/rules`
- `POST /api/rules`
- `DELETE /api/rules/{id}`
- `PATCH /api/rules/{id}/toggle`
- `GET /api/users` (admin)
- `POST /api/users` (admin)
- `PATCH /api/users/{user_id}/role` (admin)
- `GET /health`
- `GET /metrics`

---

## 6) Công nghệ, thư viện và hạ tầng

### IoT + giao tiếp
- Adafruit IO MQTT (`Adafruit-IO`)
- Serial (`pyserial`)

### AI Vision
- OpenCV + OpenCV contrib (LBPH)
- Haar Cascade detector

### AI Voice + RAG
- SpeechRecognition (Google STT)
- gTTS + pygame (TTS)
- LangChain + langchain-google-genai
- FAISS vector store
- Google Generative AI SDK

### Web + dữ liệu
- FastAPI + Uvicorn
- Jinja2 + JavaScript dashboard + Chart.js
- SQLite (WAL mode)
- PostgreSQL (multi-node data backend)
- Redis (persistent rate limiting)
- Prometheus client (metrics export)
- OpenTelemetry (tracing, optional)
- Requests (Telegram + weather API)

---

## 7) Kiến trúc xử lý chính (end-to-end)

### Luồng sensor/automation
1. Yolo:Bit gửi sensor JSON qua Serial.
2. Gateway nhận qua `SerialSingleton` callback.
3. `SensorReader` cập nhật cache và publish MQTT định kỳ.
4. Dữ liệu ghi SQLite (`sensor_readings`).
5. `RuleEngine.evaluate()` so sánh rule và kích hoạt action nếu thỏa.
6. Dashboard nhận cập nhật qua WebSocket.

### Luồng FaceAI
1. Camera capture frame.
2. Haar detect ROI mặt.
3. LBPH predict nhận diện.
4. Nếu đúng chủ: mở cửa.
5. Nếu lạ liên tục >10 giây: ghi log + gửi Telegram ảnh.

### Luồng Voice AI
1. Wake word “yolo”.
2. STT chuyển tiếng nói thành text.
3. NLP phân luồng:
   - lệnh điều khiển → MQTT,
   - câu hỏi tư vấn → RAG,
   - câu hỏi thời tiết → WeatherService + RAG.
4. TTS phản hồi lại cho người dùng.

### Luồng Auth + RBAC + Observability (Phase 5)
1. Người dùng đăng nhập `POST /login`.
2. AuthService xác thực và tạo session trong DB.
3. Middleware auth đọc cookie session và nạp hồ sơ người dùng.
4. RBAC dependency quyết định cho phép/không cho phép thao tác.
5. Observability middleware ghi trace-id, đếm request, đo latency.
6. Metrics được scrape qua `GET /metrics` phục vụ giám sát.

---

## 8) Yêu cầu phi chức năng (NFR) – mức đạt được

Theo tài liệu tổng hợp và script trình bày:

| NFR | Mục tiêu | Kết quả ghi nhận |
|---|---|---|
| Độ trễ điều khiển/dữ liệu | < 2 giây | Đạt (WebSocket realtime, đo ~1.0–1.5 giây ở dashboard) |
| Độ tin cậy MQTT | Auto reconnect | Đạt (có cơ chế reconnect callback) |
| Khả năng scale dữ liệu | Nhiều node dùng chung DB | Đạt (hỗ trợ PostgreSQL backend) |
| FaceAI edge processing | Không phụ thuộc internet để nhận diện | Đạt |
| CPU tối ưu cho FaceAI | Giảm tải đáng kể | Đạt, tài liệu ghi nhận giảm khoảng 60% |
| Voice phản hồi | Nhanh và ổn định | Đạt mức thực tế 3–4 giây tùy mạng/API |
| Bảo mật web | RBAC + chống brute-force | Đạt (role policy + rate limiting memory/redis) |
| Quan sát hệ thống | Log có cấu trúc + metrics + tracing | Đạt (structured log, `/metrics`, tracing tùy chọn) |
| Kiểm thử hồi quy | Có E2E automation | Đạt (bộ `test_e2e_phase5.py` đã pass) |

---

## 9) Nội dung gợi ý để đưa vào slide báo cáo

### Slide 1: Vấn đề và mục tiêu
- Từ Automation → Autonomous.
- YoloHome là nền tảng IoT + AI tích hợp end-to-end.

### Slide 2: Kiến trúc tổng thể
- Yolo:Bit ↔ Gateway ↔ Adafruit ↔ Web/AI modules.
- Nhấn mạnh Singleton + đa luồng.

### Slide 3: Tiến độ theo 5 phase
- Mỗi phase 3–5 bullet kết quả chính.

### Slide 4: AI stack
- FaceAI LBPH edge.
- Voice AI + RAG multi-turn.
- Weather-aware assistant.

### Slide 5: Automation + Security + RBAC
- Rule Engine live CRUD.
- Rate limiting login.
- User role policy (admin/operator/viewer).

### Slide 6: AI Analytics + Observability
- Forecasting năng lượng, anomaly detection.
- Metrics, structured logs, tracing.

### Slide 7: Demo plan
- 4 bước show-and-tell, thời gian từng bước.

### Slide 8: Rủi ro và next steps
- Migration SQLite → PostgreSQL, hardening Redis, mở rộng test tích hợp, roadmap Phase 6.

---

## 10) Kết luận tổng hợp

YoloHome đã hoàn thành đầy đủ 5 phase cốt lõi, đi từ hệ thống điều khiển IoT cơ bản sang nền tảng nhà thông minh có khả năng:

- quan sát realtime,
- hành động tự động theo ngữ cảnh,
- tương tác tự nhiên bằng giọng nói,
- học từ dữ liệu và tri thức nội bộ (RAG),
- đồng thời đảm bảo bảo mật và vận hành ở mức triển khai thực tế.

Điểm nổi bật lớn nhất của giai đoạn hiện tại là **Rule Engine + Voice RAG + Security hardening + Production observability**, tạo nền tảng tốt cho bước phát triển sản phẩm ở phase kế tiếp.
