from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from .. import config
from ..utils.srt import Cue, load_srt

_model = None

SAMPLE_RATE = 48000  # v3 Turbo (mode mặc định) — 48kHz mono float32

# 23 giọng preset thật, lấy trực tiếp từ `Vieneu().list_preset_voices()` (không
# gọi lại lúc runtime để tránh phải tải/khởi tạo cả model chỉ để liệt kê giọng
# cho trang Settings — id ở đây là phần thứ 2 của mỗi tuple trả về, dùng thẳng
# được cho tham số `voice=` của `.infer()`/`.infer_batch()`).
VOICES: list[dict[str, str]] = [
    {"id": "Minh Quân", "label": "Minh Quân — Nam · Bắc · Phong cách tự nhiên"},
    {"id": "Minh Đức", "label": "Minh Đức — Nam · Bắc · Phong cách tin tức"},
    {"id": "Phạm Tuyên", "label": "Phạm Tuyên — Nam · Bắc · Phong cách tự nhiên"},
    {"id": "Thái Sơn", "label": "Thái Sơn — Nam · Nam · Phong cách kể chuyện"},
    {"id": "Xuân Vĩnh", "label": "Xuân Vĩnh — Nam · Bắc · Phong cách tự nhiên"},
    {"id": "Thanh Bình", "label": "Thanh Bình — Nam · Bắc · Phong cách kể chuyện"},
    {"id": "Trúc Ly", "label": "Trúc Ly — Nữ · Bắc · Phong cách tự nhiên"},
    {"id": "Ngọc Linh", "label": "Ngọc Linh — Nữ · Bắc · Phong cách kể chuyện"},
    {"id": "Đoan Trang", "label": "Đoan Trang — Nữ · Bắc · Phong cách tự nhiên"},
    {"id": "Mai Anh", "label": "Mai Anh — Nữ · Bắc · Phong cách tin tức"},
    {"id": "Thục Đoan", "label": "Thục Đoan — Nữ · Nam · Phong cách kể chuyện"},
    {"id": "Minh Triết", "label": "Minh Triết — Nam · Nam · Phong cách tin tức"},
    {"id": "Thùy Dung", "label": "Thùy Dung — Nữ · Nam · Phong cách tin tức"},
    {"id": "Quang Sơn", "label": "Quang Sơn — Nam · Trung · Phong cách tự nhiên"},
    {"id": "Ngọc Trân", "label": "Ngọc Trân — Nữ · Trung · Phong cách tự nhiên"},
    {"id": "Mỹ Duyên", "label": "Mỹ Duyên — Nữ · Nam · Phong cách đọc truyện"},
    {"id": "Quỳnh Anh", "label": "Quỳnh Anh — Nữ · Bắc · Phong cách đọc truyện"},
    {"id": "Đức Trí", "label": "Đức Trí — Nam · Nam · Phong cách đọc truyện"},
    {"id": "Kim Thanh", "label": "Kim Thanh — Nữ · Nam · Phong cách đọc truyện"},
    {"id": "Ngọc Huyền", "label": "Ngọc Huyền — Nữ · Bắc · Giọng đọc tự nhiên"},
    {"id": "Adam", "label": "Adam — Nam · Nam · Giọng đọc tự nhiên"},
    {"id": "Mạnh Dũng", "label": "Mạnh Dũng — Nam · Bắc · Phong cách tự nhiên"},
    {"id": "Anh Khôi", "label": "Anh Khôi — Nam · Bắc · Phong cách kể chuyện"},
]
_VOICE_IDS = {v["id"] for v in VOICES}
DEFAULT_VOICE = "Minh Quân"

# Gộp nhiều câu / lần gọi infer_batch() (được thiết kế để tăng tốc theo lô) —
# nhưng không gộp quá lớn để nút Dừng vẫn có tác dụng trong thời gian chấp
# nhận được (chỉ dừng được SAU khi cả batch đang chạy xong).
BATCH_SIZE = 6

PREVIEW_TEXT = "Xin chào, đây là giọng đọc thử nghiệm cho video của bạn."


class TTSError(RuntimeError):
    pass


def _load_model():
    global _model
    if _model is not None:
        return _model
    try:
        from vieneu import Vieneu
    except ImportError as err:
        raise TTSError("Chưa cài package `vieneu` (pip install vieneu)") from err

    device = (os.environ.get("VIENEU_DEVICE") or config.VIENEU_DEVICE).strip().lower()
    try:
        _model = Vieneu(mode="v3turbo", device=device)
    except Exception as err:
        raise TTSError(f"Không khởi tạo được VieNeu-TTS: {err}") from err
    logger.info("VieNeu-TTS sẵn sàng trên {}", device)
    return _model


