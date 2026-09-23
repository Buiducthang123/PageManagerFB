from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

import yaml
from loguru import logger

from .. import config
from .. import jobs as jobs_mod

# Tải profile lớn (nhiều video) có thể mất nhiều phút — timeout rộng, tương tự
# MERGE_TIMEOUT_S ở video_merge.py.
DL_TIMEOUT_S = 1800.0

_MEDIA_EXTS = {".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png"}

# Người dùng hay dán nguyên đoạn share (caption + hashtag tiếng Trung + link)
# copy thẳng từ app Douyin thay vì link trần — link luôn dính liền chữ Hán
# ngay sau (không có khoảng trắng phân cách), nên KHÔNG thể dùng \S* (dừng ở
# whitespace) để bóc — phải giới hạn đúng tập ký tự hợp lệ trong URL thật, để
# tự dừng đúng chỗ link kết thúc thay vì nuốt luôn cả câu tiếng Trung phía sau.
_DOUYIN_URL_RE = re.compile(r"https?://(?:[\w-]+\.)?douyin\.com/[A-Za-z0-9/_.\-?&=%]*")


def extract_douyin_url(text: str) -> str:
    """Bóc đúng link douyin.com ra khỏi 1 đoạn text bất kỳ (có thể là link
    trần, hoặc nguyên đoạn caption+hashtag+link copy từ app) — trả về chính
    text gốc (đã strip) nếu không tìm thấy link nào, để lỗi thật (link sai)
    vẫn hiện ra rõ ràng thay vì nuốt âm thầm."""
    match = _DOUYIN_URL_RE.search(text)
    return match.group(0) if match else text.strip()


class DouyinDlError(RuntimeError):
    pass


def _base_config_path() -> Path:
    return Path(config.DOUYIN_DL_DIR) / "config.yml"


def _run_cli(
    args: list[str],
    job: Optional[jobs_mod.JobState],
    timeout: float = DL_TIMEOUT_S,
    on_poll: Optional[Callable[[], None]] = None,
) -> subprocess.CompletedProcess:
    """Chạy `python run.py <args>` của douyin-downloader qua Popen + poll 0.5s
    — copy y hệt pattern `video_merge.py::_run_ffmpeg` để cancel được giữa
    chừng thay vì phải chờ chạy xong hẳn. `on_poll` (nếu có) được gọi lại mỗi
    tick 0.5s — dùng để quét thư mục đích phát hiện file mới thay vì parse
    stdout (CLI dùng rich.Progress vẽ lại tại chỗ, không in dòng log sạch
    từng video để parse được)."""
    full_args = [sys.executable, "run.py", *args]
    env = os.environ.copy()
    # Tiến trình con (rich/CLI của douyin-downloader) tự in log màu ra stdout
    # bằng encoding console mặc định của Windows (cp1252) — crash ngay khi gặp
    # ký tự ngoài bảng đó (✗, tiếng Việt trong thông báo lỗi...) trước khi kịp
    # in ra lỗi thật. Ép UTF-8 cho chính tiến trình con, không chỉ cho cách
    # tiến trình cha (Popen encoding=) đọc lại output — đã xác nhận trực tiếp
    # 2 việc này độc lập nhau.
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        full_args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(config.DOUYIN_DL_DIR),
        env=env,
        encoding="utf-8",
        errors="replace",
    )
    if job is not None:
        job.process = proc
    try:
        start = time.time()
        stdout = stderr = ""
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=0.5)
                if job is not None and job.cancel_event.is_set():
                    raise jobs_mod.JobCancelled("Đã dừng theo yêu cầu người dùng")
                break
            except subprocess.TimeoutExpired:
                if on_poll is not None:
                    on_poll()
                if job is not None and job.cancel_event.is_set():
                    proc.kill()
                    proc.communicate()
                    raise jobs_mod.JobCancelled("Đã dừng theo yêu cầu người dùng")
                if time.time() - start > timeout:
                    proc.kill()
                    proc.communicate()
                    raise DouyinDlError(f"douyin-downloader chạy quá {timeout:.0f}s — đã huỷ")
        return subprocess.CompletedProcess(full_args, proc.returncode, stdout, stderr)
    finally:
        if job is not None:
            job.process = None


