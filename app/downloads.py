from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from pathlib import Path

from . import projects as pj

DOWNLOADS_DIR = pj.WORKSPACE_DIR / "downloads"


def download_dir(download_id: str) -> Path:
    return DOWNLOADS_DIR / download_id


def _meta_path(download_id: str) -> Path:
    return download_dir(download_id) / "meta.json"


def create_download(title: str, mode: str, input_data: dict) -> dict:
    """Tạo 1 download job mới (không thuộc project nào) — mỗi download có
    thư mục + meta.json RIÊNG, không dùng index.json chung nên không cần
    khoá, giống hệt app/merges.py."""
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    title = title.strip()
    slug = pj.slugify(title) if title else mode
    download_id = f"{date.today().isoformat()}_{slug}"
    base = download_dir(download_id)
    n = 1
    while base.exists():
        n += 1
        download_id = f"{date.today().isoformat()}_{slug}-{n}"
        base = download_dir(download_id)
    base.mkdir(parents=True)
    meta = {
        "download_id": download_id,
        "title": title or download_id,
        "mode": mode,
        "input": input_data,
        "created_at": datetime.now().isoformat(),
        "status": "pending",
        "error": None,
        "output_files": [],
    }
    _meta_path(download_id).write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


def load_meta(download_id: str) -> dict:
    path = _meta_path(download_id)
    if not path.exists():
        raise FileNotFoundError(f'Không tìm thấy download "{download_id}"')
    return json.loads(path.read_text(encoding="utf-8"))


def save_meta(download_id: str, meta: dict) -> None:
    _meta_path(download_id).write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def list_downloads() -> list[dict]:
    if not DOWNLOADS_DIR.exists():
        return []
    out = []
    for d in sorted(DOWNLOADS_DIR.iterdir(), reverse=True):
        path = d / "meta.json"
        if not path.exists():
            continue
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def delete_download(download_id: str) -> None:
    base = download_dir(download_id)
    if not base.exists():
        raise FileNotFoundError(f'Không tìm thấy download "{download_id}"')
    shutil.rmtree(base)
