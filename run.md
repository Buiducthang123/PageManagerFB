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
3. **Whisper (Stage 2)** — bấm chạy, tự nhận diện giọng nói tiếng Trung → `sub_zh.srt`. Lần đầu chạy sẽ tự tải model Whisper (`medium` mặc định) về `workspace/models/whisper/`, có thể mất vài phút tuỳ mạng.
4. **Gemini dịch (Stage 3)** — bấm chạy, dịch zh→vi + tạo `entity_dict.json` (từ điển tên riêng nhất quán) → `sub_vi.srt`.
5. Theo dõi tiến độ job và log trực tiếp trên giao diện (poll qua `/api/projects/{id}/jobs/{stage}`).

Sau bước 4, pipeline dừng ở mức code hiện tại — các bước **TTS (capcut-tts-api)**, **dựng bản nháp CapCut (pyCapCut)**, **review thủ công**, và **upload Facebook** mô tả trong [main.md](main.md) là kế hoạch, chưa có route/API tương ứng trong `app/`.

## Xử lý sự cố thường gặp

| Vấn đề | Nguyên nhân / cách xử lý |
|---|---|
| `ffprobe: command not found` khi upload | Chưa cài FFmpeg hoặc chưa có trong PATH — xem [install.md](install.md) |
| Whisper báo lỗi CUDA / không thấy GPU | Tự động fallback CPU. Có thể ép cứng bằng `WHISPER_DEVICE=cpu` trong `.env` |
| Gemini lỗi quota / 429 | Free tier có giới hạn request/ngày và RPM (~15 RPM) — đợi hoặc đổi model trong Settings |
| Port 8001 hoặc 5175 đã bị chiếm | Đổi `--port` khi chạy uvicorn, và sửa `server.port` + `proxy` trong `frontend/vite.config.ts` cho khớp |
| Model Whisper tải chậm/lỗi ổ C đầy | Cache đã được trỏ sang `workspace/models/whisper` (ổ chứa code) qua `apply_whisper_cache_env()` trong `app/config.py` — kiểm tra biến `WHISPER_CACHE_DIR` nếu cần đổi ổ khác |

## Vị trí dữ liệu

```
workspace/
├── index.json                 # danh sách project
├── models/whisper/            # cache model Whisper (HuggingFace)
└── projects/<project_id>/
    ├── project.json           # trạng thái từng stage
    ├── video.<ext>
    ├── sub_zh.srt
    ├── sub_vi.srt
    ├── entity_dict.json
    └── logs/pipeline.jsonl
```
