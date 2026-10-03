"""Gói app. Việc đầu tiên khi nạp gói: đảm bảo Visual C++ Runtime đủ mới
(app/vcrt.py) — phải chạy TRƯỚC mọi thư viện C (onnxruntime, torch...)."""

import sys as _sys
from pathlib import Path as _Path

from . import vcrt as _vcrt

_vcrt.setup()

# Thư viện thuần Python không có trên PyPI (capcut_tts_api...) — đi kèm từng bản
# app ở <code>/vendor/python (xem vendor/python/README.md). Thêm vào CUỐI sys.path:
# máy dev đã pip install thì bản đó vẫn được ưu tiên.
_VENDOR = _Path(__file__).resolve().parent.parent / "vendor" / "python"
if _VENDOR.is_dir() and str(_VENDOR) not in _sys.path:
    _sys.path.append(str(_VENDOR))
