from __future__ import annotations

import json
import os
import re
import shutil
import threading
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv

from .models import EPISODE_STAGE_ORDER, STAGE_ORDER, Episode, ProjectState, StageRecord, StageStatus

load_dotenv()

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent

_custom_workspace = os.environ.get("WORKSPACE_DIR", "").strip()
WORKSPACE_DIR = Path(_custom_workspace).expanduser().resolve() if _custom_workspace else ROOT_DIR / "workspace"
PROJECTS_DIR = WORKSPACE_DIR / "projects"
INDEX_PATH = WORKSPACE_DIR / "index.json"

_project_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(project_id: str) -> threading.Lock:
    with _locks_guard:
        if project_id not in _project_locks:
            _project_locks[project_id] = threading.Lock()
        return _project_locks[project_id]


def slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    if not slug:
        slug = re.sub(r"[^\w]+", "-", text, flags=re.UNICODE).strip("-").lower()
    return slug or "untitled"


def _ensure_workspace() -> None:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    if not INDEX_PATH.exists():
        INDEX_PATH.write_text(json.dumps({"projects": []}, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_index() -> dict:
    _ensure_workspace()
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def _save_index(index: dict) -> None:
    INDEX_PATH.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")


def project_dir(project_id: str) -> Path:
    return PROJECTS_DIR / project_id


def project_json_path(project_id: str) -> Path:
    return project_dir(project_id) / "project.json"


def episode_dir(project_id: str, episode_id: str) -> Path:
    return project_dir(project_id) / "episodes" / episode_id


def entity_dict_path(project_id: str) -> Path:
    """Entity dict dùng chung cho mọi episode trong 1 dự án dài tập — tên
    riêng được chốt ở tập trước phải áp dụng nhất quán cho các tập sau."""
    return project_dir(project_id) / "entity_dict.json"


def save_project(state: ProjectState) -> None:
    path = project_json_path(state.project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(indent=2), encoding="utf-8")


def load_project(project_id: str) -> ProjectState:
    path = project_json_path(project_id)
    if not path.exists():
        raise FileNotFoundError(f'Không tìm thấy dự án "{project_id}"')
    raw = json.loads(path.read_text(encoding="utf-8"))
    state = ProjectState.model_validate(raw)
    # Backfill stage mới thêm sau này (vd "tts") cho project.json đã lưu từ trước.
    for name in STAGE_ORDER:
        state.stages.setdefault(name, StageRecord())
    return state


@contextmanager
def locked_project(project_id: str) -> Iterator[ProjectState]:
    lock = _lock_for(project_id)
    with lock:
        state = load_project(project_id)
        yield state
        save_project(state)


def current_stage(state: ProjectState) -> str | None:
    for name in STAGE_ORDER:
        rec = state.stages.get(name) or StageRecord()
        if rec.status != "done":
            return name
    return None


def episode_current_stage(episode: Episode) -> str | None:
    for name in EPISODE_STAGE_ORDER:
        rec = episode.stages.get(name) or StageRecord()
        if rec.status != "done":
            return name
    return None


def find_episode(state: ProjectState, episode_id: str) -> Episode:
    for ep in state.episodes:
        if ep.episode_id == episode_id:
            return ep
    raise FileNotFoundError(f'Không tìm thấy tập "{episode_id}"')


def create_episode(state: ProjectState, title: str = "", insert_after_episode_id: str | None = None) -> Episode:
    """Thêm 1 tập mới vào `state.episodes` (đã sort theo `order`). Mặc định
    thêm vào cuối; truyền `insert_after_episode_id` để chèn ngay sau tập đó,
    dồn `order` các tập phía sau lên — cho phép thêm tập giữa chừng sau khi
    dự án đã chạy xong mà không đụng tới các tập khác."""
    n = len(state.episodes)
    episode_id = f"ep-{n + 1:03d}"
    while any(ep.episode_id == episode_id for ep in state.episodes):
        n += 1
        episode_id = f"ep-{n + 1:03d}"

    if insert_after_episode_id is None:
        order = len(state.episodes)
    else:
        after = find_episode(state, insert_after_episode_id)
        order = after.order + 1
        for ep in state.episodes:
            if ep.order >= order:
                ep.order += 1

    episode = Episode(
        episode_id=episode_id,
        order=order,
        title=title.strip() or None,
        created_at=datetime.now(),
    )
    state.episodes.append(episode)
    state.episodes.sort(key=lambda e: e.order)
    episode_dir(state.project_id, episode_id).mkdir(parents=True, exist_ok=True)
    return episode


def reset_episode_from(episode: Episode, stage: str) -> None:
    idx = EPISODE_STAGE_ORDER.index(stage)
    for name in EPISODE_STAGE_ORDER[idx:]:
        episode.stages[name] = StageRecord()
    if stage == "ingest":
        episode.original_filename = None
        episode.video_relpath = None
        episode.duration_sec = None


def all_episodes_stage_done(state: ProjectState, stage: str) -> bool:
    if not state.episodes:
        return False
    return all((ep.stages.get(stage) or StageRecord()).status == "done" for ep in state.episodes)


def create_project(title: str, project_type: str = "single") -> ProjectState:
    _ensure_workspace()
    title = title.strip()
    if not title:
        raise ValueError("Tên dự án không được để trống")
    slug = slugify(title)
    project_id = f"{date.today().isoformat()}_{slug}"
    base = project_dir(project_id)
    if base.exists():
        raise ValueError(f'Dự án "{project_id}" đã tồn tại')
    (base / "logs").mkdir(parents=True, exist_ok=True)
    state = ProjectState(project_id=project_id, title=title, created_at=datetime.now(), project_type=project_type)
    save_project(state)
    index = _load_index()
    index["projects"].insert(
        0,
        {"project_id": project_id, "title": title, "created_at": state.created_at.isoformat()},
    )
    _save_index(index)
    return state


def rename_project(project_id: str, title: str) -> ProjectState:
    with locked_project(project_id) as state:
        state.title = title
    index = _load_index()
    for item in index["projects"]:
        if item["project_id"] == project_id:
            item["title"] = title
            break
    _save_index(index)
    return load_project(project_id)


def delete_project(project_id: str) -> None:
    base = project_dir(project_id)
    if not base.exists():
        raise FileNotFoundError(f'Không tìm thấy dự án "{project_id}"')
    shutil.rmtree(base)
    index = _load_index()
    index["projects"] = [p for p in index["projects"] if p["project_id"] != project_id]
    _save_index(index)


def list_projects() -> list[ProjectState]:
    index = _load_index()
    out: list[ProjectState] = []
    for item in index["projects"]:
        pid = item["project_id"]
        try:
            out.append(load_project(pid))
        except FileNotFoundError:
            continue
    return out


def reset_from(state: ProjectState, stage: str) -> None:
    idx = STAGE_ORDER.index(stage)
    for name in STAGE_ORDER[idx:]:
        state.stages[name] = StageRecord()
    if stage == "ingest":
        state.original_filename = None
        state.video_relpath = None
        state.duration_sec = None


def append_log(project_id: str, stage: str, message: str) -> None:
    log_path = project_dir(project_id) / "logs" / "pipeline.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": datetime.now().isoformat(timespec="seconds"), "stage": stage, "message": message}
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_logs(project_id: str, limit: int = 40) -> list[dict]:
    log_path = project_dir(project_id) / "logs" / "pipeline.jsonl"
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