def _write_temp_config(overrides: dict) -> Path:
    """Đọc config.yml gốc (chứa cookie người dùng đã tự đăng nhập lấy — xem
    README) rồi ghi đè đúng các field cần cho 1 job cụ thể (link/mode/number/
    path/database) ra 1 file tạm — không đụng file config.yml gốc, và luôn
    tắt `database` (job vãng lai, không cần dedup/lịch sử lâu dài của tool)."""
    base_path = _base_config_path()
    if not base_path.exists():
        raise DouyinDlError(f"Không thấy config.yml gốc ở {base_path} — xem README để tạo (copy config.example.yml)")
    data = yaml.safe_load(base_path.read_text(encoding="utf-8")) or {}
    data.update(overrides)
    data["database"] = False

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False, encoding="utf-8")
    try:
        yaml.safe_dump(data, tmp, allow_unicode=True)
    finally:
        tmp.close()
    return Path(tmp.name)


def _is_sidecar_file(path: Path) -> bool:
    stem = path.stem
    return any(stem.endswith(suffix) for suffix in ("_cover", "_avatar", "_music", "_data", "_comments", "transcript"))


def read_video_info(json_path: Path) -> Optional[dict]:
    """Đọc gọn vài field hữu ích từ file `<n>.json` (nguyên bản aweme_data
    của Douyin, đã xác nhận thật qua 1 lần tải thử — không đăng nhập cũng có
    đủ field này, chỉ riêng danh sách VIDEO của trang cá nhân mới cần cookie
    để vượt giới hạn ~20 video gần nhất, KHÔNG phải để có được các field
    dưới đây). Trả None nếu file hỏng/thiếu — không phải lỗi cần chặn cả job,
    chỉ đơn giản là thiếu phần info hiển thị thêm."""
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    stats = data.get("statistics") or {}
    video = data.get("video") or {}
    # Douyin không có field "title" riêng — desc/caption chính là tiêu đề
    # hiển thị. Thumbnail lấy thẳng URL gốc trên CDN Douyin (đã xác nhận
    # thật: hotlink được từ origin khác, không bị chặn referer) — không cần
    # tự tải về, ảnh này ký hết hạn rất xa (nhiều năm) nên dùng thẳng an toàn.
    thumb_url = ((video.get("cover") or {}).get("url_list") or [None])[0]
    caption = data.get("desc") or ""
    return {
        "title": caption,
        "caption": caption,
        "author": (data.get("author") or {}).get("nickname") or "",
        "share_url": data.get("share_url") or "",
        "thumb_url": thumb_url,
        "duration_sec": round((data.get("duration") or 0) / 1000, 1),
        "likes": stats.get("digg_count"),
        "comments": stats.get("comment_count"),
        "shares": stats.get("share_count"),
        "play_url": _pick_play_url(video),
    }


def _pick_play_url(video: dict) -> str:
    """Lấy 1 link phát/tải trực tiếp video (không phải trang chia sẻ) từ
    field `video` trong aweme_data gốc — dùng đúng thứ tự ưu tiên của
    douyin-downloader (`core/downloader_base.py::_build_video_url_candidates`):
    thử lần lượt play_addr_h264/265/256/play_addr, ưu tiên URL có
    "watermark=0" (bản không watermark) trước URL `/aweme/v1/play/` (endpoint
    ký sẵn, luôn tải được nhưng có thể dính watermark) — không tái dùng thẳng
    class downloader vì nó cần khởi tạo cả `config`, ở đây chỉ cần đọc field,
    không tự tải file nào."""
    for key in ("play_addr_h264", "play_addr_265", "play_addr_256", "play_addr"):
        url_list = [u for u in ((video.get(key) or {}).get("url_list") or []) if u]
        if not url_list:
            continue
        url_list.sort(key=lambda u: 0 if "watermark=0" in u else 1)
        return _unescape_play_url(url_list[0])
    return ""


