from __future__ import annotations

import json
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

from . import jobs
from . import projects as pj
from . import social as sp
from .models import QueueItemStatus
from .stages.ingest import VIDEO_EXTENSIONS

# Tự dọn ổ đĩa cho "Dự án tự động" (người dùng chốt: thiên hướng tự dọn, mốc
# 1 ngày). Số đo thật trên 1 video 7 phút: video thành phẩm 206MB + video gốc
# 87MB + audio nền WAV 72MB ≈ 370MB, còn phụ đề/giọng đọc/cấu hình < 5MB.
# Profile Chrome đăng TikTok 300-600MB mỗi tài khoản, phần lớn là video bản
# nháp TikTok Studio tự lưu (IndexedDB) sau các lần đăng bị gián đoạn.

CLEANUP_AFTER = timedelta(days=1)
# Chạy dọn tối đa mỗi ngần này (bộ lập lịch tick mỗi 60s, không cần quét mỗi tick).
CLEANUP_INTERVAL_S = 30 * 60
# Dọn cache profile Chrome mỗi profile tối đa 1 lần/ngày.
BROWSER_CLEAN_INTERVAL = timedelta(days=1)
# Ổ còn trống dưới ngưỡng này → bộ lập lịch ngừng XỬ LÝ video mới (vẫn đăng
# các video đã sẵn sàng, vẫn dọn) cho tới khi có thêm chỗ trống.
MIN_FREE_GB = 20.0

_MARKER = pj.WORKSPACE_DIR / "social_cleanup.json"
_last_run = 0.0
last_summary: dict = {}

# Thư mục cache của Chrome — tạo lại được, xoá không mất đăng nhập (cookie ở
# Default/Network/Cookies, không đụng tới).
_CHROME_CACHE_DIRS = (
    "Default/Cache",
    "Default/Code Cache",
    "Default/GPUCache",
    "Default/DawnWebGPUCache",
    "Default/DawnGraphiteCache",
    "GrShaderCache",
    "ShaderCache",
    "BrowserMetrics",
)
# KHÔNG xoá IndexedDB của tiktok.com nữa — xem `_clean_profile`.


def free_gb() -> float:
    return shutil.disk_usage(pj.WORKSPACE_DIR).free / 1e9


def low_disk() -> bool:
    return free_gb() < MIN_FREE_GB


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return 0


def _remove(path: Path) -> int:
    size = _size(path)
    try:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        return size
    except OSError as err:
        logger.warning("social_cleanup: không xoá được {} ({})", path, err)
        return 0


def heavy_files(root: Path) -> list[Path]:
    """File nặng của 1 project pipeline: video gốc, audio WAV, video xuất."""
    out: list[Path] = []
    if not root.is_dir():
        return out
    for f in root.iterdir():
        if f.is_file() and (f.suffix.lower() in VIDEO_EXTENSIONS or f.suffix.lower() == ".wav"):
            out.append(f)
    export = root / "export"
    if export.is_dir():
        out += [f for f in export.iterdir() if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS]
    for tmp in ("_douyin_dl_tmp",):
        if (root / tmp).exists():
            out.append(root / tmp)
    return out


def remove_wav_after_export(project_id: str) -> int:
    """Gọi ngay khi xuất video xong (project do dự án tự động sinh ra): audio
    nền WAV không còn cần — bước xuất tự tạo lại nếu phải xuất lại."""
    root = pj.project_dir(project_id)
    freed = sum(_remove(f) for f in root.glob("*.wav"))
    if freed:
        logger.info("social_cleanup: xoá audio WAV sau khi xuất '{}' — giải phóng {:.0f}MB", project_id, freed / 1e6)
    return freed


