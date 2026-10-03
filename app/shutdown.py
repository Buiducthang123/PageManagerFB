"""Nút "Tắt app": dừng hẳn backend đang chạy ngầm.

Tắt trình duyệt KHÔNG tắt app (Dự án tự động cần chạy nền); muốn dừng hẳn thì
gọi `quit_app()`: huỷ mọi job đang chạy, tắt các tiến trình con (ffmpeg,
demucs, Chrome của Playwright...) để không sót tiến trình mồ côi, rồi thoát.
Chạy qua launcher thì mã thoát 0 → launcher cũng thoát theo.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes

from loguru import logger

from . import jobs

QUIT_EXIT_CODE = 0


def running_jobs() -> list[str]:
    return jobs.running_keys()


def _child_pids_windows(root_pid: int) -> list[int]:
    """Mọi tiến trình con/cháu của root_pid (Toolhelp32 snapshot — không cần psutil)."""
    TH32CS_SNAPPROCESS = 0x2

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260),
        ]

    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (None, wintypes.HANDLE(-1).value):
        return []
    parents: dict[int, list[int]] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            parents.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    out: list[int] = []
    stack = [root_pid]
    while stack:
        for child in parents.get(stack.pop(), []):
            if child not in out and child != root_pid:
                out.append(child)
                stack.append(child)
    return out


def _kill_children() -> None:
    if sys.platform != "win32":
        return
    PROCESS_TERMINATE = 0x1
    k32 = ctypes.windll.kernel32
    for pid in reversed(_child_pids_windows(os.getpid())):  # cháu trước, con sau
        handle = k32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            k32.TerminateProcess(handle, 1)
            k32.CloseHandle(handle)


def quit_app() -> None:
    """Chạy nền sau 1 giây (để kịp trả response cho trình duyệt)."""

    def _do() -> None:
        time.sleep(1.0)
        keys = running_jobs()
        if keys:
            logger.info("tắt app: huỷ {} job đang chạy: {}", len(keys), ", ".join(keys))
            for key in keys:
                jobs.request_cancel(key)
            deadline = time.monotonic() + 5
            while running_jobs() and time.monotonic() < deadline:
                time.sleep(0.2)
        try:
            _kill_children()
        except Exception:  # noqa: BLE001 — thoát vẫn phải thoát
            logger.exception("tắt app: không tắt được hết tiến trình con")
        logger.info("tắt app theo yêu cầu người dùng")
        os._exit(QUIT_EXIT_CODE)

    threading.Thread(target=_do, name="quit-app", daemon=True).start()
