from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class JobItem:
    id: str
    label: str
    status: str = "pending"
    error: Optional[str] = None


@dataclass
class JobState:
    total: int
    status: str = "running"
    done_count: int = 0
    current_label: Optional[str] = None
    items: list[JobItem] = field(default_factory=list)
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    thread: Optional[threading.Thread] = field(default=None, repr=False)

    def is_alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


_jobs: dict[str, JobState] = {}
_jobs_guard = threading.Lock()


def start_job(key: str, total: int, target: Callable[[JobState], None]) -> Optional[JobState]:
    with _jobs_guard:
        existing = _jobs.get(key)
        if existing is not None and existing.is_alive():
            return None
        job = JobState(total=total)
        _jobs[key] = job

    thread = threading.Thread(target=target, args=(job,), daemon=True)
    job.thread = thread
    thread.start()
    return job


def get_job(key: str) -> Optional[JobState]:
    return _jobs.get(key)


def is_job_running(key: str) -> bool:
    job = _jobs.get(key)
    return job is not None and job.is_alive()
