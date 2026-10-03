from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

import requests
from loguru import logger

from .. import config
from ..license import manager as license_manager
from ..license import client as license_client
from ..license import constants as license_constants

# Đăng Reels lên Facebook Page qua Graph API chính thức (không dùng Chrome như
# TikTok) — port lại từ PagesManagerSupperTool (backend/src/facebook/
# facebook.service.ts): resumable upload 3 bước start → đẩy bytes lên
# rupload.facebook.com → finish(video_state=PUBLISHED), rồi poll trạng thái.


class FacebookPublishError(RuntimeError):
    """Lỗi đăng Facebook. `permanent=True` = thử lại cũng lỗi y hệt (video sai
    định dạng, thiếu quyền...) — bộ lập lịch không tự thử lại nữa."""

    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


class FacebookTokenError(FacebookPublishError):
    """Token Page hết hạn / bị thu hồi — phải nhập lại token."""

    def __init__(self, message: str):
        super().__init__(message, permanent=True)


# Mã lỗi Graph API: 190 = token hỏng; 10/200-299 = thiếu quyền; 4/17/32/613 =
# giới hạn tốc độ (tạm thời); 1363xxx (subcode) = video không hợp lệ cho Reels.
_TOKEN_CODES = {190, 102}
_RATE_CODES = {4, 17, 32, 613}


def _graph(path: str) -> str:
    return f"https://graph.facebook.com/{config.FB_GRAPH_VERSION}{path}"


def _raise_for(resp: requests.Response, what: str) -> None:
    try:
        err = resp.json().get("error") or {}
    except Exception:
        err = {}
    if not err and resp.ok:
        return
    code = err.get("code")
    sub = err.get("error_subcode")
    msg = err.get("error_user_msg") or err.get("message") or f"HTTP {resp.status_code}"
    detail = f"{what}: {msg}" + (f" (mã {code}" + (f"/{sub}" if sub else "") + ")" if code else "")
    if code in _TOKEN_CODES:
        raise FacebookTokenError(f"{detail} — token Page hết hạn/bị thu hồi, vào trang Tài khoản nhập lại token")
    if code in _RATE_CODES or resp.status_code >= 500:
        raise FacebookPublishError(detail)
    if code == 10 or (isinstance(code, int) and 200 <= code < 300):
        raise FacebookPublishError(f"{detail} — thiếu quyền đăng video của Page", permanent=True)
    if isinstance(sub, int) and 1363000 <= sub < 1364000:
        raise FacebookPublishError(detail, permanent=True)
    raise FacebookPublishError(detail, permanent=400 <= resp.status_code < 500)


def _get(path: str, params: dict, what: str, timeout: float = 30) -> dict:
    try:
        resp = requests.get(_graph(path), params=params, timeout=timeout)
    except requests.RequestException as err:
        raise FacebookPublishError(f"{what}: lỗi mạng — {err}") from err
    _raise_for(resp, what)
    return resp.json()


def _post(path: str, data: dict, what: str, timeout: float = 60) -> dict:
    try:
        resp = requests.post(_graph(path), data=data, timeout=timeout)
    except requests.RequestException as err:
        raise FacebookPublishError(f"{what}: lỗi mạng — {err}") from err
    _raise_for(resp, what)
    return resp.json()


# ------------------------------------------------------------------ Token / Page


def page_info(page_id_or_me: str, token: str) -> dict:
    """Thông tin Page — dùng luôn để kiểm tra token còn sống không."""
    return _get(
        f"/{page_id_or_me}",
        {"fields": "id,name,category,picture{url}", "access_token": token},
        "Đọc thông tin Page",
    )


def pages_from_user_token(user_token: str) -> list[dict]:
    """Danh sách Page (kèm Page token) mà user token quản lý. Page token lấy từ
    user token DÀI HẠN thì không hết hạn; từ user token ngắn hạn thì cũng chỉ
    sống ~1-2h."""
    out: list[dict] = []
    params = {"fields": "id,name,category,access_token,tasks,picture{url}", "limit": 100, "access_token": user_token}
    data = _get("/me/accounts", params, "Lấy danh sách Page")
    out.extend(data.get("data") or [])
    nxt = (data.get("paging") or {}).get("next")
    while nxt:
        try:
            resp = requests.get(nxt, timeout=30)
        except requests.RequestException as err:
            raise FacebookPublishError(f"Lấy danh sách Page: lỗi mạng — {err}") from err
        _raise_for(resp, "Lấy danh sách Page")
        data = resp.json()
        out.extend(data.get("data") or [])
        nxt = (data.get("paging") or {}).get("next")
    return out


CALLBACK_PATH = "/api/facebook/callback"
NGROK_API = "http://127.0.0.1:4040/api/tunnels"