def _unescape_play_url(url: str) -> str:
    """Douyin trả 1 số URL CDN (`v5-dy-ov-experiment.zjcdn.com`, ...) với query
    string bị JSON-encode 2 lần — sau `json.loads` 1 lần, chuỗi vẫn còn nguyên
    literal `\\u0026` (6 ký tự: `\\`,`u`,`0`,`0`,`2`,`6`) thay vì ký tự `&` thật,
    khiến link không mở/tải được (đã xác nhận thật qua link người dùng dán:
    `...a=6383\\u0026ch=10010\\u0026...`). Giải mã lại các escape `\\uXXXX` còn
    sót để link hoạt động được."""
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), url)


def _prune_empty_dirs(start_dir: Path, stop_at: Path) -> None:
    """Sau khi chuyển video ra khỏi thư mục con gốc của douyin-downloader
    (đánh số phẳng lại), dọn nốt các thư mục cha giờ trống rỗng (thường là
    thư mục tên tác giả bằng tiếng Trung) — tránh để lại vỏ thư mục rối mắt
    không còn nội dung gì bên trong."""
    current = start_dir
    while current != stop_at and stop_at in current.parents:
        try:
            if any(current.iterdir()):
                break
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _new_media_files(dest_dir: Path, since: float) -> list[Path]:
    """Chỉ quét file trong THƯ MỤC CON của dest_dir (author/mode/.../*.mp4)
    — bỏ qua file nằm trực tiếp ở gốc dest_dir, vì đó luôn là file ĐÃ được
    `_flatten_new_files` đổi tên/di chuyển ra ngoài rồi (đánh số 1.mp4,
    2.mp4...), không phải file gốc mới sinh ra — tránh quét lại chính nó."""
    if not dest_dir.exists():
        return []
    found = [
        p
        for p in dest_dir.rglob("*")
        if p.is_file()
        and p.parent != dest_dir
        and p.suffix.lower() in _MEDIA_EXTS
        and p.stat().st_mtime >= since
        and not _is_sidecar_file(p)
    ]
    found.sort(key=lambda p: p.stat().st_mtime)
    return found


