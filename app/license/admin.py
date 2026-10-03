"""Thao tác trang /admin (user-management-plan.md mục 7).

Mọi lệnh đi bằng PHIÊN CỦA ADMIN đang đăng nhập: đọc/sửa bảng qua REST (RLS
`is_admin()` phía server mới là chốt chặn), thao tác cần service role qua
Edge Function `admin-users`. Máy này không giữ key quyền cao nào.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from . import client
from .manager import FEATURES, manager


class AdminError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _call(fn, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except client.OfflineError as err:
        raise AdminError(503, f"Không kết nối được máy chủ: {err}") from err
    except client.ApiError as err:
        status = err.status if err.status in (400, 401, 403, 404, 409) else 400
        raise AdminError(status, str(err)) from err


def list_users() -> list[dict]:
    return _call(manager.query, "admin_user_stats", {"select": "*", "order": "created_at.asc"}) or []


def recent_devices(user_id: str) -> list[dict]:
    return _call(manager.query, "device_events", {
        "select": "device_name,at", "user_id": f"eq.{user_id}", "order": "at.desc", "limit": "20",
    }) or []


def _clean_features(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise AdminError(400, "features phải là danh sách")
    return [f for f in FEATURES if f in {str(x) for x in raw}]


def update_user(user_id: str, body: dict) -> None:
    patch: dict[str, Any] = {}
    if "display_name" in body:
        patch["display_name"] = str(body["display_name"] or "")[:80]
    if "enabled" in body:
        patch["enabled"] = bool(body["enabled"])
    if "features" in body:
        patch["features"] = _clean_features(body["features"])
    if "role" in body:
        if body["role"] not in ("admin", "user"):
            raise AdminError(400, "role không hợp lệ")
        if user_id == manager.current_user_id() and body["role"] != "admin":
            raise AdminError(400, "Không tự bỏ quyền admin của tài khoản đang dùng")
        patch["role"] = body["role"]
    if "session_ttl_hours" in body:
        ttl = body["session_ttl_hours"]
        if ttl is not None and (not isinstance(ttl, int) or ttl < 1):
            raise AdminError(400, "Thời hạn phiên không hợp lệ")
        patch["session_ttl_hours"] = ttl
    if "account_expires_at" in body:
        raw = body["account_expires_at"]
        if raw:
            try:
                datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError as err:
                raise AdminError(400, "Ngày hết hạn không hợp lệ") from err
        patch["account_expires_at"] = raw or None
    if user_id == manager.current_user_id() and patch.get("enabled") is False:
        raise AdminError(400, "Không tự khoá tài khoản admin đang dùng")
    if patch:
        _call(manager.write, "PATCH", "profiles", {"id": f"eq.{user_id}"}, patch)
    if "note" in body:
        # Dòng admin_notes đã có sẵn (trigger tạo cùng lúc với profile).
        _call(manager.write, "PATCH", "admin_notes", {"user_id": f"eq.{user_id}"},
              {"note": str(body["note"] or "")[:2000], "updated_at": datetime.now(timezone.utc).isoformat()})


def create_user(body: dict) -> dict:
    payload = {
        "action": "create",
        "email": str(body.get("email") or "").strip(),
        "password": str(body.get("password") or ""),
        "display_name": str(body.get("display_name") or ""),
        "features": _clean_features(body.get("features") or []),
        "session_ttl_hours": body.get("session_ttl_hours"),
        "enabled": bool(body.get("enabled", True)),
    }
    return _call(manager.call_function, "admin-users", payload)


def reset_password(user_id: str, password: str) -> None:
    _call(manager.call_function, "admin-users", {"action": "reset_password", "user_id": user_id, "password": password})


def delete_user(user_id: str) -> None:
    if user_id == manager.current_user_id():
        raise AdminError(400, "Không tự xoá tài khoản admin đang dùng")
    _call(manager.call_function, "admin-users", {"action": "delete", "user_id": user_id})


def force_logout(user_id: str) -> None:
    _call(manager.call_rpc, "admin_force_logout", {"p_user": user_id})


def unbind_device(user_id: str) -> None:
    _call(manager.call_rpc, "admin_unbind_device", {"p_user": user_id})


def get_config() -> dict:
    rows = _call(manager.query, "app_config", {"select": "*", "id": "eq.1"}) or [{}]
    return rows[0]


def update_config(body: dict) -> dict:
    patch: dict[str, Any] = {}
    if "login_notice" in body:
        patch["login_notice"] = str(body["login_notice"] or "")[:500]
    if "min_app_version" in body:
        v = str(body["min_app_version"] or "").strip()
        patch["min_app_version"] = v or None
    for key, lo, hi in (("offline_grace_minutes", 0, 7 * 24 * 60), ("heartbeat_seconds", 60, 3600)):
        if key in body:
            try:
                value = int(body[key])
            except (TypeError, ValueError) as err:
                raise AdminError(400, f"{key} phải là số") from err
            if not lo <= value <= hi:
                raise AdminError(400, f"{key} phải trong khoảng {lo}–{hi}")
            patch[key] = value
    if patch:
        _call(manager.write, "PATCH", "app_config", {"id": "eq.1"}, patch)
    return get_config()


def require_admin() -> Optional[AdminError]:
    if manager.mode != "enabled":
        return AdminError(400, "Chưa bật đăng nhập (chạy từ source không cấu hình Supabase)")
    if not manager.can_use() or not manager.is_admin():
        return AdminError(403, "Chỉ admin")
    return None
