"""Trạng thái đăng nhập/quyền của máy này (user-management-plan.md mục 4, 13.2, 13.9).

Nguồn sự thật duy nhất là PHÁN QUYẾT CÓ CHỮ KÝ từ Edge Function `heartbeat`.
Mọi thứ app tự ghi trên máy (auth.json, registry) chỉ dùng để đếm thời gian
offline — không bao giờ để mở thêm quyền.

Trạng thái:
  disabled      — chạy từ source mà chưa cấu hình Supabase (chế độ dev cũ)
  misconfigured — bản đóng gói thiếu cấu hình: chặn hết
  signed_out    — chưa đăng nhập / bị đăng xuất (kèm lý do)
  active        — heartbeat gần nhất thành công, được dùng
  offline       — không heartbeat được, còn trong thời gian ân hạn
  locked        — hết ân hạn / phát hiện chặn Supabase / đồng hồ bị lùi
  blocked       — server từ chối nhưng vẫn giữ phiên (cần cập nhật bản mới)
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import threading
import time
from datetime import datetime
from typing import Any, Callable, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from loguru import logger

from . import client, constants, machine, store, usage

FEATURES = (
    "projects", "capcut", "tiktok_publish", "facebook_publish", "automated",
    "download", "merge", "clean_video", "monitor", "cleanup",
)

# Nuitka gắn biến này vào mọi module đã biên dịch. Không dựa vào env/file
# cấu hình — user tự đặt được (15.2).
IS_COMPILED = "__compiled__" in globals()

# Supabase không vào được trong khi Google vào được quá mốc này → coi là bị
# chặn có chủ đích (file hosts/tường lửa), không cho ân hạn nữa (13.2).
BLOCK_DETECT_MINUTES = 30.0
# Đồng hồ Windows lùi quá mốc này so với giờ server lần cuối → khoá.
CLOCK_ROLLBACK_TOLERANCE_S = 600
OFFLINE_RETRY_S = 60
LOCKED_RETRY_S = 30

SESSION_REVOKED_MSG = (
    "Phiên đăng nhập đã bị thu hồi — tài khoản vừa đăng nhập ở máy khác, hoặc admin đã đăng xuất/đặt lại mật khẩu"
)


class LoginError(Exception):
    pass


def licensing_mode() -> str:
    configured = bool(constants.SUPABASE_URL and constants.SUPABASE_ANON_KEY and constants.HEARTBEAT_PUBLIC_KEY)
    if not configured:
        return "misconfigured" if IS_COMPILED else "disabled"
    if not IS_COMPILED and os.environ.get("LICENSE_DISABLED", "").strip() == "1":
        return "disabled"
    return "enabled"


def _parse_ts(value: Any) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def verify_signed(res: Any, *, nonce: Optional[str], user_id: str, session_id: str) -> Optional[dict]:
    """Kiểm tra chữ ký Ed25519 + nonce + đúng user/phiên. None = không tin được."""
    try:
        payload = base64.b64decode(res["payload"])
        signature = base64.b64decode(res["signature"])
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(constants.HEARTBEAT_PUBLIC_KEY))
        key.verify(signature, payload)
        verdict = json.loads(payload)
    except (KeyError, TypeError, ValueError, InvalidSignature):
        return None
    if not isinstance(verdict, dict):
        return None
    if nonce is not None and verdict.get("nonce") != nonce:
        return None
    # Phán quyết từ chối kiểu no_auth không có user_id; phán quyết cho phép
    # bắt buộc đúng user + đúng phiên.
    if verdict.get("user_id") and verdict.get("user_id") != user_id:
        return None
    if verdict.get("ok") and (verdict.get("user_id") != user_id or verdict.get("session_id") != session_id):
        return None
    return verdict


class LicenseManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.mode = licensing_mode()
        self.auth = store.AuthState()
        self.verdict: Optional[dict] = None
        self.status = "disabled" if self.mode == "disabled" else "signed_out"
        self.code = ""
        self.message = ""
        self._online = False
        self._last_attempt_mono = time.monotonic()
        # main.py gán: () -> (số tài khoản TikTok, số Page Facebook)
        self.counts_provider: Optional[Callable[[], tuple[int, int]]] = None
        # Gọi khi trạng thái đổi giữa "dùng được" và "không" (dừng/chạy lại phần nền).
        self.on_change: Optional[Callable[[], None]] = None

        if self.mode == "misconfigured":
            self.status, self.message = "locked", "Bản cài thiếu cấu hình máy chủ — tải lại bộ cài"
        elif self.mode == "enabled":
            self._load_saved()

    # ------------------------------------------------------------ khởi động

    def _load_saved(self) -> None:
        auth = store.load()
        if not auth.signed_in:
            return
        verdict = verify_signed(
            {"payload": auth.verdict_payload, "signature": auth.verdict_signature},
            nonce=None, user_id=auth.user_id, session_id=auth.session_id,
        )
        if verdict is None:
            logger.warning("license: phán quyết lưu trên máy không hợp lệ — đăng nhập lại")
            store.clear()
            return
        self.auth = auth
        self.verdict = verdict
        self._evaluate_offline(initial=True)

    def start(self) -> None:
        if self.mode != "enabled" or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="license-heartbeat", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while True:
            interval = OFFLINE_RETRY_S
            try:
                with self._lock:
                    signed_in = self.auth.signed_in
                if signed_in:
                    self.heartbeat()
                    with self._lock:
                        if self._online:
                            interval = self._heartbeat_seconds()
                        elif self.status == "locked":
                            interval = LOCKED_RETRY_S
                else:
                    interval = 3600
            except Exception:
                logger.exception("license: lỗi không mong đợi ở heartbeat")
            self._wake.wait(interval)
            self._wake.clear()

    def wake(self) -> None:
        """"Thử lại ngay" ở modal khoá."""
        self._wake.set()

    def _heartbeat_seconds(self) -> int:
        if not IS_COMPILED:
            override = os.environ.get("LICENSE_HEARTBEAT_S", "").strip()
            if override.isdigit():
                return max(10, int(override))
        try:
            return max(60, int((self.verdict or {}).get("heartbeat_seconds") or 300))
        except (TypeError, ValueError):
            return 300

    # ------------------------------------------------------------ đăng nhập

    def login(self, email: str, password: str) -> dict:
        if self.mode != "enabled":
            raise LoginError("Bản này không cần đăng nhập" if self.mode == "disabled" else self.message)
        email = email.strip().lower()
        try:
            tokens = client.password_login(email, password)
        except client.OfflineError as err:
            raise LoginError("Không kết nối được máy chủ — kiểm tra mạng rồi thử lại") from err
        except client.ApiError as err:
            if err.status in (400, 401) or err.code == "invalid_credentials":
                raise LoginError("Sai email hoặc mật khẩu") from err
            raise LoginError(f"Đăng nhập lỗi: {err}") from err

        access = tokens.get("access_token", "")
        claims = client.jwt_claims(access)
        try:
            client.rpc(access, "claim_device", {"p_device_id": machine.device_id(), "p_device_name": machine.device_name()})
        except (client.OfflineError, client.ApiError) as err:
            self._best_effort_logout(access)
            raise LoginError(str(err) or "Không nhận được máy") from err

        with self._lock:
            self.auth = store.AuthState(
                user_id=str(claims.get("sub") or (tokens.get("user") or {}).get("id") or ""),
                email=email,
                session_id=str(claims.get("session_id") or ""),
                access_token=access,
                refresh_token=tokens.get("refresh_token", ""),
                access_expires_at=float(claims.get("exp") or 0),
            )
            self.verdict = None
            self.message = ""
            self._last_attempt_mono = time.monotonic()
        store.reset_offline(self.auth.user_id)
        ok = self.heartbeat()
        with self._lock:
            if not ok or self.verdict is None:
                reason = self.message or "Không lấy được xác nhận từ máy chủ"
                self._clear_local()
                raise LoginError(reason)
            if self.status == "signed_out":
                raise LoginError(self.message or "Không đăng nhập được")
        store.remember_email(email)
        logger.info("license: đăng nhập {} trên máy {}", email, machine.device_name())
        return self.snapshot()

    def logout(self) -> None:
        with self._lock:
            access = self.auth.access_token
            signed_in = self.auth.signed_in
        if signed_in:
            try:
                client.rpc(access, "release_device")
            except (client.OfflineError, client.ApiError):
                pass
            self._best_effort_logout(access)
        with self._lock:
            self._clear_local()
            self.message = ""
            self.code = ""
        self._notify()

    def register(self, email: str, password: str, display_name: str, contact: str) -> None:
        """Gửi yêu cầu tạo tài khoản — chờ admin duyệt mới đăng nhập được."""
        if self.mode != "enabled":
            raise LoginError("Bản này không cần đăng nhập" if self.mode == "disabled" else self.message)
        email = email.strip().lower()
        if len(password) < 8:
            raise LoginError("Mật khẩu tối thiểu 8 ký tự")
        if not display_name.strip():
            raise LoginError("Nhập tên của bạn")
        try:
            client.register(email, password, display_name.strip(), contact.strip())
        except client.OfflineError as err:
            raise LoginError("Không kết nối được máy chủ — kiểm tra mạng rồi thử lại") from err
        except client.ApiError as err:
            if err.status == 404:
                raise LoginError("Máy chủ chưa mở chức năng đăng ký — liên hệ admin") from err
            raise LoginError(str(err) or "Không đăng ký được") from err
        store.remember_email(email)
        logger.info("license: gửi yêu cầu đăng ký {}", email)

    def change_password(self, new_password: str) -> None:
        if len(new_password) < 8:
            raise LoginError("Mật khẩu mới tối thiểu 8 ký tự")
        with self._lock:
            if not self.auth.signed_in:
                raise LoginError("Chưa đăng nhập")
        try:
            self._ensure_access_token()
            client.update_password(self.auth.access_token, new_password)
        except client.OfflineError as err:
            raise LoginError("Không kết nối được máy chủ") from err
        except client.ApiError as err:
            if "different from the old" in str(err).lower() or err.code == "same_password":
                raise LoginError("Mật khẩu mới phải khác mật khẩu cũ") from err
            raise LoginError(f"Không đổi được mật khẩu: {err}") from err

    def call_function(self, name: str, body: dict) -> Any:
        """Gọi Edge Function bằng phiên của user đang đăng nhập (fb-token,
        admin-users). Lỗi trả về dạng client.ApiError/OfflineError."""
        with self._lock:
            if not self.auth.signed_in:
                raise client.ApiError(401, "Chưa đăng nhập")
        self._ensure_access_token()
        return client.function(self.auth.access_token, name, body)

    def call_rpc(self, name: str, args: Optional[dict] = None) -> Any:
        with self._lock:
            if not self.auth.signed_in:
                raise client.ApiError(401, "Chưa đăng nhập")
        self._ensure_access_token()
        return client.rpc(self.auth.access_token, name, args)

    def query(self, table: str, params: dict) -> Any:
        with self._lock:
            if not self.auth.signed_in:
                raise client.ApiError(401, "Chưa đăng nhập")
        self._ensure_access_token()
        return client.request_rest("GET", table, self.auth.access_token, params=params)

    def write(self, method: str, table: str, params: dict, body: Any) -> Any:
        with self._lock:
            if not self.auth.signed_in:
                raise client.ApiError(401, "Chưa đăng nhập")
        self._ensure_access_token()
        return client.request_rest(method, table, self.auth.access_token, params=params, json_body=body)

    def _best_effort_logout(self, access: str) -> None:
        if not access:
            return
        try:
            client.logout(access)
        except (client.OfflineError, client.ApiError):
            pass

    def _clear_local(self) -> None:
        store.clear()
        self.auth = store.AuthState()
        self.verdict = None
        self._online = False
        self.status = "signed_out"

    def _sign_out(self, code: str, message: str) -> None:
        logger.warning("license: đăng xuất máy này ({}): {}", code, message)
        self._clear_local()
        self.code, self.message = code, message

    # ------------------------------------------------------------ heartbeat

    def _ensure_access_token(self) -> None:
        """Làm mới access token khi sắp hết hạn. ApiError = refresh token bị
        thu hồi; OfflineError = mất mạng."""
        if self.auth.access_expires_at - time.time() > 120:
            return
        tokens = client.refresh(self.auth.refresh_token)
        access = tokens.get("access_token", "")
        claims = client.jwt_claims(access)
        with self._lock:
            self.auth.access_token = access
            self.auth.refresh_token = tokens.get("refresh_token", self.auth.refresh_token)
            self.auth.access_expires_at = float(claims.get("exp") or 0)
            store.save(self.auth)

    def heartbeat(self) -> bool:
        """1 lần heartbeat. True = nhận được phán quyết hợp lệ."""
        with self._lock:
            if not self.auth.signed_in:
                return False
            user_id = self.auth.user_id

        try:
            self._ensure_access_token()
        except client.OfflineError:
            self._failure(transport=True)
            return False
        except client.ApiError:
            if self.auth.access_expires_at <= time.time():
                with self._lock:
                    self._sign_out("session_revoked", SESSION_REVOKED_MSG)
                self._notify()
                return False
            # access token còn hạn → heartbeat để server nói rõ lý do.

        nonce = secrets.token_urlsafe(24)
        events = usage.pending(user_id)
        tiktok, fb = self._counts()
        body = {
            "nonce": nonce,
            "app_version": constants.APP_VERSION,
            "tiktok_accounts": tiktok,
            "facebook_pages": fb,
            "events": [{k: e[k] for k in ("project_id", "type", "at")} for e in events],
        }
        try:
            res = client.function(self.auth.access_token, "heartbeat", body)
        except client.OfflineError:
            self._failure(transport=True)
            return False
        except client.ApiError as err:
            if err.status == 401:
                with self._lock:
                    self._sign_out("session_revoked", SESSION_REVOKED_MSG)
                self._notify()
                return False
            logger.warning("license: heartbeat bị từ chối ({}): {}", err.status, err)
            self._failure(transport=False)
            return False

        verdict = verify_signed(res, nonce=nonce, user_id=user_id, session_id=self.auth.session_id)
        if verdict is None:
            # Phản hồi bị sửa hoặc máy chủ cấu hình sai khoá: KHÔNG tin, coi như offline.
            logger.warning("license: phán quyết heartbeat sai chữ ký — bỏ qua")
            self._failure(transport=False)
            return False

        self._apply(verdict, res)
        if verdict.get("ok") and events:
            usage.ack(user_id, events)
        return True

    def _counts(self) -> tuple[int, int]:
        if self.counts_provider is None:
            return 0, 0
        try:
            return self.counts_provider()
        except Exception:
            return 0, 0

    def _apply(self, verdict: dict, res: dict) -> None:
        with self._lock:
            before = self.can_use()
            self._last_attempt_mono = time.monotonic()
            self._online = True
            code = str(verdict.get("code") or "")
            if verdict.get("ok") or (code == "update_required" and not IS_COMPILED):
                # Chạy từ source (dev) bỏ qua kiểm tra phiên bản (15.2).
                if not verdict.get("ok"):
                    verdict = {**verdict, "ok": True}
                self.verdict = verdict
                self.auth.verdict_payload = res["payload"]
                self.auth.verdict_signature = res["signature"]
                self.auth.last_server_now = _parse_ts(verdict.get("server_now"))
                self.auth.offline_minutes = 0.0
                self.auth.blocked_minutes = 0.0
                store.save(self.auth)
                store.reset_offline(self.auth.user_id)
                self.status, self.code, self.message = "active", "", ""
            elif code == "update_required":
                self.verdict = verdict
                self.auth.verdict_payload = res["payload"]
                self.auth.verdict_signature = res["signature"]
                store.save(self.auth)
                self.status, self.code = "blocked", code
                self.message = str(verdict.get("message") or "Cần cập nhật lên bản mới")
            else:
                self._sign_out(code or "denied", str(verdict.get("message") or "Không còn quyền sử dụng"))
            changed = before != self.can_use()
        if changed:
            self._notify()

    def _failure(self, *, transport: bool) -> None:
        now_m = time.monotonic()
        with self._lock:
            elapsed_min = max(0.0, (now_m - self._last_attempt_mono) / 60)
            self._last_attempt_mono = now_m
            self._online = False
        # Dò mạng chung NGOÀI lock (mất tới vài giây).
        internet = client.probe_internet() if transport else False
        with self._lock:
            before = self.can_use()
            if not self.auth.signed_in:
                return
            self.auth.offline_minutes += elapsed_min
            if transport and internet:
                self.auth.blocked_minutes += elapsed_min
            elif transport:
                self.auth.blocked_minutes = 0.0
            store.save(self.auth)
            self._evaluate_offline()
            changed = before != self.can_use()
        if changed:
            self._notify()

    def _evaluate_offline(self, initial: bool = False) -> None:
        v = self.verdict or {}
        if v.get("code") == "update_required" and IS_COMPILED:
            self.status, self.code = "blocked", "update_required"
            self.message = str(v.get("message") or "Cần cập nhật lên bản mới")
            return
        grace = float(v.get("offline_grace_minutes") or 0)
        eff_now = self.auth.last_server_now + self.auth.offline_minutes * 60
        expires = _parse_ts(v.get("expires_at"))
        if expires and eff_now > expires:
            self._sign_out("session_expired", "Phiên đăng nhập đã hết hạn — đăng nhập lại")
            return
        if self.auth.last_server_now and time.time() < self.auth.last_server_now - CLOCK_ROLLBACK_TOLERANCE_S:
            self.status, self.code = "locked", "clock_rollback"
            self.message = "Đồng hồ máy bị chỉnh lùi — chỉnh lại giờ Windows cho đúng rồi bấm Thử lại"
            return
        if self.auth.blocked_minutes >= BLOCK_DETECT_MINUTES:
            self.status, self.code = "locked", "server_blocked"
            self.message = ("Mạng vẫn vào được nhưng không kết nối được máy chủ xác thực — "
                            "kiểm tra tường lửa, file hosts hoặc phần mềm chặn quảng cáo")
            return
        if self.auth.offline_minutes > grace:
            self.status, self.code = "locked", "offline_expired"
            self.message = "Mất kết nối tới máy chủ — đang thử lại…"
            return
        self.status, self.code = "offline", "offline"
        self.message = "Đang kết nối máy chủ…" if initial else "Đang offline"

    def _notify(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                logger.exception("license: on_change lỗi")

    # ------------------------------------------------------------ tra cứu

    def can_use(self) -> bool:
        return self.mode == "disabled" or self.status in ("active", "offline")

    def is_admin(self) -> bool:
        return self.mode == "disabled" or (self.verdict or {}).get("role") == "admin"

    def features(self) -> set[str]:
        if self.mode == "disabled" or self.is_admin():
            return set(FEATURES)
        return {f for f in (self.verdict or {}).get("features") or [] if f in FEATURES}

    def allows(self, feature: str) -> bool:
        return self.can_use() and feature in self.features()

    def current_user_id(self) -> str:
        return self.auth.user_id

    _notice_cache: tuple[float, str] = (0.0, "")

    def public_notice(self) -> str:
        """Thông báo admin ở màn đăng nhập (app_config đọc được khi chưa đăng
        nhập). Cache 5 phút, lỗi mạng thì bỏ qua."""
        if self.mode != "enabled":
            return ""
        at, text = self._notice_cache
        if time.monotonic() - at < 300 and at:
            return text
        try:
            rows = client.select(None, "app_config", {"select": "login_notice", "id": "eq.1"})
            text = str((rows or [{}])[0].get("login_notice") or "")
        except (client.OfflineError, client.ApiError, IndexError, AttributeError):
            text = ""
        self._notice_cache = (time.monotonic(), text)
        return text

    def snapshot(self) -> dict:
        with self._lock:
            v = self.verdict or {}
            grace = float(v.get("offline_grace_minutes") or 0)
            return {
                "mode": self.mode,
                "status": self.status,
                "code": self.code,
                "message": self.message,
                "email": self.auth.email or "",
                "display_name": v.get("display_name") or "",
                "role": v.get("role") or ("admin" if self.mode == "disabled" else ""),
                "features": sorted(self.features()) if self.can_use() or self.status == "locked" else [],
                "expires_at": v.get("expires_at"),
                "device_name": machine.device_name(),
                "offline_minutes": round(self.auth.offline_minutes, 1),
                "offline_remaining_minutes": max(0, round(grace - self.auth.offline_minutes)) if self.auth.signed_in else 0,
                "login_notice": v.get("login_notice") or "",
                "app_version": constants.APP_VERSION,
                "last_email": store.account_hint() or "",
            }


manager = LicenseManager()
