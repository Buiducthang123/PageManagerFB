"""Chặn API theo trạng thái đăng nhập + quyền (user-management-plan.md mục 2, 5).

Kiểm tra theo HÀNH ĐỘNG, không theo trang: luật cụ thể (đăng TikTok/Facebook,
dựng CapCut) đứng TRƯỚC luật chung của nhóm route. Luật đầu tiên khớp thắng.
Frontend chỉ ẩn menu cho gọn — chặn thật nằm ở đây.
"""

from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import JSONResponse

from .manager import manager

# Luôn mở: đăng nhập, cài đặt, kiểm tra hệ thống (mục 5: /settings luôn mở).
# /api/update: bản cũ bị chặn (min_app_version) hoặc chưa đăng nhập vẫn phải tự cập nhật được.
# /api/app: nút "Tắt app" phải bấm được cả khi chưa đăng nhập/đang bị khoá.
OPEN_PREFIXES = ("/api/license/", "/api/settings", "/api/system/", "/api/update/", "/api/app/")

# (regex trên path, các quyền — có 1 trong số đó là được)
RULES: list[tuple[re.Pattern[str], tuple[str, ...]]] = [
    (re.compile(p), feats)
    for p, feats in [
        # Đăng bài — đứng trước luật chung /api/projects, /api/social
        (r"^/api/projects/[^/]+/tiktok-publish", ("tiktok_publish",)),
        (r"^/api/projects/[^/]+/facebook-publish", ("facebook_publish",)),
        (r"^/api/social/[^/]+/queue/[^/]+/(publish|jobs/publish)", ("tiktok_publish",)),
        (r"^/api/social/[^/]+/queue/[^/]+/(facebook-publish|jobs/fbpublish)", ("facebook_publish",)),
        (r"^/api/social/[^/]+/(tiktok|tiktok-account|jobs/tiktok_login)", ("tiktok_publish",)),
        (r"^/api/social/[^/]+/facebook-page", ("facebook_publish",)),
        # Dựng draft CapCut
        (r"^/api/projects/[^/]+/(episodes/[^/]+/)?assemble", ("capcut",)),
        # Trang Tài khoản: mở khi có ít nhất 1 quyền đăng bài
        (r"^/api/accounts", ("tiktok_publish",)),
        (r"^/api/(facebook-pages|facebook)(/|$)", ("facebook_publish",)),
        # Nhóm chức năng
        (r"^/api/social-monitor", ("monitor",)),
        (r"^/api/social", ("automated",)),
        (r"^/api/projects", ("projects", "automated")),
        (r"^/api/tts/", ("projects", "automated")),
        (r"^/api/downloads", ("download",)),
        (r"^/api/douyin-browser", ("download", "automated")),
        (r"^/api/merges", ("merge",)),
        (r"^/api/hardsub", ("clean_video",)),
        (r"^/api/cleanup", ("cleanup",)),
    ]
]

FEATURE_LABELS = {
    "projects": "Dự án",
    "capcut": "Dựng CapCut",
    "tiktok_publish": "Đăng TikTok",
    "facebook_publish": "Đăng Facebook",
    "automated": "Dự án tự động",
    "download": "Tải video",
    "merge": "Ghép video",
    "clean_video": "Làm sạch video",
    "monitor": "Giám sát tiến trình",
    "cleanup": "Tự dọn ổ đĩa",
}


def required_features(path: str) -> tuple[str, ...] | None:
    """None = route không thuộc chức năng nào (vẫn cần đăng nhập)."""
    for pattern, feats in RULES:
        if pattern.search(path):
            return feats
    return None


def _deny(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": message, "license": code})


async def license_middleware(request: Request, call_next):
    path = request.url.path
    if manager.mode == "disabled" or not path.startswith("/api/") or path.startswith(OPEN_PREFIXES):
        return await call_next(request)
    # Facebook gọi lại sau đăng nhập — trình duyệt chuyển hướng, không phải
    # React gọi; vẫn chặn theo quyền facebook_publish như dưới.
    status = manager.status
    if status == "locked":
        return _deny(423, "locked", manager.message or "Ứng dụng đang bị khoá")
    if not manager.can_use():
        return _deny(401, status, manager.message or "Cần đăng nhập")
    if path.startswith("/api/admin"):
        # Chốt chặn thật ở Supabase (RLS is_admin); chặn sớm ở đây cho gọn.
        if not manager.is_admin():
            return _deny(403, "not_admin", "Chỉ admin")
        return await call_next(request)
    feats = required_features(path)
    if feats and not any(manager.allows(f) for f in feats):
        label = " / ".join(FEATURE_LABELS.get(f, f) for f in feats)
        return _deny(403, "no_feature", f"Tài khoản chưa được cấp quyền: {label}")
    return await call_next(request)
