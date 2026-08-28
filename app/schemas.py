from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .models import ProjectState


class CreateProjectRequest(BaseModel):
    title: str


class RenameProjectRequest(BaseModel):
    title: str


class StartTranscribeRequest(BaseModel):
    engine: str = "whisper"  # "whisper" | "sensevoice"


class StartTTSRequest(BaseModel):
    voice: str = ""  # rỗng = dùng mặc định server-side
    retry_failed_only: bool = False  # True = chỉ tạo lại câu lỗi/rỗng lần trước


class ProjectSummary(BaseModel):
    project_id: str
    title: str
    created_at: str
    current_stage: Optional[str]


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


class LogEntry(BaseModel):
    ts: str
    stage: str
    message: str


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
