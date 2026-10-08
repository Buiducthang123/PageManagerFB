from __future__ import annotations

import base64
import json
import re
import threading
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from loguru import logger
from playwright.sync_api import Page, sync_playwright

# Đăng video lên TikTok bằng browser automation THẬT (Playwright điều khiển
# Chromium, KHÔNG dùng official Content Posting API) — quyết định đã chốt
# cùng người dùng (xem social-auto-plan.md). Luồng thao tác + heuristic tìm
# nút Publish dịch lại từ `Katzca/AutoSocial` (github.com/Katzca/AutoSocial,
# src/tiktok-uploader.js, 1263 sao, MIT) — repo Node.js, không cài làm
# dependency, chỉ đọc code để lấy đúng thứ tự thao tác/heuristic rồi viết lại
# bằng Python cho khớp vào hệ thống job/queue sẵn có của app này.

TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"

# Nhãn nút đa ngôn ngữ — TikTok Studio hiển thị theo ngôn ngữ tài khoản, tài
# khoản VN thường là tiếng Việt hoặc tiếng Anh tuỳ cấu hình trình duyệt.
_PUBLISH_LABELS = ["publish", "post", "đăng", "đăng bài", "đăng tải"]
_CONFIRM_LABELS = ["publish", "post", "confirm", "continue", "đăng", "tiếp tục", "xác nhận"]
# Bỏ hẳn "success"/"post" đơn lẻ khỏi danh sách này — quá chung chung, dễ
# khớp nhầm chữ không liên quan gì tới việc đăng bài thành công (đã xác nhận
# thật: "success" xém dính vào nội dung khác trên trang khi đang ở màn soạn
# thảo, chưa hề bấm Publish).
_PUBLISHED_CUES = ["published", "posted", "scheduled", "đã đăng", "thành công", "đăng tải thành công"]
_FAILED_CUES = ["failed", "error", "could not", "retry", "lỗi", "thất bại", "không thành công"]
# Đã bỏ "cancel" khỏi danh sách này (xem lịch sử sửa lỗi trong
# social-auto-plan.md, mục "Bug 4") — đó mới là từ nguy hiểm thật (khớp
# trúng nút Cancel THẬT của thanh upload), không phải việc quét toàn trang.
# Đã thử giới hạn phạm vi tìm kiếm về trong khối modal/dialog để né việc đó,
# nhưng lại bỏ sót popup gợi ý thật (vd "Preview your video on your phone" —
# không nằm trong khối có class modal/dialog chuẩn) — bỏ giới hạn phạm vi,
# chỉ cần bỏ đúng từ nguy hiểm là đủ, các từ còn lại (không phải "cancel")
# an toàn để quét toàn trang.
_DISMISS_LABELS = ["got it", "continue", "later", "not now", "skip", "close", "ok", "đã hiểu", "để sau", "bỏ qua", "đóng"]


class SocialPublishError(RuntimeError):
    pass


class AccountExpiredError(SocialPublishError):
    """Profile không còn đăng nhập (hết phiên / bị đăng xuất)."""


class AccountMismatchError(SocialPublishError):
    """Profile đang đăng nhập 1 tài khoản khác tài khoản đã gán cho dự án."""


class ProfileBusyError(SocialPublishError):
    """Profile Chrome đang được 1 luồng khác mở (đăng nhập/đăng bài/kiểm tra)
    — Chrome không cho 2 tiến trình mở chung 1 thư mục profile."""


# Khoá theo thư mục profile — đăng nhập, đăng bài và kiểm tra tài khoản đều
# mở persistent context trên cùng thư mục, mở chồng nhau sẽ lỗi khoá profile.
_profile_locks: dict[str, threading.Lock] = {}
_profile_locks_guard = threading.Lock()


@contextmanager
def profile_lock(profile_dir: Path, timeout_s: float = 0):
    """`timeout_s=0` = không chờ, profile bận thì ném ProfileBusyError ngay."""
    key = str(Path(profile_dir).resolve()).lower()
    with _profile_locks_guard:
        lock = _profile_locks.setdefault(key, threading.Lock())
    acquired = lock.acquire(timeout=timeout_s) if timeout_s > 0 else lock.acquire(blocking=False)
    if not acquired:
        raise ProfileBusyError("Profile tài khoản đang được dùng (đang đăng bài hoặc mở cửa sổ đăng nhập) — thử lại sau")
    try:
        yield
    finally:
        lock.release()


@dataclass
class TikTokIdentity:
    uid: str
    username: str
    screen_name: str
    avatar_url: str


_ACCOUNT_INFO_JS = """async () => {
  const r = await fetch('/passport/web/account/info/?aid=1459', {credentials: 'include'});
  return await r.text();
}"""


def _read_identity(page: Page) -> Optional[TikTokIdentity]:
    """Đọc tài khoản đang đăng nhập qua API `/passport/web/account/info/` mà
    chính trang TikTok gọi (page phải đang ở 1 trang www.tiktok.com). Đã test
    thật: đăng nhập → message "success" kèm username/uid; profile trống/hết
    phiên → message "error", name "session_expired". Trả None = chưa đăng
    nhập; ném SocialPublishError nếu phản hồi không đọc được."""
    raw = page.evaluate(_ACCOUNT_INFO_JS)
    try:
        body = json.loads(raw)
    except ValueError as err:
        raise SocialPublishError(f"Không đọc được thông tin tài khoản TikTok: {raw[:200]}") from err
    data = body.get("data") or {}
    if body.get("message") == "success" and data.get("user_id_str"):
        return TikTokIdentity(
            uid=str(data["user_id_str"]),
            username=data.get("username") or "",
            screen_name=data.get("screen_name") or "",
            avatar_url=data.get("avatar_url") or "",
        )
    if data.get("name") == "session_expired" or data.get("error_code") == 13:
        return None
    raise SocialPublishError(f"TikTok trả về phản hồi lạ khi kiểm tra tài khoản: {raw[:200]}")


