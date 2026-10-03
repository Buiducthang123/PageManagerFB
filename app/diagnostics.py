"""Nút "Xuất log chẩn đoán" (user-management-plan.md 13.8): gom thông tin để
user gửi cho admin khi lỗi, đỡ phải hỏi qua lại.

Gồm: phiên bản + trạng thái đăng nhập (không token), kết quả Kiểm tra hệ
thống, cài đặt (.env đã che key/secret), log launcher/app, log các dự án gần
nhất. KHÔNG gồm: auth.json, cookie/profile tài khoản TikTok, token Page
Facebook, video. Mọi đoạn trông giống key/token trong log đều bị che lại.
"""

from __future__ import annotations

import io
import json
import platform
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Optional

from . import model_setup, paths, projects as pj, system_check
from .license import constants as license_constants
from .license import manager as license_manager

MAX_LOG_BYTES = 3 * 1024 * 1024  # mỗi file log chỉ lấy phần cuối
MAX_PROJECT_LOG_BYTES = 512 * 1024
RECENT_PROJECTS = 5

_SECRET_ENV = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|PASS|COOKIE)", re.I)
_REDACT = [
    (re.compile(r"(access_token|refresh_token|client_secret|api_key|apikey|password|token)([\"']?\s*[:=]\s*[\"']?)([^\"'&\s,}]+)", re.I),
     r"\1\2***"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]+"), "Bearer ***"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), "AIza***"),          # Gemini key
    (re.compile(r"EAA[0-9A-Za-z]{20,}"), "EAA***"),              # token Facebook
    (re.compile(r"sb_secret_[0-9A-Za-z_\-]+"), "sb_secret_***"),
    (re.compile(r"eyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]+"), "<jwt>"),
]


def _redact(text: str) -> str:
    for pattern, repl in _REDACT:
        text = pattern.sub(repl, text)
    return text


def _tail(path: Path, limit: int) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > limit:
                f.seek(size - limit)
            data = f.read()
    except OSError as err:
        return f"<không đọc được: {err}>"
    text = data.decode("utf-8", errors="replace")
    if size > limit:
        text = f"... (cắt, chỉ giữ {limit // 1024} KB cuối)\n" + text.split("\n", 1)[-1]
    return _redact(text)


def _settings_text() -> str:
    try:
        lines = paths.ENV_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "<không có .env>"
    out = []
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and _SECRET_ENV.search(key) and value.strip():
            out.append(f"{key}=*** (đã che, dài {len(value.strip())} ký tự)")
        else:
            out.append(line)
    return "\n".join(out)


def _recent_project_logs() -> list[Path]:
    try:
        dirs = [d for d in pj.PROJECTS_DIR.iterdir() if d.is_dir()]
    except OSError:
        return []
    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    out: list[Path] = []
    for d in dirs[:RECENT_PROJECTS]:
        logs = d / "logs"
        if logs.is_dir():
            out.extend(sorted(f for f in logs.iterdir() if f.suffix in (".log", ".jsonl", ".txt")))
    return out


def build_zip() -> tuple[bytes, str]:
    from . import updater  # tránh vòng import lúc khởi động

    buf = io.BytesIO()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        snap = license_manager.snapshot()
        info = {
            "thoi_diem": time.strftime("%Y-%m-%d %H:%M:%S"),
            "app_version": license_constants.APP_VERSION,
            "dong_goi": updater.packaged(),
            "runtime_version": updater.runtime_version(),
            "python": sys.version,
            "windows": platform.platform(),
            "thu_muc_cai": str(paths.INSTALL_ROOT),
            "workspace": str(pj.WORKSPACE_DIR),
            "dang_nhap": {k: snap.get(k) for k in (
                "mode", "status", "code", "message", "email", "role", "features",
                "device_name", "offline_minutes", "offline_remaining_minutes")},
        }
        zf.writestr("thong-tin.json", json.dumps(info, ensure_ascii=False, indent=1))
        try:
            checks = system_check.run_checks()
        except Exception as err:  # noqa: BLE001 — chẩn đoán không được hỏng vì 1 mục
            checks = [{"loi": str(err)}]
        zf.writestr("kiem-tra-he-thong.json", json.dumps(checks, ensure_ascii=False, indent=1))
        try:
            models = model_setup.status()
        except Exception as err:  # noqa: BLE001
            models = {"loi": str(err)}
        zf.writestr("model-ai.json", json.dumps(models, ensure_ascii=False, indent=1))
        zf.writestr("cai-dat.env.txt", _settings_text())

        logs_dir = paths.INSTALL_ROOT / "logs"
        if logs_dir.is_dir():
            for f in sorted(logs_dir.glob("*.log")):
                zf.writestr(f"logs/{f.name}", _tail(f, MAX_LOG_BYTES))
        for name, f in _log_files(None).items():
            if name.startswith("tai-du-lieu-ai/"):
                zf.writestr(f"logs/{name}", _tail(f, MAX_LOG_BYTES))
        launcher_state = paths.INSTALL_ROOT / "launcher.json"
        if launcher_state.exists():
            zf.writestr("launcher.json", _tail(launcher_state, 64 * 1024))
        for f in _recent_project_logs():
            zf.writestr(f"du-an/{f.parent.parent.name}/{f.name}", _tail(f, MAX_PROJECT_LOG_BYTES))
    return buf.getvalue(), f"oddlylab-chan-doan-{stamp}.zip"


