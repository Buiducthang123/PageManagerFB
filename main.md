# Reup Video Pipeline — zh → vi

> Dự án tự động hóa reup video tiếng Trung sang tiếng Việt cho OddlyLab.
> Semi-automated: 5 bước tự động + 1 bước human review trong CapCut.

---

## Tổng quan

```
URL video (zh)
    ↓
[AUTO] yt-dlp          → video.mp4
    ↓
[AUTO] Whisper medium  → sub_zh.srt
    ↓
[AUTO] Gemini Flash    → sub_vi.srt + entity_dict.json
    ↓
[AUTO] capcut-tts-api  → segment_*.mp3
    ↓
[AUTO] pyCapCut        → CapCut draft
    ↓
[HUMAN] Review + export → final.mp4
    ↓
[AUTO] Meta Graph API  → Facebook Page
```

---

## Hardware & Môi trường

| Thông số | Giá trị |
|---|---|
| GPU | GTX 1650 — 4GB VRAM |
| OS | Windows |
| Python | 3.9+ |
| Ngôn ngữ đầu vào | Tiếng Trung (Mandarin) |
| Ngôn ngữ đầu ra | Tiếng Việt |

---

## Stage 1 — Tải video

**Tool:** `yt-dlp`

**Hỗ trợ platform:** YouTube · TikTok · Douyin · Facebook · Instagram

**Output:** `video.mp4` + `metadata.json`

```bash
pip install yt-dlp
```

```python
import subprocess

def download_video(url: str, output_dir: str) -> str:
    output_path = f"{output_dir}/video.mp4"
    subprocess.run([
        "yt-dlp",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "-o", output_path,
        url
    ])
    return output_path
```

---

## Stage 2 — Nhận diện giọng nói (zh)

**Tool:** `faster-whisper` — model `medium` (chạy GPU, fit 4GB VRAM)

**Input:** `video.mp4`

**Output:** `sub_zh.srt` (kèm timestamp từng segment)

```bash
pip install faster-whisper
```

```python
from faster_whisper import WhisperModel

def transcribe(video_path: str, output_srt: str):
    model = WhisperModel("medium", device="cuda", compute_type="int8")
    segments, info = model.transcribe(video_path, language="zh", beam_size=5)

    print(f"Detected language: {info.language} ({info.language_probability:.0%})")

    with open(output_srt, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n")
            f.write(f"{format_ts(seg.start)} --> {format_ts(seg.end)}\n")
            f.write(f"{seg.text.strip()}\n\n")

def format_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
```

> **Lưu ý:** Nếu VRAM không đủ, đổi `device="cpu"` — chậm hơn nhưng vẫn chạy được.

---

## Stage 3 — Xử lý LLM (clean + dịch zh→vi)

**Tool:** `Gemini 2.0 Flash` (free API key — 1500 req/ngày)

**Input:** `sub_zh.srt`

**Output:** `sub_vi.srt` + `entity_dict.json`

**LLM làm 3 việc trong 1 lần gọi:**
1. Scan toàn bộ transcript → build entity dict (tên riêng nhất quán)
2. Clean transcript (xóa filler, sửa lỗi Whisper)
3. Dịch zh→vi tự nhiên theo block 10 câu (giữ context)

```bash
pip install google-generativeai
```

