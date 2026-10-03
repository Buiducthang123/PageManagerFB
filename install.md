# Cài đặt — ReupVideoVjpPro

Hướng dẫn cài đặt môi trường lần đầu. Xem [run.md](run.md) để biết cách chạy hằng ngày.

## 1. Yêu cầu hệ thống

| Thành phần | Yêu cầu |
|---|---|
| OS | Windows |
| Python | 3.9+ (máy đang dùng 3.13, xem `.venv`) |
| Node.js | 18+ (khuyến nghị 20+, để chạy Vite 8 / React 19) |
| FFmpeg | Bắt buộc — `ffprobe`/`ffmpeg` dùng để đo thời lượng video và tách audio (Stage Assemble) |
| GPU (tuỳ chọn) | NVIDIA, 4GB+ VRAM — tăng tốc Whisper. Không có GPU vẫn chạy được bằng CPU (Whisper fallback CPU, SenseVoice/Demucs mặc định đã chạy CPU) |
| Mạng | Cần internet khi chạy: dịch Gemini (Stage 3), TTS qua CapCut API (Stage 4), và lần đầu tải model Whisper/SenseVoice/Demucs |

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
pip install -e workspace/vendor/capcut-tts-api
```

Gói chính trong [requirements.txt](requirements.txt):

- `fastapi`, `uvicorn[standard]` — API server
- `faster-whisper` — nhận diện giọng nói tiếng Trung (Stage 2, engine mặc định)
- `funasr`, `torch`, `torchaudio` — engine STT thay thế SenseVoice (Stage 2, chạy CPU — bản torch CUDA từng bị lỗi access-violation/OOM trên máy dev)
- `google-genai` — dịch zh→vi qua Gemini (Stage 3)
- `demucs` — tách nhạc nền/SFX khỏi thoại gốc trước khi ráp CapCut (Stage Assemble)
- `pycapcut` — ráp draft CapCut từ video + phụ đề + audio TTS (Stage Assemble)
- `nvidia-cublas-cu12`, `nvidia-cuda-runtime-cu12`, `nvidia-cuda-nvrtc-cu12` — DLL CUDA cho `faster-whisper` chạy GPU (không cần cài CUDA Toolkit riêng)

> Nếu máy không có GPU NVIDIA, Whisper sẽ tự fallback sang CPU (chậm hơn nhưng vẫn chạy).

### capcut-tts-api (Stage 4 — TTS)

Package dùng để đọc phụ đề tiếng Việt thành giọng nói qua API CapCut **không có trên PyPI**, đã vendor sẵn tại [workspace/vendor/capcut-tts-api/](workspace/vendor/capcut-tts-api/) và cài editable như lệnh ở trên (`pip install -e workspace/vendor/capcut-tts-api`). Xem chi tiết SDK/CLI trong [workspace/vendor/capcut-tts-api/README.md](workspace/vendor/capcut-tts-api/README.md). Không cần API key riêng — client tự ký request bằng RSA/AWS SigV4 giả lập thiết bị CapCut.

### douyin-downloader (ingest link Douyin + trang "Tải video")

Ingest video bằng link Douyin (và trang "Tải video" độc lập trên sidebar) dùng
[jiji262/douyin-downloader](https://github.com/jiji262/douyin-downloader) tự
host qua CLI — **không có trên PyPI**, clone thủ công:

```powershell
git clone --depth 1 https://github.com/jiji262/douyin-downloader.git workspace/vendor/douyin-downloader
pip install -r workspace/vendor/douyin-downloader/requirements.txt
copy workspace\vendor\douyin-downloader\config.example.yml workspace\vendor\douyin-downloader\config.yml
```

Sửa `link: []` trong `config.yml` vừa tạo (file mẫu có sẵn 1 link demo).

Set biến môi trường trong `.env`:

```
DOUYIN_DL_DIR=D:/ReupVideoVjpPro/workspace/vendor/douyin-downloader
```

**Bắt buộc phải có cookie thật** — đã xác nhận trực tiếp: kể cả tải 1 video
công khai cũng bị Douyin chặn anti-bot nếu không có cookie (trái với mô tả
"không cần đăng nhập" trong README gốc của tool). Lấy cookie bằng cách đăng
nhập Douyin qua trình duyệt tự động của chính tool:

```powershell
cd workspace/vendor/douyin-downloader
python -m tools.cookie_fetcher --config config.yml
```

Đăng nhập xong quay lại terminal nhấn Enter — cookie tự ghi vào `config.yml`.
Cookie sẽ hết hạn theo thời gian, cần chạy lại lệnh trên khi ingest Douyin bắt
đầu báo lỗi "cookie hết hạn".

Chưa set `DOUYIN_DL_DIR` (hoặc chưa clone) thì ingest bằng link Douyin tự
fallback về API resolve cũ (snaptiktok.to) — không bắt buộc phải setup ngay.
Link TikTok luôn dùng snaptiktok.to vì douyin-downloader không hỗ trợ TikTok.
Trang "Tải video" độc lập (tải theo trang cá nhân/từ khoá) thì bắt buộc phải
setup vì không có tool thay thế.

### venv GPU riêng (trang "Làm sạch video" — xoá phụ đề cứng)

Worker `app/stages/hardsub_worker.py` cần torch **CUDA** + `rapidocr_onnxruntime`,
không cài chung vào `.venv` chính (venv chính dùng torch CPU). Tạo venv riêng:

```powershell
python -m venv D:\hardsub_venv
D:\hardsub_venv\Scripts\pip install opencv-python numpy rapidocr_onnxruntime
D:\hardsub_venv\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cu121
D:\hardsub_venv\Scripts\pip uninstall -y onnxruntime
D:\hardsub_venv\Scripts\pip install onnxruntime-gpu==1.22.0
```

Rồi đặt `HARDSUB_PYTHON=D:\hardsub_venv\Scripts\python.exe` trong `.env`. Lần
chạy đầu worker tự tải model vào `~/.cache/remove_hardsub/`: STTN `sttn.pth`
(~66MB, chế độ "Mượt" — mặc định; MIT, trọng số lấy từ video-subtitle-remover)
và LaMa `big-lama.pt` (~200MB, chế độ "Nhanh").

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
WHISPER_LANGUAGE=zh
```

