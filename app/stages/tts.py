from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional

import requests
from loguru import logger

from ..utils.srt import Cue, load_srt

_client = None

# Voice.json không được đóng gói theo khi cài package qua pip (nằm ngoài
# capcut_tts_api/), nên client.list_voices()/resolve_voice() trả rỗng và sẽ âm
# thầm dùng sai resource_id mặc định nếu không tự truyền. Tự giữ 1 danh sách
# (voice_type, resource_id) đã xác minh từ Voice.json để không phụ thuộc catalog.
#
# Chỉ giữ giọng platform "sami"/"uranus" (ByteDance) — request builder của thư
# viện hard-code platform="sami" cho mọi voice, nên các giọng "...Neural" kiểu
# Azure (vi-VN-HoaiMyNeural, vi-VN-NamMinhNeural) luôn lỗi TTSInvalidSpeaker
# (err_code 40402004) dù có trong Voice.json — đã tự test trực tiếp API xác nhận.
#
# Toàn bộ 22 giọng dưới đây đã tự gọi API thật xác nhận status="succeed" (chỉ
# loại 2 giọng Neural ở trên). Ghi chú: "Review Phim 4" và "Giọng Nam Trầm"
# dùng CHUNG resource_id trong Voice.json (khả năng lỗi catalog, có thể ra
# cùng 1 giọng thật) — tương tự "Review Phim 2" và "Bản Tin 1". Nên tự
# "Nghe thử" trước khi chọn nếu cần phân biệt rõ.
VOICES: list[dict[str, str]] = [
    {"id": "BV421_vivn_streaming", "label": "Nhỏ Ngọt Ngào — nữ truyền cảm, narrator chính (documentary)", "resource_id": "7252594014782755330"},
    {"id": "BV074_streaming", "label": "Cô Gái Hoạt Ngôn — vlog, năng động", "resource_id": "7102355709945188865"},
    {"id": "multi_female_sisi_uranus_bigtts", "label": "Bản Tin nữ — tin tức, sự kiện/số liệu", "resource_id": "7637455857285860629"},
    {"id": "multi_male_felipe_uranus_bigtts", "label": "Giọng Nam Trầm — dark history, huyền bí", "resource_id": "7637456729696996628"},
    {"id": "vi_female_huong", "label": "Giọng Nữ Phổ Thông", "resource_id": "7264854897953083905"},
    {"id": "BV074_streaming_dsp", "label": "Giọng Bé — trẻ em", "resource_id": "7550087831092251920"},
    {"id": "BV075_streaming_vibrato_dsp", "label": "Việt Méo — hiệu ứng rung giọng", "resource_id": "7569450639810465040"},
    {"id": "BV562_streaming", "label": "Mai — nữ", "resource_id": "7483736254694035984"},
    {"id": "multi_female_yangguangnv_uranus_bigtts", "label": "Ban Mai — nữ tươi sáng", "resource_id": "7637456432522218773"},
    {"id": "multi_female_richgirl_uranus_bigtts", "label": "Review Phim new — nữ", "resource_id": "7637460351541447956"},
    {"id": "multi_female_quanweinv_uranus_bigtts", "label": "Bản Tin 1 — nữ", "resource_id": "7637458743197732117"},
    {"id": "multi_female_stokie_uranus_bigtts", "label": "Review Phim 4 — trùng resource_id với Giọng Nam Trầm, kiểm lại", "resource_id": "7637456729696996628"},
    {"id": "multi_female_daqi_uranus_bigtts", "label": "Review Phim 3 — nữ", "resource_id": "7637451983389019409"},
    {"id": "multi_female_xyf04auto_uranus_bigtts", "label": "Review Phim 2 — trùng resource_id với Bản Tin 1, kiểm lại", "resource_id": "7637458743197732117"},
    {"id": "multi_female_kiwi_uranus_bigtts", "label": "Sunny Idol — nữ trẻ trung", "resource_id": "7637457995882089749"},
    {"id": "BV075_streaming_demon_dsp", "label": "Kenny Đại Đế — hiệu ứng ma quái, kịch tính", "resource_id": "7569442422665661712"},
    {"id": "BV075_streaming_robot_dsp", "label": "Robot VN — hiệu ứng robot", "resource_id": "7538698409633516816"},
    {"id": "multi_female_peiqi_uranus_bigtts", "label": "Giọng Gái Mới Lớn — nữ trẻ", "resource_id": "7637458789033151751"},
    {"id": "multi_female_xinwenjieshuo_uranus_bigtts", "label": "Nam bản tin — thuyết minh tin tức", "resource_id": "7637455039719640327"},
    {"id": "multi_female_tianmeijieshuo_uranus_bigtts", "label": "Quên Tên Tự Test — nữ", "resource_id": "7637460417295469832"},
    {"id": "BV075_streaming", "label": "Thanh Niên Tự Tin — nam trẻ", "resource_id": "7102355803792740865"},
    {"id": "BV560_streaming", "label": "Alex Đại Đế — nam", "resource_id": "7483736167565758992"},
]
_VOICE_RESOURCE_IDS = {v["id"]: v["resource_id"] for v in VOICES}
DEFAULT_VOICE = VOICES[0]["id"]

