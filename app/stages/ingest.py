from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import UploadFile

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}


def probe_duration(path: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        raw = (result.stdout or "").strip()
        return float(raw) if raw else None
    except (FileNotFoundError, ValueError, OSError):
        return None


async def save_uploaded_video(project_root: Path, file: UploadFile) -> tuple[Path, str, float | None]:
    ext = Path(file.filename or "video.mp4").suffix.lower()
    if ext not in VIDEO_EXTENSIONS:
        raise ValueError(
            f'Định dạng "{ext or "(không rõ)"}" chưa được hỗ trợ. '
            f"Nhận: {', '.join(sorted(VIDEO_EXTENSIONS))}"
        )

    dest = project_root / f"video{ext}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    for old in project_root.glob("video.*"):
        if old.resolve() != dest.resolve():
            old.unlink(missing_ok=True)
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)

    duration = probe_duration(dest)
    original = file.filename or dest.name
    return dest, original, duration