def _use_edge_function() -> bool:
    """Bật đăng nhập (Supabase) → đổi token qua Edge Function `fb-token`, máy
    user không cần FB_APP_SECRET (kế hoạch 13.1). Chạy dev chưa bật đăng nhập
    thì giữ cách cũ (secret trong .env)."""
    return license_manager.mode == "enabled"


def oauth_configured() -> bool:
    if _use_edge_function():
        return bool(config.FB_APP_ID)
    return bool(config.FB_APP_ID and config.FB_APP_SECRET)


def _edge_exchange(body: dict, label: str) -> dict:
    try:
        data = license_manager.call_function("fb-token", body)
    except license_client.OfflineError as err:
        raise FacebookPublishError(f"{label}: không kết nối được máy chủ — {err}") from err
    except license_client.ApiError as err:
        msg = str(err)
        low = msg.lower()
        if "app not active" in low or "not available" in low or "app_not_setup" in low or "can't load url" in low:
            msg = "Tài khoản Facebook này chưa được admin thêm vào app — liên hệ admin"
        raise FacebookPublishError(f"{label}: {msg}") from err
    if not isinstance(data, dict):
        raise FacebookPublishError(f"{label}: máy chủ trả dữ liệu lạ")
    return data


def resolve_redirect_uri() -> Optional[str]:
    """redirect_uri cho đăng nhập Facebook. FB_REDIRECT_URI trong .env (khác
    "auto") thì dùng nguyên; không thì hỏi ngrok đang chạy trên máy (API cục
    bộ cổng 4040) tên miền của tunnel trỏ vào cổng frontend — tên miền ngrok
    miễn phí đổi mỗi lần bật lại, tự dò thì khỏi sửa .env. None = ngrok chưa chạy."""
    if _use_edge_function():
        # App Live bắt redirect HTTPS → đi qua trạm chuyển tiếp fb-callback
        # trên Supabase, nó đẩy tiếp về localhost:<cổng trong state> (13.8).
        return f"{license_constants.SUPABASE_URL.rstrip('/')}/functions/v1/fb-callback"
    if config.FB_REDIRECT_URI and config.FB_REDIRECT_URI.lower() != "auto":
        return config.FB_REDIRECT_URI
    try:
        tunnels = requests.get(NGROK_API, timeout=2).json().get("tunnels") or []
    except Exception:
        return None
    https = [t for t in tunnels if str(t.get("public_url", "")).startswith("https://")]
    port = config.FRONTEND_URL.rsplit(":", 1)[-1]
    preferred = [t for t in https if str((t.get("config") or {}).get("addr", "")).endswith(f":{port}")]
    chosen = (preferred or https or [None])[0]
    return f"{chosen['public_url'].rstrip('/')}{CALLBACK_PATH}" if chosen else None


def login_dialog_url(state: str, redirect_uri: str) -> str:
    params = {
        "client_id": config.FB_APP_ID,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": config.FB_LOGIN_SCOPES,
        "response_type": "code",
        # Luôn hỏi lại quyền → hiện bước chọn Page, để tick thêm Page mới tạo.
        "auth_type": "rerequest",
    }
    return f"https://www.facebook.com/{config.FB_GRAPH_VERSION}/dialog/oauth?{requests.compat.urlencode(params)}"


def exchange_code(code: str, redirect_uri: str) -> str:
    """Mã `code` Facebook trả về callback → user token (ngắn hạn).
    `redirect_uri` phải y hệt lúc mở hộp thoại đăng nhập."""
    if _use_edge_function():
        data = _edge_exchange(
            {"action": "exchange_code", "code": code, "redirect_uri": redirect_uri}, "Đổi mã đăng nhập Facebook"
        )
        token = data.get("access_token")
        if not token:
            raise FacebookPublishError("Facebook không trả access_token sau khi đăng nhập")
        return token
    data = _get(
        "/oauth/access_token",
        {
            "client_id": config.FB_APP_ID,
            "client_secret": config.FB_APP_SECRET,
            "redirect_uri": redirect_uri,
            "code": code,
        },
        "Đổi mã đăng nhập Facebook",
    )
    token = data.get("access_token")
    if not token:
        raise FacebookPublishError("Facebook không trả access_token sau khi đăng nhập")
    return token


def exchange_long_lived(user_token: str) -> Optional[str]:
    """Đổi user token ngắn hạn → dài hạn (~60 ngày). Chỉ chạy được khi có
    FB_APP_ID/FB_APP_SECRET trong .env (hoặc qua Edge Function khi bật đăng
    nhập) — không có thì trả None."""
    if _use_edge_function():
        return _edge_exchange({"action": "extend", "token": user_token}, "Đổi token dài hạn").get("access_token")
    if not (config.FB_APP_ID and config.FB_APP_SECRET):
        return None
    data = _get(
        "/oauth/access_token",
        {
            "grant_type": "fb_exchange_token",
            "client_id": config.FB_APP_ID,
            "client_secret": config.FB_APP_SECRET,
            "fb_exchange_token": user_token,
        },
        "Đổi token dài hạn",
    )
    return data.get("access_token")


