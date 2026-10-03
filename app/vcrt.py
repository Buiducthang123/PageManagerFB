"""Microsoft Visual C++ Runtime đi kèm app (app/assets/vcrt/, bản 14.44 lấy từ
thư mục Redist của Visual Studio Build Tools — Microsoft cho phép đi kèm app).

Lý do: onnxruntime 1.30 (OCR, giọng đọc trên máy) build bằng VS 2022 17.10+,
cần msvcp140.dll >= 14.40. Máy user chỉ có bản cũ trong System32 → crash
"Windows fatal exception: access violation" ngay lúc import onnxruntime, kéo
sập cả backend (đã bắt được thật trong logs/crash.log của 1 máy user).

Cách xử lý (chạy SỚM NHẤT, từ app/__init__.py — trước khi bất kỳ thư viện C
nào kịp nạp msvcp140.dll của System32):
1. Bản đóng gói: chép các DLL này vào cạnh runtime\\python.exe nếu ở đó chưa có
   hoặc cũ hơn. Thư mục của exe được Windows tìm TRƯỚC System32, nên mọi tiến
   trình Python (backend lẫn các worker chạy riêng) đều dùng bản mới.
2. Nạp sẵn bản mới vào tiến trình hiện tại khi System32 cũ hơn: DLL cùng tên
   đã nạp rồi thì các thư viện sau dùng luôn bản đó.
Không bao giờ được làm app không khởi động — mọi lỗi đều bỏ qua êm.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

BUNDLED_DIR = Path(__file__).resolve().parent / "assets" / "vcrt"
# Thứ tự nạp: msvcp140.dll trước (các file còn lại phụ thuộc nó).
DLLS = ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
        "msvcp140_codecvt_ids.dll", "concrt140.dll")
MIN_VERSION = (14, 40)

_state: dict = {"system": None, "bundled": None, "preloaded": False, "copied": [], "error": ""}


def file_version(path: Path) -> Optional[tuple[int, int, int, int]]:
    """FileVersion của 1 file DLL (Windows API), None nếu không đọc được."""
    if sys.platform != "win32" or not path.is_file():
        return None
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
            return None
        ptr = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
            return None

        class VSFixedFileInfo(ctypes.Structure):
            _fields_ = [("dwSignature", ctypes.c_uint32), ("dwStrucVersion", ctypes.c_uint32),
                        ("dwFileVersionMS", ctypes.c_uint32), ("dwFileVersionLS", ctypes.c_uint32)]

        info = ctypes.cast(ptr, ctypes.POINTER(VSFixedFileInfo)).contents
        ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
        return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except (OSError, AttributeError, ValueError):
        return None


def system_dll(name: str = "msvcp140.dll") -> Path:
    return Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / name


def _packaged() -> bool:
    # = updater.packaged() — không import updater ở đây (chạy trước mọi thứ khác).
    return bool(os.environ.get("REUP_INSTALL_ROOT")) and Path(__file__).resolve().parent.parent.parent.name == "app"


def _copy_next_to_python() -> None:
    target_dir = Path(sys.executable).resolve().parent
    bundled = file_version(BUNDLED_DIR / "msvcp140.dll")
    for name in DLLS:
        src, dst = BUNDLED_DIR / name, target_dir / name
        if not src.is_file():
            continue
        have = file_version(dst)
        if have is not None and bundled is not None and have >= bundled:
            continue
        try:
            tmp = dst.with_suffix(".dll.tmp")
            shutil.copyfile(src, tmp)
            os.replace(tmp, dst)
            _state["copied"].append(name)
        except OSError as err:  # đang bị tiến trình khác giữ — lần khởi động sau chép
            _state["error"] = f"không chép được {name}: {err}"


def setup() -> None:
    if sys.platform != "win32" or not (BUNDLED_DIR / "msvcp140.dll").is_file():
        return
    try:
        _state["system"] = file_version(system_dll())
        _state["bundled"] = file_version(BUNDLED_DIR / "msvcp140.dll")
        system = _state["system"]
        # Chỉ can thiệp khi máy THẬT SỰ cũ — máy có bản mới hơn bản đi kèm thì
        # để nguyên (thư viện build bằng toolset mới có thể cần đúng bản mới).
        if system is None or system[:2] < MIN_VERSION:
            if _packaged():
                _copy_next_to_python()
            local = Path(sys.executable).resolve().parent
            for name in DLLS:
                path = local / name if (local / name).is_file() else BUNDLED_DIR / name
                if path.is_file():
                    ctypes.WinDLL(str(path))
            _state["preloaded"] = True
    except Exception as err:  # noqa: BLE001 — không bao giờ được chặn app khởi động
        _state["error"] = str(err)


def status() -> dict:
    """Cho trang Kiểm tra hệ thống: phiên bản System32, bản đi kèm, đã nạp bản đi kèm chưa."""
    fmt = lambda v: ".".join(map(str, v)) if v else ""  # noqa: E731
    system = _state["system"] if _state["system"] is not None else file_version(system_dll())
    return {
        "system": fmt(system),
        "system_ok": bool(system and system[:2] >= MIN_VERSION),
        "bundled": fmt(_state["bundled"]),
        "preloaded": _state["preloaded"],
        "copied": list(_state["copied"]),
        "error": _state["error"],
    }