def check_tiktok_account(profile_dir: Path) -> Optional[TikTokIdentity]:
    """Mở ngầm (headless) profile, đọc tài khoản đang đăng nhập. None = chưa
    đăng nhập/hết phiên. Không đợi nếu profile đang bận (ProfileBusyError)."""
    if not has_logged_in_session(profile_dir):
        return None
    with profile_lock(profile_dir), sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2000)
            return _read_identity(page)
        finally:
            try:
                context.close()
            except Exception:
                pass


def _label_regex(labels: list[str]) -> re.Pattern:
    return re.compile("|".join(re.escape(lbl) for lbl in labels), re.IGNORECASE)


def login_tiktok_interactive(
    profile_dir: Path,
    timeout_s: float = 900.0,
    should_stop: Optional[Callable[[], bool]] = None,
    url: str = TIKTOK_UPLOAD_URL,
) -> None:
    """Mở 1 cửa sổ Chrome THẬT (persistent context — giữ nguyên cookie/local
    storage như 1 Chrome profile bình thường, KHÔNG phải storage_state.json
    tạm — đúng theo cách `Katzca/AutoSocial` làm, `launchPersistentContext`)
    tới trang upload TikTok, để người dùng tự đăng nhập tay (kể cả 2FA/
    captcha). Hàm CHẶN tới khi người dùng ĐÓNG cửa sổ (tín hiệu "xong rồi")
    hoặc hết `timeout_s`. Các lần đăng bài sau tái dùng đúng `profile_dir`
    này, không cần đăng nhập lại. `url` khác = mở để xem (vd trang kênh)."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    with profile_lock(profile_dir), sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            viewport={"width": 1400, "height": 1000},
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            closed = {"value": False}
            context.on("close", lambda: closed.__setitem__("value", True))
            start = time.time()
            while not closed["value"] and time.time() - start < timeout_s:
                if should_stop is not None and should_stop():
                    break
                # Playwright sync chỉ xử lý sự kiện (page/context "close") khi
                # có 1 lời gọi API — time.sleep suông thì không bao giờ thấy
                # cửa sổ đã đóng. Và đóng cửa sổ cuối cùng KHÔNG làm tiến trình
                # Chrome thoát (đã xác nhận thật: vẫn chạy ngầm, không cửa sổ),
                # nên "hết tab" mới là tín hiệu "xong rồi", không phải context close.
                try:
                    context.cookies()
                except Exception:
                    break
                if not context.pages:
                    break
                time.sleep(1)
        finally:
            try:
                context.close()
            except Exception:
                pass


def has_logged_in_session(profile_dir: Path) -> bool:
    """Account này đã TỪNG mở qua luồng đăng nhập ít nhất 1 lần chưa — chỉ
    kiểm tra thư mục Chrome profile có tồn tại, KHÔNG khẳng định đăng nhập
    thật sự thành công. Đã thử 3 cách để phát hiện đăng nhập thật (tên cookie
    `sessionid`/`uid_tt`, nội dung trang, biến JS `SIGI_STATE`) và xác nhận cả
    3 đều KHÔNG đáng tin — TikTok set những cookie/trạng thái y hệt cho cả
    khách vãng lai lẫn tài khoản đã đăng nhập, không phân biệt được từ bên
    ngoài. Vì vậy chỉ coi đây là gợi ý yếu ("account này đã setup chưa"), phép
    thử THẬT SỰ đáng tin duy nhất là bấm đăng và xem `publish_tiktok` có chạy
    được hết luồng hay không."""
    return profile_dir.exists() and any(profile_dir.iterdir())


def _dismiss_overlays(page: Page) -> None:
    # Trước đây có 1 bug thật: danh sách nhãn có "cancel" khớp trúng nút
    # Cancel THẬT của thanh upload (đã bỏ từ đó khỏi `_DISMISS_LABELS` —
    # xem chú thích tại đó). Từng thử giới hạn phạm vi quét vào khối
    # modal/dialog để né việc này, nhưng lại bỏ sót popup gợi ý thật không
    # nằm trong khối chuẩn — bỏ giới hạn, quét lại toàn trang như cũ (an
    # toàn vì danh sách nhãn giờ không còn từ nguy hiểm nào).
    pattern = _label_regex(_DISMISS_LABELS)
    for _ in range(3):
        clicked = False
        buttons = page.get_by_role("button", name=pattern)
        count = min(buttons.count(), 5)
        for i in range(count):
            btn = buttons.nth(i)
            try:
                if btn.is_visible() and not btn.is_disabled():
                    btn.click(timeout=1200)
                    clicked = True
                    page.wait_for_timeout(400)
            except Exception:
                continue
        if not clicked:
            break


def _confirm_post_now(page: Page) -> bool:
    """Bấm nút Publish trong lúc "Content check lite" CHƯA xong (chỉ mới
    kiểm tra bản quyền nhạc xong — "Music copyright check: No issues found")
    làm TikTok hiện thêm 1 hộp thoại hỏi lại: "Continue to post? ... Do you
    want to continue posting before the check is complete?" với 2 nút
    "Cancel"/"Post now" — đã xác nhận thật qua ảnh chụp người dùng gửi, KHÔNG
    nằm trong luồng đã xử lý trước đó (không khớp `_DISMISS_LABELS` lẫn cụm
    xác nhận thành công/thất bại nào), khiến code đứng im chờ vô ích. Bấm
    thẳng "Post now" nếu thấy — người dùng đã xác nhận chỉ cần đợi xong
    Music copyright check là đủ, không cần chờ hết Content check lite (có
    thể mất thêm nhiều phút)."""
    btn = page.get_by_role("button", name=re.compile(r"^post now$", re.IGNORECASE)).first
    if btn.count() == 0:
        return False
    try:
        if btn.is_visible() and not btn.is_disabled():
            btn.click(timeout=2000)
            logger.info("social_publish: gặp hộp thoại 'Continue to post?' — đã bấm 'Post now'")
            return True
    except Exception:
        pass
    return False


# Truyền file vào trang THEO TỪNG PHẦN (mỗi phần vài MB, giải mã ngay thành
# Uint8Array rồi ghép thành File ở cuối) thay vì 1 chuỗi base64 cả file: đã
# xác nhận thật với video 216MB — cách cũ đẩy 1 chuỗi ~290MB qua 1 lần
# `page.evaluate`, trong trang còn nhân bản thêm (chuỗi atob UTF-16 + mảng
# byte) lên cỡ 1GB → tab TikTok sập giữa chừng ("Target page, context or
# browser has been closed"), 2 lần liên tiếp. Video nhỏ vẫn chạy được với
# cách cũ nên trước đó không lộ ra.
_UPLOAD_CHUNK_BYTES = 6 * 1024 * 1024  # bội số của 3 → mỗi phần base64 giải mã độc lập được

_INIT_CHUNKS_JS = "() => { window.__reupChunks = []; window.__reupFile = null; }"

_PUSH_CHUNK_JS = """
(b64) => {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    window.__reupChunks.push(bytes);
    return window.__reupChunks.length;
}
"""

_DROP_FILE_JS = """
([name, mimeType]) => {
    if (!window.__reupFile) {
        window.__reupFile = new File(window.__reupChunks || [], name, { type: mimeType });
        window.__reupChunks = null;
    }
    const file = window.__reupFile;
    if (!file.size) return { ok: false, reason: 'empty file' };
    const dt = new DataTransfer();
    dt.items.add(file);
    // TikTok có nhiều phiên bản giao diện upload: "Or drag and drop it here"
    // (1 video), "Or drag and drop them here. You can upload up to 30
    // videos." (nhiều video — đã gặp thật ở tài khoản con-bo-biet-bay), bản
    // tiếng Việt "Hoặc kéo và thả...". Tìm phần tử NHỎ NHẤT có chữ bắt đầu
    // bằng cụm kéo-thả thay vì so khớp nguyên văn 1 câu.
    const cue = /^(or\\s+)?drag\\s+and\\s+drop|^(hoặc\\s+)?kéo\\s+(và\\s+)?thả/i;
    const candidates = Array.from(document.querySelectorAll('div, span, p')).filter((el) => {
        const t = (el.textContent || '').trim();
        return t.length > 0 && t.length < 160 && cue.test(t);
    });
    candidates.sort((a, b) => (a.textContent || '').length - (b.textContent || '').length);
    const marker = candidates[0];
    let dropzone = marker ? marker.closest('div[class*="upload" i]') : null;
    if (!dropzone && marker) {
        // Không có lớp "upload": leo lên tới khối đủ lớn chứa cả nút chọn video.
        let el = marker;
        for (let k = 0; k < 6 && el.parentElement; k++) {
            el = el.parentElement;
            if (el.querySelector('button, input[type="file"]')) { dropzone = el; break; }
        }
        dropzone = dropzone || marker.parentElement;
    }
    if (!dropzone) return { ok: false, reason: 'dropzone not found' };
    const opts = { bubbles: true, cancelable: true, dataTransfer: dt };
    dropzone.dispatchEvent(new DragEvent('dragenter', opts));
    dropzone.dispatchEvent(new DragEvent('dragover', opts));
    dropzone.dispatchEvent(new DragEvent('drop', opts));
    return { ok: true };
}
"""


_LOGIN_PAGE_RE = re.compile(r"log in to tiktok|đăng nhập vào tiktok|use qr code|sử dụng mã qr", re.IGNORECASE)


def _ensure_logged_in(page: Page, wait_s: float = 12.0) -> None:
    """Tài khoản bị đăng xuất thì TikTok Studio chuyển sang trang "Log in to
    TikTok" — trước đây luồng đăng cứ chạy tiếp, 3 lần tìm khung kéo-thả không
    thấy rồi báo lỗi khó hiểu "Không kéo-thả được file video". Phát hiện sớm
    để báo đúng lỗi và hướng dẫn đăng nhập lại."""
    start = time.time()
    while time.time() - start < wait_s:
        if "/login" in page.url:
            break
        try:
            body = page.locator("body").inner_text(timeout=2000)
        except Exception:
            body = ""
        if _LOGIN_PAGE_RE.search(body):
            break
        if "drag and drop" in body.lower() or "select video" in body.lower() or "kéo và thả" in body.lower():
            return
        page.wait_for_timeout(1000)
    else:
        return  # không nhận ra trang nào — để các bước sau tự xử lý như cũ
    raise SocialPublishError(
        "Tài khoản TikTok đã bị đăng xuất — vào dự án tự động, bấm \"Đăng nhập TikTok\" để đăng nhập lại rồi bấm \"Đăng lại\""
    )


_UNSAVED_DRAFT_RE = re.compile(r"wasn.t saved|chưa được lưu|continue editing", re.IGNORECASE)


def _discard_unsaved_draft(page: Page) -> None:
    """Lần đăng trước bị gián đoạn giữa chừng → TikTok Studio hiện thanh "A
    video you were editing wasn't saved. Continue editing?" (Discard/Continue)
    ngay trên khung upload — đã gặp thật, lúc đó file kéo-thả mới không được
    nhận. Bấm "Discard" để bỏ bản nháp cũ (không phải bài đã đăng), kèm hộp
    thoại xác nhận nếu TikTok hỏi lại. Chỉ gọi ở màn upload TRƯỚC khi thả file
    mới — "Discard" ở màn chỉnh sửa sẽ huỷ luôn video đang upload."""
    page.wait_for_timeout(1500)
    try:
        banner = page.get_by_text(_UNSAVED_DRAFT_RE).first
        if banner.count() == 0 or not banner.is_visible():
            return
    except Exception:
        return
    discard_re = re.compile(r"^(discard|bỏ|huỷ bỏ|hủy bỏ)$", re.IGNORECASE)
    for _ in range(2):
        btn = page.get_by_role("button", name=discard_re).last
        try:
            if btn.count() == 0 or not btn.is_visible():
                break
            btn.click(timeout=3000)
            logger.info("social_publish: gặp bản nháp chưa lưu từ lần trước — đã bấm Discard")
            page.wait_for_timeout(1200)
        except Exception:
            break


def _set_video_file(page: Page, video_path: Path) -> None:
    # Đã thử LẦN LƯỢT 2 cách "đúng chuẩn Playwright" trước — cả 2 đều gán
    # được vào input KHÔNG BÁO LỖI nhưng `input.files` vẫn luôn rỗng:
    # (1) `set_input_files` thẳng vào input ẩn (bug đầu: bấm phải 1 khối
    #     decoy trùng tên "Select video", đã sửa bằng match chính xác, nhưng
    #     files vẫn rỗng sau khi sửa).
    # (2) Bấm đúng nút thật + chặn `expect_file_chooser` (đúng khuyến nghị
    #     chính thức của Playwright cho input ẩn) — VẪN rỗng.
    # → Kết luận: TikTok Studio hiện không đọc file qua `<input>.files` nữa
    # (rất có thể chuyển sang cơ chế khác nội bộ), CHỈ nhận file qua sự kiện
    # `drop` thật với `DataTransfer` chứa File — đúng như UI có ghi "Or drag
    # and drop it here". Đã test trực tiếp: giả lập `drop` với DataTransfer
    # tự tạo (encode file ra base64, dựng lại `File` trong browser) THÀNH
    # CÔNG — trang chuyển hẳn sang màn chỉnh sửa thật (thấy "Duration:
    # 0m32s", % tiến độ upload, ô Description/Hashtags/Cover). Đổi hẳn sang
    # cách này, không dùng input.files nữa.
    if not _load_file_by_reference(page, video_path):
        _load_file_by_chunks(page, video_path)
    for attempt in range(3):
        result = page.evaluate(_DROP_FILE_JS, [video_path.name, "video/mp4"])
        if result.get("ok"):
            logger.info("social_publish: đã kéo-thả file video vào khung upload (lần thử {})", attempt + 1)
            return
        logger.warning("social_publish: kéo-thả file lần {} không thành công ({}), thử lại", attempt + 1, result)
        page.wait_for_timeout(2000)
    raise SocialPublishError("Không kéo-thả được file video vào khung upload của TikTok sau 3 lần thử")


# Ô chọn file RIÊNG của app (không phải input của TikTok — TikTok không đọc
# input.files của nó). set_input_files gán file qua cơ chế chọn file thật của
# trình duyệt → `File` chỉ THAM CHIẾU tới file trên đĩa, không nạp nội dung vào
# RAM. Cách bơm base64 từng phần cũ dựng lại cả file trong bộ nhớ trang (~3x
# dung lượng): video 1,13GB làm tab sập vì hết RAM (gặp thật 2026-10-08).
_OWN_INPUT_ID = "__reup_file_input"
_MAKE_OWN_INPUT_JS = f"""
() => {{
    let el = document.getElementById('{_OWN_INPUT_ID}');
    if (!el) {{
        el = document.createElement('input');
        el.type = 'file';
        el.id = '{_OWN_INPUT_ID}';
        el.style.display = 'none';
        document.body.appendChild(el);
    }}
    window.__reupFile = null;
}}
"""
_TAKE_OWN_INPUT_FILE_JS = f"""
() => {{
    const el = document.getElementById('{_OWN_INPUT_ID}');
    const f = el && el.files && el.files[0];
    if (!f) return 0;
    window.__reupFile = f;
    return f.size;
}}
"""


def _load_file_by_reference(page: Page, video_path: Path) -> bool:
    try:
        page.evaluate(_MAKE_OWN_INPUT_JS)
        page.set_input_files(f"#{_OWN_INPUT_ID}", str(video_path))
        size = page.evaluate(_TAKE_OWN_INPUT_FILE_JS)
    except Exception as err:  # noqa: BLE001 — không được thì quay về cách bơm từng phần
        logger.warning("social_publish: gán file qua ô chọn file riêng lỗi ({}) — dùng cách nạp từng phần", err)
        return False
    total = video_path.stat().st_size
    if size != total:
        logger.warning("social_publish: ô chọn file riêng nhận {} / {} byte — dùng cách nạp từng phần", size, total)
        return False
    logger.info("social_publish: đã gán file {} ({:.1f}MB) theo tham chiếu, không nạp vào RAM",
                video_path.name, total / 1e6)
    return True


def _load_file_by_chunks(page: Page, video_path: Path) -> None:
    page.evaluate(_INIT_CHUNKS_JS)
    total = video_path.stat().st_size
    sent = 0
    with video_path.open("rb") as fh:
        while True:
            chunk = fh.read(_UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            page.evaluate(_PUSH_CHUNK_JS, base64.b64encode(chunk).decode("ascii"))
            sent += len(chunk)
    logger.info("social_publish: đã nạp file {} ({:.1f}MB) vào trang theo từng phần", video_path.name, total / 1e6)
    if sent != total:
        raise SocialPublishError(f"Nạp file vào trang thiếu dữ liệu ({sent}/{total} byte)")


_CAPTION_SELECTORS = ['div[contenteditable="true"]', 'textarea[placeholder*="caption" i]', "textarea"]


_UPLOAD_PROGRESS_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
# Đếm ngược thời gian còn lại của upload: "2 minutes left", "30 seconds left",
# "1 hour left"... (mạng chậm hiện "minutes"/"hour", nhanh thì "seconds").
_UPLOAD_COUNTDOWN_RE = re.compile(r"\d+\s*(hour|minute|min|second|sec)s?\s*left", re.IGNORECASE)


def _wait_for_upload_processed(
    page: Page,
    timeout_s: float = 180.0,
    stall_timeout_s: float = 45.0,
    max_timeout_s: float = 1800.0,
) -> None:
    """TikTok cần thời gian upload + xử lý video thật trước khi chuyển từ màn
    "Select video to upload" sang màn chỉnh sửa (nơi có ô caption) — đã xác
    nhận thật qua screenshot lỗi: đợi cố định 15s là KHÔNG ĐỦ cho video qua
    mạng thật (15s vẫn còn đang quay spinner Upload), khiến bước điền caption
    chạy hụt vì element chưa tồn tại. Đổi hẳn sang chờ ĐÚNG TÍN HIỆU (ô
    caption thật sự xuất hiện) thay vì đoán thời gian cố định.

    `timeout_s=180` cũ là NGƯỠNG CỨNG — mạng chậm + file lớn (vd 210MB, xem
    ảnh chụp thật "14.58%, 3 minutes left" lúc lỗi) không bao giờ kịp trong 3
    phút, bị raise SocialPublishError giữa chừng, rồi `finally: context.close()`
    ở nơi gọi ĐÓNG LUÔN TRÌNH DUYỆT — chính là hành động HUỶ NGANG upload đang
    tải dở (TikTok dừng nhận file khi tab đóng), không phải chỉ "báo lỗi suông".
    Giờ đọc thêm % tiến độ hiển thị trên trang (vd "14.58%") — còn đang NHÍCH
    LÊN thì tiếp tục chờ (tới tối đa `max_timeout_s`), chỉ thật sự coi là lỗi
    khi tiến độ ĐỨNG YÊN (treo thật) quá `stall_timeout_s`, hoặc không đọc được
    % nào cả (vd TikTok đổi giao diện) thì vẫn giữ mốc `timeout_s` gốc làm an
    toàn tối thiểu."""
    start = time.time()
    last_progress: float | None = None
    last_progress_at = start
    while True:
        now = time.time()
        for selector in _CAPTION_SELECTORS:
            if page.locator(selector).first.count() > 0:
                return
        body_text = ""
        try:
            body_text = page.locator("body").inner_text(timeout=2000) or ""
        except Exception:
            pass
        m = _UPLOAD_PROGRESS_RE.search(body_text)
        progress = float(m.group(1)) if m else None
        if progress is not None and progress != last_progress:
            last_progress = progress
            last_progress_at = now
        elapsed = now - start
        stalled_for = now - last_progress_at
        if elapsed >= max_timeout_s:
            raise SocialPublishError(
                f"Video chưa xử lý xong sau {max_timeout_s:.0f}s chờ (vẫn đang upload nhưng quá lâu) — mạng quá chậm"
            )
        if elapsed >= timeout_s and (progress is None or stalled_for >= stall_timeout_s):
            raise SocialPublishError(
                f"Video chưa xử lý xong sau {elapsed:.0f}s chờ (không thấy màn chỉnh sửa, tiến độ "
                f"{'không đọc được' if progress is None else f'đứng yên ở {progress:.0f}%'}) — "
                "có thể mạng chậm hoặc TikTok đổi giao diện"
            )
        page.wait_for_timeout(1000)


def _wait_for_upload_complete(
    page: Page,
    timeout_s: float = 180.0,
    stall_timeout_s: float = 90.0,
    max_timeout_s: float = 1800.0,
) -> None:
    """Chờ tới khi thanh tiến độ upload biến mất hẳn trước khi bấm Publish — đã
    xác nhận thật gặp hộp thoại "Sure you want to cancel your upload?" khi thao
    tác lúc video còn đang tải dở.

    Trước đây chỉ chờ cố định 180s rồi BỎ QUA (chỉ cảnh báo) và bấm Publish
    luôn — mạng chậm + file lớn (vd 203MB, ảnh thật "6.15%, 2 minutes left")
    không kịp trong 180s, nên bấm Publish KHI FILE CÒN ĐANG TẢI DỞ → TikTok lỗi/
    hỏi huỷ → `finally: context.close()` ở nơi gọi ĐÓNG TRÌNH DUYỆT = huỷ ngang
    upload. Thêm nữa: chỉ dò chữ "seconds left" là HỤT khi mạng chậm hiện
    "minutes left" (ảnh thật), khiến hàm tưởng đã tải xong và return sớm ở 6%.

    Giờ dùng cùng chiến lược với `_wait_for_upload_processed`: còn thấy dấu hiệu
    đang tải (uploading / "… left" / % < 100) thì tiếp tục chờ chừng nào tiến độ
    còn NHÍCH LÊN (tới tối đa `max_timeout_s`); chỉ coi là treo thật khi % đứng
    yên quá `stall_timeout_s`."""
    start = time.time()
    last_progress: float | None = None
    last_progress_at = start
    while True:
        now = time.time()
        body_text = ""
        try:
            body_text = (page.locator("body").inner_text() or "").lower()
        except Exception:
            pass
        m = _UPLOAD_PROGRESS_RE.search(body_text)
        progress = float(m.group(1)) if m else None
        uploading = (
            "uploading" in body_text
            or _UPLOAD_COUNTDOWN_RE.search(body_text) is not None  # "x seconds/minutes left"
            or (progress is not None and progress < 100)
        )
        if not uploading:
            return
        if progress is not None and progress != last_progress:
            last_progress = progress
            last_progress_at = now
        elapsed = now - start
        stalled_for = now - last_progress_at
        if elapsed >= max_timeout_s:
            raise SocialPublishError(
                f"Upload chưa xong sau {max_timeout_s:.0f}s chờ (mạng quá chậm) — chưa bấm Publish để khỏi huỷ upload dở"
            )
        # Quá ngưỡng mềm mà tiến độ đứng yên quá lâu (hoặc không đọc được %) → treo thật.
        if elapsed >= timeout_s and (progress is None or stalled_for >= stall_timeout_s):
            raise SocialPublishError(
                f"Upload treo sau {elapsed:.0f}s chờ (tiến độ "
                f"{'không đọc được' if progress is None else f'đứng yên ở {progress:.0f}%'}) — "
                "mạng chậm hoặc TikTok đổi giao diện; chưa bấm Publish để khỏi huỷ upload dở"
            )
        page.wait_for_timeout(1000)


def _wait_for_content_check(page: Page, timeout_s: float = 90.0) -> None:
    # Nút Post chỉ bị khoá CỨNG một lúc NGẮN ngay sau khi upload xong (kiểm
    # tra sơ bộ) — KHÔNG phải khoá suốt cả "Content check lite" (~10 phút,
    # chạy nền). Đã xác nhận thật qua ảnh người dùng gửi: bấm Post trong lúc
    # "Content check lite" còn "Checking in progress" (chỉ "Music copyright
    # check" xong) vẫn bấm được, TikTok chỉ hỏi lại xác nhận qua hộp thoại
    # "Continue to post?" (xem `_confirm_post_now`) chứ không chặn hẳn. Trước
    # đây hiểu nhầm phải chờ đủ ~10-12 phút — không cần, chỉ cần chờ qua đúng
    # khoảng khoá ngắn ban đầu rồi bấm thử, để `_confirm_post_now` xử lý nốt
    # hộp thoại xác nhận nếu content check thật sự chưa xong.
    post_button = page.get_by_role("button", name=re.compile(r"^(publish|post|đăng)$", re.IGNORECASE)).first
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            if post_button.count() > 0 and not post_button.is_disabled():
                return
        except Exception:
            pass
        page.wait_for_timeout(3000)
    logger.warning(
        "social_publish: nút Post vẫn khoá sau {}s chờ content check, vẫn tiếp tục thử (có thể selector khác đổi)",
        timeout_s,
    )


# Dán caption như Ctrl+V: bắn 1 sự kiện `paste` chứa toàn bộ caption vào ô
# đang focus — trình soạn thảo (Draft.js) của TikTok tự xử lý paste, giữ
# nguyên xuống dòng/dấu tiếng Việt. Không dùng clipboard thật của máy (cần
# quyền trình duyệt, và đè mất thứ người dùng đang copy).
_PASTE_JS = """
(text) => {
  const el = document.activeElement;
  if (!el) return false;
  const dt = new DataTransfer();
  dt.setData('text/plain', text);
  const ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
  el.dispatchEvent(ev);
  return true;
}
"""


def _norm_caption(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text or ""))


def _read_caption(target) -> str:
    try:
        return target.inner_text(timeout=3000) if target.evaluate("e => e.tagName") != "TEXTAREA" else target.input_value()
    except Exception:
        return ""


def _set_caption(page: Page, caption: str) -> None:
    """Điền caption rồi ĐỌC LẠI để chắc đúng từng chữ. Trước đây gõ từng ký tự
    (`type`, delay 10ms) — đã xác nhận thật: ô caption TikTok (Draft.js, có
    gợi ý #hashtag/@mention) làm tiếng Việt có dấu bị đảo thứ tự/dính chữ
    ("đào tạoA tay đua", "khôn-đức-bu"). Giờ dán 1 lần; không khớp thì thử
    `insert_text` (1 sự kiện nhập, không gõ phím); vẫn sai thì DỪNG — không
    đăng bài với caption hỏng."""
    if not caption:
        return
    for selector in _CAPTION_SELECTORS:
        target = page.locator(selector).first
        if target.count() == 0:
            continue
        try:
            target.click(timeout=8000)
        except Exception:
            continue
        actual = ""
        for method in ("paste", "insert_text"):
            try:
                page.keyboard.press("Control+A")
                page.keyboard.press("Delete")
                page.wait_for_timeout(300)
                if method == "paste":
                    page.evaluate(_PASTE_JS, caption)
                else:
                    page.keyboard.insert_text(caption)
                page.wait_for_timeout(800)
            except Exception as err:
                logger.warning("social_publish: điền caption bằng {} lỗi: {}", method, err)
                continue
            actual = _read_caption(target)
            if _norm_caption(actual) == _norm_caption(caption):
                logger.info("social_publish: đã điền caption ({}), đọc lại khớp", method)
                return
            logger.warning("social_publish: caption sau khi {} không khớp — đọc lại: {!r}", method, actual[:200])
        raise SocialPublishError(
            "Caption điền vào TikTok bị sai chữ, đã dừng không đăng. Đọc lại được: " + (actual[:120] or "(trống)")
        )
    raise SocialPublishError("Không tìm thấy ô nhập caption")


# JS heuristic tìm nút Publish thật — dịch trực tiếp từ `tryClickPublishButton`
# (chiến lược cuối, brute-force qua page.evaluate) trong tiktok-uploader.js
# tham khảo: né nút "Post" ở sidebar/nav (dễ nhầm với menu điều hướng), ưu
# tiên nút nằm ở nửa dưới màn hình (khớp vị trí thật của nút Publish trên
# TikTok Studio) và có class gợi ý publish/submit.
_FIND_PUBLISH_JS = """
(labels) => {
  const normalize = (v) => String(v || '').trim().replace(/\\s+/g, ' ').toLowerCase();
  const wanted = labels.map(normalize).filter(Boolean);
  const isLikely = (btn) => {
    const text = normalize(btn.textContent || btn.getAttribute('aria-label'));
    if (!text || text === 'posts') return false;
    const exact = wanted.includes(text);
    const loose = wanted.filter((l) => l !== 'post').some((l) => text.includes(l));
    if (!exact && !loose) return false;
    if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') return false;
    if (btn.closest("nav, aside, [role='navigation'], [class*='sidebar' i], [class*='menu' i]")) return false;
    return true;
  };
  const score = (btn) => {
    const rect = btn.getBoundingClientRect();
    const className = normalize(btn.className || '');
    let s = 0;
    if (/\\b(post|publish|submit)\\b/.test(className)) s += 20;
    if (rect.width >= 80 && rect.height >= 28) s += 15;
    if (window.innerHeight > 0 && rect.top >= window.innerHeight * 0.5) s += 60;
    return s;
  };
  const buttons = Array.from(document.querySelectorAll("button, [role='button']"));
  const candidates = buttons.filter(isLikely).map((btn) => ({ btn, s: score(btn) })).sort((a, b) => b.s - a.s);
  if (candidates.length > 0) {
    candidates[0].btn.scrollIntoView({ block: 'center' });
    candidates[0].btn.click();
    return true;
  }
  return false;
}
"""


def _click_publish(page: Page) -> None:
    # Đã xác nhận thật (test trực tiếp có account thật): nhãn "post" trong
    # `_PUBLISH_LABELS` khớp CHUỖI CON vào mục điều hướng sidebar "Posts"
    # (quản lý bài đã đăng) — nếu dùng `page.get_by_role("button", name=...)`
    # so khớp chuỗi con đơn giản, nó bấm NHẦM sang "Posts" (điều hướng sang
    # trang khác), rồi chính việc đổi URL đó bị `_wait_for_publish_confirmation`
    # coi nhầm là dấu hiệu "đã đăng thành công" — báo THÀNH CÔNG GIẢ dù video
    # chưa hề được gửi đi. CHỈ dùng `_FIND_PUBLISH_JS` (có loại trừ hẳn
    # nav/sidebar + chỉ nhận "post" khi khớp CHÍNH XÁC, không khớp chuỗi con)
    # — không còn dùng `get_by_role` chuỗi con nữa cho bước này.
    _dismiss_overlays(page)
    for attempt in range(6):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(500)

        try:
            clicked = bool(page.evaluate(_FIND_PUBLISH_JS, _PUBLISH_LABELS))
        except Exception:
            clicked = False

        if clicked:
            logger.info("social_publish: đã bấm nút Publish (lần thử {})", attempt + 1)
            return
        _dismiss_overlays(page)
        page.wait_for_timeout(2000)
    raise SocialPublishError("Không tìm thấy/bấm được nút Publish sau 6 lần thử")


def _wait_for_publish_confirmation(page: Page, timeout_s: float = 60.0) -> None:
    published_pattern = _label_regex(_PUBLISHED_CUES)
    failed_pattern = _label_regex(_FAILED_CUES)
    started_url = page.url
    api_success = {"value": False}
    api_failure: dict[str, Optional[str]] = {"value": None}

    def _on_response(response) -> None:
        # Đã xác nhận thật: pattern URL rộng ("/creator", "/studio", "/aweme",
        # "/upload") khớp trúng vô số API nền SPA gọi liên tục (load thông
        # tin tài khoản, gợi ý, thống kê...) — không liên quan gì tới việc
        # BẤM PUBLISH, khiến `api_success` bật lên gần như ngay lập tức bất
        # kể có bấm Publish thật hay chưa, góp phần vào lần báo "thành công"
        # giả trước đó. Thu hẹp CHỈ còn đúng cụm "/publish" (endpoint gửi bài
        # đăng thật) — không đủ tự tin cho các cụm chung chung còn lại.
        method = response.request.method
        if method not in ("POST", "PUT", "PATCH"):
            return
        url = response.url.lower()
        if "/publish" not in url:
            return
        if 200 <= response.status < 300:
            api_success["value"] = True
        elif response.status >= 400:
            api_failure["value"] = f"API trả {response.status}: {response.url}"

    page.on("response", _on_response)
    try:
        start = time.time()
        while time.time() - start < timeout_s:
            _confirm_post_now(page)
            _dismiss_overlays(page)
            body_text = ""
            try:
                body_text = (page.locator("body").inner_text() or "").lower()
            except Exception:
                pass
            if failed_pattern.search(body_text):
                raise SocialPublishError("TikTok báo lỗi sau khi bấm Publish")
            if api_failure["value"]:
                raise SocialPublishError(api_failure["value"])
            if api_success["value"] or published_pattern.search(body_text):
                return
            if page.url != started_url and "/upload" not in page.url:
                return
            page.wait_for_timeout(2000)
        raise SocialPublishError("Không xác nhận được kết quả đăng bài sau khi chờ")
    finally:
        page.remove_listener("response", _on_response)


def publish_tiktok(
    video_path: Path,
    caption: str,
    profile_dir: Path,
    screenshot_dir: Optional[Path] = None,
    expected_uid: str = "",
) -> None:
    """Đăng 1 video lên TikTok qua account đã đăng nhập sẵn trong
    `profile_dir` (xem `login_tiktok_interactive`). Ném `SocialPublishError`
    nếu thất bại — chụp màn hình lại lúc lỗi (và lúc thành công) vào
    `screenshot_dir` để biết chính xác TikTok đổi UI ở bước nào nếu selector
    có ngày nào đó không còn khớp. `expected_uid` (uid tài khoản đã gán cho
    dự án) → kiểm tra đúng tài khoản trước khi đưa file lên, lệch là dừng."""
    if not video_path.exists():
        raise SocialPublishError(f"Không thấy file video: {video_path}")
    if not has_logged_in_session(profile_dir):
        raise SocialPublishError("Account TikTok này chưa đăng nhập — bấm 'Đăng nhập TikTok' trước")

    console_log: list[str] = []
    # Chờ tối đa 90s — đủ cho 1 lượt kiểm tra tài khoản ngầm đang chạy dở,
    # nhưng không treo cả hàng đợi đăng khi người dùng đang mở cửa sổ đăng nhập.
    with profile_lock(profile_dir, timeout_s=90), sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            viewport={"width": 1400, "height": 1000},
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            # Ghi lại console browser + request lỗi ra 1 file log riêng mỗi
            # lần đăng — lần trước phải đoán mò qua ảnh chụp tĩnh mới tìm ra
            # nguyên nhân thật (file không gán được vào input), có log này
            # thì lần sau biết ngay không cần dựng lại kịch bản debug riêng.
            page.on("console", lambda msg: console_log.append(f"[console:{msg.type}] {msg.text[:300]}"))
            page.on(
                "requestfailed",
                lambda req: console_log.append(f"[request-failed] {req.url[:200]} — {req.failure}"),
            )
            try:
                page.goto(TIKTOK_UPLOAD_URL, wait_until="domcontentloaded")
                try:
                    identity = _read_identity(page)
                except SocialPublishError as err:
                    # API nhận dạng trả phản hồi lạ — không chặn đăng bài vì
                    # nó, `_ensure_logged_in` bên dưới vẫn bắt được đăng xuất.
                    logger.warning("social_publish: bỏ qua kiểm tra tài khoản — {}", err)
                    identity = TikTokIdentity(uid=expected_uid, username="", screen_name="", avatar_url="")
                if identity is None:
                    raise AccountExpiredError(
                        "Tài khoản TikTok đã bị đăng xuất — vào trang Tài khoản, bấm \"Đăng nhập lại\" rồi bấm \"Đăng lại\""
                    )
                if expected_uid and identity.uid != expected_uid:
                    raise AccountMismatchError(
                        f"Profile đang đăng nhập @{identity.username}, KHÔNG phải tài khoản đã gán cho dự án — "
                        "đã dừng, không đăng. Vào trang Tài khoản kiểm tra lại"
                    )
                _ensure_logged_in(page)
                _discard_unsaved_draft(page)
                _set_video_file(page, video_path)
                _wait_for_upload_processed(page)
                _set_caption(page, caption)
                _wait_for_upload_complete(page)
                _wait_for_content_check(page)
                _click_publish(page)
                _wait_for_publish_confirmation(page)
                if screenshot_dir:
                    screenshot_dir.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(screenshot_dir / "last-publish-success.png"), full_page=True)
                    try:
                        (screenshot_dir / "last-publish-success.log").write_text(
                            "\n".join(console_log[-300:]), encoding="utf-8"
                        )
                    except Exception:
                        pass
                logger.info("social_publish: đăng TikTok thành công — {}", video_path.name)
            except Exception as err:
                if screenshot_dir:
                    screenshot_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        page.screenshot(path=str(screenshot_dir / "last-publish-error.png"), full_page=True)
                    except Exception:
                        pass
                    try:
                        (screenshot_dir / "last-publish-error.log").write_text(
                            "\n".join(console_log[-300:]), encoding="utf-8"
                        )
                    except Exception:
                        pass
                if isinstance(err, SocialPublishError):
                    raise
                raise SocialPublishError(f"Đăng TikTok lỗi: {err}") from err
        finally:
            try:
                context.close()
            except Exception:
                pass