def _run_download(
    args: list[str],
    overrides: dict,
    dest_dir: Path,
    job: Optional[jobs_mod.JobState],
    flatten_names: bool = False,
) -> list[Path]:
    """`flatten_names=True` (trang "Tải video" độc lập): đổi tên mỗi video
    vừa tải xong thành số thứ tự (1.mp4, 2.mp4...) ngay tại gốc dest_dir —
    tên file/thư mục gốc của douyin-downloader luôn chứa tiêu đề tiếng Trung
    (đã xác nhận thực tế gây khó khi mở Explorer/đọc tên) nên đổi cho gọn,
    không phải người dùng chuẩn nào cũng đọc được tiếng Trung. Giữ
    `flatten_names=False` (mặc định) cho luồng ingest link vào project reup
    (fetch_url.py) — ở đó tiêu đề gốc vẫn hữu ích để hiển thị, không đi qua
    UI "Mở thư mục" nên không gặp vấn đề trên."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp_config = _write_temp_config({**overrides, "path": str(dest_dir)})
    start = time.time()
    seen: set[Path] = set()
    outputs: list[Path] = []

    def on_poll() -> None:
        for p in _new_media_files(dest_dir, start):
            if p in seen:
                continue
            seen.add(p)
            if flatten_names:
                target = dest_dir / f"{len(outputs) + 1}{p.suffix.lower()}"
                try:
                    original_parent = p.parent
                    p.rename(target)
                    # douyin-downloader ghi kèm 1 file `<stem>_data.json` cạnh
                    # video gốc (bật `json: True` ở overrides) — dọn theo
                    # đúng số thứ tự đã đánh phẳng để FE ghép đúng video ↔
                    # metadata (caption/link chia sẻ/tác giả...).
                    sidecar = original_parent / f"{p.stem}_data.json"
                    if sidecar.exists():
                        sidecar.rename(dest_dir / f"{len(outputs) + 1}.json")
                    _prune_empty_dirs(original_parent, dest_dir)
                except OSError:
                    target = p  # khác ổ đĩa hoặc đang bị khoá — giữ nguyên vị trí gốc
                label = target.stem
            else:
                target = p
                label = p.stem
            outputs.append(target)
            if job is not None:
                job.items.append(jobs_mod.JobItem(id=str(target), label=label, status="done"))
                job.done_count = len(outputs)
                job.total = max(job.total, len(outputs))
                job.current_label = f"Đã tải: {label}"

    try:
        result = _run_cli(["-c", str(tmp_config), *args], job, on_poll=on_poll)
    finally:
        tmp_config.unlink(missing_ok=True)

    on_poll()  # bắt nốt file kịp ghi ra giữa lần poll cuối và lúc process thoát
    files = [p for p in outputs if p.exists()]
    if not files and result.returncode != 0:
        raise DouyinDlError(f"douyin-downloader lỗi: {(result.stderr or result.stdout or '')[-800:]}")
    return files


def _new_json_files(dest_dir: Path, since: float) -> list[Path]:
    """Giống `_new_media_files` nhưng quét file `*_data.json` — dùng cho
    `scan_profile_info` (chỉ lấy thông tin, không tải video thật)."""
    if not dest_dir.exists():
        return []
    found = [
        p
        for p in dest_dir.rglob("*_data.json")
        if p.is_file() and p.stat().st_mtime >= since
    ]
    found.sort(key=lambda p: p.stat().st_mtime)
    return found


def scan_profile_info(
    profile_url: str,
    modes: list[str],
    number: dict[str, int],
    dest_dir: Path,
    job: Optional[jobs_mod.JobState] = None,
) -> list[dict]:
    """Chỉ lấy THÔNG TIN từng video — xem `_scan_profile_info_once` cho chi
    tiết. Đã gặp thật: đôi lúc douyin-downloader báo "thành công" (returncode
    0) nhưng không ghi ra file `_data.json` nào — API Douyin lỗi tạm thời
    phía trong, không phải lỗi cố định (đã xác nhận: gọi lại ngay sau đó
    thành công bình thường, không sửa gì code). Tự thử lại 1 lần trong
    trường hợp này trước khi chấp nhận là rỗng thật (profile không có video
    nào khớp bộ lọc) — đỡ phải người dùng tự bấm lại tay."""
    infos = _scan_profile_info_once(profile_url, modes, number, dest_dir, job=job)
    if infos:
        return infos
    logger.warning(
        "scan_profile_info: lần 1 ra 0 kết quả cho '{}' (modes={}) — thử lại 1 lần nữa...",
        profile_url, modes,
    )
    if job is not None:
        job.raise_if_cancelled()
        job.current_label = "Không lấy được gì, đang thử lại..."
    retry_dir = dest_dir.parent / f"{dest_dir.name}_retry"
    infos = _scan_profile_info_once(profile_url, modes, number, retry_dir, job=job)
    if not infos:
        logger.warning(
            "scan_profile_info: lần thử lại CŨNG ra 0 kết quả cho '{}' — chấp nhận là rỗng "
            "(profile không có video khớp bộ lọc, HOẶC bị Douyin chặn tạm/link hỏng — xem log "
            "chi tiết ở _scan_profile_info_once phía trên).",
            profile_url,
        )
    return infos


def _scan_profile_info_once(
    profile_url: str,
    modes: list[str],
    number: dict[str, int],
    dest_dir: Path,
    job: Optional[jobs_mod.JobState] = None,
) -> list[dict]:
    """Chỉ lấy THÔNG TIN từng video (title/caption/thumb/link/thống kê) —
    KHÔNG tải video thật (`video: False` trong config), nhanh hơn nhiều so
    với tải đầy đủ vì bỏ qua hẳn bước tải file mp4/ảnh, chỉ gọi API lấy chi
    tiết từng aweme rồi ghi JSON. Dùng khi chỉ cần xem/lọc trước, chưa cần
    tải — xem `read_video_info` cho các field trả về. `dest_dir` là thư mục
    tạm chứa các file `*_data.json` trong lúc quét, không giữ lại lâu dài
    (không có file media nào để dọn theo kiểu flatten như các hàm tải khác)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted_url = extract_douyin_url(profile_url)
    logger.info(
        "scan_profile_info: bắt đầu quét '{}' (bóc được link: '{}') — modes={} number={}",
        profile_url, extracted_url, modes, number,
    )
    overrides = {
        "link": [extracted_url],
        "mode": modes,
        "number": {**{"post": 0, "like": 0, "mix": 0, "music": 0}, **number},
        "video": False,
        "json": True,
    }
    tmp_config = _write_temp_config({**overrides, "path": str(dest_dir)})
    start = time.time()
    seen: set[Path] = set()
    infos: list[dict] = []

    def on_poll() -> None:
        for p in _new_json_files(dest_dir, start):
            if p in seen:
                continue
            seen.add(p)
            info = read_video_info(p)
            if info:
                infos.append(info)
                logger.info("scan_profile_info: [{}] {}", len(infos), info["play_url"] or "(không có link tải trực tiếp)")
                if job is not None:
                    label = (info["caption"] or p.stem)[:60]
                    job.items.append(jobs_mod.JobItem(id=str(p), label=label, status="done"))
                    job.done_count = len(infos)
                    job.total = max(job.total, len(infos))
                    job.current_label = f"Đã lấy thông tin: {len(infos)}"

    try:
        result = _run_cli(["-c", str(tmp_config), "--show-warnings"], job, on_poll=on_poll)
    finally:
        tmp_config.unlink(missing_ok=True)

    on_poll()  # bắt nốt file kịp ghi ra giữa lần poll cuối và lúc process thoát

    stdout = result.stdout or ""
    if "Failed to parse URL" in stdout:
        # Trường hợp CỤ THỂ đã xác nhận thật: link share hỏng/hết hạn —
        # douyin-downloader theo redirect ra thẳng trang chủ douyin.com (không
        # phải trang cá nhân nào), không có ID để bóc → KHÔNG PHẢI lỗi code
        # hay bị chặn, mà là link đầu vào không hợp lệ. Log rõ để người dùng
        # tự biết cần dán lại link mới, không cần hỏi lại.
        fail_line = next((ln for ln in stdout.splitlines() if "Failed to parse URL" in ln), "").strip()
        logger.error(
            "scan_profile_info: LINK KHÔNG HỢP LỆ — '{}' (bóc ra '{}') không trỏ tới trang cá "
            "nhân nào (douyin-downloader báo: {}). Kiểm tra bằng `curl -IL <link>` sẽ thấy nó "
            "redirect ra trang chủ douyin.com rồi 404 — cần dán lại link chia sẻ MỚI từ app Douyin.",
            profile_url, extracted_url, fail_line,
        )
    else:
        summary_lines = [ln for ln in stdout.splitlines() if any(k in ln for k in ("Total", "Success", "Failed", "Skipped"))]
        logger.info(
            "scan_profile_info: CLI xong (returncode={}) — {} file _data.json tìm thấy, {} info parse được. Tóm tắt CLI: {}",
            result.returncode, len(seen), len(infos), " | ".join(summary_lines) or "(không có bảng tóm tắt)",
        )
        if not infos:
            stderr_tail = result.stderr or ""
            success_match = re.search(r"Success\s*[│|]\s*(\d+)", stdout)
            success_count = int(success_match.group(1)) if success_match else 0
            diag_lines = [ln for ln in stderr_tail.splitlines() if " - ERROR - " in ln or " - WARNING - " in ln]

            if success_count > 0:
                # CLI tự báo Success > 0 (tải/xử lý được aweme_data thật) nhưng
                # app KHÔNG đọc được file `_data.json` nào — đây là LỖI CODE
                # phía app (đường dẫn/quyền ghi file/parse sai), KHÔNG PHẢI do
                # Douyin chặn — phải kiểm tra `_new_json_files`/`read_video_info`,
                # không đổ tại mạng/rate-limit trong trường hợp này.
                logger.error(
                    "scan_profile_info: CLI báo {} THÀNH CÔNG nhưng app KHÔNG đọc được _data.json nào trong '{}' — "
                    "NGHI LỖI CODE phía app (không phải Douyin chặn) — kiểm tra _new_json_files/read_video_info.",
                    success_count, dest_dir,
                )
            elif diag_lines:
                # Success=0 thật sự (CLI cũng không lấy được gì) — log rõ dòng
                # ERROR/WARNING cụ thể từ chính douyin-downloader làm lý do.
                logger.warning(
                    "scan_profile_info: 0 kết quả — {} dòng ERROR/WARNING từ douyin-downloader (mới nhất trước): {}",
                    len(diag_lines), " || ".join(diag_lines[-5:]),
                )
            else:
                logger.warning(
                    "scan_profile_info: 0 kết quả, không có dòng ERROR/WARNING rõ ràng nào trong log — "
                    "stderr tail (2000 ký tự cuối): {}",
                    stderr_tail[-2000:] or "(rỗng)",
                )

    if not infos and result.returncode != 0:
        raise DouyinDlError(f"douyin-downloader lỗi: {(result.stderr or result.stdout or '')[-800:]}")
    return infos


