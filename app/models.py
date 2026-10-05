from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

STAGE_ORDER = ("ingest", "transcribe", "translate", "tts", "assemble")
# "assemble" ở cuối chỉ thật sự dùng cho dự án "split" (mỗi đoạn tự ráp draft
# riêng) — dự án "multi" không đụng tới stage này ở cấp episode (multi ráp
# chung qua stages["assemble"] cấp PROJECT, xem _start_assemble_multi).
EPISODE_STAGE_ORDER = ("ingest", "transcribe", "translate", "tts", "assemble")

ProjectType = Literal["single", "multi"]


class StageStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class StageRecord(BaseModel):
    status: StageStatus = StageStatus.pending
    output: Optional[str] = None
    progress: Optional[str] = None
    error: Optional[str] = None
    at: Optional[datetime] = None
    engine: Optional[str] = None


class Episode(BaseModel):
    episode_id: str
    order: int
    title: Optional[str] = None
    original_filename: Optional[str] = None
    video_relpath: Optional[str] = None
    duration_sec: Optional[float] = None
    stages: dict[str, StageRecord] = Field(
        default_factory=lambda: {name: StageRecord() for name in EPISODE_STAGE_ORDER}
    )
    # "Xuất video trực tiếp" (ffmpeg, không qua CapCut) cho RIÊNG đoạn này —
    # hành động PHỤ song song với "assemble"/CapCut, cố tình nằm NGOÀI `stages`/
    # EPISODE_STAGE_ORDER giống `ProjectState.export` (nếu đưa vào stage order,
    # `episode_current_stage()` sẽ coi đoạn đã xong CapCut nhưng chưa xuất trực
    # tiếp là "chưa xong" — sai). Chỉ dùng cho dự án split.
    export: StageRecord = Field(default_factory=StageRecord)
    created_at: datetime


