\# FallAI Deployment Model



\## Mô hình tư thế



\- Mô hình: YOLO11n-Pose

\- Định dạng triển khai: OpenVINO

\- Số keypoint: 17

\- Kích thước đầu vào: 512

\- Ngưỡng phát hiện người: 0.35

\- Ngưỡng keypoint: 0.25



\## Mô hình chuỗi



\- Checkpoint: mdl/gru\_tapo\_blend\_095.pt

\- Kiến trúc: GRU hai đầu ra

\- Số đặc trưng mỗi khung hình: 92

\- Chiều dài chuỗi: 24 khung hình

\- Tốc độ lấy mẫu: 8 FPS

\- Khoảng thời gian quan sát: 3 giây

\- Tư thế: standing, sitting, lying

\- Sự kiện: normal, falling, postfall

\- Số keypoint tối thiểu: 7

\- Số lần xác nhận liên tiếp: 3

\- Số kết quả normal để tái kích hoạt: 8



Lưu ý: số 095 trong tên checkpoint là hệ số nội suy alpha bằng

0.95, không phải ngưỡng xác nhận té ngã. Ngưỡng sự kiện được đọc

từ metadata trong checkpoint.

