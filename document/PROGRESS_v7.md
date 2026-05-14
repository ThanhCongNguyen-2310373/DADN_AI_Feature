# PROGRESS v7

> **Ngày cập nhật:** 14/05/2026  
> **Giai đoạn:** cải tiến FaceAI (thu thập dữ liệu, train, nhận diện, quản lý dataset)

---

## 1. Tổng quan thay đổi

| # | Hạng mục | Trạng thái |
|---|----------|-----------|
| 1 | Cải tiến thu thập mẫu khuôn mặt theo hướng dẫn | ✅ Hoàn thành |
| 2 | Kiểm tra chất lượng ảnh khi thu thập | ✅ Hoàn thành |
| 3 | Cân bằng ảnh + căn chỉnh mắt trước khi lưu | ✅ Hoàn thành |
| 4 | Tối ưu train (augmentation + validation + báo cáo) | ✅ Hoàn thành |
| 5 | Smoothing theo thời gian + tracking + ngưỡng động | ✅ Hoàn thành |
| 6 | Công cụ quản lý dataset (list/stats/clean) | ✅ Hoàn thành |
| 7 | Đồng bộ quy trình mới vào giao diện web | ✅ Hoàn thành |
| 8 | Tách luồng camera và bổ sung điều khiển phiên chụp | ✅ Hoàn thành |

---

## 2. Cải tiến phần thu thập dữ liệu

### 2.1 Thêm hướng dẫn thu thập mẫu
- Quy trình chụp được chia theo giai đoạn: nhìn thẳng, quay trái/phải 15-30 độ, nhìn lên/xuống, gần/xa, biểu cảm, che mặt nhẹ.
- Hướng dẫn hiển thị trực tiếp trên cửa sổ preview và in ra console.

### 2.2 Kiểm tra chất lượng ảnh
- Loại bỏ ảnh bị nhòe bằng Laplacian variance.
- Loại bỏ ảnh quá tối/quá sáng theo ngưỡng sáng.
- Loại bỏ ảnh có khuôn mặt quá nhỏ theo cấu hình.

### 2.3 Cân bằng ảnh trước khi lưu
- Áp dụng CLAHE để tăng tương phản ảnh grayscale.
- Căn chỉnh ROI theo vị trí mắt (nếu phát hiện mắt) trước khi resize.

### 2.4 Thêm chế độ thu thập nhanh
- Tự động chụp liên tục khi ảnh đạt chất lượng (cooldown ngắn).
- Hiển thị số ảnh còn thiếu (đếm ngược).
- Hỗ trợ tạm dừng/tiếp tục, bỏ qua 1 lần và chụp thủ công.

### 2.5 Đồng bộ lên giao diện web
- Cập nhật mô tả hướng dẫn và phím tắt trong trang đăng ký khuôn mặt.
- Nâng mặc định số ảnh lên 80 (giới hạn 80-200) và cảnh báo thiếu mẫu.
- API enroll dùng pipeline thu thập mới, có lọc chất lượng và hướng dẫn.

### 2.6 Tách luồng camera để tránh đơ khi chụp
- Camera preview của web và FaceAI dùng frame cache chung từ một nguồn đọc duy nhất.
- Phiên chụp web không mở webcam thứ hai nữa, tránh lỗi `MSMF: can't grab frame`.
- Bổ sung trạng thái phiên chụp, tạm dừng, tiếp tục và huỷ ngay trên UI.

---

## 3. Cải tiến phần train model 

### 3.1 Đồng bộ kích thước ảnh
- Train và nhận diện đều dùng ROI 160x160.

### 3.2 Tăng độ đa dạng dữ liệu
- Khuyến nghị tối thiểu 80-120 ảnh/người.
- Tách validation theo tỉ lệ cấu hình.

### 3.3 Data augmentation
- Xoay nhẹ (±10 độ), thay đổi sáng/tương phản, nhiễu nhẹ.
- Cắt/zoom nhẹ và thêm occlusion ngẫu nhiên (che 15-30%).

### 3.4 Đánh giá sau train
- Tạo tập validation riêng.
- Tính độ chính xác tổng và theo từng người.
- Lưu báo cáo train ra file JSON.
- Tính thống kê similarity (mean/std) để sinh ngưỡng động.

---

## 4. Cải tiến thuật toán nhận diện 

### 4.1 Làm mịn và cân bằng ảnh
- Thêm Gaussian blur trước detect.
- Dùng CLAHE (hoặc equalizeHist fallback).

### 4.2 Ngưỡng động theo người
- Đọc ngưỡng khuyến nghị từ file `face_thresholds.json`.
- Hạ nhẹ ngưỡng so với global, đồng thời có floor để an toàn.

