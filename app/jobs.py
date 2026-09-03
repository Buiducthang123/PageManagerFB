from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


class JobCancelled(Exception):
    """Raise từ trong on_progress khi job.cancel_event đã được set — cho phép
    dừng job giữa chừng ở lần callback kế tiếp thay vì phải chờ tự xong."""


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
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    # Tiến trình con đang chạy (vd demucs) — request_cancel() kill thẳng cái
    # này để dừng ngay, không phải chờ nó tự check cancel_event giữa các bước.
    process: Optional[subprocess.Popen] = field(default=None, repr=False)

    def is_alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def raise_if_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise JobCancelled("Đã dừng theo yêu cầu người dùng")


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


def request_cancel(key: str) -> bool:
    """Đánh dấu job cần dừng + kill ngay tiến trình con (nếu có, vd demucs)
    thay vì chỉ chờ cờ được check ở lần on_progress kế tiếp. Trả về False nếu
    không có job nào đang chạy với key này."""
    job = _jobs.get(key)
    if job is None or not job.is_alive():
        return False
    job.cancel_event.set()
    proc = job.process
    if proc is not None and proc.poll() is None:
        proc.kill()
    return True
