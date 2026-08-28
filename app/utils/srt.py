from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Cue:
    id: int
    start: str
    end: str
    text: str


def parse_ts(ts: str) -> float:
    ts = ts.strip().replace(".", ",")
    hms, _, ms = ts.partition(",")
    parts = hms.split(":")
    if len(parts) != 3:
        return 0.0
    h, m, s = (int(p) for p in parts)
    return h * 3600 + m * 60 + s + int((ms or "0").ljust(3, "0")[:3]) / 1000.0


def format_ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds % 1) * 1000))
    if ms >= 1000:
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(text: str) -> list[Cue]:
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.rstrip() for ln in block.strip().splitlines()]
        if len(lines) < 2:
            continue
        idx = 0
        cue_id: int | None = None
        if lines[0].strip().isdigit():
            cue_id = int(lines[0].strip())
            idx = 1
        if idx >= len(lines) or "-->" not in lines[idx]:
            continue
        start, _, end = lines[idx].partition("-->")
        body = "\n".join(lines[idx + 1 :]).strip()
        cues.append(
            Cue(
                id=cue_id or (len(cues) + 1),
                start=start.strip(),
                end=end.strip(),
                text=body,
            )
        )
    return cues


def write_srt(path: Path, cues: list[Cue]) -> None:
    parts = [f"{cue.id}\n{cue.start} --> {cue.end}\n{cue.text.strip()}\n" for cue in cues]
    path.write_text("\n".join(parts) + ("\n" if parts else ""), encoding="utf-8")


def load_srt(path: Path) -> list[Cue]:
    if not path.exists():
        return []
    return parse_srt(path.read_text(encoding="utf-8"))


def update_cue_text(path: Path, cue_id: int, text: str) -> list[Cue]:
    """Sửa text của đúng 1 cue trong file .srt, giữ nguyên mọi cue khác."""
    cues = load_srt(path)
    for cue in cues:
        if cue.id == cue_id:
            cue.text = text.strip()
            write_srt(path, cues)
            return cues
    raise ValueError(f"Không tìm thấy câu #{cue_id} trong {path.name}")