Ghi chú:

- **GEMINI_API_KEY** — bắt buộc để chạy bước dịch (Stage 3). Free tier ~1500 req/ngày.
- **WORKSPACE_DIR** (tuỳ chọn) — nơi lưu dữ liệu dự án (video, srt, log, model cache). Để trống thì mặc định là `./workspace` cạnh code.
- **WHISPER_MODEL** — `tiny`/`base`/`small`/`medium`/`large-v3`. `medium` là khuyến nghị cho GPU 4GB VRAM.
- **WHISPER_DEVICE** — `auto` (thử CUDA trước, tự rơi về CPU nếu lỗi) / `cuda` / `cpu`.
- **WHISPER_LANGUAGE** — ngôn ngữ nhận diện: `zh` (mặc định)/`en`/`yue`/`ja`/`ko`/`vi`/`auto`.
- **WHISPER_CACHE_DIR** (tuỳ chọn) — nơi cache model Whisper tải về từ HuggingFace. Mặc định `workspace/models/whisper` — hữu ích nếu ổ C ít dung lượng.
- **SENSEVOICE_DEVICE** (tuỳ chọn, mặc định `cpu`) — engine STT thay thế Whisper, chọn ở Stage 2 trên giao diện.
- **SENSEVOICE_CACHE_DIR** (tuỳ chọn) — mặc định `workspace/models/sensevoice`.
- **CAPCUT_DRAFTS_DIR** (tuỳ chọn) — thư mục draft CapCut thật trên máy (vd `D:\Capcut Data\CapCut Drafts`) để Stage Assemble ghi thẳng vào, mở CapCut lên là thấy ngay. Để trống thì ghi vào `workspace/capcut_drafts` (phải tự copy qua tay).

Các giá trị GEMINI_API_KEY/GEMINI_MODEL/WHISPER_MODEL/WHISPER_DEVICE/WHISPER_LANGUAGE cũng có thể chỉnh lại sau trong giao diện web, ở phần Settings — sẽ tự ghi đè vào `.env`.

## 5. Kiểm tra cài đặt xong

```powershell
.venv\Scripts\python.exe -c "import fastapi, faster_whisper, google.genai, funasr, torch, demucs, pycapcut, capcut_tts_api; print('OK')"
```

Nếu in ra `OK` là backend sẵn sàng. Bước tiếp theo: xem [run.md](run.md).
