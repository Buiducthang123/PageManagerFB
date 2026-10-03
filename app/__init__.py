"""Gói app. Việc đầu tiên khi nạp gói: đảm bảo Visual C++ Runtime đủ mới
(app/vcrt.py) — phải chạy TRƯỚC mọi thư viện C (onnxruntime, torch...)."""

from . import vcrt as _vcrt

_vcrt.setup()
