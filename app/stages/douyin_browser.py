from __future__ import annotations

import json
import random
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from loguru import logger
from playwright.sync_api import BrowserContext, Page, Response, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from .. import projects as pj
from .douyin_dl import extract_douyin_url, parse_aweme_info

# Crawl Douyin bằng Chrome THẬT (Playwright persistent context — 1 Chrome
# profile đăng nhập sẵn, giữ nguyên cookie/local storage như trình duyệt
# thường), thay cho douyin-downloader (tự tạo chữ ký request bằng Python).
#
# Không tự gọi API: chỉ MỞ trang cá nhân kênh rồi CUỘN như người xem, và BẮT
# LẠI chính các response API danh sách video mà trang Douyin tự gọi. Request
# do chính code của Douyin phát ra, nên tự có đủ chữ ký chống bot + vân tay
# trình duyệt thật + cookie thật — không phụ thuộc thuật toán ký bị dịch
# ngược (hỏng mỗi khi Douyin đổi), và nhịp gọi là nhịp cuộn có độ ngẫu nhiên.
# Ý tưởng lấy từ script Console diepvantien/douyin-dowload-all-video (chạy
# fetch ngay trong trang douyin.com), nhưng bỏ phần tự fetch dồn dập 1s/lần.

# Thư mục user-data RIÊNG cho Chrome thật (channel="chrome") — KHÔNG trỏ thẳng
# vào "User Data" mặc định của Chrome: từ Chrome 136, Chrome từ chối cho công
# cụ tự động điều khiển thư mục mặc định, và profile đang mở cũng bị khoá.
# KHÔNG sao chép phiên từ profile Chrome sẵn có được: đã thử thật (profile
# "Thắng ok", có đủ cookie sessionid) — cookie Chrome mới mã hoá kiểu v20
# (app-bound encryption), chuyển sang thư mục user-data khác là không giải mã
# được, Chrome coi như chưa đăng nhập. Phải đăng nhập 1 lần ngay trong profile
# này (`login_interactive`, quét QR bằng app Douyin).
PROFILE_DIR = pj.WORKSPACE_DIR / "douyin_chrome_profile"
# Kết quả kiểm tra đăng nhập gần nhất (đọc cookie lúc cửa sổ còn mở) — route
# trạng thái đọc file này thay vì phải mở hẳn 1 Chrome chỉ để hỏi.
LOGIN_MARKER = pj.WORKSPACE_DIR / "douyin_browser_login.json"

_POST_API = "/aweme/v1/web/aweme/post/"
_DETAIL_API = "/aweme/v1/web/aweme/detail/"
# Cookie chỉ có khi đã đăng nhập (khách vãng lai chỉ có ttwid/msToken/...).
_LOGIN_COOKIES = ("sessionid", "sessionid_ss", "sid_guard")
_CAPTCHA_CUES = ("验证码", "请完成下列验证", "captcha", "verifycenter", "安全验证")

# 1 Chrome profile không mở được 2 lần cùng lúc (Chrome khoá thư mục profile)
# — mọi thao tác (đăng nhập, crawl, dò video) phải xếp hàng qua khoá này.
_browser_lock = threading.Lock()


class DouyinBrowserError(RuntimeError):
    pass