def _duration_ms(audio) -> int:
    return int(len(audio) / SAMPLE_RATE * 1000)


def _save_mp3(audio, out_path: Path) -> None:
    """`.save()` của VieNeu chỉ ghi được wav/flac theo đuôi file — tự convert
    sang mp3 bằng ffmpeg để khớp định dạng `segment_NNN.mp3` toàn bộ pipeline
    (assemble/dub_audio) đang kỳ vọng."""
    model = _load_model()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        model.save(audio, str(tmp_path))
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(tmp_path), "-codec:a", "libmp3lame", "-qscale:a", "2", str(out_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not out_path.exists():
            raise TTSError(f"ffmpeg chuyển mp3 lỗi: {(result.stderr or '')[-300:]}")
    finally:
        tmp_path.unlink(missing_ok=True)


def _synthesize_batch(texts: list[str], voice: str) -> list:
    model = _load_model()
    try:
        return model.infer_batch(texts, voice=voice)
    except Exception as err:
        raise TTSError(f"VieNeu-TTS lỗi khi tổng hợp theo lô: {err}") from err


def _synthesize_one(text: str, voice: str):
    model = _load_model()
    try:
        return model.infer(text, voice=voice)
    except Exception as err:
        raise TTSError(f"VieNeu-TTS lỗi: {err}") from err


def preview_voice(voice: str, text: str = PREVIEW_TEXT) -> bytes:
    """Đọc thử 1 câu mẫu — dùng để nghe trước khi chạy TTS cho cả video."""
    if voice not in _VOICE_IDS:
        raise TTSError(f"Không rõ giọng đọc: {voice}")
    audio = _synthesize_one(text, voice)
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        _save_mp3(audio, tmp_path)
        return tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)


def _synthesize_cues(
    cues: list[Cue],
    output_dir: Path,
    voice: str,
    on_progress: Optional[Callable[[int, int, str], None]],
) -> list[dict]:
    """Sinh audio TTS theo lô (BATCH_SIZE câu/lần gọi infer_batch — đúng cách
    VieNeu-TTS được thiết kế để tăng tốc, khác hẳn CapCut TTS vốn là network
    call nên dùng thread pool song song). Lưu audio/segment_NNN.mp3, trả về
    manifest [{id, start, end, path, duration_ms, text, error?}] theo đúng
    thứ tự cue gốc — path=None nếu câu rỗng/lỗi."""
    output_dir.mkdir(parents=True, exist_ok=True)
    total = len(cues)
    results: dict[int, dict] = {}
    done_count = 0

    def _empty_entry(cue: Cue) -> dict:
        return {"id": cue.id, "start": cue.start, "end": cue.end, "path": None, "duration_ms": 0, "text": (cue.text or "").strip()}

    non_empty = [c for c in cues if (c.text or "").strip()]
    for i in range(0, len(non_empty), BATCH_SIZE):
        batch = non_empty[i : i + BATCH_SIZE]
        texts = [c.text.strip() for c in batch]
        try:
            audios: list = _synthesize_batch(texts, voice)
        except TTSError as err:
            logger.warning("VieNeu batch lỗi ({} câu), thử lại từng câu riêng: {}", len(batch), err)
            audios = []
            for txt in texts:
                try:
                    audios.append(_synthesize_one(txt, voice))
                except TTSError as one_err:
                    audios.append(one_err)

        for cue, audio in zip(batch, audios):
            entry = _empty_entry(cue)
            if isinstance(audio, Exception):
                entry["error"] = str(audio)
                logger.warning("TTS lỗi câu {}: {}", cue.id, audio)
            else:
                try:
                    out_path = output_dir / f"segment_{cue.id:03d}.mp3"
                    _save_mp3(audio, out_path)
                    entry["path"] = out_path.name
                    entry["duration_ms"] = _duration_ms(audio)
                except TTSError as err:
                    entry["error"] = str(err)
                    logger.warning("TTS lỗi lưu file câu {}: {}", cue.id, err)
            results[cue.id] = entry
            done_count += 1
            if on_progress:
                on_progress(done_count, total, f"câu {done_count}/{total}")

    for cue in cues:
        if cue.id not in results:
            results[cue.id] = _empty_entry(cue)

    return [results[cue.id] for cue in cues]


