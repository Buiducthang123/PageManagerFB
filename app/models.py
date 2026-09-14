from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

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
    auto_engine: str = "whisper"
    auto_tts_engine: str = "capcut"
    auto_voice: str = ""
    auto_audio_mode: str = "separated"
    auto_min_video_speed: float = 0.85
    # Dự án đơn đã được cắt thành nhiều đoạn (mỗi đoạn 1 draft CapCut riêng,
    # chạy TUẦN TỰ) — khi True, stages ở cấp project (transcribe/translate/
    # tts/assemble) không còn dùng nữa, chỉ episodes mới có ý nghĩa.
    split_mode: bool = False


def empty_stages() -> dict[str, StageRecord]:
    return {name: StageRecord() for name in STAGE_ORDER}


def empty_episode_stages() -> dict[str, StageRecord]:
    return {name: StageRecord() for name in EPISODE_STAGE_ORDER}
