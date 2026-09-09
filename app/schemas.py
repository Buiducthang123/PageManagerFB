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


class UpdateAutoPipelineRequest(BaseModel):
    enabled: bool
    engine: str = "whisper"
    voice: str = ""
    audio_mode: AudioMode = "separated"
    min_video_speed: float = Field(0.85, ge=0.7, le=1.0)


class SplitProjectRequest(BaseModel):
    split_points_s: list[float]  # N-1 mốc cắt (giây, tăng dần) cho N đoạn


class StartTranscribeRequest(BaseModel):
    engine: str = "whisper"  # "whisper" | "sensevoice"


class StartTTSRequest(BaseModel):
    voice: str = ""  # rỗng = dùng mặc định server-side
    retry_failed_only: bool = False  # True = chỉ tạo lại câu lỗi/rỗng lần trước


class UpdateCueRequest(BaseModel):
    text_vi: str


class TTSCueRequest(BaseModel):
    voice: str = ""  # rỗng = dùng mặc định server-side


class StartAssembleRequest(BaseModel):
    # "separated" = tách nhạc nền/SFX khỏi thoại gốc bằng demucs (mặc định)
    # "original" = giữ nguyên âm thanh gốc (thoại + nhạc nền), không tách, không tắt
    # "mute" = tắt hẳn âm thanh gốc, bỏ qua bước tách
    audio_mode: AudioMode = "separated"
    # Tốc độ video tối thiểu khi cần chậm lại để nhường thêm thời gian cho
    # giọng đọc TTS (0.7-1.0, mặc định 0.85 = chậm tối đa 15%) — xem
    # MIN_VIDEO_SPEED trong app/stages/assemble.py.
    min_video_speed: float = Field(0.85, ge=0.7, le=1.0)


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
    gemini_models: list[ModelOption] = []
    whisper_models: list[ModelOption] = []
    whisper_languages: list[ModelOption] = []
    tts_voices: list[ModelOption] = []
    whisper_cache_dir: str = ""
    capcut_drafts_dir: str = ""


class UpdateAppSettingsRequest(BaseModel):
    workspace_dir: Optional[str] = None
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    whisper_model: Optional[str] = None
    whisper_device: Optional[str] = None
    whisper_language: Optional[str] = None


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