class ProjectState(BaseModel):
    project_id: str
    title: str
    created_at: datetime
    project_type: ProjectType = "single"
    original_filename: Optional[str] = None
    video_relpath: Optional[str] = None
    duration_sec: Optional[float] = None
    stages: dict[str, StageRecord] = Field(
        default_factory=lambda: {name: StageRecord() for name in STAGE_ORDER}
    )
    episodes: list[Episode] = Field(default_factory=list)
    auto_pipeline: bool = False
    auto_engine: str = "ocr"
    # [x,y,w,h] phân số 0-1 — thu hẹp vùng OCR quét (transcribe đọc lời thoại
    # LẪN tự dò vùng che phụ đề cũ lúc export) — None = quét mặc định (25%
    # đáy cho transcribe, nguyên khung hình cho dò vùng che). Giảm khả năng
    # OCR đọc nhầm hoạ tiết/nhân vật phức tạp ngoài dải phụ đề thành "chữ giả"
    # (đã xác nhận thật, xem detect_subtitle_region).
    auto_ocr_crop_region: Optional[list[float]] = None
    auto_tts_engine: str = "capcut"
    auto_voice: str = ""
    auto_audio_mode: str = "original"
    # dB, chỉ áp dụng khi auto_audio_mode="original" — xem export_direct.ORIGINAL_AUDIO_VOLUME_DB
    auto_original_audio_volume_db: float = -13.0
    auto_min_video_speed: float = 0.85
    # dB — âm lượng nhạc nền TỰ THÊM (file music.* upload riêng qua "Xuất
    # video trực tiếp") — xem export_direct.MUSIC_VOLUME (mặc định cũ khi
    # None). None = dùng mặc định cũ (project tạo tay, chưa từng chỉnh).
    auto_music_volume_db: Optional[float] = None
    # Độ mờ nền vùng che phụ đề cũ — xem SocialProjectState.blur_strength.
    # None = mặc định hệ thống (export_direct.BLUR_SIGMA_RATIO).
    auto_blur_strength: Optional[float] = None
    # Cỡ chữ phụ đề mới — xem export_direct.DEFAULT_SUBTITLE_FONT_SIZE.
    auto_subtitle_font_size: int = 6
    # Dự án đơn đã được cắt thành nhiều đoạn (mỗi đoạn 1 draft CapCut riêng,
    # chạy TUẦN TỰ) — khi True, stages ở cấp project (transcribe/translate/
    # tts/assemble) không còn dùng nữa, chỉ episodes mới có ý nghĩa.
    split_mode: bool = False
    # "Xuất video trực tiếp" (ffmpeg, không qua CapCut) — hành động PHỤ, song
    # song với "assemble"/CapCut, không bắt buộc theo tuần tự pipeline. Cố
    # tình KHÔNG nằm trong `stages`/STAGE_ORDER: nếu thêm vào STAGE_ORDER,
    # `current_stage()` sẽ coi mọi project đã xong CapCut nhưng chưa xuất
    # trực tiếp là "chưa xong" — sai, vì đây là lựa chọn không bắt buộc.
    export: StageRecord = Field(default_factory=StageRecord)
    # "Gộp đoạn" — nối final.mp4 của các đoạn ĐÃ XUẤT (dự án split) thành 1 video
    # duy nhất tại export/merged.mp4. Hành động PHỤ, nằm NGOÀI `stages` như
    # `export`. `output` = đường dẫn merged.mp4, `progress` = danh sách đoạn đã gộp.
    segments_merge: StageRecord = Field(default_factory=StageRecord)
    export_blur_region: Optional[list[float]] = None  # [x,y,w,h] phân số, lưu lại giữa các lần xuất
    # Danh sách [start_s, end_s] (giây, THEO TIMELINE VIDEO GỐC) mà phụ đề cứng
    # thật sự hiện trong `export_blur_region` — xem
    # transcribe_ocr.detect_subtitle_visibility(). None/[] = chưa dò được (hoặc
    # chưa dò) → export che SUỐT video như trước; có giá trị → chỉ che đúng
    # các khoảng này, áp dụng được với MỌI engine transcribe (không chỉ OCR).
    export_blur_active_ranges: Optional[list[list[float]]] = None
    # Project này có phải do "Dự án tự động" (app/social.py) tự sinh ra từ 1
    # video crawl được không — None = project bình thường (tạo tay). Khác
    # `auto_pipeline` (chỉ điều khiển tự chạy tiếp stage kế) — field này còn
    # dùng để hook sau assemble/export biết cần báo ngược lại đúng hàng đợi
    # social nào (xem `_maybe_chain_social` trong main.py).
    social_link: Optional["SocialLink"] = None
    # Đăng tay lên TikTok từ dự án đơn (không qua hàng đợi dự án tự động):
    # caption người dùng dán lần gần nhất + lịch sử các lần đăng thành công.
    tiktok_caption: str = ""
    tiktok_posts: list["TikTokPostRecord"] = Field(default_factory=list)
    # Đăng tay lên Facebook Page (Reels) — cùng ý nghĩa 2 field TikTok ở trên.
    facebook_caption: str = ""
    facebook_posts: list["FacebookPostRecord"] = Field(default_factory=list)
    # Ảnh bìa tiếng Việt (app/stages/cover.py) — phủ lên 2-4 khung ảnh bìa chữ
    # Trung ở đầu video lúc xuất. Video không có ảnh bìa thì không làm gì.
    cover_enabled: bool = True
    cover_bg: str = "#F2555A"
    cover_fg: str = "#FFFFFF"
    # Tiêu đề Việt đang dùng (AI viết lần đầu, người dùng sửa được). None = chưa tạo.
    cover_title: Optional[str] = None
    # None = chưa dò; 0 = video không có ảnh bìa.
    cover_frames: Optional[int] = None
    cover_end_s: Optional[float] = None  # giây (timeline video GỐC) hết ảnh bìa
    cover_zh: list[str] = Field(default_factory=list)  # chữ tiêu đề Trung nhận diện được
    cover_generated_at: Optional[datetime] = None
    cover_error: Optional[str] = None
    # Có ảnh bìa nhưng không tìm được cảnh trùng để lấy nền thật → giữ ảnh bìa
    # gốc; lý do để hiện cho người dùng. None = không bỏ qua.
    cover_skip_reason: Optional[str] = None


