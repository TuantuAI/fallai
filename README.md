# FallAI

Đồ án phát hiện và cảnh báo té ngã thời gian thực bằng **YOLO11n-Pose + GRU đa nhiệm**, triển khai bằng FastAPI trên laptop Windows. Máy chủ trực tiếp đọc webcam USB hoặc camera IP/RTSP, xử lý AI, lưu sự kiện và gửi SMS qua Dcom/SIM; điện thoại trong mạng LAN chỉ mở website để theo dõi.

> Phạm vi đã khóa: một camera hoạt động, một người chính trong khung hình, môi trường trong nhà, laptop làm máy chủ, các thiết bị khác cùng LAN có thể truy cập website.

## 1. Thành phần đã có

- Đăng ký, đăng nhập, session và mật khẩu băm bằng `scrypt`.
- Dịch vụ camera chạy nền trên máy chủ, hỗ trợ webcam USB và camera IP/RTSP.
- Một pipeline AI dùng chung cho mọi thiết bị xem; WebSocket chỉ truyền kết quả và MJPEG truyền hình ảnh đã vẽ keypoint.
- YOLO11n-Pose/OpenVINO trích xuất 17 keypoint.
- Đặc trưng 92 chiều mỗi frame: 85 đặc trưng khớp (`x, y, confidence, dx, dy`) và 7 đặc trưng toàn cục của bounding box (vị trí, kích thước, tỷ lệ, vận tốc tâm).
- GRU một chiều, hai đầu ra:
  - tư thế: `standing`, `sitting`, `lying`;
  - hành động: `normal`, `falling`, `postfall`.
- Trạng thái quan sát: `clear`, `partial`, `occluded`, `lost`.
- Chính sách cảnh báo xác nhận nhiều dự đoán liên tiếp, chỉ gửi một lần cho đến khi hệ thống trở lại bình thường.
- SQLite lưu người dùng, liên hệ, sự kiện, ảnh và trạng thái SMS.
- Hàng đợi Dcom riêng, retry và không chặn luồng camera.
- Công cụ tạo nhãn, trích keypoint, train, tự chọn ngưỡng trên validation, evaluate và benchmark.
- Unit test cho đặc trưng, chính sách cảnh báo, database, SMS và mạng GRU.

## 2. Cấu trúc ngắn

```text
fallai/
├─ app/          backend, AI, SMS và website
├─ train/        chuẩn bị dữ liệu, train và evaluate
├─ tools/        script PowerShell và công cụ kiểm thử
├─ data/         dữ liệu, SQLite, ảnh sự kiện, kết quả đo
├─ mdl/          YOLO OpenVINO và gru.pt
├─ cert/         chứng chỉ HTTPS LAN
├─ docs/         tài liệu dữ liệu, chỉ số, ánh xạ báo cáo
├─ tests/        unit test
├─ .env.example
└─ req.txt
```

## 3. Cài đặt Windows chi tiết

### Bước 1 — Giải nén vào đường dẫn ngắn

Giải nén để file này nằm đúng tại:

```text
C:\fallai\README.md
```

Không đặt trong nhiều tầng như `Downloads\DoAn\SourceCode\BanCuoi...` để tránh đường dẫn dài.

### Bước 2 — Cài Python 3.11 64-bit