def download_single(
    url: str, dest_dir: Path, job: Optional[jobs_mod.JobState] = None, flatten_names: bool = False
) -> tuple[Path, str]:
    files = _run_download(
        ["--show-warnings"], {"link": [extract_douyin_url(url)], "json": True}, dest_dir, job, flatten_names=flatten_names
    )
    if not files:
        raise DouyinDlError("Không tải được video — link sai, video riêng tư, hoặc cookie hết hạn")
    video_path = files[-1]
    title = video_path.stem
    return video_path, title


def download_profile_batch(
    profile_url: str,
    modes: list[str],
    number: dict[str, int],
    dest_dir: Path,
    job: Optional[jobs_mod.JobState] = None,
    flatten_names: bool = False,
) -> list[Path]:
    overrides = {
        "link": [extract_douyin_url(profile_url)],
        "mode": modes,
        "number": {**{"post": 0, "like": 0, "mix": 0, "music": 0}, **number},
        "json": True,
    }
    files = _run_download(["--show-warnings"], overrides, dest_dir, job, flatten_names=flatten_names)
    if not files:
        raise DouyinDlError("Không tải được video nào — trang cá nhân trống, riêng tư, hoặc cookie hết hạn")
    return files


_SEARCH_LINK_FIELDS = ("share_url", "url", "link")


