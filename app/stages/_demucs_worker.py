"""Chạy trong tiến trình con riêng (subprocess), KHÔNG import trực tiếp từ
dub_audio.py — Demucs (CPU) đã 2 lần bị ghi nhận treo cứng trên máy dev này
(0% CPU, không thoát), có vẻ liên quan tới việc chạy chung process với CUDA
(Whisper) trong 1 server sống lâu. Cô lập ra subprocess để 1 lần treo không
kéo sập cả FastAPI server, và để dub_audio.py có thể áp timeout cứng rồi kill
thay vì treo vô thời hạn.

Dùng: python -m app.stages._demucs_worker <raw_wav_path> <output_wav_path>
"""
from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: _demucs_worker.py <raw_wav> <output_wav>", file=sys.stderr)
        return 2

    raw_wav = Path(argv[1])
    output_path = Path(argv[2])

    from demucs.api import Separator, save_audio

    separator = Separator(model="htdemucs", device="cpu", progress=False)
    origin, stems = separator.separate_audio_file(raw_wav)
    background = origin - stems["vocals"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_audio(background, str(output_path), samplerate=separator.samplerate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
