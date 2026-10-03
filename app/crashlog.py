"""Dấu vết để biết backend chết lúc nào, đang làm gì.

- `logs/crash.log`: faulthandler ghi stack của MỌI thread khi tiến trình
  crash ở tầng C (torch/ctranslate2/onnx access violation...) — kiểu chết này
  không để lại traceback nào trong app.log.
- `logs/heartbeat.log`: cứ 30s ghi 1 dòng RAM tiến trình / RAM máy còn trống /
  job đang chạy. Tiến trình bị Windows hay antivirus kết thúc thì không kịp ghi
  gì cả — dòng heartbeat cuối cùng cho biết nó chết vào khoảng lúc nào, lúc đó
  ngốn bao nhiêu RAM và đang chạy việc gì.

Chạy từ source (không qua launcher) thì stdout không vào file nào, nên thêm 1
sink loguru ghi `logs/app.log` để nút "Xem log" vẫn có cái để xem."""

from __future__ import annotations

import ctypes
import faulthandler
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from . import jobs, paths, updater

LOG_DIR = paths.INSTALL_ROOT / "logs"
HEARTBEAT_S = 30.0
HEARTBEAT_MAX_BYTES = 1 * 1024 * 1024  # quá cỡ thì giữ nửa cuối

_crash_file = None  # giữ file mở suốt đời tiến trình — faulthandler cần fd còn sống
_started = False


def _memory() -> tuple[Optional[float], Optional[float], Optional[float]]:
    """(MB tiến trình đang dùng, MB RAM máy còn trống, MB RAM máy tổng) — chỉ Windows."""
    if sys.platform != "win32":
        return None, None, None

    class PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    class MEMSTAT(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    mb = 1024 * 1024
    proc_mb = free_mb = total_mb = None
    try:
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.windll.kernel32
        # HANDLE là con trỏ 64-bit — để mặc định c_int thì pseudo-handle -1 bị cắt, gọi luôn lỗi.
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        if k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            proc_mb = pmc.WorkingSetSize / mb
        ms = MEMSTAT()
        ms.dwLength = ctypes.sizeof(MEMSTAT)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            free_mb, total_mb = ms.ullAvailPhys / mb, ms.ullTotalPhys / mb
    except (OSError, AttributeError):
        pass
    return proc_mb, free_mb, total_mb


def _trim(path: Path) -> None:
    try:
        if path.stat().st_size > HEARTBEAT_MAX_BYTES:
            data = path.read_bytes()[-HEARTBEAT_MAX_BYTES // 2:]
            path.write_bytes(data.split(b"\n", 1)[-1])
    except OSError:
        pass


def _heartbeat_line() -> str:
    proc_mb, free_mb, total_mb = _memory()
    parts = [time.strftime("%Y-%m-%d %H:%M:%S")]
    if proc_mb is not None:
        parts.append(f"app {proc_mb:.0f} MB")
    if free_mb is not None and total_mb is not None:
        parts.append(f"máy còn {free_mb:.0f}/{total_mb:.0f} MB")
    keys = jobs.running_keys()
    parts.append("job: " + (", ".join(keys) if keys else "-"))
    return " · ".join(parts)


def _heartbeat_loop() -> None:
    path = LOG_DIR / "heartbeat.log"
    while True:
        try:
            _trim(path)
            with path.open("a", encoding="utf-8") as f:
                f.write(_heartbeat_line() + "\n")
        except Exception:  # noqa: BLE001 — heartbeat không bao giờ được làm chết app
            pass
        time.sleep(HEARTBEAT_S)


def start() -> None:
    global _crash_file, _started
    if _started:
        return
    _started = True
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _crash_file = (LOG_DIR / "crash.log").open("a", encoding="utf-8")
        _crash_file.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} khởi động (pid {os.getpid()}) =====\n")
        _crash_file.flush()
        faulthandler.enable(file=_crash_file, all_threads=True)
    except OSError:
        logger.exception("crashlog: không bật được faulthandler")
    if not updater.packaged():
        logger.add(LOG_DIR / "app.log", rotation="5 MB", retention=3, encoding="utf-8", enqueue=True)
    with (LOG_DIR / "heartbeat.log").open("a", encoding="utf-8") as f:
        f.write(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} khởi động (pid {os.getpid()}) =====\n")
    threading.Thread(target=_heartbeat_loop, name="heartbeat", daemon=True).start()

