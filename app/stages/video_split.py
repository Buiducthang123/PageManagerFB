from __future__ import annotations

import subprocess
from pathlib import Path


class VideoSplitError(RuntimeError):
    pass


def _format_ts(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:09.6f}"


def split_video(video_path: Path, split_points_s: list[float], out_paths: list[Path], total_duration_s: float) -> None:
    """Cắt `video_path` thành `len(out_paths)` đoạn theo `split_points_s`
    (N-1 mốc, đã sort tăng dần, nằm trong (0, total_duration_s)) — đoạn i là
    [boundaries[i], boundaries[i+1]).

    Dùng `-c copy` (stream-copy, không encode lại) — nhanh, nhưng điểm cắt
    thực tế trên file xuất ra có thể lệch tới keyframe gần nhất (thường dưới
    1 giây) thay vì đúng khung hình yêu cầu. Chấp nhận được vì mục đích chỉ
    là chia nhỏ file cho CapCut load nổi, không cần khớp khung hình tuyệt đối
    (khác hẳn yêu cầu chính xác của việc cắt cue phụ đề)."""
    boundaries = [0.0, *split_points_s, total_duration_s]
    if len(out_paths) != len(boundaries) - 1:
        raise VideoSplitError(
            f"Số điểm cắt ({len(split_points_s)}) không khớp số file xuất ({len(out_paths)})"
        )

    for i, out_path in enumerate(out_paths):
        start, end = boundaries[i], boundaries[i + 1]
        if end <= start:
            raise VideoSplitError(f"Đoạn {i + 1} không hợp lệ: {start:.2f}s -> {end:.2f}s")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", _format_ts(start),
                "-to", _format_ts(end),
                "-i", str(video_path),
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                str(out_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not out_path.exists():
            raise VideoSplitError(f"ffmpeg cắt đoạn {i + 1} lỗi: {(result.stderr or '')[-500:]}")
