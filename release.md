# Phát hành bản cho user

Kế hoạch tổng: [user-management-plan.md](user-management-plan.md) mục 8, 12.

## Cấu trúc trên máy user

```
OddlyLabReup\
  launcher.pyw        user mở cái này (shortcut chạy bằng runtime\pythonw.exe) — chọn bản, tìm cổng, tự lùi bản lỗi, khởi động lại sau cập nhật
  runtime\            Python + thư viện + Chromium + ffmpeg + douyin-downloader (hiếm đổi)
  app\<bản>\          code app biên dịch Nuitka + giao diện (giữ 2 bản gần nhất)
  workspace\          dữ liệu user — cập nhật/gỡ cài không bao giờ đụng
  .env                cài đặt của user (trang Cài đặt ghi vào đây)
  logs\               launcher.log, app.log
```

## Máy build cần

- `.venv` của dự án + `pip install nuitka` (đã cài)
- Visual Studio Build Tools, workload C++ (Python 3.13 không dùng MinGW được)
- Inno Setup 6 (`%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`), GitHub CLI
- Ổ C máy build gần đầy: cache pip/tạm của build Runtime đã chuyển sang `build\` (ổ D)
- ffmpeg trong PATH, Node/npm

## Nơi để file: GitHub Releases

Repo public chỉ chứa file build (không có source): https://github.com/Buiducthang123/oddlylab-reup-releases
Cần `gh auth login` (đã làm) và `npx supabase login` (đã làm). Sau này có thẻ thanh toán thì đổi sang R2
được — app chỉ đọc link trong bảng `app_releases`.

## Phát hành bản App mới (thường xuyên, ~3 MB)

Gọn nhất — 1 lệnh làm cả 2 bước dưới:

```
.\release.bat 1.0.2 "Sua loi ..., them ..."
```
(PowerShell bắt buộc có `.\` ở đầu; trong cmd gõ `release.bat ...`; Git Bash gõ `./release.bat ...`.)

Hoặc từng bước:

```
.venv\Scripts\python.exe tools\build_release.py 1.0.2 --notes "Sửa lỗi ..."
.venv\Scripts\python.exe tools\publish_github.py app 1.0.2
```
Lệnh thứ 2 upload zip lên release `app-1.0.2` và ghi vào bảng `app_releases` — máy user thấy "Có bản mới"
trong ≤ 30 phút. Bắt buộc mọi người cập nhật: trang **Quản lý user → Cấu hình chung → Bản app tối thiểu**.

Bản mới không khởi động được trong 60 giây → launcher tự quay về bản trước và ghi `logs\launcher.log`.

## Runtime (hiếm — khi thêm/đổi thư viện)

```
.venv\Scripts\python.exe tools\build_runtime.py 1.0.0
.venv\Scripts\python.exe tools\publish_github.py runtime 1.0.0
```
Runtime được chia các phần < 1,9 GB (GitHub giới hạn 2 GB/file); lệnh thứ 2 upload và sinh
`installer\runtime_parts.iss` (link + SHA-256 từng phần) cho bộ cài. Bản App cần Runtime mới thì build với
`--min-runtime <bản>`; máy có Runtime cũ được báo "cần cài lại bộ cài mới".

## Bộ cài lần đầu

```
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" /DAppVersion=1.0.1 installer\setup.iss
.venv\Scripts\python.exe tools\publish_github.py installer 1.0.1
```
Lệnh cuối in ra link bộ cài để gửi user. Setup.exe nhỏ (~3 MB); Runtime tải lúc cài.

Launcher là `launcher.pyw` chạy bằng `runtime\pythonw.exe` (shortcut Desktop/Start Menu trỏ vào đó). **Không** build launcher.exe bằng Nuitka `--onefile`: Windows Defender chặn nhầm là virus ("CreateProcess failed; code 225").

## Trước khi gửi bộ cài cho user

- Facebook: redirect đi qua trạm chuyển tiếp HTTPS `https://szmjvnhgolzeztcgvups.supabase.co/functions/v1/fb-callback` (đã đăng ký trong Meta). Thêm từng user làm **Tester** của app Facebook.
- Thử bộ cài trên một máy Windows sạch (chưa có Python/ffmpeg).
- Chưa ký số → SmartScreen báo "Unknown publisher": bấm *More info → Run anyway*.
