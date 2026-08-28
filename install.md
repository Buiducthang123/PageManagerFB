# Cài đặt — ReupVideoVjpPro

Hướng dẫn cài đặt môi trường lần đầu. Xem [run.md](run.md) để biết cách chạy hằng ngày.

## 1. Yêu cầu hệ thống

| Thành phần | Yêu cầu |
|---|---|
| OS | Windows |
| Python | 3.9+ (máy đang dùng 3.13, xem `.venv`) |
| Node.js | 18+ (khuyến nghị 20+, để chạy Vite 8 / React 19) |
| FFmpeg | Bắt buộc — `ffprobe` dùng để đo thời lượng video khi upload |
| GPU (tuỳ chọn) | NVIDIA, 4GB+ VRAM — tăng tốc Whisper. Không có GPU vẫn chạy được bằng CPU |

Cài FFmpeg và đảm bảo `ffprobe`/`ffmpeg` có trong PATH:

```powershell
winget install Gyan.FFmpeg
```

Kiểm tra lại:

```powershell
ffprobe -version
```

## 2. Backend (Python)

Thư mục gốc dự án đã có sẵn virtualenv `.venv/` với các gói cần thiết. Nếu tạo lại từ đầu:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Gói chính trong [requirements.txt](requirements.txt):

- `fastapi`, `uvicorn[standard]` — API server
- `faster-whisper` — nhận diện giọng nói tiếng Trung
- `google-genai` — dịch zh→vi qua Gemini
- `nvidia-cublas-cu12`, `nvidia-cuda-runtime-cu12`, `nvidia-cuda-nvrtc-cu12` — DLL CUDA cho `faster-whisper` chạy GPU (không cần cài CUDA Toolkit riêng)

> Nếu máy không có GPU NVIDIA, Whisper sẽ tự fallback sang CPU (chậm hơn nhưng vẫn chạy).

## 3. Frontend (Node)

```powershell
cd frontend
npm install
```

## 4. Cấu hình `.env`

Copy file mẫu rồi điền key:

```powershell
copy .env.example .env
```

Sửa `.env` ở thư mục gốc:

```
GEMINI_API_KEY=<key lấy từ https://aistudio.google.com/apikey>
GEMINI_MODEL=gemini-3.5-flash-lite
WHISPER_MODEL=medium
WHISPER_DEVICE=auto
```

Ghi chú:

- **GEMINI_API_KEY** — bắt buộc để chạy bước dịch (Stage 3). Free tier ~1500 req/ngày.
- **WORKSPACE_DIR** (tuỳ chọn) — nơi lưu dữ liệu dự án (video, srt, log). Để trống thì mặc định là `./workspace` cạnh code.
- **WHISPER_MODEL** — `tiny`/`base`/`small`/`medium`/`large-v3`. `medium` là khuyến nghị cho GPU 4GB VRAM.
- **WHISPER_DEVICE** — `auto` (thử CUDA trước, tự rơi về CPU nếu lỗi) / `cuda` / `cpu`.
- **WHISPER_CACHE_DIR** (tuỳ chọn) — nơi cache model Whisper tải về từ HuggingFace. Mặc định `workspace/models/whisper` — hữu ích nếu ổ C ít dung lượng.

Các giá trị này cũng có thể chỉnh lại sau trong giao diện web, ở phần Settings — sẽ tự ghi đè vào `.env`.

## 5. Kiểm tra cài đặt xong

```powershell
.venv\Scripts\python.exe -c "import fastapi, faster_whisper, google.genai; print('OK')"
```

Nếu in ra `OK` là backend sẵn sàng. Bước tiếp theo: xem [run.md](run.md).