# ------------------------------------------------------------------ Nút "Xem log"
# Đọc thẳng trong app thay vì bắt user mở file txt. Chỉ cho đọc file log nằm
# trong INSTALL_ROOT/logs, hoặc <dự án>/logs khi xem từ trang dự án — tên file
# đi qua whitelist (danh sách file thật đang có), không nhận đường dẫn tự do.

VIEW_LOG_BYTES = 512 * 1024
_LOG_ORDER = ("app.log", "launcher.log", "crash.log", "heartbeat.log")
PROJECT_LOG_PREFIX = "du-an/"
_PROJECT_LOG_SUFFIXES = (".jsonl", ".log", ".txt")


PROJECT_LOG = PROJECT_LOG_PREFIX + "pipeline.jsonl"


def _project_root(project_id: str) -> Optional[Path]:
    root = pj.PROJECTS_DIR.resolve()
    d = (pj.PROJECTS_DIR / project_id).resolve()
    return d if root in d.parents and (d / "project.json").is_file() else None


def _log_files(project_id: Optional[str]) -> dict[str, Path]:
    """Tên hiển thị → file thật. Log dự án đứng trước (đang xem dự án thì đó là
    thứ cần nhất) và LUÔN có khi dự án tồn tại — kể cả chưa có pipeline.jsonl,
    vì đầu tab đó còn liệt kê lỗi hiện tại của các bước (lấy từ project.json)."""
    out: dict[str, Path] = {}
    proj = _project_root(project_id) if project_id else None
    if proj is not None:
        out[PROJECT_LOG] = proj / "logs" / "pipeline.jsonl"
        logs = proj / "logs"
        if logs.is_dir():
            for f in sorted(logs.iterdir()):
                if f.is_file() and f.suffix in _PROJECT_LOG_SUFFIXES:
                    out.setdefault(PROJECT_LOG_PREFIX + f.name, f)
    logs_dir = paths.INSTALL_ROOT / "logs"
    if logs_dir.is_dir():
        rank = {name: i for i, name in enumerate(_LOG_ORDER)}
        for f in sorted(logs_dir.glob("*.log"), key=lambda f: (rank.get(f.name, len(rank)), f.name)):
            if f.is_file():
                out[f.name] = f
    # Log từng lần tải dữ liệu AI (model_setup.py ghi ở thư mục tạm) — worker chết vì
    # crash C thì stack faulthandler nằm ở đây.
    try:
        from . import config

        for f in sorted(config.temp_dir().glob("model_fetch_*.log")):
            out["tai-du-lieu-ai/" + f.name.removeprefix("model_fetch_")] = f
    except OSError:
        pass
    return out


def list_logs(project_id: Optional[str] = None) -> list[dict]:
    items = []
    for name, f in _log_files(project_id).items():
        try:
            st = f.stat()
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            size, mtime = 0, 0.0
        items.append({"name": name, "size": size, "mtime": mtime})
    return items


def _format_jsonl(text: str) -> str:
    """pipeline.jsonl → "thời điểm · bước · nội dung" cho dễ đọc."""
    out = []
    for line in text.splitlines():
        try:
            e = json.loads(line)
            out.append(f"{str(e.get('ts', '')).replace('T', ' ')} · {e.get('stage', '')} · {e.get('message', '')}")
        except (ValueError, AttributeError):
            out.append(line)
    return "\n".join(out)


def _current_errors(project_id: str) -> list[str]:
    """Bước nào đang ở trạng thái lỗi (theo project.json) — có lỗi không được ghi
    vào pipeline.jsonl (vd lỗi tải video của dự án tự động), nên đưa lên đầu."""
    try:
        state = pj.load_project(project_id)
    except (OSError, ValueError):
        return []
    lines = []
    for name, rec in state.stages.items():
        if rec.status == "failed" and rec.error:
            lines.append(f"Lỗi: bước {name} · {rec.error}")
    for ep in state.episodes:
        for name, rec in ep.stages.items():
            if rec.status == "failed" and rec.error:
                lines.append(f"Lỗi: tập {ep.order} · bước {name} · {rec.error}")
    return lines


def read_log(name: str, max_lines: int = 1000, project_id: Optional[str] = None) -> str:
    """`max_lines` dòng cuối của 1 file log (đã che key/token). Raise
    FileNotFoundError nếu tên không thuộc danh sách log hiện có."""
    path = _log_files(project_id).get(name)
    if path is None:
        raise FileNotFoundError(name)
    text = _tail(path, VIEW_LOG_BYTES) if path.exists() else ""
    if path.suffix == ".jsonl":
        text = _format_jsonl(text)
    lines = text.splitlines()[-max_lines:]
    if name == PROJECT_LOG and project_id:
        errors = _current_errors(project_id)
        head = (["===== Lỗi hiện tại =====", *errors, ""] if errors else ["===== Không có bước nào đang lỗi =====", ""])
        lines = [_redact(l) for l in head] + (["===== Các bước đã chạy ====="] + lines if lines else ["(chưa có log bước nào)"])
    return "\n".join(lines)