def _last_activity(root: Path) -> datetime:
    """Mốc hoạt động XỬ LÝ THẬT gần nhất của 1 project: lúc tạo project, lúc
    các bước xong (`stages.*.at`, `export.at` trong project.json) và lúc ghi
    file video/audio.

    KHÔNG dùng giờ sửa file project.json: file này bị ghi lại cả vì việc không
    liên quan tới xử lý (vd mở trang project → app tự sửa trạng thái bước bị
    gián đoạn do server restart). Đã gặp thật: giờ tự xoá của video lỗi cứ bị
    đẩy lùi mãi mỗi lần như vậy, nên sẽ không bao giờ được dọn."""
    points: list[datetime] = []
    try:
        data = json.loads((root / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    candidates = [data.get("created_at")]
    candidates += [(rec or {}).get("at") for rec in (data.get("stages") or {}).values()]
    candidates.append((data.get("export") or {}).get("at"))
    for value in candidates:
        if not value:
            continue
        try:
            points.append(datetime.fromisoformat(str(value)).replace(tzinfo=None))
        except ValueError:
            continue
    for f in heavy_files(root):
        try:
            points.append(datetime.fromtimestamp(f.stat().st_mtime))
        except OSError:
            continue
    return max(points) if points else datetime.min


_PROTECTED = {
    QueueItemStatus.processing: "Đang xử lý — không dọn",
    QueueItemStatus.ready: "Sẵn sàng đăng — không dọn",
}
_RULE_LABELS = {
    "ready": "Sẵn sàng đăng",
    "processing": "Đang xử lý",
    "posted": "Đã đăng",
    "failed": "Video lỗi",
    "skipped": "Đã bỏ qua",
    "pending": "Chờ xử lý lại (bản xử lý cũ)",
    "stale": "Bản xử lý cũ không còn dùng",
    "orphan": "Dự án tự động đã bị xoá",
}

_plan_cache: tuple[float, list[dict]] | None = None
_marker_lock = threading.Lock()


def kept_projects() -> set[str]:
    """Project người dùng đã chọn "Giữ lại" — loại khỏi tự dọn vĩnh viễn cho
    tới khi người dùng bỏ chọn."""
    return set(_load_marker().get("keep_projects") or [])


def set_keep(project_id: str, keep: bool) -> None:
    global _plan_cache
    with _marker_lock:
        marker = _load_marker()
        kept = set(marker.get("keep_projects") or [])
        if keep:
            kept.add(project_id)
        else:
            kept.discard(project_id)
        marker["keep_projects"] = sorted(kept)
        _save_marker(marker)
    _plan_cache = None


def cleanup_plan(now: datetime | None = None, use_cache: bool = False) -> list[dict]:
    """Lịch tự dọn của MỌI project do dự án tự động sinh ra còn file nặng —
    DÙNG CHUNG cho việc dọn thật (`_clean_projects`) và hiển thị (màn giám
    sát, trang dự án), để thứ người dùng thấy luôn khớp thứ sẽ bị xoá.

    Mỗi mục: project_id, social_id, aweme_id, rule, rule_label, size_mb,
    due_at (None = được bảo vệ, không dọn), protected_reason, is_current."""
    global _plan_cache
    if use_cache and _plan_cache and time.time() - _plan_cache[0] < 60:
        return _plan_cache[1]
    now = now or datetime.now()
    states = {}
    for summary in sp.list_social_projects():
        try:
            states[summary.id] = sp.load_state(summary.id)
        except FileNotFoundError:
            continue
    running = jobs.running_keys()
    kept = kept_projects()
    plan: list[dict] = []
    if pj.PROJECTS_DIR.is_dir():
        for root in pj.PROJECTS_DIR.iterdir():
            meta = root / "project.json"
            if not meta.is_file():
                continue
            try:
                link = json.loads(meta.read_text(encoding="utf-8")).get("social_link")
            except (OSError, ValueError):
                continue
            if not link:
                continue  # project tạo tay — không tự dọn
            files = heavy_files(root)
            if not files:
                continue
            pid = root.name
            st = states.get(link.get("social_id"))
            item = next((i for i in st.queue if i.aweme_id == link.get("aweme_id")), None) if st else None
            current = item is not None and item.project_id == pid
            entry = {
                "project_id": pid,
                "social_id": link.get("social_id"),
                "social_title": st.title if st else link.get("social_id"),
                "aweme_id": link.get("aweme_id"),
                "video_title": (item.title if item else "") or "",
                "size_mb": round(sum(_size(f) for f in files) / 1e6),
                "is_current": current,
                "due_at": None,
                "protected_reason": None,
            }
            if st is None:
                rule = "orphan"
            elif not current:
                rule = "stale"
            else:
                rule = item.status.value
            entry["rule"] = rule
            entry["rule_label"] = _RULE_LABELS.get(rule, rule)
            entry["kept_by_user"] = pid in kept
            if pid in kept:
                entry["protected_reason"] = "Bạn đã chọn giữ lại — không tự xoá"
            elif any(k.startswith(f"{pid}:") for k in running):
                entry["protected_reason"] = "Đang chạy một bước — không dọn"
            elif current and item.status in _PROTECTED:
                entry["protected_reason"] = _PROTECTED[item.status]
            elif current and item.status == QueueItemStatus.posted:
                entry["due_at"] = (item.posted_at or now) + CLEANUP_AFTER
            else:
                entry["due_at"] = _last_activity(root) + CLEANUP_AFTER
            plan.append(entry)
    plan.sort(key=lambda e: (e["due_at"] is None, e["due_at"] or datetime.max))
    _plan_cache = (time.time(), plan)
    return plan


def _clean_projects(now: datetime) -> tuple[int, int]:
    """Dọn các project tới hạn trong `cleanup_plan` (đã đăng ≥ 1 ngày; lỗi/
    bỏ qua/bản xử lý cũ không hoạt động ≥ 1 ngày). Không bao giờ đụng video
    đang xử lý / sẵn sàng đăng, hay project đang có job chạy. Trả (số project
    đã dọn, số byte giải phóng)."""
    global _plan_cache
    cleaned = freed = 0
    cleaned_items: list[tuple[str, str]] = []
    for entry in cleanup_plan(now):
        if entry["due_at"] is None or entry["due_at"] > now:
            continue
        root = pj.project_dir(entry["project_id"])
        size = sum(_remove(f) for f in heavy_files(root))
        if size:
            cleaned += 1
            freed += size
            logger.info(
                "social_cleanup: dọn project '{}' ({}) — giải phóng {:.0f}MB",
                entry["project_id"], entry["rule_label"], size / 1e6,
            )
        if entry["is_current"]:
            cleaned_items.append((entry["social_id"], entry["aweme_id"]))
    for social_id, aweme_id in cleaned_items:
        try:
            with sp.locked_state(social_id) as s:
                for i in s.queue:
                    if i.aweme_id == aweme_id:
                        i.files_cleaned_at = now
        except FileNotFoundError:
            pass
    _plan_cache = None
    return cleaned, freed


def delete_now(project_ids: list[str], mode: str) -> list[dict]:
    """Người dùng chọn xoá ngay (nhiều project một lúc) ở trang "Tự dọn ổ đĩa".

    mode="files": xoá file nặng như lượt tự dọn, giữ phụ đề/cấu hình.
    mode="project": xoá HẲN thư mục project pipeline (như xoá dự án ở trang
    Dự án); video trong hàng đợi dự án tự động giữ nguyên trạng thái, chỉ bỏ
    liên kết `project_id`.

    Luôn từ chối project hệ thống đang bảo vệ (đang xử lý, sẵn sàng đăng,
    đang chạy 1 bước) — lấy từ `cleanup_plan` mới nhất, không dùng cache."""
    global _plan_cache
    if mode not in ("files", "project"):
        raise ValueError("mode phải là 'files' hoặc 'project'")
    plan = {e["project_id"]: e for e in cleanup_plan()}
    now = datetime.now()
    results: list[dict] = []
    for pid in dict.fromkeys(project_ids):
        entry = plan.get(pid)
        if entry is None:
            results.append({"project_id": pid, "ok": False, "error": "Không có trong danh sách dọn (đã dọn hoặc không tồn tại)"})
            continue
        if entry["protected_reason"] and not entry["kept_by_user"]:
            results.append({"project_id": pid, "ok": False, "error": entry["protected_reason"]})
            continue
        root = pj.project_dir(pid)
        try:
            if mode == "files":
                freed = sum(_remove(f) for f in heavy_files(root))
            else:
                freed = _size(root)
                pj.delete_project(pid)
        except (OSError, FileNotFoundError) as err:
            results.append({"project_id": pid, "ok": False, "error": str(err)})
            continue
        if entry["social_id"] and entry["is_current"]:
            try:
                with sp.locked_state(entry["social_id"]) as s:
                    for i in s.queue:
                        if i.aweme_id == entry["aweme_id"] and i.project_id == pid:
                            if mode == "project":
                                i.project_id = None
                            i.files_cleaned_at = now
            except FileNotFoundError:
                pass
        if mode == "project":
            set_keep(pid, False)
        logger.info("social_cleanup: người dùng xoá ngay ({}) '{}' — giải phóng {:.0f}MB", mode, pid, freed / 1e6)
        results.append({"project_id": pid, "ok": True, "freed_mb": round(freed / 1e6)})
    _plan_cache = None
    return results


def _load_marker() -> dict:
    try:
        return json.loads(_MARKER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_marker(data: dict) -> None:
    try:
        _MARKER.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _clean_profile(profile: Path, tiktok: bool) -> int:
    """CHỈ xoá bộ nhớ đệm thuần (cache HTTP/mã JS/đồ hoạ) — tuyệt đối không
    đụng dữ liệu trang (IndexedDB, Local Storage, Service Worker, cookie).
    Đã gặp thật: bản đầu xoá thêm IndexedDB của tiktok.com (tưởng chỉ chứa
    video bản nháp) → sau đó MỌI tài khoản TikTok bị đăng xuất dù cookie
    phiên vẫn còn hạn (TikTok nhiều khả năng lưu dữ liệu nhận diện thiết bị ở
    đó, mất đi thì coi là thiết bị lạ và huỷ phiên). `tiktok` giữ lại cho
    tương thích, không còn xoá gì thêm."""
    return sum(_remove(profile / rel) for rel in _CHROME_CACHE_DIRS)


def _clean_browser_profiles(now: datetime) -> int:
    """Dọn cache profile Chrome (TikTok từng dự án + Chrome crawl Douyin), mỗi
    profile tối đa 1 lần/ngày, CHỈ khi profile đó không đang mở (không có job
    đăng/đăng nhập TikTok của dự án đó, trình duyệt Douyin đang rảnh)."""
    from .stages import douyin_browser

    marker = _load_marker()
    done = marker.setdefault("browser_cleaned_at", {})
    running = jobs.running_keys()
    freed = 0

    def due(key: str) -> bool:
        last = done.get(key)
        return not last or now - datetime.fromisoformat(last) >= BROWSER_CLEAN_INTERVAL

    for summary in sp.list_social_projects():
        try:
            st = sp.load_state(summary.id)
        except FileNotFoundError:
            continue
        profile = Path(st.tiktok_session_path) if st.tiktok_session_path else sp.social_dir(st.id) / "tiktok_profile"
        key = f"tiktok:{st.id}"
        if not profile.is_dir() or not due(key):
            continue
        if any(k.startswith(f"social:{st.id}:") and (k.endswith(":publish") or k.endswith(":tiktok_login")) for k in running):
            continue
        freed += _clean_profile(profile, tiktok=True)
        done[key] = now.isoformat()

    if douyin_browser.PROFILE_DIR.is_dir() and due("douyin"):
        if douyin_browser._browser_lock.acquire(blocking=False):
            try:
                freed += _clean_profile(douyin_browser.PROFILE_DIR, tiktok=False)
                done["douyin"] = now.isoformat()
            finally:
                douyin_browser._browser_lock.release()
    # Nạp lại marker trước khi ghi: trong lúc dọn, người dùng có thể vừa bấm
    # "Giữ lại" (`set_keep`) — chỉ gộp phần mốc dọn profile, không ghi đè.
    with _marker_lock:
        fresh = _load_marker()
        fresh.setdefault("browser_cleaned_at", {}).update(done)
        _save_marker(fresh)
    return freed


def maybe_run(force: bool = False) -> dict | None:
    """Gọi từ bộ lập lịch mỗi tick; tự giới hạn chạy tối đa mỗi 30 phút."""
    global _last_run, last_summary
    if not force and time.time() - _last_run < CLEANUP_INTERVAL_S:
        return None
    _last_run = time.time()
    now = datetime.now()
    try:
        projects_cleaned, project_bytes = _clean_projects(now)
        browser_bytes = _clean_browser_profiles(now)
    except Exception:
        logger.exception("social_cleanup: lỗi khi dọn")
        return None
    last_summary = {
        "at": now.isoformat(timespec="seconds"),
        "projects_cleaned": projects_cleaned,
        "freed_mb": round((project_bytes + browser_bytes) / 1e6),
        "free_gb": round(free_gb(), 1),
    }
    if project_bytes + browser_bytes:
        logger.info("social_cleanup: {}", last_summary)
        # Lưu lại lần dọn CÓ giải phóng dung lượng — kết quả trong bộ nhớ bị
        # mất mỗi lần server khởi động/tự nạp lại code.
        with _marker_lock:
            marker = _load_marker()
            marker["last_freed"] = last_summary
            _save_marker(marker)
    return last_summary


def last_freed_summary() -> dict | None:
    """Lần dọn gần nhất có giải phóng dung lượng (lưu trên đĩa)."""
    return _load_marker().get("last_freed") or None
