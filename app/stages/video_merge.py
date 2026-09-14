from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from .. import jobs as jobs_mod

MERGE_TIMEOUT_S = 3600.0  # video re-encode có thể lâu với nhiều file/độ phân giải cao


class VideoMergeError(RuntimeError):
    pass


def _run_ffmpeg(args: list[str], job: Optional[jobs_mod.JobState]) -> subprocess.CompletedProcess:
    """Chạy ffmpeg qua Popen (không phải subprocess.run) + poll 0.5s — cho
    phép `jobs.request_cancel` kill NGAY tiến trình con thay vì phải chờ
    ffmpeg tự chạy xong (xem comment tương tự trong dub_audio.py/tts.py)."""
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if job is not None:
        job.process = proc
    try:
        start = time.time()
        stdout = stderr = ""
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=0.5)
                if job is not None and job.cancel_event.is_set():
                    raise jobs_mod.JobCancelled("Đã dừng theo yêu cầu người dùng")
                break
            except subprocess.TimeoutExpired:
                if job is not None and job.cancel_event.is_set():
                    proc.kill()
                    proc.communicate()
                    raise jobs_mod.JobCancelled("Đã dừng theo yêu cầu người dùng")
                if time.time() - start > MERGE_TIMEOUT_S:
                    proc.kill()
                    proc.communicate()
                    raise VideoMergeError(f"ffmpeg chạy quá {MERGE_TIMEOUT_S:.0f}s — đã huỷ")
        return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
    finally:
        if job is not None:
            job.process = None


def merge_videos(
    input_paths: list[Path],
    output_path: Path,
    job: Optional[jobs_mod.JobState] = None,
) -> None:
    """Ghép nhiều video thành 1 file theo ĐÚNG thứ tự trong `input_paths`.

    Thử stream-copy trước (concat demuxer, `-c copy`) — nhanh, không encode
    lại, nhưng chỉ cho kết quả đúng khi mọi input CÙNG codec/độ phân giải/
    framerate. Nếu lỗi (khác codec — trường hợp phổ biến khi ghép video tải
    từ nhiều nguồn/nhiều lần khác nhau), tự động fallback sang encode lại
    (concat filter) — chậm hơn nhưng luôn ghép được bất kể input khác nhau
    thế nào."""
    if len(input_paths) < 2:
        raise VideoMergeError("Cần ít nhất 2 video để ghép")
    for p in input_paths:
        if not p.exists():
            raise VideoMergeError(f"Không thấy file: {p}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for p in input_paths:
            escaped = str(p.resolve()).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
        list_path = Path(f.name)

    try:
        result = _run_ffmpeg(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(output_path)],
            job,
        )
        if result.returncode == 0 and output_path.exists():
            return
    finally:
        list_path.unlink(missing_ok=True)

    # Fallback: encode lại, chấp nhận input không đồng nhất codec/độ phân giải.
    inputs_args: list[str] = []
    filter_inputs: list[str] = []
    for i, p in enumerate(input_paths):
        inputs_args += ["-i", str(p)]
        filter_inputs.append(f"[{i}:v:0][{i}:a:0]")
    filter_complex = "".join(filter_inputs) + f"concat=n={len(input_paths)}:v=1:a=1[outv][outa]"

    result = _run_ffmpeg(
        [
            "ffmpeg", "-y",
            *inputs_args,
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", "[outa]",
            str(output_path),
        ],
        job,
    )
    if result.returncode != 0 or not output_path.exists():
        raise VideoMergeError(f"ffmpeg ghép video lỗi: {(result.stderr or '')[-800:]}")