POLL_INTERVAL_S = 1.5
POLL_TIMEOUT_S = 60.0
REQUEST_GAP_S = 0.6  # nghỉ nhẹ giữa các câu, tránh dồn dập vào API không chính thức
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_S = 3.0  # tăng dần giữa các lần thử lại (3s, 6s, ...) — lỗi gặp
# thật (ExceededConcurrentLimit, "call sami response is empty") đều tạm thời

PREVIEW_TEXT = "Xin chào, đây là giọng đọc thử nghiệm cho video của bạn."


class TTSError(RuntimeError):
    pass


def _get_client():
    global _client
    if _client is None:
        from capcut_tts_api import CapCutClient

        _client = CapCutClient()
    return _client


def _synthesize(text: str, voice: str) -> tuple[bytes, int]:
    """Trả về (audio_bytes, duration_ms).

    Không dùng client.generate_speech(wait=True) — thư viện có bug: API trả
    status="succeed" nhưng code chỉ check == "success" nên luôn timeout dù
    task đã xong. Tự tạo task + poll thủ công, và tự truyền resource_id vì
    catalog Voice.json không có sẵn sau khi cài qua pip.
    """
    client = _get_client()
    resource_id = _VOICE_RESOURCE_IDS.get(voice)
    create_res = client.create_tts_task(texts=text, voice=voice, resource_id=resource_id, rate="1.0")
    tasks = (create_res.get("data") or {}).get("tasks") or []
    if not tasks:
        raise TTSError(f"CapCut TTS không trả về task: {create_res}")
    task_id, token = tasks[0]["id"], tasks[0]["token"]

    start = time.time()
    while time.time() - start < POLL_TIMEOUT_S:
        q = client.query_tts_task(task_id, token)
        q_tasks = (q.get("data") or {}).get("tasks") or []
        status = q_tasks[0].get("status") if q_tasks else None
        if status == "succeed":
            payload = json.loads(q_tasks[0]["payload"])
            subs = payload.get("audio_subtitles") or []
            if not subs or not subs[0].get("speech_url"):
                raise TTSError(f"CapCut TTS không có speech_url: {payload}")
            sub = subs[0]
            resp = requests.get(sub["speech_url"], timeout=30)
            resp.raise_for_status()
            return resp.content, int(sub.get("duration") or 0)
        if status == "failed":
            raise TTSError(f"CapCut TTS task failed: {q}")
        time.sleep(POLL_INTERVAL_S)
    raise TTSError(f"CapCut TTS timeout sau {POLL_TIMEOUT_S:.0f}s (task_id={task_id})")


