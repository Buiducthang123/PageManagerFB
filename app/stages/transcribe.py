from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from .. import config
from ..utils.srt import Cue, format_ts, parse_ts, write_srt

_model = None
_model_device: Optional[str] = None


class TranscribeError(RuntimeError):
    pass


def _nvidia_bin_dirs() -> list[Path]:
    import sysconfig

    root = Path(sysconfig.get_path("purelib")) / "nvidia"
    if not root.is_dir():
        return []
    return [p for p in root.glob("*/bin") if p.is_dir()]


def _preload_cuda_dlls() -> None:
    """ctranslate2 cần cublas64_12.dll. CUDA Toolkit thường chưa cài —
    pip `nvidia-cublas-cu12` để DLL trong site-packages."""
    dirs = _nvidia_bin_dirs()
    extra = os.pathsep.join(str(d) for d in dirs)
    if extra:
        os.environ["PATH"] = extra + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        for d in dirs:
            try:
                os.add_dll_directory(str(d))
            except OSError:
                continue
    if dirs:
        logger.info("CUDA DLL dirs: {}", ", ".join(str(d) for d in dirs))


def _cuda_ok() -> bool:
    try:
        import ctranslate2

        return "int8" in ctranslate2.get_supported_compute_types("cuda")
    except Exception:
        return False


def _load_model():
    global _model, _model_device
    if _model is not None:
        return _model

    cache = config.apply_whisper_cache_env()
    _preload_cuda_dlls()
    from faster_whisper import WhisperModel

    preferred = (os.environ.get("WHISPER_DEVICE") or config.WHISPER_DEVICE).strip().lower()
    model_name = (os.environ.get("WHISPER_MODEL") or config.WHISPER_MODEL).strip()
    attempts: list[tuple[str, str]] = []
    if preferred in ("cuda", "auto") and _cuda_ok():
        attempts.append(("cuda", "int8"))
    attempts.append(("cpu", "int8"))

    last_err: Exception | None = None
    for device, compute in attempts:
        try:
            logger.info(
                "Tải Whisper {} trên {} ({}) cache={}",
                model_name,
                device,
                compute,
                cache,
            )
            _model = WhisperModel(
                model_name,
                device=device,
                compute_type=compute,
                download_root=str(cache),
            )
            _model_device = device
            logger.info("Whisper sẵn sàng trên {}", device)
            return _model
        except Exception as err:
            last_err = err
            logger.warning("Không tải được Whisper trên {}: {}", device, err)
            _model = None
            _model_device = None
    raise TranscribeError(
        f"Không khởi tạo được Whisper: {last_err}. "
        f"Cache đang để ở {cache} (ổ D). Ổ C đầy sẽ không còn dùng để tải model."
    )


WINDOW_S = 25.0
OVERLAP_S = 6.0
STEP_S = WINDOW_S - OVERLAP_S
SAMPLE_RATE = 16000

# Whisper đôi khi tự dừng decode giữa chừng 1 khối 25s dù audio phía sau vẫn
# có thoại thật (đã xác nhận bằng debug trực tiếp: 1 khối chỉ ra segment cho
# nửa đầu rồi im, khối kế tiếp thì trust-zone lại bắt đầu sau đó vài giây —
# để lại "vùng chết" không khối nào bao phủ). Coi khoảng trống giữa 2 cue
# liền nhau lớn hơn ngần này là bất thường (phân biệt với khoảng lặng tự
# nhiên ngắn) và decode lại riêng đúng đoạn đó.
GAP_FILL_THRESHOLD_S = 3.0
# Decode lại đúng bằng đệm sát mép khoảng trống (~10-12s) đã thử và thường bị
# CỤT GIỮA CHỪNG dù cùng audio, cùng model — chunk phải neo đủ dài, gần
# WINDOW_S, Whisper mới decode trọn vẹn (đã tự test trực tiếp xác nhận: chunk
# ngắn ra rỗng, chunk 25s neo lùi 10s trước khoảng trống ra đúng nội dung).
GAP_FILL_LEAD_S = 10.0


def _dedup_cues(raw: list[tuple[float, float, str]]) -> list[Cue]:
    """Loại cue trùng lặp sinh ra ở vùng overlap giữa 2 khối kề nhau."""
    kept: list[tuple[float, float, str]] = []
    for s, e, text in raw:
        if kept:
            ps, pe, _ = kept[-1]
            overlap = min(e, pe) - max(s, ps)
            dur = min(e - s, pe - ps)
            if dur > 0 and overlap / dur > 0.5:
                continue
        kept.append((s, e, text))
    return [Cue(id=i, start=format_ts(s), end=format_ts(e), text=text) for i, (s, e, text) in enumerate(kept, 1)]


