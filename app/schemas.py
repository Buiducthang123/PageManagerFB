from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from .models import Episode, ProjectState, ProjectType

AudioMode = Literal["separated", "original", "mute"]


class CreateProjectRequest(BaseModel):
    title: str
    project_type: ProjectType = "single"


class CreateEpisodeRequest(BaseModel):
    title: str = ""
    insert_after_episode_id: Optional[str] = None  # None = thêm vào cuối


class ReorderEpisodesRequest(BaseModel):
    episode_ids: list[str]  # thứ tự mới, đầy đủ mọi episode_id hiện có


class RenameProjectRequest(BaseModel):
    title: str


class IngestUrlRequest(BaseModel):
    url: str


class CreateDownloadRequest(BaseModel):
    mode: str  # "single" | "profile" | "search" | "info"
    title: str = ""
    url: str = ""  # single & profile
    modes: list[str] = []  # profile: post/like/mix/music
    number: dict[str, int] = {}  # profile: giới hạn số lượng mỗi mode, 0 = không giới hạn
    keyword: str = ""  # search
    search_max: int = 20  # search
    dest_dir: str = ""  # tuỳ chọn — trống thì lưu mặc định trong workspace/downloads/<id>/files


class CreateSocialProjectRequest(BaseModel):
    title: str
    douyin_profile_url: str
    # Tối đa 3 bài/ngày/tài khoản — vượt mức này dễ bị nền tảng gắn cờ
    # hành vi bất thường, nhất là tài khoản mới.
    posts_per_day: int = Field(1, ge=1, le=3)


class CrawlSocialRequest(BaseModel):
    limit: Optional[int] = None  # số video MỚI NHẤT quét — None/0 = quét hết toàn bộ trang


