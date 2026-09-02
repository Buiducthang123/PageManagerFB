from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Callable, Optional

import requests

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


def download_video_from_share(
    project_root: Path,
    share_text: str,
    on_progress: Optional[ProgressCallback] = None,
) -> tuple[Path, str, float | None]:
    if on_progress:
        on_progress(0, 1, "Đang resolve link...")
    video_url, title = resolve_download_link(share_text)

    dest = project_root / "video.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    for old in project_root.glob("video.*"):
        if old.suffix.lower() in VIDEO_EXTENSIONS and old.resolve() != dest.resolve():
            old.unlink(missing_ok=True)

    try:
        with requests.get(video_url, headers={"user-agent": _HEADERS["user-agent"]}, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            downloaded = 0
            with dest.open("wb") as out:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    out.write(chunk)
                    downloaded += len(chunk)
                    if on_progress:
                        mb = downloaded / (1024 * 1024)
                        on_progress(downloaded, total or downloaded, f"Đang tải video... {mb:.1f}MB")
    except requests.RequestException as err:
        dest.unlink(missing_ok=True)
        raise FetchUrlError(f"Tải video thất bại: {err}") from err

    duration = probe_duration(dest)
    return dest, title, duration
