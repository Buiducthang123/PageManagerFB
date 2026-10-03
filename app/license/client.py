"""Gọi Supabase (Auth REST, RPC, Edge Function) bằng httpx — không dùng
supabase-py để tránh kéo thêm thư viện vào bản đóng gói."""

from __future__ import annotations

import base64
import json
from typing import Any, Optional

import httpx

from . import constants

TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class OfflineError(Exception):
    """Không tới được Supabase (mất mạng, DNS, bị chặn, timeout)."""


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code


def _headers(access_token: Optional[str] = None) -> dict[str, str]:
    # Key publishable (sb_publishable_...) không phải JWT — chỉ gửi ở header
    # `apikey`; `Authorization` chỉ mang access token của user khi đã đăng nhập.
    h = {"apikey": constants.SUPABASE_ANON_KEY, "Content-Type": "application/json"}
    if access_token:
        h["Authorization"] = f"Bearer {access_token}"
    return h


def _request(method: str, path: str, *, access_token: Optional[str] = None, json_body: Any = None,
             params: Optional[dict] = None) -> Any:
    url = constants.SUPABASE_URL.rstrip("/") + path
    try:
        with httpx.Client(timeout=TIMEOUT) as c:
            res = c.request(method, url, headers=_headers(access_token), json=json_body, params=params)
    except httpx.TransportError as err:
        raise OfflineError(str(err) or type(err).__name__) from err
    if res.status_code >= 500 and res.status_code not in (502,):
        # Supabase sập/quá tải: coi như offline (không phải bị từ chối).
        raise OfflineError(f"Supabase lỗi {res.status_code}")
    try:
        data = res.json() if res.content else None
    except ValueError:
        data = None
    if res.status_code >= 400:
        msg, code = _error_text(data, res.text)
        raise ApiError(res.status_code, msg, code)
    return data


def _error_text(data: Any, raw: str) -> tuple[str, str]:
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        # Lỗi Graph API Facebook (qua fb-token): {"error": {"message", "code"}}
        inner = data["error"]
        return str(inner.get("message") or raw), str(inner.get("code") or "")
    if isinstance(data, dict):
        code = str(data.get("error_code") or data.get("code") or data.get("error") or "")
        msg = str(data.get("msg") or data.get("message") or data.get("error_description") or data.get("error") or raw)
        return msg, code
    return raw[:300], ""


def jwt_claims(token: str) -> dict:
    """Đọc claim của JWT (không kiểm tra chữ ký — chỉ để biết exp/session_id;
    server mới là nơi kiểm tra)."""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except (IndexError, ValueError):
        return {}


def password_login(email: str, password: str) -> dict:
    return _request("POST", "/auth/v1/token", params={"grant_type": "password"},
                    json_body={"email": email, "password": password})


def refresh(refresh_token: str) -> dict:
    return _request("POST", "/auth/v1/token", params={"grant_type": "refresh_token"},
                    json_body={"refresh_token": refresh_token})


def logout(access_token: str) -> None:
    _request("POST", "/auth/v1/logout", access_token=access_token, params={"scope": "local"})


def update_password(access_token: str, new_password: str) -> None:
    _request("PUT", "/auth/v1/user", access_token=access_token, json_body={"password": new_password})


def rpc(access_token: str, name: str, args: Optional[dict] = None) -> Any:
    return _request("POST", f"/rest/v1/rpc/{name}", access_token=access_token, json_body=args or {})


def select(access_token: Optional[str], table: str, params: dict) -> Any:
    return _request("GET", f"/rest/v1/{table}", access_token=access_token, params=params)


def request_rest(method: str, table: str, access_token: str, *, params: Optional[dict] = None,
                 json_body: Any = None) -> Any:
    """REST bảng/view (trang admin). RLS phía server quyết định được đọc/ghi gì."""
    return _request(method, f"/rest/v1/{table}", access_token=access_token, params=params, json_body=json_body)


def function(access_token: str, name: str, body: dict) -> Any:
    return _request("POST", f"/functions/v1/{name}", access_token=access_token, json_body=body)


def probe_internet() -> bool:
    """Mạng chung có vào được không (để phân biệt "mất mạng" với "chặn riêng
    Supabase", 13.2). Có phản hồi HTTP bất kỳ là coi như vào được."""
    for url in ("https://generativelanguage.googleapis.com/", "https://www.google.com/generate_204"):
        try:
            with httpx.Client(timeout=httpx.Timeout(8.0)) as c:
                c.get(url)
            return True
        except httpx.TransportError:
            continue
    return False