### 4.3 Temporal smoothing
- Tổng hợp 10-20 frame gần nhất.
- Chỉ xác nhận người đã đăng ký khi tỉ lệ khớp >= 60%.

### 4.4 Theo dõi khuôn mặt (tracking)
- Sử dụng tracker (CSRT/KCF) để giữ ROI ổn định.
- Tái phát hiện theo chu kỳ để tránh drift.

---

## 5. Cải tiến quy trình train và sử dụng 

### 5.1 Quy trình mới đề xuất
- Bước 1: Mở giao diện thu thập mẫu có hướng dẫn.
- Bước 2: Tự động loại bỏ mẫu kém chất lượng.
- Bước 3: Cân bằng ảnh (CLAHE) và căn chỉnh mắt.
- Bước 4: Gợi ý thu thêm mẫu nếu chưa đủ.
- Bước 5: Train và tạo báo cáo độ tin cậy.
- Bước 6: Kiểm tra nhanh 10 frame live sau khi train.

### 5.2 Quản lý dataset
- Công cụ `face_dataset_tools.py` hỗ trợ list/stats/clean.
- Có thể xoá ảnh nhòe, quá tối/sáng, hoặc nghi occlusion (tuỳ chọn).

### 5.3 Lưu ý bảo mật
- Không để dataset trên máy công cộng.
- Cân nhắc phân quyền hoặc mã hoá thư mục dataset.

---

## 6. Danh sách file cập nhật

- `config.py`
- `ai/face_recognition/face_recognizer.py`
- `ai/face_recognition/face_register.py`
- `ai/face_recognition/face_dataset_tools.py`
- `web_app/app.py`
- `web_app/templates/members.html`
 - `web_app/templates/index.html`
 - `web_app/static/js/dashboard.js`
 - `web_app/static/css/style.css`

---

## 7. Ghi chú vận hành

- 720p@30fps tăng tải CPU, cần theo dõi hiệu năng thực tế.
- Nếu hệ thống quá tải, giảm `FACE_FRAME_SKIP` hoặc `CAMERA_FPS`.
- Sau khi train mới, nên chạy quick check để xác thực.

---

## 8. Cập nhật UI: thông báo nổi bật & điều khiển train 

Mục tiêu: làm cho việc nhận diện rõ ràng hơn trên giao diện web và cho phép nhóm chủ động train lại model chỉ với các thư mục thành viên đã chọn.

- Thêm thông báo nổi bật (modal) khi hệ thống "Đã nhận diện" một người đã đăng ký. Modal hiển thị tên người và độ tin cậy, nằm ở đầu trang (center-top), tự ẩn sau 6s và có nút đóng để tắt ngay.
	- File: `web_app/templates/index.html` — thêm thành phần modal `#face-modal`.
	- File: `web_app/static/css/style.css` — thêm style cho `.face-modal` và trạng thái `.face-modal.show`.
	- File: `web_app/static/js/dashboard.js` — thêm `showFaceModal()`/`hideFaceModal()`; khi trạng thái nhận diện chuyển sang `known` sẽ hiển thị cả toast nhỏ lẫn modal nổi bật để người dùng dễ nhận biết.

- Cải thiện trải nghiệm train model trên web:
	- Trang `members.html` giờ có checkbox để chọn những thư mục thành viên muốn đưa vào lần train tiếp theo (ví dụ chỉ chọn `Cong` và `Bame`).
	- Thêm 2 nút: `Train toàn bộ` và `Train phần chọn`. Khi khởi chạy sẽ tạo một job nền (không block UI) và trả về ngay.
	- Có trang trạng thái train (`/api/face/train/status`) để client poll tiến trình; UI hiển thị progress bar, stage, số người/ảnh đã nạp, thông báo lỗi/nội dung hoàn tất.
	- Backend: `web_app/app.py` — thêm `FaceTrainSession`, endpoint `/api/face/train` nhận payload `mode` + `selected_members`, và endpoint `/api/face/train/status` để trả trạng thái hiện thời.
	- Training function `ai/face_recognition/face_register.py::train_face_model()` được bổ sung `progress_callback` và tham số `include_members` để chỉ train các thư mục được chọn; hàm phát các trạng thái load/train/evaluate/save qua callback.

Tác động:
- Giảm rủi ro train nhầm data cũ vì giờ có thể bỏ chọn trước khi train.
- Người dùng biết ngay lập tức khi hệ thống nhận diện chủ nhà thông qua modal nổi bật và toast.

Hướng dẫn nhanh sử dụng:
- Mở trang `Thành viên` → đánh dấu (hoặc bỏ chọn) các thành viên muốn đưa vào lần train → bấm `Train phần chọn` hoặc `Train toàn bộ`.
- Mở Dashboard để xem thông báo nổi bật khi người đã đăng ký xuất hiện trước camera.

