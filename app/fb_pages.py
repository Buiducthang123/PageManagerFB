from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from loguru import logger

from . import projects as pj
from . import social as sp
from .models import FacebookPage
from .stages import facebook_publish as fb

# Danh sách Facebook Page đăng Reels — cùng pattern app/accounts.py (1 file
# JSON + threading.Lock). Token Page chỉ nằm trong file này; dự án tự động chỉ
# trỏ tới `page_id`.
INDEX_PATH = pj.WORKSPACE_DIR / "accounts" / "facebook_pages.json"

_lock = threading.Lock()


def _load_all() -> list[FacebookPage]:
    if not INDEX_PATH.exists():
        return []
    raw = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    return [FacebookPage.model_validate(p) for p in raw.get("items", [])]


def _save_all(items: list[FacebookPage]) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(
        json.dumps({"items": [p.model_dump(mode="json") for p in items]}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def list_pages() -> list[FacebookPage]:
    with _lock:
        return _load_all()


def get_page(page_id: str) -> Optional[FacebookPage]:
    return next((p for p in list_pages() if p.page_id == page_id), None)


@contextmanager
def locked_page(page_id: str) -> Iterator[FacebookPage]:
    with _lock:
        items = _load_all()
        page = next((p for p in items if p.page_id == page_id), None)
        if page is None:
            raise FileNotFoundError(f'Không tìm thấy Facebook Page "{page_id}"')
        yield page
        _save_all(items)


def upsert(page: FacebookPage) -> None:
    """Thêm hoặc cập nhật token/thông tin Page — giữ nguyên `added_at` cũ."""
    with _lock:
        items = _load_all()
        old = next((p for p in items if p.page_id == page.page_id), None)
        if old is not None:
            page.added_at = old.added_at
            items = [page if p.page_id == page.page_id else p for p in items]
        else:
            items.append(page)
        _save_all(items)


def delete_page(page_id: str) -> None:
    with _lock:
        items = _load_all()
        if not any(p.page_id == page_id for p in items):
            raise FileNotFoundError(f'Không tìm thấy Facebook Page "{page_id}"')
        _save_all([p for p in items if p.page_id != page_id])


def projects_by_page() -> dict[str, list[tuple[str, str]]]:
    """page_id -> [(social_id, title)] của các dự án tự động đang gán."""
    out: dict[str, list[tuple[str, str]]] = {}
    for summary in sp.list_social_projects():
        try:
            st = sp.load_state(summary.id)
        except FileNotFoundError:
            continue
        if st.facebook_page_id:
            out.setdefault(st.facebook_page_id, []).append((st.id, st.title))
    return out


def mask_token(token: str) -> str:
    return f"…{token[-6:]}" if len(token) > 6 else "…"


def _page_from_graph(p: dict, token: str, source: str) -> FacebookPage:
    return FacebookPage(
        page_id=str(p["id"]),
        name=p.get("name") or "",
        category=p.get("category"),
        picture_url=((p.get("picture") or {}).get("data") or {}).get("url"),
        access_token=token,
        source=source,
        status="ok",
        checked_at=datetime.now(),
        added_at=datetime.now(),
    )


def import_from_pages_manager(path: Path) -> list[FacebookPage]:
    """Nhập Page + token từ file `data/pages.json` của PagesManagerSupperTool
    (đã có sẵn token Page lấy qua OAuth — không cần đăng nhập lại)."""
    if not path.exists():
        raise FileNotFoundError(f"Không thấy file {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    imported: list[FacebookPage] = []
    for p in raw.get("pages") or []:
        token = p.get("pageAccessToken") or ""
        if not p.get("pageId") or not token:
            continue
        page = FacebookPage(
            page_id=str(p["pageId"]),
            name=p.get("pageName") or "",
            category=p.get("category"),
            picture_url=p.get("pictureUrl"),
            access_token=token,
            source="pagesmanager",
            added_at=datetime.now(),
        )
        upsert(page)
        imported.append(page)
    for page in imported:
        check_page(page.page_id)
    return [get_page(p.page_id) or p for p in imported]


def import_from_token(token: str, source: str = "token") -> list[FacebookPage]:
    """Dán tay 1 token: user token (→ lấy mọi Page nó quản lý) hoặc Page token
    (→ đúng Page đó). User token ngắn hạn được tự đổi sang dài hạn nếu .env có
    FB_APP_ID/FB_APP_SECRET."""
    token = token.strip()
    if not token:
        raise ValueError("Chưa dán token")
    try:
        long_lived = fb.exchange_long_lived(token)
    except fb.FacebookPublishError as err:
        # Page token / token của app khác không đổi được — dùng nguyên token.
        logger.info("facebook: không đổi được token dài hạn ({}) — dùng nguyên token", err)
        long_lived = None
    user_token = long_lived or token
    try:
        pages = fb.pages_from_user_token(user_token)
    except fb.FacebookTokenError:
        raise
    except fb.FacebookPublishError:
        pages = None  # không phải user token → thử như Page token
    saved: list[FacebookPage] = []
    if pages is not None:
        if not pages:
            raise ValueError("Token hợp lệ nhưng không quản lý Page nào (thiếu quyền pages_show_list?)")
        for p in pages:
            if not p.get("access_token"):
                continue
            page = _page_from_graph(p, p["access_token"], source)
            page.token_expires_at = fb.token_expires_at(page.access_token)
            upsert(page)
            saved.append(page)
    else:
        info = fb.page_info("me", token)
        page = _page_from_graph(info, token, source)
        page.token_expires_at = fb.token_expires_at(token)
        upsert(page)
        saved.append(page)
    return saved


def check_page(page_id: str) -> FacebookPage:
    page = get_page(page_id)
    if page is None:
        raise FileNotFoundError(f'Không tìm thấy Facebook Page "{page_id}"')
    status, detail, info = "ok", None, None
    try:
        info = fb.page_info(page_id, page.access_token)
    except fb.FacebookTokenError as err:
        status, detail = "expired", str(err)
    except fb.FacebookPublishError as err:
        status, detail = "error", str(err)
    expires = fb.token_expires_at(page.access_token) if status == "ok" and page.token_expires_at is None else None
    with locked_page(page_id) as p:
        p.status, p.status_detail, p.checked_at = status, detail, datetime.now()
        if info:
            p.name = info.get("name") or p.name
            p.category = info.get("category") or p.category
            p.picture_url = ((info.get("picture") or {}).get("data") or {}).get("url") or p.picture_url
        if expires is not None:
            p.token_expires_at = expires
        return p


def mark_expired(page_id: str, detail: str) -> None:
    try:
        with locked_page(page_id) as p:
            p.status, p.status_detail, p.checked_at = "expired", detail, datetime.now()
    except FileNotFoundError:
        pass