def _fill_gaps(
    model,
    audio,
    kwargs: dict,
    cues: list[Cue],
    total_s: float,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> list[Cue]:
    """Dò khoảng trống bất thường giữa 2 cue liền nhau (kể cả đầu/cuối video)
    và decode lại riêng đúng đoạn đó — xem giải thích ở GAP_FILL_THRESHOLD_S."""
    boundaries = [(format_ts(0.0), cues[0].start if cues else format_ts(total_s))]
    for i in range(len(cues) - 1):
        boundaries.append((cues[i].end, cues[i + 1].start))
    if cues:
        boundaries.append((cues[-1].end, format_ts(total_s)))

    extra: list[tuple[float, float, str]] = []
    for start_ts, end_ts in boundaries:
        gap_start, gap_end = parse_ts(start_ts), parse_ts(end_ts)
        if gap_end - gap_start < GAP_FILL_THRESHOLD_S:
            continue
        s = max(0.0, gap_start - GAP_FILL_LEAD_S)
        e = min(total_s, s + WINDOW_S)
        if e - s < WINDOW_S:  # gần cuối video, lùi điểm bắt đầu để vẫn đủ 1 khối dài
            s = max(0.0, e - WINDOW_S)
        chunk = audio[int(s * SAMPLE_RATE) : int(e * SAMPLE_RATE)]
        segments, _ = model.transcribe(chunk, **kwargs)
        for seg in segments:
            text = (seg.text or "").strip()
            if not text:
                continue
            s_abs, e_abs = seg.start + s, seg.end + s
            if s_abs >= gap_start - 1.0 and e_abs <= gap_end + 1.0:
                extra.append((s_abs, e_abs, text))
        if on_progress:
            on_progress(0, 0, f"vá khoảng trống {gap_start:.0f}s-{gap_end:.0f}s")

    if not extra:
        return cues

    logger.info("Whisper: vá được {} câu ở các khoảng trống bất thường", len(extra))
    all_raw = [(parse_ts(c.start), parse_ts(c.end), c.text) for c in cues] + extra
    all_raw.sort(key=lambda c: c[0])
    return _dedup_cues(all_raw)


def transcribe_video(
    video_path: Path,
    output_srt: Path,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> tuple[list[Cue], str]:
    if not video_path.exists():
        raise TranscribeError(f"Không thấy video: {video_path}")

    model = _load_model()
    from faster_whisper.audio import decode_audio

    language = (os.environ.get("WHISPER_LANGUAGE") or config.WHISPER_LANGUAGE).strip().lower()
    kwargs = dict(
        language=None if language == "auto" else language,
        beam_size=5,
        condition_on_previous_text=False,
        # Không dùng vad_filter: faster-whisper có bug đã biết (SYSTRAN/faster-whisper#1355,
        # #1270) tự cắt bỏ nội dung khi VAD gộp 1 segment dài hơn ~30s (giới hạn kiến trúc
        # Whisper), và coi nhầm thoại bị nhạc nền lớn đè lên là "không phải giọng nói". Thay
        # vào đó tự chia audio thành khối cố định ngắn hơn 30s (có overlap để không cắt cụt
        # câu ở ranh giới) rồi để Whisper decode toàn bộ từng khối.
        vad_filter=False,
        word_timestamps=True,
        hallucination_silence_threshold=2.0,
    )

    device = _model_device or "?"
    audio = decode_audio(str(video_path), sampling_rate=SAMPLE_RATE)
    total_s = len(audio) / SAMPLE_RATE

    offsets: list[float] = []
    o = 0.0
    while o < total_s:
        offsets.append(o)
        o += STEP_S
    if not offsets:
        offsets = [0.0]

    n_window = int(WINDOW_S * SAMPLE_RATE)
    raw_cues: list[tuple[float, float, str]] = []
    lang_label: Optional[str] = None

    for idx, offset in enumerate(offsets):
        start_sample = int(offset * SAMPLE_RATE)
        end_sample = min(len(audio), start_sample + n_window)
        chunk = audio[start_sample:end_sample]
        segments, info = model.transcribe(chunk, **kwargs)
        if lang_label is None:
            lang_label = f"{info.language} ({(info.language_probability or 0):.0%})"

        trust_start = offset + (0.0 if idx == 0 else OVERLAP_S / 2)
        trust_end = offset + WINDOW_S - (0.0 if idx == len(offsets) - 1 else OVERLAP_S / 2)
        for seg in segments:
            text = (seg.text or "").strip()
            if not text:
                continue
            s_abs, e_abs = seg.start + offset, seg.end + offset
            center = (s_abs + e_abs) / 2
            if trust_start <= center <= trust_end:
                raw_cues.append((s_abs, e_abs, text))

        if on_progress:
            done = min(int(offset + WINDOW_S), int(total_s))
            on_progress(done, max(1, int(total_s)), f"{device} · {done}s/{int(total_s)}s")

    raw_cues.sort(key=lambda c: c[0])
    cues = _dedup_cues(raw_cues)
    cues = _fill_gaps(model, audio, kwargs, cues, total_s, on_progress=on_progress)
    lang = lang_label or "?"
    logger.info("Whisper language={} duration={:.1f}s device={} câu={}", lang, total_s, device, len(cues))

    output_srt.parent.mkdir(parents=True, exist_ok=True)
    write_srt(output_srt, cues)
    return cues, f"{lang} · {device}"