Tải từ [Python 3.11 cho Windows](https://www.python.org/downloads/windows/).

Khi cài:

1. Chọn **Add python.exe to PATH**.
2. Chọn bản 64-bit.
3. Mở PowerShell mới và kiểm tra:

```powershell
py -3.11 --version
```

Không dùng Python 3.13/3.14 cho bản khóa này vì bộ thư viện AI được kiểm thử theo Python 3.11.

### Bước 3 — Cài PyCharm

Tải [PyCharm](https://www.jetbrains.com/pycharm/download/), bản Community là đủ.

Trong PyCharm:

1. Chọn **Open** → `C:\fallai`.
2. Mở **Terminal** ở dưới cửa sổ.
3. Tất cả lệnh tiếp theo chạy tại `C:\fallai`.

### Bước 4 — Cho phép script trong đúng cửa sổ PowerShell hiện tại

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

Lệnh này chỉ áp dụng cho cửa sổ hiện tại, không thay đổi vĩnh viễn cấu hình Windows.

### Bước 5 — Cài môi trường và thư viện

```powershell
.\tools\setup.ps1
```

Script sẽ:

1. Tạo `.venv`.
2. Cài PyTorch CPU dành cho máy không có NVIDIA.
3. Cài FastAPI, OpenVINO, Ultralytics, OpenCV, scikit-learn và pySerial.
4. Tạo `.env` từ mẫu.
5. Chạy kiểm tra cú pháp và unit test.

Quá trình tải PyTorch/OpenVINO có thể lâu. Không đóng PowerShell giữa chừng.

### Bước 6 — Chọn interpreter trong PyCharm

Vào:

```text
File → Settings → Project → Python Interpreter → Add Interpreter → Existing
```

Chọn:

```text
C:\fallai\.venv\Scripts\python.exe
```

### Bước 7 — Tải và xuất YOLO Pose sang OpenVINO

```powershell
.\tools\pose_export.ps1
```

Kết quả cần có:

```text
C:\fallai\mdl\yolo11n-pose_openvino_model\
```

Chỉ chạy lại khi muốn xuất lại mô hình.

## 4. Cấu hình camera máy chủ

Mở `.env` và chọn một trong hai loại nguồn.

Webcam USB đầu tiên:

```env
CAMERA_SOURCE=0
CAMERA_NAME=Camera USB
CAMERA_BACKEND=auto
CAMERA_WIDTH=1280
CAMERA_HEIGHT=720
CAMERA_RECONNECT_SECONDS=3
```

Nếu máy có nhiều webcam, thử `CAMERA_SOURCE=1`, rồi `2`.

Camera IP/RTSP:

```env
CAMERA_SOURCE=rtsp://camera_user:camera_password@192.168.1.100:554/stream1
CAMERA_NAME=Camera phòng khách
CAMERA_BACKEND=ffmpeg
```

Thay tài khoản, mật khẩu, IP và đường dẫn stream theo camera thực tế. URL đầy đủ chỉ nằm trong `.env`; giao diện và log không hiển thị mật khẩu.

Kiểm tra nhanh nguồn camera trước khi chạy website:

```powershell
.\.venv\Scripts\python.exe -m tools.camera_test --source 0
```

Kết quả đúng có `CAMERA_READY` và tạo `data\camera_test.jpg`.

## 5. Chạy website trên laptop

```powershell
.\tools\run.ps1
```

Nếu chưa tạo chứng chỉ, mở trên laptop:

```text
http://localhost:8000
```

Đăng ký tài khoản. Lúc chưa có `mdl\gru.pt`, website vẫn hiển thị YOLO Pose và bộ xương nhưng **không xác nhận té ngã**. Đây là hành vi an toàn, không dùng mô hình GRU ngẫu nhiên để tạo cảnh báo giả.

Sau khi đăng nhập, nhấn **Khởi động giám sát**. Backend sẽ mở nguồn `CAMERA_SOURCE`; trình duyệt không yêu cầu quyền camera.

## 6. Mở website trên điện thoại bằng HTTPS

Điện thoại chỉ xem luồng đã xử lý từ máy chủ. HTTPS vẫn nên dùng để bảo vệ tài khoản và dữ liệu trong mạng LAN.

### Bước 1 — Cài mkcert

Mở PowerShell:

```powershell
winget install FiloSottile.mkcert
```

Đóng và mở lại PowerShell sau khi cài.

### Bước 2 — Tìm IPv4 của laptop

```powershell
ipconfig
```

Ví dụ laptop có:

```text
IPv4 Address: 192.168.1.10
```

Nên cấu hình router cấp IP cố định cho laptop để chứng chỉ không phải tạo lại.

### Bước 3 — Tạo chứng chỉ

```powershell
.\tools\cert.ps1 -Ip 192.168.1.10
```

Thay IP bằng IPv4 thực tế của laptop.

Khi HTTPS đã chạy ổn định, mở `.env`, đổi `COOKIE_SECURE=true` rồi khởi động lại server để cookie đăng nhập chỉ được gửi qua kết nối bảo mật.

### Bước 4 — Cho điện thoại tin cậy CA của mkcert

Trên laptop chạy:

```powershell
mkcert -CAROOT
```

Mở thư mục được in ra, chép `rootCA.pem` sang điện thoại.

- Android: **Cài đặt → Bảo mật → Mã hóa và thông tin xác thực → Cài chứng chỉ CA**. Tên mục thay đổi tùy hãng.
- iPhone: mở file để cài profile, sau đó vào **Settings → General → About → Certificate Trust Settings** và bật tin cậy hoàn toàn.

Chỉ cài CA do chính laptop của bạn tạo. Không chia sẻ khóa `rootCA-key.pem`.

### Bước 5 — Mở cổng Windows Firewall

Mở PowerShell bằng **Run as administrator** rồi chạy một lần:

```powershell
New-NetFirewallRule -DisplayName "FallAI 8000" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000
```

### Bước 6 — Chạy và truy cập từ điện thoại

```powershell
.\tools\run.ps1
```

Điện thoại và laptop phải cùng Wi-Fi. Trên điện thoại mở:

```text
https://192.168.1.10:8000
```

Đăng nhập để xem cùng luồng camera máy chủ. Không cấp quyền camera cho điện thoại. Việc đóng tab hoặc đăng xuất **không dừng AI**; chỉ nút **Dừng giám sát** hoặc tắt server mới dừng camera.

## 7. Dữ liệu huấn luyện

Mô hình GRU cần **video hoặc chuỗi ảnh liên tiếp**, không phải ảnh rời. Nguồn chính và giấy phép nằm trong [docs/DATA.md](docs/DATA.md).

Khuyến nghị bắt đầu với CAUCAFall:

- Trang chính thức: <https://data.mendeley.com/datasets/7w7fccy7ky/4>
- Có 10 người, năm loại té, năm hoạt động bình thường, thay đổi ánh sáng và che khuất.
- Giấy phép CC BY 4.0.

Sau khi tải, chỉ cần chép video `.avi` vào cấu trúc ngắn:

```text
data\raw\cauca\s01\fall_front.avi
data\raw\cauca\s01\walk.avi
data\raw\cauca\s02\fall_side.avi
...
```

Mỗi `s01`, `s02` phải là một người khác nhau. Không trộn cùng người vào nhiều split.

## 8. Gắn nhãn theo mốc thời gian

Tạo file nhãn mẫu:

```powershell
.\.venv\Scripts\python.exe -m train.scan --root data\raw --out data\labels.csv
```

Script cố tình ghi `EDIT_ME` vì không được tự đoán nhãn từ tên file. Mở video để đọc timestamp:

```powershell
.\.venv\Scripts\python.exe -m tools.label_view data\raw\cauca\s01\fall_front.avi
```

Phím:

- `Space`: dừng/chạy và in mốc hiện tại.
- `A/D`: lùi/tiến 0,5 giây.
- `Q`: thoát.

Sửa `data\labels.csv`. Một video có thể có nhiều dòng:

```csv
source,subject,start,end,posture,event,split,src_fps,note
cauca/s01/fall_front.avi,s01,0.0,2.1,standing,normal,train,23,
cauca/s01/fall_front.avi,s01,2.1,2.55,standing,falling,train,23,
cauca/s01/fall_front.avi,s01,2.55,3.2,lying,falling,train,23,
cauca/s01/fall_front.avi,s01,3.2,-1,lying,postfall,train,23,
```

Tên nhãn bắt buộc:

```text
posture: standing | sitting | lying
event:   normal | falling | postfall
split:   train | val | test
```

`end=-1` nghĩa là đến cuối video. Xem thêm [data/labels.sample.csv](data/labels.sample.csv).

Các đoạn của cùng một source phải phủ liên tục từ `start=0` đến dòng cuối `end=-1`, không chồng lấn và không để khoảng trống.
Hai nhãn độc lập: trong giai đoạn `falling`, posture có thể đổi từ `standing` sang `lying`, vì vậy hãy tách thêm mốc khi tư thế thay đổi.

## 9. Trích keypoint từ video

Chạy thử hai source trước:

```powershell
.\.venv\Scripts\python.exe -m train.extract --limit 2
```

Nếu thành công, chạy toàn bộ:

```powershell
.\.venv\Scripts\python.exe -m train.extract
```

Kết quả nằm trong `data\seq`. Đây là bước tốn thời gian nhất vì YOLO phải xử lý toàn bộ video. Có thể chạy qua đêm, cắm sạc và chọn chế độ hiệu năng cao.

## 10. Huấn luyện GRU

```powershell
.\.venv\Scripts\python.exe -m train.fit --epochs 80 --batch 64 --threads 6
```

Thiết lập đã chọn:

- Chuỗi 24 frame ở 8 FPS, tương đương khoảng ba giây.
- Hidden size 64, hai tầng GRU, dropout 0,2.
- AdamW, learning rate `1e-3`.
- Class weights để giảm lệch lớp.
- Keypoint dropout, frame dropout và nhiễu tọa độ để chống che khuất.
- Early stopping sau 10 epoch không cải thiện.
- Tự chọn ngưỡng fall tốt nhất từ `0.50–0.90` trên validation.

Kết quả:

```text
mdl\gru.pt
mdl\gru.history.json
```

Máy i5-12450H có thể train GRU này bằng CPU. Không cần fine-tune YOLO trong bản đầu.

Tạo baseline luật ngưỡng để so sánh học thuật (không dùng trong runtime):

```powershell
.\.venv\Scripts\python.exe -m train.base
```

Script tự chọn ngưỡng tỷ lệ bounding box, vận tốc đi xuống và độ hạ tâm trên validation, sau đó đánh giá một lần trên test. Kết quả lưu tại `data\base.json`.

## 11. Đánh giá chỉ số

```powershell
.\.venv\Scripts\python.exe -m train.eval
```

Kết quả:

```text
data\eval.json
```

Bao gồm:

- Confusion matrix tư thế.
- Confusion matrix hành động.
- Precision, Recall và F1 ở mức window.
- TP, FP, FN ở mức sự kiện.
- False alarms/hour.
- Precision, Recall và F1 riêng cho `clear`, `partial`, `occluded`.
- Độ trễ phát hiện trung bình và P95.
- Độ trễ GRU P50/P95.

Định nghĩa và cách đưa vào báo cáo nằm trong [docs/METRICS.md](docs/METRICS.md).
Kịch bản và mục tiêu nghiệm thu nằm trong [docs/TEST_PLAN.md](docs/TEST_PLAN.md).

Benchmark toàn pipeline trên video chưa dùng để train:

```powershell
.\.venv\Scripts\python.exe -m tools.bench data\raw\test\video.avi --seconds 60
```

Kết quả nằm tại `data\bench.json`, gồm AI latency, pose latency, throughput FPS, CPU và RAM.

Khi camera web đang chạy, nút **Tải CSV** xuất log từng frame gồm E2E, AI, YOLO, GRU, FPS, số keypoint và kết quả. Dùng file này để tính P50/P95 độ trễ website trong Chương III.

## 12. Cấu hình và kiểm tra Dcom

### Điều kiện phần cứng

Dcom phải:

- Có SIM gửi được SMS.
- Có driver Windows.
- Xuất hiện trong **Device Manager → Ports (COM & LPT)**.
- Hỗ trợ lệnh AT và SMS.

Nếu Dcom chỉ xuất hiện dưới `Network adapters` thì có thể là bản HiLink chỉ cấp Internet và không cho Python gửi SMS.

### Tìm cổng COM

Trong Device Manager, ghi lại cổng, ví dụ `COM5`. Đóng phần mềm quản lý Dcom của nhà sản xuất vì phần mềm đó có thể giữ cổng.

Sửa `.env`:

```env
SMS_ON=true
SMS_PORT=COM5
SMS_BAUD=115200
SMS_TIMEOUT=12
SMS_RETRIES=2
```

Kiểm tra lệnh AT và gửi SMS:

```powershell
.\.venv\Scripts\python.exe -m tools.sms_test --port COM5 --phone +84912345678
```

Kết quả đúng phải có phản hồi `OK`, thông tin `+CPIN`, `+CSQ`, `+CREG` và cuối cùng `+CMGS`.

Tin nhắn được chuyển thành không dấu để tương thích GSM text mode. Dcom chạy trong thread riêng; lỗi SMS không làm dừng camera.

## 13. Chạy bản hoàn chỉnh

Sau khi có `mdl\gru.pt`, chứng chỉ và Dcom:

```powershell
.\tools\run.ps1
```

Chỉ dùng **một Uvicorn worker**. Nhiều worker sẽ tải nhiều bản YOLO/GRU và mỗi tiến trình có thể tranh cổng COM.

## 14. Các giá trị mặc định và ý nghĩa

| Giá trị | Mặc định | Lý do |
|---|---:|---|
| Camera yêu cầu | 1280×720 | Máy chủ yêu cầu độ phân giải; camera có thể trả mức gần nhất hỗ trợ |
| JPEG website | Chất lượng 0,68 | Giảm băng thông cho nhiều thiết bị xem |
| FPS lấy mẫu | 8 | Bắt được chuyển động nhưng phù hợp CPU |
| YOLO input | 512 | Cân bằng tốc độ và khớp nhỏ |
| Chuỗi GRU | 24 frame | Khoảng 3 giây lịch sử |
| Keypoint confidence | 0,25 | Điểm khởi đầu, phải kiểm chứng |
| Tối thiểu quan sát | 7/17 | Dưới mức này trả `occluded` |
| Xác nhận | 3 dự đoán | Giảm một-frame false positive |
| Cooldown | 45 giây | Không gửi SMS lặp lại |

Ngưỡng fall cuối cùng không lấy cứng từ `.env`: `train.fit` tự tối ưu trên validation và lưu vào `gru.pt`; runtime ưu tiên giá trị trong checkpoint.

## 15. Lỗi thường gặp

| Lỗi | Cách xử lý |
|---|---|
| `py -3.11` không tồn tại | Cài Python 3.11 64-bit và mở PowerShell mới |
| PowerShell chặn `.ps1` | `Set-ExecutionPolicy -Scope Process Bypass` |
| GRU báo thiếu checkpoint | Hoàn thành `train.extract` → `train.fit` |
| Website báo camera chưa kết nối | Kiểm tra `CAMERA_SOURCE`, đóng ứng dụng khác đang giữ webcam hoặc thử RTSP bằng VLC |
| Điện thoại không mở được web | Cùng Wi-Fi, đúng IPv4, mở Firewall cổng 8000 |
| Độ trễ RTSP tăng dần | Dùng `CAMERA_BACKEND=ffmpeg`, ưu tiên substream và kiểm tra mạng LAN |
| `Access denied COM5` | Đóng phần mềm Dcom hoặc tiến trình Python cũ |
| `AT` timeout | Sai COM, baud, driver hoặc Dcom không hỗ trợ AT |
| RAM cao | Khởi động lại, đóng tab/app nền; không chạy nhiều Uvicorn worker |
| YOLO mất người sau bàn | Kết quả `occluded/lost`; đặt camera cao ở góc nhìn xuống |

## 16. Giới hạn cần ghi đúng trong báo cáo

- Không nhìn xuyên bàn ghế; che hoàn toàn cần camera thứ hai hoặc cảm biến khác.
- Không dùng để chẩn đoán hay thay thế hệ thống y tế được chứng nhận.
- Kết quả phụ thuộc góc camera, ánh sáng, dữ liệu huấn luyện và chất lượng Dcom.
- Các giá trị Precision/Recall/F1 chỉ được công bố sau khi chạy `train.eval` trên test set chưa từng dùng điều chỉnh mô hình.
- Sự kiện giả lập phải do người khỏe mạnh thực hiện trên đệm an toàn, không yêu cầu người cao tuổi ngã thử.

## 17. Lệnh kiểm tra dự án

```powershell
.\.venv\Scripts\python.exe -m compileall -q app train tools
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Swagger API khi server chạy:

```text
https://localhost:8000/docs
```
