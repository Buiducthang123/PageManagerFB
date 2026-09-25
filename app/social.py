from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from . import projects as pj
from .models import SocialProjectState, SocialProjectSummary

# Nhái nguyên pattern `app/projects.py` — JSON file/thư mục + threading.Lock,
# không dùng database, giữ nhất quán với toàn bộ codebase (đã xác nhận qua
# khảo sát: không có SQLite/SQLAlchemy nào trong app/).
SOCIAL_DIR = pj.WORKSPACE_DIR / "social"
INDEX_PATH = SOCIAL_DIR / "index.json"

_social_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_index_lock = threading.Lock()


def _lock_for(social_id: str) -> threading.Lock:
    with _locks_guard:
        if social_id not in _social_locks:
            _social_locks[social_id] = threading.Lock()
        return _social_locks[social_id]


def _ensure_workspace() -> None:
    SOCIAL_DIR.mkdir(parents=True, exist_ok=True)
    if not INDEX_PATH.exists():
        INDEX_PATH.write_text(json.dumps({"items": []}, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_index() -> dict:
    _ensure_workspace()
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def _save_index(index: dict) -> None:
    INDEX_PATH.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")


@contextmanager
def _locked_index() -> Iterator[dict]:
    with _index_lock:
        index = _load_index()
        yield index
        _save_index(index)


def social_dir(social_id: str) -> Path:
    return SOCIAL_DIR / social_id


def state_path(social_id: str) -> Path:
    return social_dir(social_id) / "state.json"


def save_state(state: SocialProjectState) -> None:
    path = state_path(state.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(indent=2), encoding="utf-8")


def load_state(social_id: str) -> SocialProjectState:
    path = state_path(social_id)
    if not path.exists():
        raise FileNotFoundError(f'Không tìm thấy dự án tự động "{social_id}"')
    raw = json.loads(path.read_text(encoding="utf-8"))
    return SocialProjectState.model_validate(raw)


@contextmanager
def locked_state(social_id: str) -> Iterator[SocialProjectState]:
    lock = _lock_for(social_id)
    with lock:
        state = load_state(social_id)
        yield state
        save_state(state)


def create_social_project(
    title: str,
    douyin_profile_url: str,
    posts_per_day: int = 1,
) -> SocialProjectState:
    _ensure_workspace()
    title = title.strip()
    if not title:
        raise ValueError("Tên dự án không được để trống")
    douyin_profile_url = douyin_profile_url.strip()
    if not douyin_profile_url:
        raise ValueError("Chưa dán link trang cá nhân Douyin")
    slug = pj.slugify(title)
    social_id = f"{date.today().isoformat()}_{slug}"
    base = social_dir(social_id)
    if base.exists():
        raise ValueError(f'Dự án tự động "{social_id}" đã tồn tại')
    base.mkdir(parents=True, exist_ok=True)
    state = SocialProjectState(
        id=social_id,
        title=title,
        douyin_profile_url=douyin_profile_url,
        posts_per_day=posts_per_day,
        created_at=datetime.now(),
    )
    save_state(state)
    with _locked_index() as index:
        index["items"].insert(
            0,
            {
                "id": social_id,
                "title": title,
                "douyin_profile_url": douyin_profile_url,
                "status": state.status,
                "created_at": state.created_at.isoformat(),
            },
        )
    return state


def sync_index_entry(social_id: str) -> None:
    """Đồng bộ lại title/status trong index.json sau khi `locked_state` sửa
    đổi state — index chỉ cache vài field để list nhanh không phải mở hết
    state.json từng dự án, xem `rename_project` ở projects.py cho pattern gốc."""
    state = load_state(social_id)
    with _locked_index() as index:
        for item in index["items"]:
            if item["id"] == social_id:
                item["title"] = state.title
                item["status"] = state.status
                item["douyin_profile_url"] = state.douyin_profile_url
                break


def delete_social_project(social_id: str) -> None:
    base = social_dir(social_id)
    if not base.exists():
        raise FileNotFoundError(f'Không tìm thấy dự án tự động "{social_id}"')
    import shutil

    shutil.rmtree(base)
    with _locked_index() as index:
        index["items"] = [p for p in index["items"] if p["id"] != social_id]


def list_social_projects() -> list[SocialProjectSummary]:
    index = _load_index()
    out: list[SocialProjectSummary] = []
    for item in index["items"]:
        sid = item["id"]
        try:
            state = load_state(sid)
        except FileNotFoundError:
            continue
        out.append(
            SocialProjectSummary(
                id=state.id,
                title=state.title,
                douyin_profile_url=state.douyin_profile_url,
                status=state.status,
                created_at=state.created_at,
            )
        )
    return out