def token_expires_at(token: str) -> Optional[int]:
    """Unix time token hết hạn (0 = không hết hạn), None = không đọc được."""
    try:
        data = _get("/debug_token", {"input_token": token, "access_token": token}, "Đọc hạn token")
    except FacebookPublishError as err:
        logger.info("facebook: không đọc được hạn token — {}", err)
        return None
    info = data.get("data") or {}
    exp = info.get("data_access_expires_at") if info.get("expires_at") is None else info.get("expires_at")
    return int(exp) if exp is not None else None


# ------------------------------------------------------------------ Reels


def _phase_errors(status: dict) -> list[str]:
    msgs: list[str] = []
    for phase in ("uploading_phase", "processing_phase", "publishing_phase"):
        p = status.get(phase) or {}
        if p.get("status") == "error":
            errs = p.get("errors") or []
            detail = "; ".join(str(e.get("message") or e) for e in errs) if errs else "lỗi không rõ"
            msgs.append(f"{phase}: {detail}")
    return msgs


def publish_reel(
    page_id: str,
    token: str,
    video_path: Path,
    description: str,
    on_progress: Optional[Callable[[str], None]] = None,
    confirm_timeout_s: float = 900.0,
) -> dict:
    """Đăng 1 Reel. Trả {"video_id", "permalink_url", "confirmed"}.

    Sau bước finish, Facebook đã NHẬN yêu cầu đăng — nếu chờ xác nhận quá
    `confirm_timeout_s` mà vẫn đang xử lý thì coi là đã đăng (`confirmed=False`)
    thay vì báo lỗi: báo lỗi sẽ khiến bộ lập lịch thử lại và đăng TRÙNG."""
    progress = on_progress or (lambda _msg: None)
    if not video_path.exists():
        raise FacebookPublishError(f"Không thấy file video: {video_path}", permanent=True)
    size = video_path.stat().st_size

    progress("Khởi tạo upload Reel...")
    start = _post(f"/{page_id}/video_reels", {"upload_phase": "start", "access_token": token}, "Khởi tạo upload Reel")
    video_id = start.get("video_id")
    upload_url = start.get("upload_url")
    if not video_id or not upload_url:
        raise FacebookPublishError(f"Khởi tạo upload Reel: phản hồi thiếu video_id/upload_url ({start})")

    progress(f"Đang tải video lên Facebook ({size / 1_048_576:.0f} MB)...")
    try:
        with video_path.open("rb") as f:
            resp = requests.post(
                upload_url,
                data=f,
                headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)},
                timeout=(30, 1800),
            )
    except requests.RequestException as err:
        raise FacebookPublishError(f"Tải video lên Facebook: lỗi mạng — {err}") from err
    _raise_for(resp, "Tải video lên Facebook")

    progress("Đang gửi yêu cầu đăng Reel...")
    _post(
        f"/{page_id}/video_reels",
        {
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": description,
            "access_token": token,
        },
        "Đăng Reel",
    )

    progress("Facebook đang xử lý video...")
    deadline = time.time() + confirm_timeout_s
    last: dict = {}
    while time.time() < deadline:
        time.sleep(8)
        try:
            last = _get(f"/{video_id}", {"fields": "status,permalink_url", "access_token": token}, "Kiểm tra trạng thái Reel")
        except FacebookTokenError:
            raise
        except FacebookPublishError as err:
            logger.warning("facebook: kiểm tra trạng thái Reel {} lỗi (thử lại) — {}", video_id, err)
            continue
        status = last.get("status") or {}
        errors = _phase_errors(status)
        if errors or status.get("video_status") == "error":
            raise FacebookPublishError("Facebook từ chối video: " + ("; ".join(errors) or "video_status=error"), permanent=True)
        publishing = (status.get("publishing_phase") or {}).get("status")
        progress(f"Facebook đang xử lý video ({status.get('video_status') or '...'})...")
        if publishing == "complete":
            return {"video_id": video_id, "permalink_url": _abs_url(last.get("permalink_url")), "confirmed": True}
    logger.warning("facebook: Reel {} chưa xác nhận xong sau {:.0f}s — coi là đã đăng", video_id, confirm_timeout_s)
    return {"video_id": video_id, "permalink_url": _abs_url(last.get("permalink_url")), "confirmed": False}


def _abs_url(url: Optional[str]) -> Optional[str]:
    if url and url.startswith("/"):
        return f"https://www.facebook.com{url}"
    return url
