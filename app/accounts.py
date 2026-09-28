from __future__ import annotations

import json
import shutil
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from loguru import logger

from . import projects as pj
from . import social as sp
from .models import TikTokAccount

# Danh sách tài khoản TikTok đăng bài — 1 file JSON chung + threading.Lock,
# cùng pattern app/social.py. Profile Chrome của mỗi tài khoản nằm ở
# `workspace/accounts/<id>/profile`, tách khỏi thư mục dự án tự động để xoá
# dự án không xoá mất phiên đăng nhập, và để đổi tài khoản giữa các dự án.
ACCOUNTS_DIR = pj.WORKSPACE_DIR / "accounts"
INDEX_PATH = ACCOUNTS_DIR / "index.json"

_lock = threading.Lock()


def _load_all() -> list[TikTokAccount]:
    if not INDEX_PATH.exists():
        return []
    raw = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    return [TikTokAccount.model_validate(a) for a in raw.get("items", [])]


def _save_all(items: list[TikTokAccount]) -> None:
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(
        json.dumps({"items": [a.model_dump(mode="json") for a in items]}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def list_accounts() -> list[TikTokAccount]:
    with _lock:
        return _load_all()


def get_account(account_id: str) -> Optional[TikTokAccount]:
    return next((a for a in list_accounts() if a.id == account_id), None)


@contextmanager
def locked_account(account_id: str) -> Iterator[TikTokAccount]:
    with _lock:
        items = _load_all()
        acc = next((a for a in items if a.id == account_id), None)
        if acc is None:
            raise FileNotFoundError(f'Không tìm thấy tài khoản "{account_id}"')
        yield acc
        _save_all(items)


def default_profile_dir(account_id: str) -> Path:
    return ACCOUNTS_DIR / account_id / "profile"


def create_account(label: str = "", profile_dir: Optional[Path] = None) -> TikTokAccount:
    account_id = uuid.uuid4().hex[:10]
    acc = TikTokAccount(
        id=account_id,
        label=label.strip(),
        profile_dir=str(profile_dir or default_profile_dir(account_id)),
        created_at=datetime.now(),
    )
    with _lock:
        items = _load_all()
        items.append(acc)
        _save_all(items)
    return acc


def delete_account(account_id: str) -> None:
    with _lock:
        items = _load_all()
        acc = next((a for a in items if a.id == account_id), None)
        if acc is None:
            raise FileNotFoundError(f'Không tìm thấy tài khoản "{account_id}"')
        _save_all([a for a in items if a.id != account_id])
    # Chỉ xoá profile nằm trong thư mục tài khoản do app tạo — không bao giờ
    # xoá 1 đường dẫn bất kỳ ngoài workspace/accounts.
    root = (ACCOUNTS_DIR / account_id).resolve()
    if ACCOUNTS_DIR.resolve() in root.parents and root.exists():
        shutil.rmtree(root, ignore_errors=True)


def projects_by_account() -> dict[str, list[tuple[str, str]]]:
    """account_id -> [(social_id, title)] của các dự án tự động đang gán."""
    out: dict[str, list[tuple[str, str]]] = {}
    for summary in sp.list_social_projects():
        try:
            st = sp.load_state(summary.id)
        except FileNotFoundError:
            continue
        if st.tiktok_account_id:
            out.setdefault(st.tiktok_account_id, []).append((st.id, st.title))
    return out


def migrate_legacy_profiles() -> int:
    """Chạy lúc khởi động: dự án tự động có profile TikTok kiểu cũ
    (`<social_dir>/tiktok_profile`) mà chưa gán tài khoản → chuyển profile
    sang `workspace/accounts/<id>/profile` thành 1 tài khoản mới và gán lại,
    KHÔNG phải đăng nhập lại (cookie nằm nguyên trong thư mục profile). Chuyển
    không được (vd Chrome đang mở profile đó) thì giữ nguyên chỗ cũ."""
    created = 0
    for summary in sp.list_social_projects():
        try:
            st = sp.load_state(summary.id)
        except FileNotFoundError:
            continue
        if st.tiktok_account_id:
            continue
        old = Path(st.tiktok_session_path) if st.tiktok_session_path else sp.social_dir(st.id) / "tiktok_profile"
        if not (old.exists() and any(old.iterdir())):
            continue
        acc = create_account(label=st.title)
        target = Path(acc.profile_dir)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(target))
        except Exception as err:
            logger.warning("accounts: không chuyển được profile '{}' ({}) — dùng tại chỗ", old, err)
            with locked_account(acc.id) as a:
                a.profile_dir = str(old)
        with sp.locked_state(st.id) as s:
            s.tiktok_account_id = acc.id
        created += 1
        logger.info("accounts: đã tạo tài khoản {} từ profile cũ của dự án '{}'", acc.id, st.id)
    return created
