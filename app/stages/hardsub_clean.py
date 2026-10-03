"""Chạy worker xoá phụ đề cứng (`hardsub_worker.py`) trong subprocess bằng
Python của venv GPU riêng (config.HARDSUB_PYTHON) — xem docstring worker vì
sao không chạy chung process/venv với server.

Worker in tiến độ dạng `@@PROGRESS {...}` và lỗi dạng `@@ERROR ...` ra stdout;
ở đây đọc từng dòng, quy ra % tổng cho job, ghi toàn bộ output vào log.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from .. import config, jobs as jobs_mod

WORKER_PATH = Path(__file__).with_name("hardsub_worker.py")

# Tỉ trọng mỗi pha trên thanh tiến độ tổng, theo chế độ vá. Đo trên test30.mp4 (30s):
# OCR thưa ~22s, OCR dày ~74s (dao động mạnh theo lượng chữ đổi); render nhanh ~110s,
# render STTN ~475s.
_PHASES = {
    "fast": {
        "ocr": (0, 20, "Quét chữ"),
        "dense": (20, 55, "Quét kỹ đoạn chữ đổi"),
        "shots": (55, 58, "Phân tích cảnh"),
        "render": (58, 100, "Xoá chữ + encode"),
    },
    "sttn": {
        "ocr": (0, 8, "Quét chữ"),
        "dense": (8, 22, "Quét kỹ đoạn chữ đổi"),
        "shots": (22, 24, "Chia cảnh"),
        "render": (24, 100, "Xoá chữ (vá mượt) + encode"),
    },
}
ENGINES = tuple(_PHASES)


class HardsubError(Exception):
    pass


def worker_python() -> str:
    return config.HARDSUB_PYTHON or sys.executable


_env_cache: Optional[tuple[float, dict]] = None
_ENV_PROBE = (
    "import json, cv2, numpy, torch, rapidocr_onnxruntime; "
    "print(json.dumps({'cuda': bool(torch.cuda.is_available())}))"
)


def environment_status(force: bool = False) -> dict:
    """Máy này chạy được "Làm sạch video" không: Python của worker phải có
    torch + opencv + rapidocr_onnxruntime. Bản đóng gói cho user chưa kèm môi
    trường này (cần Runtime GPU riêng) → báo rõ thay vì chạy rồi lỗi giữa chừng.
    Cache 10 phút (thử import torch mất vài giây)."""
    global _env_cache
    now = time.monotonic()
    if not force and _env_cache and now - _env_cache[0] < 600:
        return _env_cache[1]
    python = worker_python()
    status: dict = {"ok": False, "cuda": False, "message": ""}
    if config.HARDSUB_PYTHON and not Path(python).exists():
        status["message"] = f"Không thấy Python của môi trường làm sạch video: {python}"
    else:
        try:
            out = subprocess.run(
                [python, "-c", _ENV_PROBE], capture_output=True, text=True, timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if out.returncode == 0:
                info = json.loads((out.stdout or "{}").strip().splitlines()[-1])
                status = {"ok": True, "cuda": bool(info.get("cuda")), "message": ""}
                if not status["cuda"]:
                    status["message"] = "Không có GPU NVIDIA dùng được — chạy bằng CPU sẽ rất chậm"
            else:
                status["message"] = "Máy này chưa cài thành phần cho \"Làm sạch video\" (cần torch + rapidocr_onnxruntime, thường chỉ có trên máy có card NVIDIA)"
        except (OSError, subprocess.SubprocessError, ValueError, IndexError) as err:
            status["message"] = f"Không kiểm tra được môi trường làm sạch video: {err}"
    _env_cache = (now, status)
    return status


def clean_video(
    input_path: Path,
    output_path: Path,
    *,
    icon_pad: float = 0.0,
    all_text: bool = False,
    nvenc: bool = False,
    engine: str = "sttn",
    log_path: Optional[Path] = None,
    on_progress: Optional[Callable[[int, str], None]] = None,
    on_warning: Optional[Callable[[str], None]] = None,
    job: Optional[jobs_mod.JobState] = None,
) -> Path:
    """Xoá chữ Hán in sẵn khỏi `input_path`, ghi ra `output_path`.
    `on_progress(percent, label)`; `on_warning(msg)` cho lỗi không chặn (vd
    NVENC không dùng được -> worker tự lùi về encode CPU); `job` cho phép Dừng
    kill thẳng worker."""
    if not input_path.exists():
        raise HardsubError(f"Không thấy video: {input_path}")
    python = worker_python()
    if config.HARDSUB_PYTHON and not Path(python).exists():
        raise HardsubError(f"HARDSUB_PYTHON trỏ tới file không tồn tại: {python}")

    # Lưu kết quả OCR cạnh video: "Chạy lại" (vd đổi tuỳ chọn xoá icon) bỏ qua được bước OCR.
    # Worker tự bỏ cache nếu video/tham số OCR khác đi.
    cmd = [python, "-u", str(WORKER_PATH), str(input_path), "-o", str(output_path),
           "--mask-cache", str(output_path.with_name("ocr_masks.pkl"))]
    if icon_pad > 0:
        cmd += ["--icon-pad", f"{icon_pad:g}"]
    if all_text:
        cmd.append("--all-text")
    if nvenc:
        cmd.append("--nvenc")
    if engine not in ENGINES:
        raise HardsubError(f"Chế độ vá không hợp lệ: {engine}")
    cmd += ["--engine", engine]
    # Worker mặc định CUDA (tự lùi CPU nếu không có); chỉ ép CPU khi người dùng
    # chọn "CPU" ở Cài đặt → Thiết bị xử lý AI.
    if config.ai_device_mode() == "cpu":
        cmd += ["--device", "cpu"]
    phases = _PHASES[engine]

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        cmd,
        cwd=str(output_path.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if job is not None:
        job.process = proc

    tail: list[str] = []
    error_msg: Optional[str] = None
    log = log_path.open("w", encoding="utf-8") if log_path else None
    try:
        assert proc.stdout is not None
        # request_cancel() kill worker từ thread khác -> stdout EOF -> vòng này
        # tự thoát (ffmpeg con của worker cũng tự thoát khi stdin bị đóng).
        for line in proc.stdout:
            line = line.rstrip("\n")
            if log:
                log.write(line + "\n")
                log.flush()
            if line.startswith("@@PROGRESS "):
                try:
                    p = json.loads(line[len("@@PROGRESS "):])
                except json.JSONDecodeError:
                    continue
                lo, hi, label = phases.get(p.get("phase"), (0, 100, p.get("phase", "")))
                total = max(1, int(p.get("total") or 1))
                done = min(int(p.get("done") or 0), total)
                if on_progress:
                    on_progress(int(lo + (hi - lo) * done / total), f"{label} {done}/{total}")
                continue
            if line.startswith("@@WARN "):
                if on_warning:
                    on_warning(line[len("@@WARN "):])
                continue
            if line.startswith("@@ERROR "):
                error_msg = line[len("@@ERROR "):]
                continue
            tail.append(line)
            del tail[:-40]
        proc.wait()
    finally:
        if log:
            log.close()
        if job is not None:
            job.process = None

    if job is not None and job.cancel_event.is_set():
        raise jobs_mod.JobCancelled("Đã dừng theo yêu cầu người dùng")
    if proc.returncode != 0 or not output_path.exists():
        if error_msg:
            raise HardsubError(error_msg)
        detail = "\n".join(tail[-15:]).strip()
        if "No module named" in detail:
            detail = (
                "Python chạy worker thiếu thư viện (cần torch CUDA, rapidocr_onnxruntime, opencv) — "
                f"đặt HARDSUB_PYTHON trong .env trỏ tới venv GPU. Chi tiết: {detail[-500:]}"
            )
        raise HardsubError(f"Worker lỗi (exit={proc.returncode}): {detail[-1500:]}")
    if on_progress:
        on_progress(100, "Xong")
    return output_path
