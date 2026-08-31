# Chạy dự án — ReupVideoVjpPro

Giả định đã cài đặt xong theo [install.md](install.md).

## Chạy chế độ development (2 terminal)

**Terminal 1 — Backend (FastAPI, port 8001):**

```powershell
.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8001
```

Chạy lệnh này từ thư mục gốc dự án (`d:\ReupVideoVjpPro`), không phải từ trong `app/`.

**Terminal 2 — Frontend (Vite, port 5175):**

```powershell
cd frontend
npm run dev
```

Mở trình duyệt: **http://localhost:5175** — Vite tự proxy các request `/api` sang backend ở port 8001 (cấu hình tại [frontend/vite.config.ts](frontend/vite.config.ts)).

## Chạy bản build production (1 server, gộp frontend vào backend)

```powershell
cd frontend
npm run build
cd ..
.venv\Scripts\Activate.ps1
uvicorn app.main:app --port 8001
```

`app/main.py` tự phát hiện `frontend/dist/` đã build và serve luôn SPA tại `http://localhost:8001` — không cần chạy Vite dev server nữa.

## Luồng sử dụng trong giao diện

1. **Tạo dự án mới** — đặt tên, hệ thống tạo project trong `workspace/projects/<ngày>_<slug>/`.
2. **Upload video** (Stage 1 — Ingest) — kéo thả file `.mp4/.mkv/.webm/.mov/.avi/.m4v`.
   - *Lưu ý:* main.md mô tả tải video tự động bằng `yt-dlp` từ URL, nhưng bản hiện tại chỉ hỗ trợ **upload file thủ công** — chưa có tính năng tải từ URL.
3. **Nhận diện giọng nói (Stage 2)** — chọn engine **Whisper** (mặc định, GPU/CPU) hoặc **SenseVoice** (funasr, luôn chạy CPU) rồi bấm chạy → `sub_zh.srt`. Lần đầu chạy engine nào sẽ tự tải model của engine đó (`workspace/models/whisper/` hoặc `workspace/models/sensevoice/`), có thể mất vài phút tuỳ mạng.
4. **Gemini dịch (Stage 3)** — bấm chạy, dịch zh→vi + tạo `entity_dict.json` (từ điển tên riêng nhất quán) → `sub_vi.srt`. Có thể dịch lại từng câu riêng lẻ hoặc sửa tay trực tiếp trên giao diện.
5. **TTS (Stage 4)** — chọn giọng đọc (danh sách tại `app/stages/tts.py::VOICES`, nghe thử trước khi chọn), bấm chạy → gọi API CapCut đọc từng câu `sub_vi.srt` thành audio, lưu `audio/manifest.json` + file audio từng câu. Có thể chạy lại riêng các câu lỗi ("Retry failed") hoặc redo từng câu đơn lẻ.
6. **Dựng bản nháp CapCut (Stage Assemble)** — bấm chạy: tách nhạc nền/SFX khỏi thoại gốc bằng Demucs (lần đầu chạy tự tải model, có thể mất vài phút), rồi ráp video + audio nền + audio TTS + phụ đề thành 1 draft CapCut bằng `pycapcut`. Draft ghi vào `CAPCUT_DRAFTS_DIR` (mở CapCut lên là thấy) hoặc `workspace/capcut_drafts/` nếu chưa cấu hình.
7. Theo dõi tiến độ job và log trực tiếp trên giao diện (poll qua `/api/projects/{id}/jobs/{stage}`).

Sau bước 6, pipeline dừng ở mức code hiện tại — **review thủ công trong CapCut** và **upload Facebook** mô tả trong [main.md](main.md) vẫn là thao tác tay, chưa có route/API tương ứng trong `app/`.

## Xử lý sự cố thường gặp

| Vấn đề | Nguyên nhân / cách xử lý |
|---|---|
| `ffprobe: command not found` khi upload | Chưa cài FFmpeg hoặc chưa có trong PATH — xem [install.md](install.md) |
| Whisper báo lỗi CUDA / không thấy GPU | Tự động fallback CPU. Có thể ép cứng bằng `WHISPER_DEVICE=cpu` trong `.env` |
| SenseVoice chạy chậm | Luôn chạy CPU theo thiết kế (bản torch CUDA từng lỗi access-violation/OOM trên máy dev) — dùng Whisper nếu cần tốc độ |
| Gemini lỗi quota / 429 | Free tier có giới hạn request/ngày và RPM (~15 RPM) — đợi hoặc đổi model trong Settings |
| TTS báo lỗi / một số câu "failed" | API CapCut không chính thức, đôi khi rớt task — dùng nút "Retry failed segments" ở Stage 4 để chạy lại riêng các câu lỗi |
| Assemble báo lỗi Demucs | Lần đầu chạy cần tải model về `workspace/models/whisper/huggingface` (dùng chung cache dir với Whisper) — kiểm tra mạng/dung lượng ổ |
| Draft CapCut không hiện trong app CapCut | Chưa cấu hình `CAPCUT_DRAFTS_DIR` trỏ đúng thư mục Drafts thật của CapCut trên máy — xem [install.md](install.md) |
| Port 8001 hoặc 5175 đã bị chiếm | Đổi `--port` khi chạy uvicorn, và sửa `server.port` + `proxy` trong `frontend/vite.config.ts` cho khớp |
| Model Whisper tải chậm/lỗi ổ C đầy | Cache đã được trỏ sang `workspace/models/whisper` (ổ chứa code) qua `apply_whisper_cache_env()` trong `app/config.py` — kiểm tra biến `WHISPER_CACHE_DIR` nếu cần đổi ổ khác |

## Vị trí dữ liệu

```
workspace/
├── index.json                 # danh sách project
├── models/whisper/            # cache model Whisper (+ Demucs, dùng chung HF_HOME)
├── models/sensevoice/         # cache model SenseVoice
├── capcut_drafts/             # draft CapCut (nếu chưa cấu hình CAPCUT_DRAFTS_DIR)
├── vendor/capcut-tts-api/     # SDK TTS vendor (không có trên PyPI)
└── projects/<project_id>/
    ├── project.json           # trạng thái từng stage
    ├── video.<ext>
    ├── sub_zh.srt
    ├── sub_vi.srt
    ├── entity_dict.json
    ├── audio/manifest.json    # kết quả TTS từng câu
    └── logs/pipeline.jsonl
```
