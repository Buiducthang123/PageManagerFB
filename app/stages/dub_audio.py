from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from .. import config
from .. import jobs as jobs_mod

DEFAULT_MODEL = "htdemucs"
ROOT_DIR = Path(__file__).resolve().parent.parent.parent

try:
    DEMUCS_TIMEOUT_S = int(os.environ.get("DEMUCS_TIMEOUT_S", "1800").strip() or "1800")
except ValueError:
    DEMUCS_TIMEOUT_S = 1800


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
    job: Optional[jobs_mod.JobState] = None,
) -> Path:
    """Tách nhạc nền/tiếng động khỏi thoại gốc bằng Demucs (htdemucs), lưu
    output_path — dùng để ghép cùng giọng đọc TTS ở bước dựng CapCut thay vì
    tắt hẳn audio gốc (mất luôn nhạc nền + SFX).

    Chạy Demucs trong subprocess riêng (`_demucs_worker.py`), KHÔNG import
    trực tiếp trong process chính — đã ghi nhận Demucs (CPU) treo cứng/segfault
    trên máy dev này (nghi liên quan chạy chung process với CUDA Whisper),
    cô lập ra để 1 lần lỗi không kéo sập cả server. `job`, nếu truyền vào, cho
    phép dừng thủ công (`jobs.request_cancel`) kill thẳng tiến trình con này
    ngay lập tức, không cần chờ timeout."""
    if not video_path.exists():
        raise DubAudioError(f"Không thấy video: {video_path}")

    config.apply_whisper_cache_env()  # đảm bảo HF_HOME đã trỏ sang D trước khi demucs tải model

    raw_wav = output_path.parent / "_raw_audio.wav"
    if on_progress:
        on_progress(0, 1, "trích audio gốc")
    _extract_wav(video_path, raw_wav)

    if on_progress:
        on_progress(0, 1, "tách nhạc nền (demucs, có thể mất vài phút)")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.stages._demucs_worker", str(raw_wav), str(output_path)],
        cwd=str(ROOT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if job is not None:
        job.process = proc
    stdout = stderr = ""
    cancelled = False
    try:
        start = time.time()
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=0.5)
                # `request_cancel()` (route /cancel) có thể đã tự kill thẳng
                # `job.process` từ thread khác — communicate() lúc đó trả về
                # BÌNH THƯỜNG (không raise TimeoutExpired) vì process đã chết
                # rồi. Phải tự check cancel_event ở đây nữa, không chỉ trong
                # nhánh TimeoutExpired bên dưới, nếu không sẽ bị hiểu nhầm
                # thành "demucs tự lỗi/thoát" thay vì "bị dừng theo yêu cầu".
                cancelled = job is not None and job.cancel_event.is_set()
                break
            except subprocess.TimeoutExpired:
                if job is not None and job.cancel_event.is_set():
                    proc.kill()
                    stdout, stderr = proc.communicate()
                    cancelled = True
                    break
                if time.time() - start > DEMUCS_TIMEOUT_S:
                    proc.kill()
                    proc.communicate()
                    raise DubAudioError(
                        f"Demucs treo/chạy quá {DEMUCS_TIMEOUT_S}s — đã huỷ tiến trình con. "
                        f"Thử lại, hoặc tăng DEMUCS_TIMEOUT_S trong .env nếu video quá dài."
                    )
    finally:
        if job is not None:
            job.process = None
        raw_wav.unlink(missing_ok=True)

    if cancelled:
        raise jobs_mod.JobCancelled("Đã dừng theo yêu cầu người dùng")

    if proc.returncode != 0 or not output_path.exists():
        detail = (stderr or stdout or "").strip()[-1000:]
        raise DubAudioError(f"Demucs lỗi (subprocess exit={proc.returncode}): {detail}")

    if on_progress:
        on_progress(1, 1, "xong")
    return output_path
