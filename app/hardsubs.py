"""Lưu các lượt "Làm sạch video" (xoá phụ đề cứng) — đứng riêng, không thuộc
project nào. Cùng kiểu với merges.py: mỗi lượt 1 thư mục + meta.json riêng
dưới workspace/hardsub/<id>/ (input.*, clean.mp4, worker.log)."""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from pathlib import Path

from . import projects as pj

HARDSUB_DIR = pj.WORKSPACE_DIR / "hardsub"
OUTPUT_FILENAME = "clean.mp4"


def item_dir(item_id: str) -> Path:
    return HARDSUB_DIR / item_id


def _meta_path(item_id: str) -> Path:
    return item_dir(item_id) / "meta.json"


def create_item(input_filename: str, options: dict) -> dict:
    HARDSUB_DIR.mkdir(parents=True, exist_ok=True)
    title = Path(input_filename).stem.strip()
    slug = pj.slugify(title) if title else "lam-sach"
    item_id = f"{date.today().isoformat()}_{slug}"
    base = item_dir(item_id)
    n = 1
    while base.exists():
        n += 1
        item_id = f"{date.today().isoformat()}_{slug}-{n}"
        base = item_dir(item_id)
    base.mkdir(parents=True)
    meta = {
        "item_id": item_id,
        "title": title or item_id,
        "created_at": datetime.now().isoformat(),
        "status": "pending",
        "error": None,
        "warning": None,
        "input_filename": input_filename,
        "input_path": None,
        "options": options,
        "output_filename": None,
        "elapsed_s": None,
    }
    save_meta(item_id, meta)
    return meta


def load_meta(item_id: str) -> dict:
    path = _meta_path(item_id)
    if not path.exists():
        raise FileNotFoundError(f'Không tìm thấy lượt làm sạch "{item_id}"')
    return json.loads(path.read_text(encoding="utf-8"))


def save_meta(item_id: str, meta: dict) -> None:
    _meta_path(item_id).write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def update_meta(item_id: str, **fields) -> dict:
    meta = load_meta(item_id)
    meta.update(fields)
    save_meta(item_id, meta)
    return meta


def list_items() -> list[dict]:
    if not HARDSUB_DIR.exists():
        return []
    out = []
    for d in sorted(HARDSUB_DIR.iterdir(), reverse=True):
        path = d / "meta.json"
        if not path.exists():
            continue
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def delete_item(item_id: str) -> None:
    base = item_dir(item_id)
    if not base.exists():
        raise FileNotFoundError(f'Không tìm thấy lượt làm sạch "{item_id}"')
    shutil.rmtree(base)