def _launch(p) -> BrowserContext:
    """Mở Chrome THẬT đã cài trên máy (channel="chrome", không phải Chromium
    đóng gói sẵn của Playwright) — vân tay trình duyệt giống hệt Chrome người
    dùng hằng ngày."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        channel="chrome",
        headless=False,
        no_viewport=True,
        args=["--disable-blink-features=AutomationControlled", "--window-size=1400,950"],
        ignore_default_args=["--enable-automation"],
    )


def _is_logged_in(context: BrowserContext) -> bool:
    try:
        cookies = context.cookies("https://www.douyin.com")
    except Exception:
        return False
    return any(c.get("name") in _LOGIN_COOKIES and c.get("value") for c in cookies)


def _write_login_marker(logged_in: bool) -> None:
    try:
        LOGIN_MARKER.write_text(
            json.dumps({"logged_in": logged_in, "checked_at": datetime.now().isoformat(timespec="seconds")}),
            encoding="utf-8",
        )
    except OSError:
        pass


def login_status() -> dict:
    """Trạng thái đăng nhập lần kiểm tra gần nhất — không mở trình duyệt."""
    try:
        data = json.loads(LOGIN_MARKER.read_text(encoding="utf-8"))
        return {"logged_in": bool(data.get("logged_in")), "checked_at": data.get("checked_at")}
    except (OSError, ValueError):
        return {"logged_in": False, "checked_at": None}


def login_interactive(timeout_s: float = 900.0, should_stop: Optional[Callable[[], bool]] = None) -> bool:
    """Mở Chrome tới douyin.com để người dùng tự đăng nhập (quét QR/SMS, tự
    giải captcha nếu có). Chặn tới khi người dùng ĐÓNG cửa sổ hoặc hết giờ.
    Kiểm tra cookie đăng nhập mỗi 2s lúc cửa sổ còn mở (đóng rồi thì không đọc
    được cookie nữa) — trả về trạng thái đăng nhập cuối cùng thấy được."""
    if not _browser_lock.acquire(timeout=5):
        raise DouyinBrowserError("Trình duyệt Douyin đang bận (đang crawl/dò video) — thử lại sau ít phút")
    try:
        with sync_playwright() as p:
            context = _launch(p)
            logged_in = False
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=60000)
                closed = {"value": False}
                context.on("close", lambda: closed.__setitem__("value", True))
                start = time.time()
                while not closed["value"] and time.time() - start < timeout_s:
                    if should_stop is not None and should_stop():
                        break
                    logged_in = _is_logged_in(context)
                    time.sleep(2)
            finally:
                try:
                    context.close()
                except Exception:
                    pass
            _write_login_marker(logged_in)
            return logged_in
    finally:
        _browser_lock.release()


def _human_pause(lo: float, hi: float) -> None:
    time.sleep(random.uniform(lo, hi))


def _human_scroll(page: Page) -> None:
    """Cuộn bằng con lăn chuột (không dùng window.scrollTo — trang Douyin
    cuộn trong 1 khối con, con lăn tự cuộn đúng khối nằm dưới con trỏ), mỗi
    lần 1 quãng ngẫu nhiên, thỉnh thoảng cuộn ngược lên 1 chút như người xem."""
    vw = page.viewport_size or {"width": 1400, "height": 950}
    page.mouse.move(vw["width"] * random.uniform(0.35, 0.65), vw["height"] * random.uniform(0.45, 0.75))
    if random.random() < 0.12:
        page.mouse.wheel(0, -random.randint(150, 400))
        _human_pause(0.4, 1.0)
    for _ in range(random.randint(2, 4)):
        page.mouse.wheel(0, random.randint(250, 550))
        _human_pause(0.15, 0.45)


def _has_captcha(page: Page) -> bool:
    try:
        html = (page.content() or "").lower()
    except Exception:
        return False
    return any(cue.lower() in html for cue in _CAPTCHA_CUES)


def _save_debug(page: Page, screenshot_dir: Optional[Path], name: str) -> None:
    if screenshot_dir is None:
        return
    try:
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot_dir / f"{name}.png"))
    except Exception:
        pass


def crawl_profile(
    profile_url: str,
    limit: int | None = None,
    on_progress: Optional[Callable[[int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    screenshot_dir: Optional[Path] = None,
    max_duration_s: float = 900.0,
) -> list[dict]:
    """Lấy thông tin video của 1 kênh (mới nhất trước), cùng định dạng dict
    với `douyin_dl.read_video_info`. `limit` None/0 = cuộn tới hết kênh (vẫn
    bị chặn bởi `max_duration_s`). Ném `DouyinBrowserError` khi không lấy được
    gì (link sai, captcha, bị chặn) — kèm ảnh chụp màn hình ở `screenshot_dir`."""
    url = extract_douyin_url(profile_url)
    if not _browser_lock.acquire(timeout=1200):
        raise DouyinBrowserError("Chờ trình duyệt Douyin rảnh quá 20 phút — có thể cửa sổ đăng nhập đang mở")
    try:
        with sync_playwright() as p:
            context = _launch(p)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                pending: list[Response] = []
                page.on("response", lambda r: pending.append(r) if _POST_API in r.url else None)

                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                deadline = time.time() + 25
                while "/user/" not in page.url and time.time() < deadline:
                    page.wait_for_timeout(500)
                if "/user/" not in page.url:
                    _save_debug(page, screenshot_dir, "douyin-browser-crawl-error")
                    raise DouyinBrowserError(
                        f"Link không dẫn tới trang cá nhân nào (đang ở {page.url[:120]}) — dán lại link kênh mới"
                    )
                _write_login_marker(_is_logged_in(context))
                _human_pause(2.5, 5.0)

                infos: list[dict] = []
                seen: set[str] = set()
                has_more = True
                api_responses = 0
                idle_rounds = 0
                start = time.time()

                while time.time() - start < max_duration_s:
                    if should_stop is not None and should_stop():
                        break
                    new_items = 0
                    while pending:
                        resp = pending.pop(0)
                        try:
                            data = resp.json()
                        except Exception:
                            continue
                        api_responses += 1
                        for aweme in data.get("aweme_list") or []:
                            info = parse_aweme_info(aweme)
                            if info["aweme_id"] and info["aweme_id"] not in seen:
                                seen.add(info["aweme_id"])
                                infos.append(info)
                                new_items += 1
                        has_more = bool(data.get("has_more"))
                    if new_items:
                        idle_rounds = 0
                        if on_progress is not None:
                            on_progress(len(infos))
                    else:
                        idle_rounds += 1

                    if limit and len(infos) >= limit:
                        break
                    if api_responses and not has_more:
                        break
                    if not api_responses and idle_rounds >= 6 and _has_captcha(page):
                        _save_debug(page, screenshot_dir, "douyin-browser-crawl-error")
                        raise DouyinBrowserError(
                            "Douyin hiện captcha xác minh — bấm 'Đăng nhập Douyin' để tự giải tay, rồi crawl lại"
                        )
                    # Cuộn mãi không ra dữ liệu mới (đã chạm đáy, hoặc trang
                    # không tải thêm) — dừng thay vì cuộn vô ích tới hết giờ.
                    if idle_rounds >= 12:
                        break

                    _human_scroll(page)
                    # Thỉnh thoảng dừng lâu hơn như người đang xem kỹ 1 video.
                    if random.random() < 0.15:
                        _human_pause(5.0, 10.0)
                    else:
                        _human_pause(1.8, 4.0)

                if not infos:
                    _save_debug(page, screenshot_dir, "douyin-browser-crawl-error")
                    raise DouyinBrowserError(
                        "Không bắt được video nào từ trang cá nhân — kênh trống/riêng tư, chưa đăng nhập, "
                        "hoặc Douyin đổi API (xem ảnh chụp douyin-browser-crawl-error.png)"
                    )
                logger.info(
                    "douyin_browser: crawl '{}' xong — {} video, {} response API, has_more={}",
                    url, len(infos), api_responses, has_more,
                )
                return infos[:limit] if limit else infos
            except PlaywrightError as err:
                if "closed" in str(err).lower():
                    raise DouyinBrowserError("Cửa sổ Chrome bị đóng giữa chừng — đừng đóng cửa sổ khi đang crawl") from err
                raise DouyinBrowserError(f"Lỗi trình duyệt khi crawl: {err}") from err
            finally:
                try:
                    context.close()
                except Exception:
                    pass
    finally:
        _browser_lock.release()


_DETAIL_FETCH_JS = """
async (awemeId) => {
  const qs = new URLSearchParams({
    device_platform: 'webapp', aid: '6383', channel: 'channel_pc_web', aweme_id: awemeId,
  });
  const r = await fetch('/aweme/v1/web/aweme/detail/?' + qs.toString(), { credentials: 'include' });
  if (!r.ok) return null;
  const text = await r.text();
  return text || null;
}
"""


def fetch_video_info(aweme_id: str, screenshot_dir: Optional[Path] = None) -> Optional[dict]:
    """Lấy `play_url` MỚI cho 1 video đã biết (link cũ hết hạn) — mở trang
    video như người xem, bắt response API chi tiết nếu trang tự gọi; không
    thấy thì gọi đúng API đó từ BÊN TRONG trang (request vẫn đi qua trình
    duyệt thật + cookie thật). Trả None nếu không lấy được (không raise) —
    nơi gọi coi đó là tín hiệu nghi risk-control."""
    if not _browser_lock.acquire(timeout=1200):
        return None
    try:
        with sync_playwright() as p:
            context = _launch(p)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                pending: list[Response] = []
                page.on("response", lambda r: pending.append(r) if _DETAIL_API in r.url else None)
                page.goto(f"https://www.douyin.com/video/{aweme_id}", wait_until="domcontentloaded", timeout=60000)
                deadline = time.time() + 15
                while time.time() < deadline:
                    while pending:
                        try:
                            detail = (pending.pop(0).json() or {}).get("aweme_detail")
                        except Exception:
                            continue
                        if detail:
                            return parse_aweme_info(detail)
                    page.wait_for_timeout(500)
                _human_pause(1.0, 2.5)
                try:
                    text = page.evaluate(_DETAIL_FETCH_JS, aweme_id)
                    detail = (json.loads(text) or {}).get("aweme_detail") if text else None
                except Exception as err:
                    logger.warning("douyin_browser: gọi API chi tiết trong trang lỗi ({})", err)
                    detail = None
                if detail:
                    return parse_aweme_info(detail)
                _save_debug(page, screenshot_dir, "douyin-browser-detail-error")
                return None
            except PlaywrightError as err:
                logger.warning("douyin_browser: dò chi tiết video '{}' lỗi trình duyệt ({})", aweme_id, err)
                return None
            finally:
                try:
                    context.close()
                except Exception:
                    pass
    finally:
        _browser_lock.release()