class TikTokPostRecord(BaseModel):
    account_id: str
    username: str = ""
    caption: str = ""
    posted_at: datetime


class FacebookPostRecord(BaseModel):
    page_id: str
    page_name: str = ""
    video_id: str = ""
    permalink_url: Optional[str] = None
    caption: str = ""
    posted_at: datetime


class SocialLink(BaseModel):
    social_id: str
    aweme_id: str


class QueueItemStatus(str, Enum):
    pending = "pending"  # mới crawl được, chưa xử lý
    processing = "processing"  # đang chạy pipeline (project_id đã gán)
    ready = "ready"  # export xong, chờ tới lượt đăng
    posted = "posted"
    failed = "failed"
    skipped = "skipped"  # người dùng bỏ qua tay


class QueueItem(BaseModel):
    aweme_id: str
    title: str
    # Bản dịch tiếng Việt của `title` (thường là tiếng Trung nguyên bản từ
    # Douyin) — dịch lười (chỉ khi kích hoạt video, không dịch hết cả hàng đợi
    # lúc crawl để tránh tốn quota Gemini cho video có thể không bao giờ được
    # chọn đăng) qua `translate.translate_title()`. Dùng cho tên dự án + caption
    # đăng bài. None = chưa dịch (video còn `pending`).
    title_vi: Optional[str] = None
    play_url: str
    # Link share gốc (vd https://v.douyin.com/xxx) — `play_url` là link CDN
    # có CHỮ KÝ HẾT HẠN NHANH (đã xác nhận thật: lỗi 403 Forbidden khi tải
    # video kích hoạt lâu sau lúc crawl), nên lúc kích hoạt PHẢI dò lại
    # `play_url` mới từ `share_url` này (xem `_start_social_activate`) thay
    # vì dùng thẳng bản đã lưu — share_url không có chữ ký hết hạn.
    share_url: str = ""
    thumb_url: Optional[str] = None
    duration_sec: float = 0
    discovered_at: datetime
    status: QueueItemStatus = QueueItemStatus.pending
    project_id: Optional[str] = None  # trỏ sang workspace/projects/<id> khi đã kích hoạt pipeline
    posted_at: Optional[datetime] = None
    platform_post_id: Optional[str] = None
    error: Optional[str] = None
    # Khi `status=failed`: lỗi xảy ra ở bước nào — "activate" (tải video từ
    # Douyin thất bại, trước khi có project pipeline nào chạy xong) hay
    # "publish" (đã xuất video xong, đăng TikTok thất bại). Frontend dùng để
    # hiện đúng nút retry ("Kích hoạt lại" vs "Đăng lại") — trước đây MỌI
    # video `failed` đều hiện nút "Đăng lại" dù chưa hề có video xuất ra để
    # đăng (đã xác nhận thật: bấm vào báo lỗi "Không tìm thấy video đã xuất").
    failed_stage: Optional[Literal["activate", "publish"]] = None
    # Đã tự dọn file nặng (video gốc/thành phẩm/audio WAV) của project gắn
    # với video này — xem app/social_cleanup.py. Còn giữ phụ đề/cấu hình.
    files_cleaned_at: Optional[datetime] = None
    # Lỗi lần ĐĂNG gần nhất — video đã xử lý xong (có video thành phẩm) mà đăng
    # TikTok lỗi thì VẪN là "sẵn sàng đăng" (người dùng chốt), không chuyển
    # sang "lỗi": không làm dừng dự án, bộ lập lịch tự thử lại sau
    # SOCIAL_PUBLISH_RETRY_MIN phút, tối đa SOCIAL_PUBLISH_MAX_AUTO_RETRY lần
    # liên tiếp rồi chờ người dùng bấm "Đăng lại".
    publish_error: Optional[str] = None
    publish_failed_at: Optional[datetime] = None
    publish_fail_count: int = 0
    # Caption đăng bài (tiêu đề thu hút + đoạn mô tả + câu hỏi + hashtag), viết
    # bằng `translate.generate_caption` lần đầu đăng rồi lưu lại dùng cho các
    # lần đăng lại; người dùng sửa được trước khi đăng.
    caption_vi: Optional[str] = None
    # Mỗi nền tảng ghi nhận riêng — `status=posted`/`posted_at` chỉ khi MỌI nền
    # tảng đang bật của dự án đã đăng xong (xem `_finalize_if_published` trong
    # main.py), nên TikTok xong mà Facebook lỗi thì chỉ thử lại Facebook, không
    # đăng trùng TikTok. `publish_error/_failed_at/_fail_count` ở trên là của
    # TikTok (giữ tên cũ), `fb_*` là của Facebook.
    tiktok_posted_at: Optional[datetime] = None
    fb_posted_at: Optional[datetime] = None
    fb_video_id: Optional[str] = None
    fb_permalink: Optional[str] = None
    fb_publish_error: Optional[str] = None
    fb_publish_failed_at: Optional[datetime] = None
    fb_publish_fail_count: int = 0

    @model_validator(mode="after")
    def _legacy_tiktok_posted(self) -> "QueueItem":
        # Video đã `posted` từ trước khi có nhiều nền tảng — chỉ có thể là TikTok.
        if (
            self.status == QueueItemStatus.posted
            and self.tiktok_posted_at is None
            and self.fb_posted_at is None
            and self.posted_at is not None
        ):
            self.tiktok_posted_at = self.posted_at
        return self


