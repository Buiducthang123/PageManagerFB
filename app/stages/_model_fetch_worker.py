"""Tải 1 model AI trong tiến trình con riêng (nút "Cài đặt môi trường" —
app/model_setup.py). Chạy riêng để: tải lỗi/treo không kéo sập backend, và
huỷ được bằng cách kill thẳng tiến trình.

Gọi đúng hàm mà stage dùng lúc chạy thật (faster-whisper download_model,
funasr get_or_download_model_dir, demucs get_model, Vieneu(...)) — nên file
tải về đúng chỗ và đúng bộ file stage cần, không phải đoán danh sách file.
Thư mục cache (HF_HOME, MODELSCOPE_CACHE...) nhận qua biến môi trường từ app.

Dùng: python _model_fetch_worker.py <component> [arg]
  whisper <tên model> <thư mục cache> | sensevoice | demucs | vieneu | lama <đường dẫn đích>
Phát hành dạng source cạnh gói `app` đã biên dịch (xem tools/build_release.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

LAMA_URL = "https://github.com/Sanster/models/releases/download/add_big_lama/big-lama.pt"


def _whisper(name: str, cache_dir: str) -> None:
    from faster_whisper.utils import download_model

    download_model(name, cache_dir=cache_dir)


def _sensevoice() -> None:
    from funasr.download.download_model_from_hub import get_or_download_model_dir

    for model in ("iic/SenseVoiceSmall", "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"):
        get_or_download_model_dir(model, "master")


def _demucs() -> None:
    from demucs.pretrained import get_model

    get_model("htdemucs")


def _vieneu() -> None:
    from vieneu import Vieneu

    Vieneu(mode="v3turbo", device="cpu")


def _lama(dest: str) -> None:
    import requests

    target = Path(dest)
    part = target.with_suffix(target.suffix + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    done = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={done}-"} if done else {}
    with requests.get(LAMA_URL, headers=headers, stream=True, timeout=60) as resp:
        if resp.status_code == 200:  # server không hỗ trợ tải tiếp → tải lại từ đầu
            done = 0
        elif resp.status_code != 206:
            resp.raise_for_status()
        with part.open("ab" if done else "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
    part.replace(target)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: _model_fetch_worker.py <component> [arg]", file=sys.stderr)
        return 2
    component, arg = argv[1], (argv[2] if len(argv) > 2 else "")
    if component == "whisper":
        _whisper(arg, argv[3])
    elif component == "sensevoice":
        _sensevoice()
    elif component == "demucs":
        _demucs()
    elif component == "vieneu":
        _vieneu()
    elif component == "lama":
        _lama(arg)
    else:
        print(f"không biết component {component}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
