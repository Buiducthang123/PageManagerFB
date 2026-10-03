"""Hàng đợi sự kiện thống kê (user-management-plan.md mục 6): mỗi lần tạo dự
án / dự án hoàn thành ghi 1 sự kiện vào file trên máy, gửi lên kèm heartbeat.
Mất mạng thì gửi bù sau; server có khoá duy nhất (user, project, type) nên
gửi trùng không đếm 2 lần."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

from loguru import logger

from . import store

_lock = threading.Lock()
MAX_KEEP = 5000


def _path():
    return store.data_dir() / "usage_outbox.json"


def _load() -> list[dict]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(items: list[dict]) -> None:
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items[-MAX_KEEP:], ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def record(user_id: str, project_id: str, type_: str) -> None:
    if not user_id or not project_id or type_ not in ("created", "completed"):
        return
    with _lock:
        items = _load()
        if any(i.get("user_id") == user_id and i.get("project_id") == project_id and i.get("type") == type_ for i in items):
            return
        items.append({
            "user_id": user_id,
            "project_id": project_id[:80],
            "type": type_,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        try:
            _save(items)
        except OSError as err:
            logger.warning("usage: không ghi được hàng đợi thống kê ({})", err)


def pending(user_id: str, limit: int = 200) -> list[dict]:
    with _lock:
        return [i for i in _load() if i.get("user_id") == user_id][:limit]


def ack(user_id: str, sent: list[dict]) -> None:
    keys = {(i["project_id"], i["type"]) for i in sent}
    with _lock:
        items = [i for i in _load() if not (i.get("user_id") == user_id and (i.get("project_id"), i.get("type")) in keys)]
        try:
            _save(items)
        except OSError as err:
            logger.warning("usage: không ghi được hàng đợi thống kê ({})", err)