class SocialProjectSummary(BaseModel):
    id: str
    title: str
    douyin_profile_url: str
    status: Literal["active", "paused"] = "active"
    created_at: datetime


class SocialProjectState(BaseModel):
    id: str
    title: str
    douyin_profile_url: str
    status: Literal["active", "paused"] = "active"
    posts_per_day: int = 1
    # Cấu hình pipeline mặc định cho video tự động sinh ra — cùng ý nghĩa
    # field `auto_*` của ProjectState, tách riêng vì đây là mặc định cho CẢ
    # HÀNG ĐỢI, không phải 1 project cụ thể.
    engine: str = "ocr"
    # [x,y,w,h] phân số 0-1 — xem ProjectState.auto_ocr_crop_region, cùng ý
    # nghĩa, áp dụng chung cho MỌI video của dự án tự động này.
    ocr_crop_region: Optional[list[float]] = None
    # Cho phép dò lại play_url qua dịch vụ bên thứ 3 (api3.viesnap.com) khi
    # link đã lưu hết hạn — tầng dự phòng TRƯỚC KHI cần gọi API Douyin thật
    # (không tốn hạn mức/không góp phần risk-control tài khoản mình, xem
    # `_start_social_activate`) — mặc định BẬT, tắt được nếu không muốn phụ
    # thuộc dịch vụ bên thứ 3 (không có cam kết/SLA).
    use_viesnap_fallback: bool = True
    # Crawl + dò lại play_url bằng Chrome thật (app/stages/douyin_browser.py —
    # mở trang kênh, cuộn như người xem, bắt response API trang tự gọi) thay
    # cho douyin-downloader. Chrome dùng chung 1 profile cho mọi dự án.
    crawl_via_browser: bool = False
    tts_engine: str = "capcut"
    voice: str = ""
    audio_mode: str = "original"
    original_audio_volume_db: float = -13.0
    min_video_speed: float = 0.85
    # dB — âm lượng nhạc nền NGƯỜI DÙNG TỰ THÊM (file upload riêng, khác nhạc
    # nền GỐC của video đã tách/giữ nguyên qua audio_mode) — xem
    # export_direct.MUSIC_VOLUME (hằng số tuyến tính cũ, dùng khi field này
    # không set/None ở project thường). File nhạc lưu tại
    # `<social_dir>/music.*`, logo tại `<social_dir>/logo.*` — copy sang mỗi
    # project mới lúc kích hoạt (xem `_start_social_activate`), KHÔNG lưu
    # thẳng trong `ProjectState` vì mỗi lần kích hoạt tạo project mới hoàn
    # toàn (project cũ không giữ file export cũ).
    music_volume_db: float = -13.0
    # Độ mờ nền vùng che phụ đề cũ: hệ số độ mờ Gaussian theo chiều cao vùng
    # chữ gốc (0.3 = mặc định, càng lớn càng mờ). Copy sang
    # ProjectState.auto_blur_strength mỗi lần kích hoạt video.
    blur_strength: float = 0.3
    # Hashtag cố định luôn thêm vào caption mọi video của dự án (vd "#Pokemon
    # #AIContent"), cách nhau bằng dấu cách/phẩy.
    caption_hashtags: str = ""
    # Cỡ chữ phụ đề mới — xem ProjectState.auto_subtitle_font_size.
    subtitle_font_size: int = 6
    # Ảnh bìa tiếng Việt — xem ProjectState.cover_*; copy sang mỗi video lúc kích hoạt.
    cover_enabled: bool = True
    cover_bg: str = "#F2555A"
    cover_fg: str = "#FFFFFF"
    queue: list[QueueItem] = Field(default_factory=list)
    last_crawl_at: Optional[datetime] = None
    # Crawl lỗi/bị huỷ liên tiếp — bộ lập lịch nghỉ tăng dần trước khi tự crawl
    # lại (xem SOCIAL_CRAWL_RETRY_HOURS), thay vì mở Chrome lại mỗi vài phút
    # vì `last_crawl_at` (chỉ ghi khi thành công) vẫn đang "tới hạn".
    crawl_fail_count: int = 0
    last_crawl_failed_at: Optional[datetime] = None
    last_crawl_error: Optional[str] = None
    # Mốc đăng bài THÀNH CÔNG gần nhất — bộ lập lịch nền (`_social_scheduler_loop`
    # trong main.py) dùng để giãn cách các lần đăng theo đúng `posts_per_day`
    # (khoảng cách mục tiêu = 24h / posts_per_day), không đăng dồn dập.
    last_post_at: Optional[datetime] = None
    # Giờ hẹn đăng bài kế tiếp — tính sẵn ngay sau mỗi lần đăng thành công
    # (`_compute_next_post_at` trong main.py): giãn cách có độ ngẫu nhiên và
    # chỉ rơi vào khung giờ cao điểm, thay vì chia đều cứng 24h/posts_per_day
    # (nhịp đều như máy + đăng lúc 3h sáng là dấu hiệu bot dễ bị phát hiện).
    # None = chưa tính (dự án cũ) — bộ lập lịch tự tính bù ở tick đầu tiên.
    next_post_at: Optional[datetime] = None
    # Số lần LIÊN TIẾP bị nghi risk-control — càng bị lại càng nghỉ lâu hơn
    # (xem DOUYIN_BACKOFF_HOURS). Về 0 ngay khi 1 lần gọi API Douyin thành công.
    douyin_backoff_level: int = 0
    # Đã phát hiện Douyin risk-control tạm khoá API (vd dò lại play_url cho
    # đúng 1 video đã biết `share_url` nhưng trả về rỗng — dấu hiệu đáng tin
    # cậy hơn hẳn "crawl 0 kết quả mới", vì crawl 0 kết quả còn có thể do
    # kênh THẬT SỰ không có video mới) — bộ lập lịch bỏ qua crawl/kích hoạt
    # cho dự án này tới hết mốc này, tránh càng thử càng bị khoá lâu hơn.
    douyin_backoff_until: Optional[datetime] = None
    created_at: datetime
    # Đăng bài qua Playwright (app/stages/social_publish.py) — mỗi account 1
    # thư mục Chrome PERSISTENT CONTEXT riêng (không phải file storage_state
    # tạm — giữ nguyên cookie/local storage như 1 Chrome profile thật, đúng
    # cách `Katzca/AutoSocial` làm) — rỗng = chưa gán, mặc định
    # `<social_dir>/tiktok_profile` khi cần dùng lần đầu.
    tiktok_session_path: str = ""
    # Tài khoản TikTok gán cho dự án (id trong app/accounts.py) — có thì
    # profile Chrome lấy theo tài khoản, `tiktok_session_path` chỉ còn là
    # đường dẫn cũ trước khi có trang Tài khoản.
    tiktok_account_id: str = ""
    # Nền tảng đăng — bật/tắt riêng, mỗi nền tảng có lịch riêng:
    # - TikTok: `posts_per_day` / `last_post_at` / `next_post_at` ở trên (tên cũ).
    # - Facebook: các field `facebook_*` bên dưới.
    # `*_post_times` rỗng = tự chọn giờ ngẫu nhiên trong khung giờ cao điểm;
    # có giá trị (vd ["10:00", "19:30"]) = đăng đúng các giờ đó ±
    # `post_time_jitter_min` phút, số bài/ngày = số mốc giờ.
    tiktok_enabled: bool = True
    tiktok_post_times: list[str] = Field(default_factory=list)
    facebook_enabled: bool = False
    # id Page trong app/fb_pages.py (token lưu ở đó, không lưu trong dự án).
    facebook_page_id: str = ""
    facebook_posts_per_day: int = 1
    facebook_post_times: list[str] = Field(default_factory=list)
    facebook_last_post_at: Optional[datetime] = None
    facebook_next_post_at: Optional[datetime] = None
    post_time_jitter_min: int = 10