```python
import google.generativeai as genai
import json

genai.configure(api_key="YOUR_FREE_GEMINI_KEY")
model = genai.GenerativeModel("gemini-2.0-flash")

def build_entity_dict(full_transcript: str) -> dict:
    prompt = f"""Đọc transcript sau, liệt kê TẤT CẢ tên riêng (người, địa danh, công ty).
Tự chọn 1 cách phiên âm tiếng Việt nhất quán cho mỗi tên.
CHỈ trả về JSON dict, không giải thích.

Transcript:
{full_transcript}"""
    resp = model.generate_content(prompt)
    return json.loads(resp.text.strip().strip("```json").strip("```"))

def translate_block(segments: list, entity_dict: dict) -> list:
    system = f"""Dịch giả chuyên nghiệp zh→vi.
Tên riêng BẮT BUỘC theo dict: {json.dumps(entity_dict, ensure_ascii=False)}
Nhiệm vụ: clean + dịch từng câu. Trả về JSON: [{{"id":1,"text_vi":"..."}}]
CHỈ JSON."""

    user = json.dumps([{"id": s["id"], "text": s["text"]} for s in segments],
                      ensure_ascii=False)
    resp = model.generate_content(f"{system}\n\n{user}",
                                  generation_config={"temperature": 0.1})
    clean = resp.text.strip().strip("```json").strip("```")
    return json.loads(clean)

def process_llm(srt_segments: list) -> list:
    full_text = " ".join([s["text"] for s in srt_segments])
    entity_dict = build_entity_dict(full_text)

    results = []
    for i in range(0, len(srt_segments), 10):  # block 10 câu
        block = srt_segments[i:i+10]
        translated = translate_block(block, entity_dict)
        results.extend(translated)

    return results, entity_dict
```

---

## Stage 4 — Tạo giọng đọc tiếng Việt (TTS)

**Tool:** `capcut-tts-api` (K07VN/capcut-tts-api)

**Input:** `sub_vi.srt` (từng segment)

**Output:** `audio/segment_001.mp3`, `segment_002.mp3`, ...

```bash
git clone https://github.com/K07VN/capcut-tts-api
cd capcut-tts-api
pip install -e .
```

### Giọng tiếng Việt khuyến nghị

| Giọng | Voice Type | Dùng cho |
|---|---|---|
| Nhỏ Ngọt Ngào | `BV421_vivn_streaming` | Documentary, nữ truyền cảm |
| Bản Tin nữ | `multi_female_sisi_uranus_bigtts` | Tin tức, chuyên nghiệp |
| Giọng Nam Trầm | `multi_male_felipe_uranus_bigtts` | Dark history, huyền bí |
| Cô Gái Hoạt Ngôn | `BV074_streaming` | Vlog, năng động |

```python
from capcut_tts_api import CapCutClient
import os

client = CapCutClient()

def tts_segments(segments: list, output_dir: str,
                 voice: str = "BV421_vivn_streaming"):
    os.makedirs(output_dir, exist_ok=True)
    audio_files = []

    for seg in segments:
        output_path = f"{output_dir}/segment_{seg['id']:03d}.mp3"

        result = client.tts(
            texts=seg["text_vi"],
            voice=voice,
            rate="1.0",
            wait=True
        )

        with open(output_path, "wb") as f:
            f.write(result.audio_data)

        audio_files.append(output_path)
        print(f"✅ TTS segment {seg['id']}: {output_path}")

    return audio_files
```

---

## Stage 5 — Dựng bản nháp CapCut

**Tool:** `pyCapCut` (GuanYixuan/pyCapCut)

**Input:** `video.mp4` + `audio/*.mp3` + `sub_vi.srt`

**Output:** CapCut draft (folder trong thư mục draft của CapCut)

```bash
pip install git+https://github.com/GuanYixuan/pyCapCut
```

**Thư mục draft CapCut (Windows):**
```
C:\Users\<tên>\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft\
```

```python
# Assembly logic — tham khảo pyCapCut docs để implement
# Video track: video.mp4
# Audio track: segment_001.mp3, segment_002.mp3, ... (theo timestamp)
# Text track: sub_vi.srt
```

> **Lưu ý:** Format draft của CapCut thay đổi theo version. Kiểm tra CapCut version
> và đối chiếu với pyCapCut changelog trước khi dùng.

---

## Stage 6 — Human review (CapCut)

**Người thực hiện:** Bạn

**Mở CapCut → tìm project → kiểm tra:**

- [ ] TTS có sync đúng timestamp không?
- [ ] Tên riêng có nhất quán không?
- [ ] Câu dịch có tự nhiên không? (sửa những câu kỳ)
- [ ] Thêm nhạc nền (royalty-free) nếu cần
- [ ] Fine-tune subtitle position/style
- [ ] Export `final.mp4` (1080p recommended)