class UpdateSocialProjectRequest(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None  # "active" | "paused"
    posts_per_day: Optional[int] = Field(None, ge=1, le=3)
    engine: Optional[str] = None
    tts_engine: Optional[str] = None
    voice: Optional[str] = None
    audio_mode: Optional[str] = None
    original_audio_volume_db: Optional[float] = None
    music_volume_db: Optional[float] = None
    subtitle_font_size: Optional[int] = None
    min_video_speed: Optional[float] = None
    use_viesnap_fallback: Optional[bool] = None
    crawl_via_browser: Optional[bool] = None


class UpdateAutoPipelineRequest(BaseModel):
    enabled: bool
    engine: str = "ocr"
    tts_engine: str = "capcut"  # "capcut" | "vieneu"
    voice: str = ""
    audio_mode: AudioMode = "original"
    # dB, chỉ áp dụng khi audio_mode="original" — xem export_direct.ORIGINAL_AUDIO_VOLUME_DB
    original_audio_volume_db: float = -13.0
    min_video_speed: float = Field(0.85, ge=0.7, le=1.0)


class SplitProjectRequest(BaseModel):
    split_points_s: list[float]  # N-1 mốc cắt (giây, tăng dần) cho N đoạn


class StartTranscribeRequest(BaseModel):
    engine: str = "ocr"  # "whisper" | "sensevoice" | "ocr"
    # Chỉ engine "ocr" dùng — vùng khoanh tay trên preview video, dạng phân số
    # [x, y, w, h] (0-1). None/rỗng = engine "ocr" tự dùng mặc định 25% đáy
    # khung hình (xem config.OCR_CROP_BOTTOM_FRACTION).
    crop_region: Optional[list[float]] = None


class StartTTSRequest(BaseModel):
    voice: str = ""  # rỗng = dùng mặc định server-side
    engine: str = "capcut"  # "capcut" | "vieneu" — bỏ qua khi retry_failed_only=True
    retry_failed_only: bool = False  # True = chỉ tạo lại câu lỗi/rỗng lần trước


class UpdateCueRequest(BaseModel):
    text_vi: str


class TTSCueRequest(BaseModel):
    voice: str = ""  # rỗng = dùng mặc định server-side


class StartAssembleRequest(BaseModel):
    # "separated" = tách nhạc nền/SFX khỏi thoại gốc bằng demucs (mặc định)
    # "original" = giữ nguyên âm thanh gốc (thoại + nhạc nền), không tách, không tắt
    # "mute" = tắt hẳn âm thanh gốc, bỏ qua bước tách
    audio_mode: AudioMode = "original"
    original_audio_volume_db: float = -13.0
    # Tốc độ video tối thiểu khi cần chậm lại để nhường thêm thời gian cho
    # giọng đọc TTS (0.7-1.0, mặc định 0.85 = chậm tối đa 15%) — xem
    # MIN_VIDEO_SPEED trong app/stages/assemble.py.
    min_video_speed: float = Field(0.85, ge=0.7, le=1.0)


class StartExportRequest(BaseModel):
    audio_mode: AudioMode = "original"
    original_audio_volume_db: float = -13.0
    # Cỡ chữ phụ đề mới — tính theo hệ toạ độ kịch bản 288px libass (KHÔNG
    # phải px thật, xem export_direct.DEFAULT_SUBTITLE_FONT_SIZE), 6 = mặc
    # định người dùng chốt.
    subtitle_font_size: int = Field(6, ge=1, le=100)
    min_video_speed: float = Field(0.85, ge=0.7, le=1.0)


class UpdateExportBlurRegionRequest(BaseModel):
    region: Optional[list[float]] = None  # [x,y,w,h] phân số 0-1, None = bỏ khoanh vùng


class UpdateOcrCropRegionRequest(BaseModel):
    region: Optional[list[float]] = None  # [x,y,w,h] phân số 0-1, None = bỏ khoanh vùng


class ProjectSummary(BaseModel):
    project_id: str
    title: str
    created_at: str
    current_stage: Optional[str]
    project_type: ProjectType = "single"


class SrtCue(BaseModel):
    id: int
    start: str
    end: str
    text: str
    text_vi: Optional[str] = None


class JobItemResponse(BaseModel):
    id: str
    label: str
    status: str
    error: Optional[str] = None


class JobStatusResponse(BaseModel):
    registered: bool
    orphaned: bool = False
    status: Optional[str] = None
    total: int = 0
    done_count: int = 0
    current_label: Optional[str] = None
    items: list[JobItemResponse] = []
    error: Optional[str] = None
    started_at: Optional[float] = None


class ModelOption(BaseModel):
    id: str
    label: str


class AppSettingsResponse(BaseModel):
    workspace_dir: str
    gemini_api_key_masked: str = ""
    gemini_model: str = ""
    whisper_model: str = ""
    whisper_device: str = ""
    whisper_language: str = ""
    translate_pace: str = ""
    gemini_models: list[ModelOption] = []
    whisper_models: list[ModelOption] = []
    whisper_languages: list[ModelOption] = []
    translate_paces: list[ModelOption] = []
    tts_voices: list[ModelOption] = []
    tts_voices_vieneu: list[ModelOption] = []
    whisper_cache_dir: str = ""
    capcut_drafts_dir: str = ""


class UpdateAppSettingsRequest(BaseModel):
    workspace_dir: Optional[str] = None
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    whisper_model: Optional[str] = None
    whisper_device: Optional[str] = None
    whisper_language: Optional[str] = None
    translate_pace: Optional[str] = None


class TTSManifestEntryResponse(BaseModel):
    id: int
    start: str
    end: str
    path: Optional[str] = None
    duration_ms: int = 0
    error: Optional[str] = None
    text: Optional[str] = None


class LogEntry(BaseModel):
    ts: str
    stage: str
    message: str


class EpisodeDetail(BaseModel):
    episode: Episode
    current_stage: Optional[str]
    video_url: Optional[str] = None
    cues: list[SrtCue] = []
    sub_zh: Optional[str] = None
    sub_vi: Optional[str] = None
    tts_manifest: list[dict] = []


class ProjectDetailResponse(BaseModel):
    project: ProjectState
    current_stage: Optional[str]
    video_url: Optional[str] = None
    cues: list[SrtCue] = []
    entity_dict: dict[str, str] = {}
    sub_zh: Optional[str] = None
    sub_vi: Optional[str] = None
    tts_manifest: list[dict] = []
    logs: list[LogEntry] = []
    episodes: list[EpisodeDetail] = []