def _extract_search_links(jsonl_path: Path, max_results: int) -> list[str]:
    links: list[str] = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        link = next((item[f] for f in _SEARCH_LINK_FIELDS if item.get(f)), None)
        if not link and item.get("aweme_id"):
            link = f"https://www.douyin.com/video/{item['aweme_id']}"
        if link:
            links.append(link)
        if len(links) >= max_results:
            break
    return links


def search_and_download(
    keyword: str,
    max_results: int,
    dest_dir: Path,
    job: Optional[jobs_mod.JobState] = None,
    flatten_names: bool = False,
) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    search_start = time.time()
    result = _run_cli(["--search", keyword, "--search-max", str(max_results), "-p", str(dest_dir)], job)

    search_dir = dest_dir / "search"
    jsonl_files = sorted(search_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime) if search_dir.exists() else []
    jsonl_files = [p for p in jsonl_files if p.stat().st_mtime >= search_start]
    if not jsonl_files:
        raise DouyinDlError(f"Tìm từ khoá không ra kết quả: {(result.stderr or result.stdout or '')[-800:]}")

    links = _extract_search_links(jsonl_files[-1], max_results)
    if not links:
        raise DouyinDlError("Không lấy được link video nào từ kết quả tìm kiếm")

    return _run_download(["--show-warnings"], {"link": links, "json": True}, dest_dir, job, flatten_names=flatten_names)
