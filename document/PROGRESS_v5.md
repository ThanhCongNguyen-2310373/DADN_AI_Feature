# BÁO CÁO TIẾN ĐỘ - PHASE 5 (Production Scale)

## 1. Mục tiêu Phase 5

Phase 5 tập trung nâng cấp YoloHome từ mô hình smart home single-node thành nền tảng hướng production, bảo đảm tốt hơn về dữ liệu, bảo mật, observability và quy trình release.

Yêu cầu đã đặt ra:

- Hỗ trợ PostgreSQL cho triển khai multi-node.
- Hồ sơ người dùng và RBAC.
- Bộ test end-to-end tự động.
- API dự báo và phát hiện bất thường bằng ML.
- Observability (structured log, metrics, tracing tùy chọn).
- Rate limiting bền vững với Redis.
- Chiến lược dependency lock và release.

## 2. Phạm vi đã hiện thực

### 2.1 Cơ sở dữ liệu cho multi-node

Trạng thái: HOÀN THÀNH

Đã làm:

- Thêm cơ chế chọn backend qua cấu hình: `DATABASE_BACKEND=sqlite|postgresql`.
- Tạo module PostgreSQL mới, tương thích giao diện CRUD với backend hiện tại.
- Vẫn giữ SQLite làm mặc định để bảo đảm tương thích local/dev.
- Thêm các bảng auth/session ở cả hai backend:

- `users`
- `user_profiles`
- `sessions`

Tệp chính:

- core/database.py
- core/database_postgres.py
- config.py
- .env.example

### 2.2 Hồ sơ người dùng + RBAC

Trạng thái: HOÀN THÀNH

Đã làm:

- Thêm AuthService cho hash mật khẩu, xác thực và quản lý session.
- Khởi tạo admin mặc định từ biến môi trường.
- Thêm hệ role RBAC:

- admin
- operator
- viewer

- Ràng buộc quyền truy cập trên các endpoint thay đổi dữ liệu.
- Bổ sung API quản trị user (chỉ admin):

- GET /api/users
- POST /api/users
- PATCH /api/users/{user_id}/role

- Bổ sung API thông tin người dùng hiện tại:

- GET /api/me

Tệp chính:

- core/auth_service.py
- web_app/app.py
- core/database.py

### 2.3 Test tự động end-to-end

Trạng thái: HOÀN THÀNH

Đã làm:

- Thêm bộ test E2E riêng cho các luồng Phase 5:

- login/session
- enforcement RBAC
- tạo user
- CRUD rule
- endpoint ML
- endpoint metrics
- hạn chế brute-force đăng nhập

Tệp chính:

- tests/test_e2e_phase5.py

### 2.4 ML forecasting + anomaly detection

Trạng thái: HOÀN THÀNH

Đã làm:

- Thêm service phân tích cho:

- dự báo năng lượng ngắn hạn (linear trend baseline + confidence interval)
- phát hiện bất thường theo z-score trên dữ liệu cảm biến

- Bổ sung API:

- GET /api/ml/forecast
- GET /api/ml/anomalies

Tệp chính:

- core/ml_analytics.py
- web_app/app.py

### 2.5 Observability

Trạng thái: HOÀN THÀNH

Đã làm:

- Structured logging (JSON mode) điều khiển qua config.
- HTTP middleware cho observability gồm:

- request count
- request latency histogram
- active requests gauge
- trace id propagation qua header

- Thêm endpoint metrics:

- GET /metrics

- Thêm khởi tạo tracing tùy chọn (OpenTelemetry + OTLP endpoint) theo flag.
- Thêm health endpoint:

- GET /health

Tệp chính:

- core/observability.py
- web_app/app.py
- main.py

### 2.6 Rate limiting bền vững (Redis)

Trạng thái: HOÀN THÀNH

Đã làm:

- Thiết kế rate limiter theo backend có thể thay thế:

- memory (mặc định)
- redis (bền vững qua process/node)

- Luồng login sử dụng abstraction thống nhất.
- Có fallback an toàn về memory nếu Redis không sẵn sàng.

Tệp chính:

- core/rate_limiter.py
- web_app/app.py
- config.py

### 2.7 Dependency lock + release strategy

Trạng thái: HOÀN THÀNH

Đã làm:

- Thêm tệp mô tả dependency top-level để lock:

- requirements.in

- Thêm lock file dùng cho CI/release:

- requirements.lock.txt

- Thêm script sinh lock:

- scripts/lock_dependencies.ps1

- Thêm workflow CI:

- smoke tests
- full E2E tests
- release packaging theo tag

Tệp chính:

- requirements.in
- requirements.lock.txt
- scripts/lock_dependencies.ps1
- .github/workflows/smoke-tests.yml
- .github/workflows/e2e-tests.yml
- .github/workflows/release.yml

## 3. API đã thêm/đổi

Đã thêm:

- GET /api/me
- GET /api/users
- POST /api/users
- PATCH /api/users/{user_id}/role
- POST /api/voice/ask
- GET /api/ml/forecast
- GET /api/ml/anomalies
- GET /health
- GET /metrics

Tăng cường bảo mật:

- Áp dụng role check cho các thao tác ghi/thay đổi dữ liệu như:

- điều khiển thiết bị
- face enroll/train
- tạo/xóa/bật-tắt rule

## 4. Cấu hình bổ sung (Phase 5)

Đã thêm biến môi trường:

- DATABASE_BACKEND
- DATABASE_PATH
- POSTGRES_DSN / POSTGRES_HOST / POSTGRES_PORT / POSTGRES_DB / POSTGRES_USER / POSTGRES_PASSWORD
- WEB_SESSION_TTL
- RATE_LIMIT_BACKEND / RATE_LIMIT_MAX_ATTEMPTS / RATE_LIMIT_WINDOW_SECS / REDIS_URL
- LOG_STRUCTURED / METRICS_ENABLED / TRACING_ENABLED / OTLP_ENDPOINT

Tệp đã cập nhật:

- config.py
- .env.example
- .env

## 5. Kiểm chứng và kết quả test

Đã kiểm chứng:

- Compile check cho các module mới và điểm tích hợp app.
- Chạy full pytest (gồm smoke + e2e).

Kết quả:

- pytest -q => 4 passed, 0 failed.

Lỗi đã xử lý trong quá trình debug:

- Sửa tham số redirect của TestClient trong E2E.
- Sửa xung đột unique username bằng cách tạo username ngẫu nhiên.

## 6. Rủi ro và hướng tiếp theo

Vấn đề hiện tại (không chặn runtime):

- Còn cảnh báo markdown lint/style trong một số tài liệu tổng hợp cũ. Vấn đề này không ảnh hưởng đến runtime và kết quả test.

Đề xuất tiếp theo:

- Chạy CI với service PostgreSQL + Redis trên runner để đạt độ tương đồng hạ tầng cao hơn.
- Mở rộng E2E với harness tích hợp MQTT/Serial thật.
- Nâng cấp mô hình dự báo từ linear baseline sang time-series model khi dữ liệu đủ lớn.


