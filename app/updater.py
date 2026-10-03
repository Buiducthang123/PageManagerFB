"""Tự cập nhật lớp App (user-management-plan.md mục 12).

- Danh sách bản lấy từ bảng `app_releases` (đọc được khi CHƯA đăng nhập: bản
  cũ bị chặn vì `min_app_version` vẫn tự cập nhật được).
- Tải zip về `<cài đặt>\\app\\_download\\`, kiểm tra SHA-256 + kích thước, giải
  nén vào `<cài đặt>\\app\\<bản>.partial\\` rồi đổi tên thành `<bản>\\` — không
  ghi đè bản đang chạy; hỏng giữa chừng không ảnh hưởng bản cũ.
- Xong thì thoát với mã 75: launcher chạy lại bản mới nhất, bản mới không lên
  trong 60s thì launcher tự quay về bản cũ.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import zipfile
from typing import Optional

import httpx
from loguru import logger

from . import paths
from .license import client as license_client
from .license import constants

RESTART_EXIT_CODE = 75

_lock = threading.Lock()
_state: dict = {"status": "idle", "progress": 0, "message": "", "version": ""}


def packaged() -> bool:
    """Chỉ bản chạy qua launcher mới tự cài bản mới được (chạy từ source thì dùng git)."""
    return bool(os.environ.get("REUP_INSTALL_ROOT")) and paths.CODE_ROOT.parent.name == "app"


def version_key(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", (v or "").split("-")[0])) or (0,)


def runtime_version() -> str:
    try:
        data = json.loads((paths.INSTALL_ROOT / "runtime" / "runtime.json").read_text(encoding="utf-8"))
        return str(data.get("version") or "")
    except (OSError, ValueError):
        return ""


def latest_release() -> Optional[dict]:
    rows = license_client.select(None, "app_releases", {
        "select": "version,url,sha256,size_bytes,notes,min_runtime,force,published_at",
        "layer": "eq.app", "name": "eq.", "variant": "eq.",
    }) or []
    rows = [r for r in rows if isinstance(r, dict) and r.get("version")]
    if not rows:
        return None
    return max(rows, key=lambda r: version_key(r["version"]))


def status() -> dict:
    with _lock:
        snap = dict(_state)
    snap.update({"current": constants.APP_VERSION, "packaged": packaged()})
    try:
        rel = latest_release()
    except (license_client.OfflineError, license_client.ApiError) as err:
        snap.update({"available": False, "check_error": str(err)})
        return snap
    if rel and version_key(rel["version"]) > version_key(constants.APP_VERSION):
        need_rt = rel.get("min_runtime") or ""
        rt = runtime_version()
        snap.update({
            "available": True,
            "latest": rel["version"],
            "notes": rel.get("notes") or "",
            "force": bool(rel.get("force")),
            "size_bytes": rel.get("size_bytes"),
            "runtime_ok": not need_rt or (bool(rt) and version_key(rt) >= version_key(need_rt)),
            "min_runtime": need_rt,
        })
    else:
        snap["available"] = False
    return snap


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


def start_download() -> dict:
    if not packaged():
        raise RuntimeError("Đang chạy từ source — cập nhật bằng git, không tự cài")
    with _lock:
        if _state["status"] in ("downloading", "installing"):
            return dict(_state)
    rel = latest_release()
    if not rel or version_key(rel["version"]) <= version_key(constants.APP_VERSION):
        raise RuntimeError("Đang dùng bản mới nhất")
    need_rt = rel.get("min_runtime") or ""
    rt = runtime_version()
    if need_rt and (not rt or version_key(rt) < version_key(need_rt)):
        raise RuntimeError(f"Bản {rel['version']} cần Runtime {need_rt} trở lên — tải bộ cài mới để cài lại")
    if not str(rel.get("url", "")).startswith("https://"):
        raise RuntimeError("Link tải bản cập nhật không an toàn (cần https)")
    _set(status="downloading", progress=0, message="Đang tải…", version=rel["version"])
    threading.Thread(target=_download_and_install, args=(rel,), daemon=True, name="updater").start()
    return dict(_state)


def _download_and_install(rel: dict) -> None:
    version = rel["version"]
    app_dir = paths.INSTALL_ROOT / "app"
    dl_dir = app_dir / "_download"
    dl_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dl_dir / f"{version}.zip"
    try:
        h = hashlib.sha256()
        done = 0
        with httpx.stream("GET", rel["url"], follow_redirects=True, timeout=httpx.Timeout(60.0)) as res:
            res.raise_for_status()
            total = int(res.headers.get("content-length") or rel.get("size_bytes") or 0)
            with zip_path.open("wb") as f:
                for chunk in res.iter_bytes(1 << 20):
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if total:
                        _set(progress=min(99, int(done * 100 / total)))
        if h.hexdigest() != str(rel["sha256"]).lower():
            raise RuntimeError("File tải về sai mã kiểm tra (SHA-256) — có thể hỏng hoặc bị tráo, đã huỷ")
        if rel.get("size_bytes") and done != int(rel["size_bytes"]):
            raise RuntimeError("File tải về sai kích thước — đã huỷ")

        _set(status="installing", message="Đang giải nén…")
        partial = app_dir / f"{version}.partial"
        final = app_dir / version
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir(parents=True)
        with zipfile.ZipFile(zip_path) as zf:
            root = partial.resolve()
            for member in zf.infolist():
                target = (partial / member.filename).resolve()
                if root != target and root not in target.parents:
                    raise RuntimeError(f"File cập nhật chứa đường dẫn lạ: {member.filename}")
            zf.extractall(partial)
        meta = json.loads((partial / "release.json").read_text(encoding="utf-8"))
        if meta.get("version") != version:
            raise RuntimeError(f"Gói cập nhật ghi phiên bản {meta.get('version')} khác {version}")
        if final.exists():
            shutil.rmtree(final)
        partial.rename(final)
        zip_path.unlink(missing_ok=True)
        _set(status="ready", progress=100, message=f"Đã tải bản {version} — khởi động lại để dùng")
        logger.info("updater: đã cài bản {} vào {}", version, final)
    except Exception as err:  # noqa: BLE001 — mọi lỗi đều báo ra giao diện, bản cũ không bị đụng
        logger.exception("updater: cập nhật lỗi")
        _set(status="error", message=str(err) or type(err).__name__)
        zip_path.unlink(missing_ok=True)


def sync_launcher() -> None:
    """Chép `launcher.py` đi kèm bản app đang chạy đè lên `<cài đặt>\\launcher.pyw`
    nếu khác — launcher chỉ được cài bằng Setup, không có đường cập nhật nào
    khác. launcher.pyw chỉ được đọc lúc mở app nên chép đè lúc đang chạy an
    toàn; lần mở app sau mới dùng bản mới."""
    if not packaged():
        return
    src = paths.CODE_ROOT / "launcher.py"
    dst = paths.INSTALL_ROOT / "launcher.pyw"
    try:
        if not src.exists() or (dst.exists() and dst.read_bytes() == src.read_bytes()):
            return
        tmp = dst.with_suffix(".pyw.tmp")
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
        logger.info("updater: đã cập nhật launcher.pyw theo bản app {}", constants.APP_VERSION)
    except OSError as err:
        logger.warning("updater: không cập nhật được launcher.pyw ({})", err)


def request_restart() -> None:
    """Thoát với mã 75 sau 1 giây (đủ để trả response) — launcher chạy lại bản mới."""
    if not packaged():
        raise RuntimeError("Đang chạy từ source — tự khởi động lại server")

    def _exit() -> None:
        logger.info("updater: thoát để launcher chạy bản mới")
        os._exit(RESTART_EXIT_CODE)

    threading.Timer(1.0, _exit).start()
