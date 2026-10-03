from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes

# Mã hoá bằng DPAPI của Windows (CryptProtectData, phạm vi user hiện tại):
# chỉ đúng tài khoản Windows đã mã hoá mới giải mã được — copy thư mục
# workspace sang máy/tài khoản Windows khác thì không đọc được mật khẩu.
# Không phải Windows (không dùng thật, chỉ để không vỡ khi chạy thử) thì lưu
# base64 thường, đánh dấu bằng tiền tố "plain:".


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _from_blob(blob: _Blob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def protect(text: str) -> str:
    raw = text.encode("utf-8")
    if sys.platform != "win32":
        return "plain:" + base64.b64encode(raw).decode()
    src, out = _to_blob(raw), _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("Không mã hoá được dữ liệu (CryptProtectData)")
    return "dpapi:" + base64.b64encode(_from_blob(out)).decode()


def unprotect(token: str) -> str:
    kind, _, payload = token.partition(":")
    data = base64.b64decode(payload)
    if kind == "plain":
        return data.decode("utf-8")
    if kind != "dpapi" or sys.platform != "win32":
        raise ValueError("Dữ liệu mã hoá không đọc được trên máy này")
    src, out = _to_blob(data), _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise ValueError("Không giải mã được — dữ liệu được mã hoá trên máy hoặc tài khoản Windows khác")
    return _from_blob(out).decode("utf-8")
