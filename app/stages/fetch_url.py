from __future__ import annotations

import base64
import html
import json
import re
import shutil
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import requests
from loguru import logger

from .. import config
from . import douyin_dl
from .ingest import VIDEO_EXTENSIONS, probe_duration

SNAPTIKTOK_ENDPOINT = "https://snaptiktok.to/api/ajaxSearch"

_HEADERS = {
    "accept": "*/*",
    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
    "origin": "https://snaptiktok.to",
    "referer": "https://snaptiktok.to/vi/douyin-downloader",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "x-requested-with": "XMLHttpRequest",
}

_TITLE_RE = re.compile(r"<h3>(.*?)</h3>", re.DOTALL)
_DL_LINK_RE = re.compile(r'class="tik-button-dl[^"]*"[^>]*href="([^"]+)"')


class FetchUrlError(Exception):
    pass


def resolve_download_link(share_text: str) -> tuple[str, str]:
    """Gọi API snaptiktok.to để lấy link mp4 gốc (không watermark) + tiêu đề.

    `share_text` có thể là link trần (v.douyin.com/..., douyin.com/video/...,
    tiktok.com/...) hoặc nguyên đoạn text share copy từ app (site tự bóc tách link).
    """
    share_text = share_text.strip()
    if not share_text:
        raise FetchUrlError("Link/đoạn share trống")

    try:
        resp = requests.post(
            SNAPTIKTOK_ENDPOINT,
            headers=_HEADERS,
            data={"q": share_text, "cursor": "0", "page": "0", "lang": "vi"},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.ConnectionError as err:
        # Đo thật: DNS nhà mạng VN trả 127.0.0.1 cho snaptiktok.to (bị chặn), DNS Google thì ra IP thật.
        raise FetchUrlError("không kết nối được snaptiktok.to (mất mạng, hoặc nhà mạng chặn tên miền này)") from err
    except requests.RequestException as err:
        raise FetchUrlError(f"Không gọi được dịch vụ resolve link: {err}") from err
    except ValueError as err:
        raise FetchUrlError(f"Phản hồi không phải JSON hợp lệ: {err}") from err

    if payload.get("status") != "ok":
        raise FetchUrlError(f"Dịch vụ resolve link báo lỗi: {payload.get('status')}")

    body = payload.get("data") or ""
    links = _DL_LINK_RE.findall(body)
    if not links:
        raise FetchUrlError("Không tìm thấy link tải trong phản hồi — link có thể sai hoặc video riêng tư")

    # Link đầu tiên thường trỏ qua trang trung gian (pro.snapcdn.app) kèm quảng cáo;
    # ưu tiên link CDN gốc (không phải snapcdn.app) nếu có.
    direct = next((u for u in links if "snapcdn.app" not in u), links[0])
    direct = html.unescape(direct)

    title_match = _TITLE_RE.search(body)
    title = html.unescape(title_match.group(1)).strip() if title_match else "video"

    return direct, title


ProgressCallback = Callable[[int, int, str], None]


def _is_douyin(share_text: str) -> bool:
    # share_text có thể là cả đoạn caption kèm link, không chỉ link trần —
    # tìm domain douyin.com ở bất kỳ đâu trong chuỗi thay vì parse URL cứng.
    m = re.search(r"https?://\S*douyin\.com\S*", share_text)
    if not m:
        return False
    return "douyin.com" in urlparse(m.group(0)).netloc


def _replace_dest_video(project_root: Path, source: Path) -> Path:
    dest = project_root / f"video{source.suffix.lower()}"
    for old in project_root.glob("video.*"):
        if old.suffix.lower() in VIDEO_EXTENSIONS and old.resolve() != source.resolve():
            old.unlink(missing_ok=True)
    if source.resolve() != dest.resolve():
        shutil.move(str(source), str(dest))
    return dest


def _download_via_douyin_dl(
    project_root: Path,
    share_text: str,
    on_progress: Optional[ProgressCallback] = None,
) -> tuple[Path, str, float | None]:
    if on_progress:
        on_progress(0, 1, "Đang tải video...")
    tmp_dir = project_root / "_douyin_dl_tmp"
    video_path, title = douyin_dl.download_single(share_text, tmp_dir)
    dest = _replace_dest_video(project_root, video_path)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    duration = probe_duration(dest)
    return dest, title, duration


class SlowDownloadError(FetchUrlError):
    """Link tải được nhưng quá chậm — nơi gọi nên chuyển sang nguồn khác."""


# Cửa sổ đo tốc độ khi có `min_mbps` — đo trên CỬA SỔ TRƯỢT (không phải trung
# bình từ đầu) để bắt cả trường hợp đang nhanh rồi bị bóp giữa chừng.
_SPEED_WINDOW_S = 20.0


def _stream_download(
    video_url: str,
    dest: Path,
    on_progress: Optional[ProgressCallback] = None,
    headers: Optional[dict[str, str]] = None,
    min_mbps: Optional[float] = None,
) -> None:
    """`min_mbps` (MB/s): tốc độ trong `_SPEED_WINDOW_S` giây gần nhất dưới mức
    này thì dừng, ném `SlowDownloadError` — chỉ truyền khi nơi gọi CÒN nguồn
    dự phòng. Đã xác nhận thật: link `play_url` lưu lúc crawl có khi trỏ tới
    node CDN "experiment" của Douyin chỉ ~0.2 MB/s (video 200MB mất ~15 phút),
    trong khi link lấy lại qua viesnap cho cùng video chạy ~17 MB/s."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    for old in dest.parent.glob("video.*"):
        if old.suffix.lower() in VIDEO_EXTENSIONS and old.resolve() != dest.resolve():
            old.unlink(missing_ok=True)
    req_headers = headers if headers is not None else {"user-agent": _HEADERS["user-agent"]}
    try:
        with requests.get(video_url, headers=req_headers, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            downloaded = 0
            started = time.monotonic()
            samples: list[tuple[float, int]] = [(started, 0)]
            with dest.open("wb") as out:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    out.write(chunk)
                    downloaded += len(chunk)
                    if on_progress:
                        mb = downloaded / (1024 * 1024)
                        on_progress(downloaded, total or downloaded, f"Đang tải video... {mb:.1f}MB")
                    if min_mbps:
                        now = time.monotonic()
                        samples.append((now, downloaded))
                        while len(samples) > 2 and now - samples[1][0] >= _SPEED_WINDOW_S:
                            samples.pop(0)
                        t0, b0 = samples[0]
                        almost_done = total and downloaded >= total * 0.9
                        if now - t0 >= _SPEED_WINDOW_S and not almost_done:
                            mbps = (downloaded - b0) / (1024 * 1024) / (now - t0)
                            if mbps < min_mbps:
                                raise SlowDownloadError(
                                    f"Tải quá chậm ({mbps:.2f} MB/s < {min_mbps} MB/s, host {urlparse(video_url).netloc}) — chuyển nguồn khác"
                                )
            if total and downloaded < total:
                raise FetchUrlError(f"Tải thiếu ({downloaded / 1e6:.1f}/{total / 1e6:.1f} MB) — kết nối bị ngắt giữa chừng")
    except (SlowDownloadError, FetchUrlError):
        dest.unlink(missing_ok=True)
        raise
    except requests.RequestException as err:
        dest.unlink(missing_ok=True)
        raise FetchUrlError(f"Tải video thất bại: {err}") from err


def _download_via_snaptiktok(
    project_root: Path,
    share_text: str,
    on_progress: Optional[ProgressCallback] = None,
) -> tuple[Path, str, float | None]:
    if on_progress:
        on_progress(0, 1, "Đang lấy link video...")
    video_url, title = resolve_download_link(share_text)
    dest = project_root / "video.mp4"
    _stream_download(video_url, dest, on_progress)
    duration = probe_duration(dest)
    return dest, title, duration


VIESNAP_ENDPOINT = "https://api3.viesnap.com/douyin/info"
# Origin/Referer giả — cùng kiểu bypass đã dùng cho snaptiktok.to ở trên
# (backend viesnap.com có vẻ chỉ chặn theo Origin, không xác thực thật) —
# đã xác nhận thật bằng test trực tiếp: endpoint trả 200 kèm cdn_url tải
# được ngay (HTTP 206, đủ byte) mà không cần cookie Douyin của mình.
_VIESNAP_HEADERS = {
    "accept": "*/*",
    "content-type": "application/json",
    "origin": "https://alldublinfarmpainters.ie",
    "referer": "https://alldublinfarmpainters.ie/",
    "user-agent": _HEADERS["user-agent"],
}


def fetch_douyin_info_viesnap(video_url: str) -> Optional[dict]:
    """Gọi dịch vụ bên thứ 3 (api3.viesnap.com) để lấy `play_url` MỚI cho 1
    video Douyin — KHÔNG dùng cookie/API Douyin của mình, nên không tốn hạn
    mức/không góp phần bị risk-control (xem social-auto-plan.md, mục "biện
    pháp giảm risk-control"). Nhận được cả URL trần dạng
    `douyin.com/video/{aweme_id}` (không cần link share có token) — tiện hơn
    hẳn `share_url` (không phải video nào cũng có sẵn). Không raise — dịch
    vụ bên thứ 3 không có SLA, lỗi bất kỳ đâu (mạng/parse/thiếu field) đều
    trả None êm, nơi gọi tự rơi xuống tầng dự phòng kế tiếp."""
    try:
        resp = requests.post(VIESNAP_ENDPOINT, headers=_VIESNAP_HEADERS, json={"url": video_url}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        best = (data.get("qualities") or {}).get("best") or {}
        cdn_url = best.get("cdn_url")
        if not cdn_url:
            return None
        cdn_headers_b64 = best.get("cdn_headers") or ""
        dl_headers: dict[str, str] = {}
        if cdn_headers_b64:
            try:
                dl_headers = json.loads(base64.b64decode(cdn_headers_b64).decode("utf-8"))
            except Exception:
                dl_headers = {}
        return {
            "play_url": cdn_url,
            "headers": dl_headers or {"user-agent": _HEADERS["user-agent"]},
            "title": data.get("title") or "",
        }
    except Exception:
        return None


def download_from_viesnap(
    project_root: Path,
    video_url: str,
    title: str,
    on_progress: Optional[ProgressCallback] = None,
    min_mbps: Optional[float] = None,
) -> Optional[tuple[Path, str, float | None]]:
    """Tầng dự phòng TRƯỚC KHI cần gọi lại API Douyin trực tiếp — xem
    `fetch_douyin_info_viesnap`. Trả None (không raise) nếu dịch vụ bên thứ 3
    không dùng được, để nơi gọi rơi xuống tầng dự phòng cuối (Douyin API)."""
    info = fetch_douyin_info_viesnap(video_url)
    if info is None:
        return None
    dest = project_root / "video.mp4"
    try:
        _stream_download(info["play_url"], dest, on_progress, headers=info["headers"], min_mbps=min_mbps)
    except FetchUrlError:
        return None
    duration = probe_duration(dest)
    return dest, (info.get("title") or title), duration


def download_from_direct_url(
    project_root: Path,
    video_url: str,
    title: str,
    on_progress: Optional[ProgressCallback] = None,
    min_mbps: Optional[float] = None,
) -> tuple[Path, str, float | None]:
    """Tải thẳng từ 1 URL CDN đã có sẵn (vd `play_url` từ `douyin_dl.read_video_info`)
    — không đi qua bước resolve share-link/gọi lại douyin-downloader CLI như
    `download_video_from_share`, vì URL này đã là link tải trực tiếp rồi."""
    dest = project_root / "video.mp4"
    # CDN Douyin (douyinvod.com) trả 403 nếu thiếu Referer douyin.com — đã xác
    # nhận thật với play_url bắt từ trình duyệt: không Referer → 403 text/html,
    # có Referer → 200 video/mp4 đủ byte.
    _stream_download(
        video_url, dest, on_progress,
        headers={"user-agent": _HEADERS["user-agent"], "referer": "https://www.douyin.com/"},
        min_mbps=min_mbps,
    )
    duration = probe_duration(dest)
    return dest, title, duration


_AWEME_ID_RE = re.compile(r"(?:/video/|/note/|/share/video/|modal_id=|aweme_id=)(\d{15,21})")


def resolve_aweme_id(share_text: str) -> Optional[str]:
    """ID video Douyin từ link dài (douyin.com/video/<id>, ?modal_id=<id>) hoặc
    link rút gọn v.douyin.com (đi theo redirect tới trang share có id)."""
    url = douyin_dl.extract_douyin_url(share_text)
    m = _AWEME_ID_RE.search(url)
    if m:
        return m.group(1)
    try:
        resp = requests.get(url, headers={"user-agent": _HEADERS["user-agent"]}, timeout=15, allow_redirects=True)
        for hop in [*resp.history, resp]:
            m = _AWEME_ID_RE.search(hop.headers.get("location", "") or hop.url)
            if m:
                return m.group(1)
    except requests.RequestException:
        pass
    return None


def _download_via_browser(
    project_root: Path, share_text: str, on_progress: Optional[ProgressCallback]
) -> tuple[Path, str, float | None]:
    """Mở trang video bằng Chrome thật (profile Douyin dùng chung, đăng nhập ở
    Cài đặt) để lấy play_url — chạy như khách vẫn được, đăng nhập thì ít gặp
    captcha hơn. Cần máy có cài Google Chrome."""
    from . import douyin_browser  # nạp playwright chỉ khi thật sự cần

    if not config.chrome_installed():
        raise FetchUrlError("máy chưa cài Google Chrome")
    aweme_id = resolve_aweme_id(share_text)
    if not aweme_id:
        raise FetchUrlError("không đọc được ID video từ link")
    if on_progress:
        on_progress(0, 1, "Đang mở Chrome lấy video (đừng đóng cửa sổ Chrome)...")
    info = douyin_browser.fetch_video_info(aweme_id)
    if not info or not info.get("play_url"):
        hint = "" if douyin_browser.login_status().get("logged_in") else " — thử Đăng nhập Douyin ở Cài đặt"
        raise FetchUrlError(f"Chrome không lấy được link video (có thể bị captcha){hint}")
    return download_from_direct_url(project_root, info["play_url"], info.get("title") or aweme_id, on_progress)


# Node CDN "experiment" của Douyin có lúc chỉ ~0.2 MB/s (xem `_stream_download`);
# gọi lại viesnap thường được trả node khác cho cùng video (đo thật: 4 lần gọi
# ra 2-3 host khác nhau).
_VIESNAP_NODE_TRIES = 4
_VIESNAP_DOWNLOAD_TRIES = 2
# Chỉ để bắt kiểu "đứng hình" — mạng nhà user chậm thật (~0.5 MB/s) vẫn phải qua được.
_VIESNAP_STALL_MBPS = 0.1


def _viesnap_info(url: str) -> Optional[dict]:
    info = None
    for _ in range(_VIESNAP_NODE_TRIES):
        info = fetch_douyin_info_viesnap(url) or info
        if info and "-experiment." not in urlparse(info["play_url"]).netloc:
            break
    return info


def _try_viesnap(
    project_root: Path, url: str, on_progress: Optional[ProgressCallback]
) -> tuple[Optional[tuple[Path, str, float | None]], str]:
    """(kết quả hoặc None, lý do lỗi). Tải hỏng giữa chừng thì lấy link MỚI thử
    lại 1 lần — các tầng sau (snaptiktok hay bị nhà mạng chặn, Chrome chậm) còn
    kém hơn thử lại viesnap."""
    why = "viesnap không trả link video"
    for attempt in range(1, _VIESNAP_DOWNLOAD_TRIES + 1):
        info = _viesnap_info(url)
        if info is None:
            return None, why
        dest = project_root / "video.mp4"
        try:
            _stream_download(info["play_url"], dest, on_progress, headers=info["headers"], min_mbps=_VIESNAP_STALL_MBPS)
        except FetchUrlError as err:
            why = str(err)
            logger.info("Tải Douyin: viesnap lần {} lỗi ({}, host {})", attempt, why, urlparse(info["play_url"]).netloc)
            continue
        return (dest, info.get("title") or "video", probe_duration(dest)), ""
    return None, why


def _download_douyin(
    project_root: Path, share_text: str, on_progress: Optional[ProgressCallback]
) -> tuple[Path, str, float | None]:
    """Thử lần lượt (người dùng chốt ưu tiên viesnap):
    1) viesnap — dịch vụ bên thứ 3, nhanh, không cần cookie;
    2) snaptiktok — dịch vụ bên thứ 3 khác, không cần cookie;
    3) Chrome thật (profile Douyin của app) — khi 2 dịch vụ trên chết;
    4) douyin-downloader — chỉ khi máy có config.yml chứa cookie (máy dev).
    Mỗi tầng lỗi thì ghi lý do vào log rồi xuống tầng sau. Lời nhắn trên giao
    diện KHÔNG nêu tên dịch vụ (user chỉ cần biết đang tải / đang thử cách
    khác); lý do từng tầng nằm trong log để admin gỡ lỗi."""
    url = douyin_dl.extract_douyin_url(share_text)
    failures: list[str] = []
    total = 4 if config.douyin_dl_available() else 3

    def step(label: str) -> None:
        if on_progress:
            on_progress(0, 1, label)

    step("Đang tải video...")
    result, why = _try_viesnap(project_root, url, on_progress)
    if result is not None:
        return result
    failures.append(f"viesnap: {why}")
    logger.info("Tải Douyin: viesnap lỗi ({}) — thử snaptiktok", why)

    step(f"Đang thử cách tải khác (2/{total})...")
    try:
        return _download_via_snaptiktok(project_root, url, on_progress)
    except FetchUrlError as err:
        failures.append(f"snaptiktok: {err}")
        logger.info("Tải Douyin: snaptiktok lỗi ({}) — thử Chrome", err)

    step(f"Đang thử cách tải khác (3/{total}) — app sẽ mở Chrome, đừng đóng cửa sổ đó...")
    try:
        return _download_via_browser(project_root, url, on_progress)
    except FetchUrlError as err:
        failures.append(f"Chrome: {err}")
        logger.info("Tải Douyin: Chrome lỗi ({})", err)
    except Exception as err:  # noqa: BLE001 — playwright lỗi đủ kiểu, không được chặn tầng sau
        logger.exception("Tải Douyin: Chrome lỗi không mong đợi")
        failures.append(f"Chrome: {err}")

    if config.douyin_dl_available():
        step(f"Đang thử cách tải khác (4/{total})...")
        try:
            return _download_via_douyin_dl(project_root, url, on_progress)
        except douyin_dl.DouyinDlError as err:
            failures.append(f"douyin-downloader: {err}")

    logger.warning("Tải Douyin thất bại {} — {}", url, " | ".join(failures))
    tips = ["kiểm tra link và mạng"]
    if not config.chrome_installed():
        tips.append("cài Google Chrome")
    tips.append("đăng nhập Douyin ở Cài đặt")
    raise FetchUrlError(f"Không tải được video Douyin — {', '.join(tips)} rồi thử lại. "
                        "Vẫn lỗi thì bấm Kiểm tra hệ thống → Xuất log chẩn đoán gửi admin.")


def download_video_from_share(
    project_root: Path,
    share_text: str,
    on_progress: Optional[ProgressCallback] = None,
) -> tuple[Path, str, float | None]:
    """Link Douyin → chuỗi dự phòng `_download_douyin`. TikTok → snaptiktok."""
    project_root.mkdir(parents=True, exist_ok=True)
    if _is_douyin(share_text):
        return _download_douyin(project_root, share_text, on_progress)
    try:
        return _download_via_snaptiktok(project_root, share_text, on_progress)
    except FetchUrlError as err:
        logger.warning("Tải TikTok thất bại: {}", err)
        raise FetchUrlError("Không tải được video — kiểm tra link (video phải để công khai) và mạng rồi thử lại") from err