def _synthesize_with_retry(text: str, voice: str) -> tuple[bytes, int]:
    """Gọi `_synthesize`, tự thử lại tối đa `RETRY_ATTEMPTS` lần nếu lỗi —
    2 lỗi thật đã gặp (ExceededConcurrentLimit, "call sami response is
    empty") đều là lỗi tạm thời phía CapCut, thử lại sau vài giây thường
    qua ngay."""
    last_err: TTSError | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return _synthesize(text, voice)
        except TTSError as err:
            last_err = err
            if attempt < RETRY_ATTEMPTS:
                wait = RETRY_BACKOFF_S * attempt
                logger.warning("TTS lỗi (lần {}/{}), thử lại sau {:.0f}s: {}", attempt, RETRY_ATTEMPTS, wait, err)
                time.sleep(wait)
    assert last_err is not None
    raise last_err


def preview_voice(voice: str, text: str = PREVIEW_TEXT) -> bytes:
    """Đọc thử 1 câu mẫu — dùng để nghe trước khi chạy TTS cho cả video."""
    if voice not in _VOICE_RESOURCE_IDS:
        raise TTSError(f"Không rõ giọng đọc: {voice}")
    audio_bytes, _ = _synthesize(text, voice)
    return audio_bytes


def _synthesize_cues(
    cues: list[Cue],
    output_dir: Path,
    voice: str,
    on_progress: Optional[Callable[[int, int, str], None]],
) -> list[dict]:
    """Sinh audio TTS cho từng cue, lưu audio/segment_NNN.mp3, trả về manifest
    [{id, start, end, path, duration_ms, error}] — path=None nếu câu rỗng/lỗi."""
    output_dir.mkdir(parents=True, exist_ok=True)
    total = len(cues)
    manifest: list[dict] = []

    for i, cue in enumerate(cues, 1):
        text = (cue.text or "").strip()
        entry = {"id": cue.id, "start": cue.start, "end": cue.end, "path": None, "duration_ms": 0}
        if not text:
            manifest.append(entry)
            if on_progress:
                on_progress(i, total, f"câu {cue.id} rỗng, bỏ qua")
            continue
        try:
            audio_bytes, duration_ms = _synthesize_with_retry(text, voice)
            out_path = output_dir / f"segment_{cue.id:03d}.mp3"
            out_path.write_bytes(audio_bytes)
            entry["path"] = out_path.name
            entry["duration_ms"] = duration_ms
        except TTSError as err:
            logger.warning("TTS lỗi câu {} (đã thử lại {} lần): {}", cue.id, RETRY_ATTEMPTS, err)
            entry["error"] = str(err)
        manifest.append(entry)
        if on_progress:
            on_progress(i, total, f"câu {cue.id}/{total}")
        time.sleep(REQUEST_GAP_S)

    return manifest


def retry_failed_segments(
    project_root: Path,
    voice: str = DEFAULT_VOICE,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> tuple[Path, list[dict]]:
    """Chỉ tạo lại audio cho những câu bị lỗi/rỗng ở lần chạy TTS trước —
    đọc `audio/manifest.json` + `sub_vi.srt` hiện có, sửa tại chỗ, không đụng
    tới các câu đã thành công (đỡ tốn quota gọi lại cả video)."""
    audio_dir = project_root / "audio"
    manifest_path = audio_dir / "manifest.json"
    if not manifest_path.exists():
        raise TTSError("Chưa chạy TTS lần nào — chạy TTS trước.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    vi_cues = {c.id: c for c in load_srt(project_root / "sub_vi.srt")}
    failed = [e for e in manifest if e.get("error") and vi_cues.get(e["id"]) and (vi_cues[e["id"]].text or "").strip()]

    total = len(failed) or 1
    for i, entry in enumerate(failed, 1):
        cue = vi_cues[entry["id"]]
        try:
            audio_bytes, duration_ms = _synthesize_with_retry(cue.text.strip(), voice)
            out_path = audio_dir / f"segment_{cue.id:03d}.mp3"
            out_path.write_bytes(audio_bytes)
            entry["path"] = out_path.name
            entry["duration_ms"] = duration_ms
            entry.pop("error", None)
        except TTSError as err:
            logger.warning("Retry TTS câu {} vẫn lỗi: {}", cue.id, err)
            entry["error"] = str(err)
        if on_progress:
            on_progress(i, total, f"retry câu {entry['id']} ({i}/{len(failed)})")
        time.sleep(REQUEST_GAP_S)

    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path, manifest


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