---

## Stage 7 — Upload Facebook Page

**Tool:** Meta Graph API

```python
import requests

def upload_to_facebook(video_path: str, page_id: str,
                       access_token: str, title: str, description: str):
    # Bước 1: Khởi tạo upload session
    init_resp = requests.post(
        f"https://graph.facebook.com/v18.0/{page_id}/videos",
        data={
            "upload_phase": "start",
            "file_size": os.path.getsize(video_path),
            "access_token": access_token
        }
    )
    upload_session_id = init_resp.json()["upload_session_id"]

    # Bước 2: Upload file
    with open(video_path, "rb") as f:
        requests.post(
            f"https://graph-video.facebook.com/v18.0/{page_id}/videos",
            data={
                "upload_phase": "transfer",
                "upload_session_id": upload_session_id,
                "access_token": access_token,
                "start_offset": 0
            },
            files={"video_file_chunk": f}
        )

    # Bước 3: Finish + publish
    requests.post(
        f"https://graph.facebook.com/v18.0/{page_id}/videos",
        data={
            "upload_phase": "finish",
            "upload_session_id": upload_session_id,
            "title": title,
            "description": description,
            "access_token": access_token
        }
    )
    print("✅ Đã upload lên Facebook Page!")
```

---

## Cấu trúc thư mục dự án

```
reup_pipeline/
├── main.py                  # Entry point chạy toàn bộ pipeline
├── config.py                # API keys, paths, voice settings
├── stages/
│   ├── download.py          # Stage 1: yt-dlp
│   ├── transcribe.py        # Stage 2: Whisper
│   ├── translate.py         # Stage 3: Gemini LLM
│   ├── tts.py               # Stage 4: capcut-tts-api
│   ├── assemble.py          # Stage 5: pyCapCut
│   └── upload.py            # Stage 7: Meta API
├── utils/
│   ├── srt_parser.py        # Parse/write .srt files
│   └── audio_utils.py       # Duration check, padding
└── projects/
    └── <project_name>/
        ├── video.mp4
        ├── sub_zh.srt
        ├── sub_vi.srt
        ├── entity_dict.json
        └── audio/
            ├── segment_001.mp3
            ├── segment_002.mp3
            └── ...
```

---

## Dependencies

```bash
pip install yt-dlp
pip install faster-whisper
pip install google-generativeai
pip install requests

# capcut-tts-api (clone từ GitHub)
git clone https://github.com/K07VN/capcut-tts-api
cd capcut-tts-api && pip install -e .

# pyCapCut (clone từ GitHub)
pip install git+https://github.com/GuanYixuan/pyCapCut
```

---

## API Keys cần có

| Service | Lấy ở đâu | Chi phí |
|---|---|---|
| Gemini API | [aistudio.google.com](https://aistudio.google.com) | Free — 1500 req/ngày |
| Meta Graph API | [developers.facebook.com](https://developers.facebook.com) | Free |
| capcut-tts-api | Không cần key | Free (reverse API) |

---

## Accuracy ước tính

| Stage | Độ chính xác |
|---|---|
| Whisper zh transcribe | ~93–95% (Mandarin chuẩn) |
| LLM clean transcript | ~97–98% sau clean |
| LLM dịch zh→vi | ~90–93% câu thông thường |
| Tên riêng (entity dict) | ~98–99% nhất quán |
| **Tổng output dùng được** | **~90–92% không cần sửa** |

---

## Lưu ý quan trọng

- **Bản quyền:** Chỉ dùng clip Creative Commons / Public Domain làm nguồn gốc. Nếu reup clip người khác, Facebook Rights Manager sẽ quét và claim.
- **capcut-tts-api:** Là reverse API — ByteDance có thể đổi endpoint bất cứ lúc nào. Theo dõi repo để cập nhật.
- **Gemini rate limit:** 15 RPM. Nếu video dài nhiều segment, thêm `time.sleep(4)` giữa các block.
- **pyCapCut format:** Kiểm tra CapCut version và pyCapCut changelog trước khi dùng.