def retry_failed_segments(
    project_root: Path,
    voice: str = DEFAULT_VOICE,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> tuple[Path, list[dict]]:
    """Chỉ tạo lại audio cho những câu bị lỗi/rỗng ở lần chạy TTS trước — xem
    comment tương ứng trong `tts.py` (cùng ý tưởng, khác engine)."""
    audio_dir = project_root / "audio"
    manifest_path = audio_dir / "manifest.json"
    if not manifest_path.exists():
        raise TTSError("Chưa chạy TTS lần nào — chạy TTS trước.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    vi_cues = {c.id: c for c in load_srt(project_root / "sub_vi.srt")}
    failed = [e for e in manifest if e.get("error") and vi_cues.get(e["id"]) and (vi_cues[e["id"]].text or "").strip()]

    total = len(failed) or 1
    done_count = 0
    by_id = {e["id"]: e for e in manifest}

    for i in range(0, len(failed), BATCH_SIZE):
        batch = failed[i : i + BATCH_SIZE]
        cues = [vi_cues[e["id"]] for e in batch]
        texts = [c.text.strip() for c in cues]
        try:
            audios: list = _synthesize_batch(texts, voice)
        except TTSError as err:
            audios = []
            for txt in texts:
                try:
                    audios.append(_synthesize_one(txt, voice))
                except TTSError as one_err:
                    audios.append(one_err)

        for entry, cue, audio in zip(batch, cues, audios):
            if isinstance(audio, Exception):
                entry["error"] = str(audio)
                logger.warning("Retry TTS câu {} vẫn lỗi: {}", cue.id, audio)
            else:
                try:
                    out_path = audio_dir / f"segment_{cue.id:03d}.mp3"
                    _save_mp3(audio, out_path)
                    entry["path"] = out_path.name
                    entry["duration_ms"] = _duration_ms(audio)
                    entry["text"] = cue.text.strip()
                    entry.pop("error", None)
                except TTSError as err:
                    entry["error"] = str(err)
                    logger.warning("Retry TTS câu {} vẫn lỗi: {}", cue.id, err)
            done_count += 1
            if on_progress:
                on_progress(done_count, total, f"retry {done_count}/{len(failed)}")

    manifest = sorted(by_id.values(), key=lambda m: m["id"])
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path, manifest


def tts_single_segment(
    project_root: Path,
    cue_id: int,
    voice: str = DEFAULT_VOICE,
) -> dict:
    """Đọc lại đúng 1 câu (vd. sau khi sửa/dịch lại câu đó) — chỉ ghi đè
    segment_NNN.mp3 + entry tương ứng trong manifest.json, không đụng các câu
    khác."""
    vi_cues = {c.id: c for c in load_srt(project_root / "sub_vi.srt")}
    cue = vi_cues.get(cue_id)
    if cue is None:
        raise TTSError(f"Không tìm thấy câu #{cue_id} trong sub_vi.srt")

    audio_dir = project_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = audio_dir / "manifest.json"
    manifest: list[dict] = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else []
    )
    by_id = {m["id"]: m for m in manifest}

    text = (cue.text or "").strip()
    entry = by_id.get(cue_id) or {"id": cue.id, "start": cue.start, "end": cue.end, "path": None, "duration_ms": 0}
    entry["start"], entry["end"] = cue.start, cue.end
    entry["text"] = text
    entry.pop("error", None)

    if not text:
        entry["path"] = None
        entry["duration_ms"] = 0
    else:
        audio = _synthesize_one(text, voice)
        out_path = audio_dir / f"segment_{cue.id:03d}.mp3"
        _save_mp3(audio, out_path)
        entry["path"] = out_path.name
        entry["duration_ms"] = _duration_ms(audio)

    by_id[cue.id] = entry
    manifest = sorted(by_id.values(), key=lambda m: m["id"])
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return entry


def tts_project(
    project_root: Path,
    voice: str = DEFAULT_VOICE,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> tuple[Path, list[dict]]:
    vi_path = project_root / "sub_vi.srt"
    cues = load_srt(vi_path)
    if not cues:
        raise TTSError("Chưa có sub_vi.srt — chạy Gemini trước.")

    audio_dir = project_root / "audio"
    manifest = _synthesize_cues(cues, audio_dir, voice, on_progress)

    manifest_path = audio_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path, manifest
