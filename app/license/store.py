"""Lưu phiên đăng nhập trên máy (user-management-plan.md 13.2).

- File `%LOCALAPPDATA%\\ReupVideoVjpPro\\auth.json` (không nằm trong workspace,
  nên đổi/copy workspace không mang phiên theo). Token mã hoá DPAPI
  (`secret_store`) — copy file sang tài khoản Windows khác là vô dụng.
- Cả file có HMAC: sửa tay (vd xoá số phút offline) là bị coi như chưa đăng nhập.
- Số phút offline ghi thêm 1 bản trong registry HKCU; lúc đọc lấy bản LỚN hơn.
  HMAC không chặn được chép đè lại bản auth.json cũ để reset ân hạn; muốn
  reset phải sửa đồng thời cả 2 nơi.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

from .. import secret_store
from . import machine

_REG_PATH = r"Software\ReupVideoVjpPro"
_lock = threading.Lock()


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(base) / "ReupVideoVjpPro"
    path.mkdir(parents=True, exist_ok=True)
    return path


def auth_path() -> Path:
    return data_dir() / "auth.json"


@dataclass
class AuthState:
    user_id: str = ""
    email: str = ""
    session_id: str = ""
    access_token: str = ""
    refresh_token: str = ""
    access_expires_at: float = 0.0  # epoch giây (theo "exp" trong JWT)
    # Phán quyết có chữ ký gần nhất — lúc offline chỉ tin cái này.
    verdict_payload: str = ""
    verdict_signature: str = ""
    last_server_now: float = 0.0  # epoch giây, từ phán quyết gần nhất
    offline_minutes: float = 0.0  # phút app CHẠY mà không heartbeat được
    blocked_minutes: float = 0.0  # phút Supabase không vào được trong khi Google vào được
    extra: dict = field(default_factory=dict)

    @property
    def signed_in(self) -> bool:
        return bool(self.user_id and self.refresh_token)


def _sign(body: dict) -> str:
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hmac.new(machine.local_secret(), raw, hashlib.sha256).hexdigest()


def load() -> AuthState:
    path = auth_path()
    if not path.exists():
        return AuthState()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        body, mac = doc["body"], doc["mac"]
        if not hmac.compare_digest(_sign(body), str(mac)):
            logger.warning("license: auth.json bị sửa (sai HMAC) — coi như chưa đăng nhập")
            return AuthState()
        for key in ("access_token", "refresh_token"):
            if body.get(key):
                body[key] = secret_store.unprotect(body[key])
        known = {k: v for k, v in body.items() if k in AuthState.__dataclass_fields__}
        state = AuthState(**known)
    except Exception as err:  # noqa: BLE001 — file hỏng/sai máy: đăng nhập lại là xong
        logger.warning("license: không đọc được auth.json ({}) — coi như chưa đăng nhập", err)
        return AuthState()
    if state.user_id:
        state.offline_minutes = max(state.offline_minutes, _reg_get_minutes(state.user_id))
    return state


def save(state: AuthState) -> None:
    body = asdict(state)
    for key in ("access_token", "refresh_token"):
        if body.get(key):
            body[key] = secret_store.protect(body[key])
    doc = {"body": body, "mac": _sign(body)}
    with _lock:
        path = auth_path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    if state.user_id:
        _reg_set_minutes(state.user_id, state.offline_minutes)


def clear() -> None:
    with _lock:
        try:
            auth_path().unlink()
        except FileNotFoundError:
            pass


def _reg_name(user_id: str) -> str:
    return "om_" + hashlib.sha256(f"{user_id}".encode()).hexdigest()[:16]


def _reg_get_minutes(user_id: str) -> float:
    if sys.platform != "win32":
        return 0.0
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as key:
            value, _ = winreg.QueryValueEx(key, _reg_name(user_id))
            return float(value)
    except (OSError, ValueError):
        return 0.0


def _reg_set_minutes(user_id: str, minutes: float) -> None:
    if sys.platform != "win32":
        return
    import winreg

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as key:
            winreg.SetValueEx(key, _reg_name(user_id), 0, winreg.REG_SZ, f"{minutes:.2f}")
    except OSError as err:
        logger.warning("license: không ghi được registry ({})", err)


def reset_offline(user_id: str) -> None:
    """Heartbeat thành công → số phút offline về 0 ở CẢ 2 nơi."""
    _reg_set_minutes(user_id, 0.0)


def account_hint() -> Optional[str]:
    """Email lần đăng nhập trước — điền sẵn ở màn đăng nhập."""
    try:
        return (data_dir() / "last_email.txt").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def remember_email(email: str) -> None:
    try:
        (data_dir() / "last_email.txt").write_text(email, encoding="utf-8")
    except OSError:
        pass
