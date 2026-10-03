"""launcher.exe — user mở cái này (user-management-plan.md mục 12).

Cấu trúc thư mục cài đặt:
    <ROOT>\\launcher.exe
    <ROOT>\\runtime\\python.exe        Python + thư viện bên thứ 3 + ffmpeg + Chromium
    <ROOT>\\app\\<phiên bản>\\           code app đã biên dịch + frontend build
    <ROOT>\\workspace\\                  dữ liệu user — launcher không bao giờ đụng
    <ROOT>\\launcher.json                bản đang dùng, bản hỏng, cổng

Việc làm:
1. App đang chạy rồi (cổng trong launcher.json trả lời) → chỉ mở trình duyệt.
2. Chọn bản mới nhất không nằm trong danh sách "hỏng".
3. Chạy backend bằng runtime\\pythonw.exe; chờ tối đa 60s cho /api/license/status.
4. Không lên → đánh dấu hỏng, quay về bản trước, ghi log.
5. Lên → mở trình duyệt, dọn bản cũ (giữ 2 bản), chờ backend thoát. Mã thoát
   75 = app vừa cài bản mới, xin khởi động lại → quay lại bước 2.
6. Mã 0 = user bấm "Tắt app" → thoát. Mã khác = backend chết bất thường (crash
   tầng C, bị Task Manager/antivirus/hết RAM kết thúc...) → ghi lý do + dòng
   heartbeat cuối vào launcher.log rồi tự bật lại cùng cổng (tab đang mở tự nối
   lại). Quá 3 lần trong 5 phút thì dừng hẳn và báo user, tránh lặp vô hạn.

File này được app tự chép đè lên <ROOT>\\launcher.pyw mỗi lần khởi động
(app/updater.py sync_launcher) — sửa launcher không cần user cài lại Setup.

Chỉ dùng thư viện chuẩn: launcher biên dịch riêng (Nuitka onefile), nhỏ, hiếm khi đổi.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

RESTART_EXIT_CODE = 75
QUIT_EXIT_CODES = (0,)  # app/shutdown.py QUIT_EXIT_CODE — user bấm "Tắt app"
MAX_CRASH_RESTARTS = 3
CRASH_WINDOW_S = 300
HEALTH_TIMEOUT_S = 60
PREFERRED_PORTS = (8001, 8011, 8021)  # đã đăng ký sẵn với app Facebook (13.8)
KEEP_VERSIONS = 2

ROOT = Path(sys.argv[0]).resolve().parent if getattr(sys, "frozen", False) or "__compiled__" in globals() else Path(__file__).resolve().parent
if os.environ.get("REUP_LAUNCHER_ROOT"):
    ROOT = Path(os.environ["REUP_LAUNCHER_ROOT"]).resolve()
STATE_FILE = ROOT / "launcher.json"
LOG_DIR = ROOT / "logs"


def log(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with (LOG_DIR / "launcher.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    if sys.stdout and sys.stdout.isatty():
        print(line)


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def version_key(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", v.split("-")[0])) or (0,)


def installed_versions() -> list[str]:
    base = ROOT / "app"
    if not base.is_dir():
        return []
    out = [d.name for d in base.iterdir() if d.is_dir() and (d / "release.json").exists()]
    return sorted(out, key=version_key, reverse=True)


def health(port: int, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/license/status", timeout=timeout) as res:
            return res.status == 200 and b'"mode"' in res.read(4096)
    except Exception:
        return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick_port(state: dict) -> int:
    for port in (state.get("port"), *PREFERRED_PORTS):
        if port and port_free(int(port)):
            return int(port)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def runtime_python() -> Path:
    if os.environ.get("REUP_RUNTIME_PYTHON"):  # chỉ để test launcher trên máy dev
        return Path(os.environ["REUP_RUNTIME_PYTHON"])
    rt = ROOT / "runtime"
    for name in ("pythonw.exe", "python.exe"):
        if (rt / name).exists():
            return rt / name
    raise SystemExit(f"Thiếu runtime: {rt} — cài lại bằng Setup.exe")


def child_env(port: int) -> dict:
    rt = ROOT / "runtime"
    env = dict(os.environ)
    env.update({
        "REUP_INSTALL_ROOT": str(ROOT),
        "FRONTEND_URL": f"http://localhost:{port}",
        "PLAYWRIGHT_BROWSERS_PATH": str(rt / "ms-playwright"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    })
    if (rt / "douyin-downloader" / "run.py").exists():
        env["DOUYIN_DL_DIR"] = str(rt / "douyin-downloader")
    extra_path = [str(rt / "ffmpeg"), str(rt), str(rt / "Scripts")]
    env["PATH"] = os.pathsep.join([*extra_path, env.get("PATH", "")])
    return env


def start_backend(version: str, port: int) -> subprocess.Popen:
    code_dir = ROOT / "app" / version
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = (LOG_DIR / "app.log").open("a", encoding="utf-8")
    out.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} bản {version} cổng {port} =====\n")
    out.flush()
    cmd = [str(runtime_python()), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)]
    return subprocess.Popen(
        cmd, cwd=str(code_dir), env=child_env(port), stdout=out, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def wait_healthy(proc: subprocess.Popen, port: int) -> bool:
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        if health(port):
            return True
        time.sleep(1)
    return False


def prune(current: str, state: dict) -> None:
    """Giữ bản đang chạy + bản tốt gần nhất trước đó, xoá phần còn lại."""
    good = [v for v in installed_versions() if v not in state.get("bad", [])]
    keep = {current, *good[:KEEP_VERSIONS]}
    prev = state.get("previous")
    if prev:
        keep.add(prev)
    for v in installed_versions():
        if v not in keep:
            log(f"dọn bản cũ {v}")
            shutil.rmtree(ROOT / "app" / v, ignore_errors=True)


def run_once(state: dict, open_browser: bool = True) -> int | None:
    """Chạy 1 vòng. Trả mã thoát của backend, None nếu không bản nào lên được."""
    bad = set(state.get("bad", []))
    candidates = [v for v in installed_versions() if v not in bad]
    if not candidates:
        log("không còn bản app nào chạy được")
        return None
    for version in candidates:
        port = pick_port(state)
        log(f"khởi động bản {version} ở cổng {port}")
        proc = start_backend(version, port)
        if not wait_healthy(proc, port):
            why = f"thoát với mã {proc.returncode}" if proc.poll() is not None else f"không trả lời sau {HEALTH_TIMEOUT_S}s"
            log(f"bản {version} không lên được ({why}, xem logs/app.log) — đánh dấu hỏng, thử bản trước")
            try:
                proc.kill()
            except OSError:
                pass
            state.setdefault("bad", []).append(version)
            save_state(state)
            continue
        if state.get("current") != version:
            state["previous"] = state.get("current")
        state["current"], state["port"] = version, port
        save_state(state)
        prune(version, state)
        if open_browser and not os.environ.get("REUP_LAUNCHER_NO_BROWSER"):
            webbrowser.open(f"http://localhost:{port}/")
        return proc.wait()
    return None


# Mã thoát hay gặp khi tiến trình chết không qua đường thoát bình thường của Python.
_EXIT_REASONS = {
    1: "bị kết thúc từ bên ngoài (Task Manager / antivirus / Windows) hoặc lỗi khởi động",
    0xC0000005: "crash bộ nhớ (access violation) trong thư viện C — xem logs/crash.log",
    0xC0000409: "crash (stack buffer overrun) trong thư viện C — xem logs/crash.log",
    0xC00000FD: "crash (stack overflow) — xem logs/crash.log",
    0xC0000017: "hết bộ nhớ",
    0xC000013A: "bị tắt bằng Ctrl+C / đóng phiên Windows",
}


def describe_exit(code: int) -> str:
    unsigned = code & 0xFFFFFFFF
    reason = _EXIT_REASONS.get(unsigned, "")
    text = f"mã {code}" + (f" / 0x{unsigned:08X}" if unsigned > 0xFFFF else "")
    return f"{text}: {reason}" if reason else text


def last_heartbeat() -> str:
    """Dòng heartbeat cuối (app/crashlog.py ghi 30s/lần) — cho biết lúc chết app ngốn bao nhiêu RAM, đang chạy job gì."""
    try:
        lines = (LOG_DIR / "heartbeat.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return next((l for l in reversed(lines) if l and not l.startswith("=====")), "")


def alert(title: str, text: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)  # MB_ICONERROR
    except Exception:
        pass


def main() -> int:
    state = load_state()
    port = state.get("port")
    if port and health(int(port)):
        if not os.environ.get("REUP_LAUNCHER_NO_BROWSER"):
            webbrowser.open(f"http://localhost:{port}/")
        return 0
    open_browser = True
    crashes: list[float] = []
    while True:
        code = run_once(state, open_browser)
        if code is None:
            return 1
        if code == RESTART_EXIT_CODE:
            log("app xin khởi động lại (vừa cập nhật)")
        elif code in QUIT_EXIT_CODES:
            log(f"app thoát (mã {code}, người dùng tắt)")
            return 0
        else:
            now = time.time()
            crashes = [t for t in crashes if now - t < CRASH_WINDOW_S] + [now]
            beat = last_heartbeat()
            log(f"app chết bất thường ({describe_exit(code)})" + (f" — heartbeat cuối: {beat}" if beat else ""))
            if len(crashes) > MAX_CRASH_RESTARTS:
                log(f"đã tự bật lại {MAX_CRASH_RESTARTS} lần trong {CRASH_WINDOW_S // 60} phút mà vẫn chết — dừng")
                alert("OddlyLab Reup", "App bị tắt đột ngột nhiều lần liên tiếp nên đã dừng hẳn.\n\n"
                      f"Mở lại app, vào Kiểm tra hệ thống → Xem log (hoặc thư mục {LOG_DIR}) và gửi log cho admin.")
                return 1
            log(f"tự bật lại app (lần {len(crashes)}/{MAX_CRASH_RESTARTS} trong {CRASH_WINDOW_S // 60} phút)")
        state = load_state()
        open_browser = False  # tab đang mở tự kết nối lại vào cùng cổng


if __name__ == "__main__":
    sys.exit(main())
