from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

STAGE_ORDER = ("ingest", "transcribe", "translate", "tts", "assemble")


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


class ProjectState(BaseModel):
    project_id: str
    title: str
    created_at: datetime
    original_filename: Optional[str] = None
    video_relpath: Optional[str] = None
    duration_sec: Optional[float] = None
    stages: dict[str, StageRecord] = Field(
        default_factory=lambda: {name: StageRecord() for name in STAGE_ORDER}
    )


def empty_stages() -> dict[str, StageRecord]:
    return {name: StageRecord() for name in STAGE_ORDER}
