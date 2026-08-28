from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Optional

from .. import config

DEFAULT_MODEL = "htdemucs"


class DubAudioError(RuntimeError):
    pass


def _extract_wav(video_path: Path, out_wav: Path) -> None:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-acodec", "pcm_s16le", "-ar", "44100", str(out_wav)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not out_wav.exists():
        raise DubAudioError(f"ffmpeg trích audio gốc lỗi: {result.stderr[-500:]}")


def extract_background(
    video_path: Path,
    output_path: Path,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> Path:
    """Tách nhạc nền/tiếng động khỏi thoại gốc bằng Demucs (htdemucs), lưu
    output_path — dùng để ghép cùng giọng đọc TTS ở bước dựng CapCut thay vì
    tắt hẳn audio gốc (mất luôn nhạc nền + SFX)."""
    if not video_path.exists():
        raise DubAudioError(f"Không thấy video: {video_path}")

    config.apply_whisper_cache_env()  # đảm bảo HF_HOME đã trỏ sang D trước khi demucs tải model
    from demucs.api import Separator, save_audio

    raw_wav = output_path.parent / "_raw_audio.wav"
    if on_progress:
        on_progress(0, 1, "trích audio gốc")
    _extract_wav(video_path, raw_wav)

    if on_progress:
        on_progress(0, 1, "tách nhạc nền (demucs, có thể mất vài phút)")
    try:
        separator = Separator(model=DEFAULT_MODEL, device="cpu", progress=False)
        origin, stems = separator.separate_audio_file(raw_wav)
        vocals = stems["vocals"]
        background = origin - vocals
        output_path.parent.mkdir(parents=True, exist_ok=True)
        save_audio(background, str(output_path), samplerate=separator.samplerate)
    except Exception as err:
        raise DubAudioError(f"Demucs lỗi: {err}") from err
    finally:
        raw_wav.unlink(missing_ok=True)

    if on_progress:
        on_progress(1, 1, "xong")
    return output_path
