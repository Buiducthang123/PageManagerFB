"""Mã máy (user-management-plan.md mục 4): SHA-256 của MachineGuid Windows
ghép DEVICE_SALT. Không lưu file nào — copy workspace sang máy khác thì mã
không đi theo. Chỉ dùng để HIỂN THỊ "đang dùng trên máy nào"; quyết định
1 tài khoản/1 máy dựa vào session_id phía server."""

from __future__ import annotations

import hashlib
import os
import platform
import sys
from functools import lru_cache

from .constants import DEVICE_SALT


def _machine_guid() -> str:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
                0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                if value:
                    return str(value)
        except OSError:
            pass
    # Không phải Windows (chỉ để chạy thử): ghép tên máy, không ổn định bằng.
    return f"fallback:{platform.node()}"


@lru_cache(maxsize=1)
def device_id() -> str:
    return hashlib.sha256(f"{DEVICE_SALT}:{_machine_guid()}".encode("utf-8")).hexdigest()


def device_name() -> str:
    return (os.environ.get("COMPUTERNAME") or platform.node() or "may-khong-ten")[:80]


@lru_cache(maxsize=1)
def local_secret() -> bytes:
    """Khoá HMAC cho auth.json — gắn với máy này. Coi như có thể bị dò ra
    (mục 8); chỉ để sửa tay file là bị phát hiện."""
    return hashlib.sha256(f"{DEVICE_SALT}:auth:{_machine_guid()}".encode("utf-8")).digest()
