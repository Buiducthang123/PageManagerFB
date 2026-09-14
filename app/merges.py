from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from pathlib import Path

from . import projects as pj

MERGES_DIR = pj.WORKSPACE_DIR / "merges"


def merge_dir(merge_id: str) -> Path:
    return MERGES_DIR / merge_id


def _meta_path(merge_id: str) -> Path:
    return merge_dir(merge_id) / "meta.json"


def create_merge(title: str, input_filenames: list[str]) -> dict:
    """Tạo 1 merge job mới — mỗi merge có thư mục + meta.json RIÊNG (không
    dùng chung 1 index.json như projects, nên không cần khoá — đọc/ghi
    đồng thời nhiều merge không đụng nhau)."""
    MERGES_DIR.mkdir(parents=True, exist_ok=True)
    title = title.strip()
    slug = pj.slugify(title) if title else "ghep-video"
    merge_id = f"{date.today().isoformat()}_{slug}"
    base = merge_dir(merge_id)
    n = 1
    while base.exists():
        n += 1
        merge_id = f"{date.today().isoformat()}_{slug}-{n}"
        base = merge_dir(merge_id)
    base.mkdir(parents=True)
    meta = {
        "merge_id": merge_id,
        "title": title or merge_id,
        "created_at": datetime.now().isoformat(),
        "status": "pending",
        "error": None,
        "input_filenames": input_filenames,
        "output_filename": None,
    }
    _meta_path(merge_id).write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


def load_meta(merge_id: str) -> dict:
    path = _meta_path(merge_id)
    if not path.exists():
        raise FileNotFoundError(f'Không tìm thấy merge "{merge_id}"')
    return json.loads(path.read_text(encoding="utf-8"))


def save_meta(merge_id: str, meta: dict) -> None:
    _meta_path(merge_id).write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def list_merges() -> list[dict]:
    if not MERGES_DIR.exists():
        return []
    out = []
    for d in sorted(MERGES_DIR.iterdir(), reverse=True):
        path = d / "meta.json"
        if not path.exists():
            continue
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def delete_merge(merge_id: str) -> None:
    base = merge_dir(merge_id)
    if not base.exists():
        raise FileNotFoundError(f'Không tìm thấy merge "{merge_id}"')
    shutil.rmtree(base)