AccountStatus = Literal["unknown", "ok", "expired", "mismatch", "error"]


class FacebookPage(BaseModel):
    """1 Facebook Page đăng Reels qua Graph API bằng Page access token. Token
    CHỈ nằm trong file này ở máy người dùng — API trả về frontend luôn che đi."""

    page_id: str
    name: str = ""
    category: Optional[str] = None
    picture_url: Optional[str] = None
    access_token: str
    # Unix time token hết hạn — 0 = không hết hạn, None = không rõ.
    token_expires_at: Optional[int] = None
    # "oauth" (đăng nhập Facebook trong tool) | "pagesmanager" (nhập từ
    # PagesManagerSupperTool) | "token" (dán tay)
    source: str = "token"
    # ok = token còn dùng được; expired = hết hạn/bị thu hồi; error = không
    # kiểm tra được (mạng...); unknown = chưa kiểm tra.
    status: Literal["unknown", "ok", "expired", "error"] = "unknown"
    status_detail: Optional[str] = None
    checked_at: Optional[datetime] = None
    added_at: datetime


class TikTokAccount(BaseModel):
    """1 tài khoản TikTok = 1 thư mục Chrome profile đã đăng nhập tay. Thông
    tin nhận dạng (username/uid...) đọc từ API `/passport/web/account/info/`
    mà chính trang TikTok dùng — xem social_publish.check_tiktok_account."""

    id: str
    # Ghi chú người dùng tự đặt (vd "Kênh Pokemon phụ") — không bắt buộc.
    label: str = ""
    profile_dir: str
    username: str = ""
    screen_name: str = ""
    uid: str = ""
    avatar_url: str = ""
    # unknown = chưa kiểm tra lần nào; ok = đang đăng nhập; expired = hết
    # phiên/chưa đăng nhập; mismatch = profile đang đăng nhập 1 tài khoản KHÁC
    # tài khoản đã ghi nhận (chặn đăng); error = không kiểm tra được (mạng...).
    status: AccountStatus = "unknown"
    status_detail: Optional[str] = None
    checked_at: Optional[datetime] = None
    created_at: datetime


def empty_stages() -> dict[str, StageRecord]:
    return {name: StageRecord() for name in STAGE_ORDER}


def empty_episode_stages() -> dict[str, StageRecord]:
    return {name: StageRecord() for name in EPISODE_STAGE_ORDER}
