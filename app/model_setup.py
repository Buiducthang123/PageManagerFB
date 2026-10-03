"""Nút "Cài đặt môi trường" ở trang Kiểm tra hệ thống: tải trước các model AI
(thay vì để stage tự tải ngầm ở lần chạy đầu — user chỉ thấy app "đứng") và
hiện rõ đang tải cái nào, tới đâu.

- Tải TUẦN TỰ từng model, mỗi model 1 tiến trình con riêng
  (stages/_model_fetch_worker.py) — tải lỗi/treo không kéo sập backend, Huỷ
  = kill tiến trình.
- Tiến độ = dung lượng thư mục đích tăng thêm so với lúc bắt đầu, chia cho
  dung lượng ước lượng (đo thật trên máy dev) — các thư viện tải (HF hub,
  modelscope) không có API báo tiến độ chung, đo thư mục thì dùng được cho mọi
  nguồn. Chưa xong thì chặn ở 99%.
- Model đang được tải NGẦM bởi 1 dự án (không qua nút này) cũng nhận ra được:
  thấy file tải dở (.incomplete của HF / thư mục tạm của modelscope) vừa được
  ghi trong 60s gần đây.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from . import config

WORKER = Path(__file__).parent / "stages" / "_model_fetch_worker.py"
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_MB = 1024 * 1024
POLL_S = 1.0
SPEED_WINDOW_S = 20.0  # HF ghi theo khối 10 MB — cửa sổ ngắn thì tốc độ nhảy 0 ↔ x liên tục
EXTERNAL_FRESH_S = 60.0

# Dung lượng tải về (MB), đo thật trên máy dev.
_WHISPER_MB = {"tiny": 75, "base": 145, "small": 484, "medium": 1460, "large-v2": 2948, "large-v3": 2948}


def _hf_hub() -> Path:
    return config.whisper_cache_dir() / "huggingface" / "hub"


def _ms_root() -> Path:
    return config.sensevoice_cache_dir() / "modelscope"


def _whisper_name() -> str:
    return (os.environ.get("WHISPER_MODEL") or config.WHISPER_MODEL).strip()


def _any(globs: list[tuple[Path, str]]) -> bool:
    return any(next(base.glob(pattern), None) is not None for base, pattern in globs)


@dataclass
class Component:
    id: str
    label: str  # cho user — tên theo chức năng, không nêu tên model
    purpose: str
    tech: Callable[[], str]  # tên model thật — chỉ admin/file chẩn đoán thấy
    size_mb: Callable[[], int]
    installed: Callable[[], bool]
    # Thư mục/file đích — đo dung lượng để ra tiến độ. Hàm vì có cái chỉ xuất hiện khi bắt đầu tải.
    watch: Callable[[], list[Path]]
    args: Callable[[], list[str]]


def _whisper_dirs() -> list[Path]:
    name = _whisper_name()
    cache = config.whisper_cache_dir()
    return [*cache.glob(f"models--*--faster-whisper-{name}"), *_hf_hub().glob(f"models--*--faster-whisper-{name}")]


# modelscope bản mới lưu `models/iic--SenseVoiceSmall/snapshots/...`, bản cũ `models/iic/SenseVoiceSmall`.
_MS_SENSEVOICE = ("models/iic--SenseVoiceSmall", "models/iic/SenseVoiceSmall")
_MS_VAD = ("models/iic--speech_fsmn_vad_zh-cn-16k-common-pytorch", "models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch")


def _ms_dirs() -> list[Path]:
    root = _ms_root()
    return [root / d for d in (*_MS_SENSEVOICE, *_MS_VAD)] + [root / "._____temp"]


def _ms_has(dirs: tuple[str, ...]) -> bool:
    return _any([(_ms_root() / d, "**/model.pt") for d in dirs])


COMPONENTS: list[Component] = [
    Component(
        id="model_whisper",
        label="Nhận diện giọng nói",
        purpose="Lấy lời thoại từ giọng nói trong video",
        tech=lambda: f"faster-whisper {_whisper_name()} · {config.whisper_cache_dir()}",
        size_mb=lambda: _WHISPER_MB.get(_whisper_name(), 1500),
        installed=lambda: any(next(d.glob("snapshots/*/model.bin"), None) for d in _whisper_dirs()),
        watch=_whisper_dirs,
        args=lambda: ["whisper", _whisper_name(), str(config.whisper_cache_dir())],
    ),
    Component(
        id="model_sensevoice",
        label="Nhận diện giọng nói (cách 2)",
        purpose="Cách nhận diện giọng nói thứ 2, nhanh hơn",
        tech=lambda: f"SenseVoiceSmall + fsmn-vad (modelscope) · {_ms_root()}",
        size_mb=lambda: 901,
        installed=lambda: _ms_has(_MS_SENSEVOICE) and _ms_has(_MS_VAD),
        watch=_ms_dirs,
        args=lambda: ["sensevoice"],
    ),
    Component(
        id="model_demucs",
        label="Tách nhạc nền",
        purpose="Giữ nhạc nền và tiếng động, bỏ giọng nói gốc",
        tech=lambda: f"demucs htdemucs · {_hf_hub()}",
        size_mb=lambda: 81,
        installed=lambda: _any([(_hf_hub(), "models--adefossez--HTDemucs/snapshots/*/*.safetensors"),
                                (config.models_dir(), "torch/hub/checkpoints/955717e8*")]),
        watch=lambda: [_hf_hub() / "models--adefossez--HTDemucs", config.models_dir() / "torch" / "hub"],
        args=lambda: ["demucs"],
    ),
    Component(
        id="model_vieneu",
        label="Giọng đọc chạy trên máy",
        purpose="Chỉ cần khi chọn giọng đọc chạy trên máy (không dùng giọng CapCut)",
        tech=lambda: f"VieNeu-TTS v3 Turbo (ONNX) + MOSS audio tokenizer · {_hf_hub()}",
        size_mb=lambda: 582,
        installed=lambda: _any([(_hf_hub(), "models--pnnbao-ump--VieNeu-TTS-v3-Turbo/snapshots/*/onnx_update/vieneu_backbone_shared.data")])
        and _any([(_hf_hub(), "models--OpenMOSS-Team--MOSS-Audio-Tokenizer-Nano-ONNX/snapshots/*/*.onnx")]),
        watch=lambda: [_hf_hub() / "models--pnnbao-ump--VieNeu-TTS-v3-Turbo",
                       _hf_hub() / "models--OpenMOSS-Team--MOSS-Audio-Tokenizer-Nano-ONNX"],
        args=lambda: ["vieneu"],
    ),
    Component(
        id="model_lama",
        label="Xoá chữ trên ảnh bìa",
        purpose="Xoá chữ cũ khi làm ảnh bìa tiếng Việt",
        tech=lambda: f"big-lama.pt · {config.lama_model_path()}",
        size_mb=lambda: 197,
        installed=lambda: config.lama_model_path().exists(),
        watch=lambda: [config.lama_model_path().with_suffix(".pt.part"), config.lama_model_path()],
        args=lambda: ["lama", str(config.lama_model_path())],
    ),
]
_BY_ID = {c.id: c for c in COMPONENTS}


def _size(paths: list[Path]) -> int:
    total = 0
    stack = list(paths)
    while stack:
        p = stack.pop()
        try:
            if p.is_file():
                total += p.stat().st_size
            elif p.is_dir():
                stack.extend(p.iterdir())
        except OSError:
            continue
    return total


def _fresh_partial(paths: list[Path]) -> bool:
    """Có file tải dở vừa được ghi gần đây → đang có ai đó (1 dự án) tải model này."""
    now = time.time()
    stack = list(paths)
    while stack:
        p = stack.pop()
        try:
            if p.is_dir():
                if p.name == "._____temp":
                    if any(now - f.stat().st_mtime < EXTERNAL_FRESH_S for f in p.rglob("*") if f.is_file()):
                        return True
                    continue
                stack.extend(p.iterdir())
            elif p.suffix in (".incomplete", ".part") and now - p.stat().st_mtime < EXTERNAL_FRESH_S:
                return True
        except OSError:
            continue
    return False


@dataclass
class _Task:
    status: str = "idle"  # idle | queued | downloading | done | error | cancelled
    downloaded: int = 0
    total: int = 0
    speed: float = 0.0  # byte/s
    error: str = ""
    error_tech: str = ""
    ended_at: float = 0.0  # time.time() lúc lượt tải của nút này dừng
    samples: list[tuple[float, int]] = field(default_factory=list)


_lock = threading.Lock()
_tasks: dict[str, _Task] = {c.id: _Task() for c in COMPONENTS}
_queue: list[str] = []
_proc: Optional[subprocess.Popen] = None
_runner: Optional[threading.Thread] = None
_cancel = threading.Event()


def _worker_env() -> dict:
    env = dict(os.environ)  # HF_HOME/MODELSCOPE_CACHE/TORCH_HOME đã đặt ở config.apply_whisper_cache_env
    env["PYTHONIOENCODING"] = "utf-8"
    env["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    # Xet (giao thức tải mới của HF) chỉ ghi ra file lúc xong → không đo được
    # tiến độ; HTTP thường ghi dần vào *.incomplete và tự tải tiếp khi rớt mạng.
    env["HF_HUB_DISABLE_XET"] = "1"
    env["HF_HUB_DOWNLOAD_TIMEOUT"] = "60"  # mạng chậm: mặc định 10s hay timeout giữa chừng
    env["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    return env


def _run_one(comp: Component) -> None:
    global _proc
    task = _tasks[comp.id]
    start_size = _size(comp.watch())
    with _lock:
        task.status, task.error, task.downloaded, task.speed, task.samples = "downloading", "", 0, 0.0, []
        task.total = comp.size_mb() * _MB
    logger.info("model_setup: bắt đầu tải {}", comp.id)
    log_path = config.temp_dir() / f"model_fetch_{comp.id}.log"
    with log_path.open("w", encoding="utf-8", errors="replace") as out:
        proc = subprocess.Popen(
            [sys.executable, "-X", "faulthandler", str(WORKER), *comp.args()],  # crash C → stack vào log tải
            cwd=str(WORKER.parent), env=_worker_env(), stdout=out, stderr=subprocess.STDOUT,
            creationflags=_CREATE_NO_WINDOW,
        )
        _proc = proc
        while proc.poll() is None:
            if _cancel.is_set():
                proc.kill()
                break
            now = time.monotonic()
            grown = max(0, _size(comp.watch()) - start_size)
            with _lock:
                task.downloaded = grown
                task.samples = [s for s in task.samples if now - s[0] <= SPEED_WINDOW_S] + [(now, grown)]
                first = task.samples[0]
                task.speed = (grown - first[1]) / (now - first[0]) if now > first[0] else 0.0
            time.sleep(POLL_S)
        proc.wait()
        _proc = None
    with _lock:
        task.ended_at = time.time()
        if _cancel.is_set():
            task.status = "cancelled"
        elif proc.returncode == 0 and comp.installed():
            task.status, task.downloaded = "done", max(task.downloaded, task.total)
        else:
            task.status = "error"
            task.error, task.error_tech = _tail_error(log_path)
            task.error_tech = task.error_tech or f"tiến trình tải thoát với mã {proc.returncode}"
            if (proc.returncode or 0) & 0xFFFFFFFF == 0xC0000005:
                # Access violation khi nạp torch/onnxruntime — gần như luôn do Visual C++
                # Runtime của máy quá cũ (xem app/vcrt.py). Mở lại app để app chép bản mới.
                task.error = ("Lỗi thư viện hệ thống (Microsoft Visual C++) — tắt hẳn rồi mở lại app, "
                              "vẫn lỗi thì cài https://aka.ms/vs/17/release/vc_redist.x64.exe")
                task.error_tech = f"access violation (0xC0000005) · {task.error_tech}"
    if task.status == "error":
        logger.warning("model_setup: tải {} lỗi: {}", comp.id, task.error_tech)
    else:
        logger.info("model_setup: {} → {}", comp.id, task.status)


def _tail_error(log_path: Path) -> tuple[str, str]:
    """(lời nhắn cho user, dòng lỗi gốc cho admin)."""
    try:
        lines = [l.strip() for l in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    except OSError:
        lines = []
    text = lines[-1][-300:] if lines else ""
    low = text.lower()
    if any(k in low for k in ("connection", "timed out", "timeout", "resolve", "getaddrinfo", "max retries")):
        return "Lỗi mạng — kiểm tra Internet rồi bấm Thử lại", text
    if "no space" in low or "errno 28" in low:
        return "Ổ đĩa hết chỗ — dọn bớt hoặc đổi thư mục dữ liệu AI ở Cài đặt", text
    return "Tải không thành công — bấm Thử lại, vẫn lỗi thì gửi log cho admin", text


def _run_queue() -> None:
    global _runner
    while True:
        with _lock:
            if not _queue or _cancel.is_set():
                for cid in _queue:
                    _tasks[cid].status = "cancelled" if _cancel.is_set() else "idle"
                _queue.clear()
                _runner = None
                return
            cid = _queue.pop(0)
        try:
            _run_one(_BY_ID[cid])
        except Exception as err:  # noqa: BLE001 — 1 model lỗi không được chặn các model sau
            logger.exception("model_setup: lỗi không mong đợi khi tải {}", cid)
            with _lock:
                _tasks[cid].status, _tasks[cid].error = "error", "Tải không thành công — bấm Thử lại"
                _tasks[cid].error_tech = str(err)


def start(ids: list[str]) -> list[str]:
    """Xếp các model (chưa có) vào hàng đợi tải. Trả về id thực sự được xếp."""
    global _runner
    added: list[str] = []
    with _lock:
        if _runner is None:
            _cancel.clear()
        for cid in ids:
            comp = _BY_ID.get(cid)
            if comp is None or cid in _queue or _tasks[cid].status == "downloading" or comp.installed():
                continue
            _queue.append(cid)
            _tasks[cid].status, _tasks[cid].error = "queued", ""
            added.append(cid)
        if added and _runner is None:
            _runner = threading.Thread(target=_run_queue, name="model-setup", daemon=True)
            _runner.start()
    return added


def cancel() -> None:
    _cancel.set()
    proc = _proc
    if proc is not None and proc.poll() is None:
        proc.kill()


def is_busy() -> bool:
    return _runner is not None


def status() -> dict:
    items = []
    for comp in COMPONENTS:
        task = _tasks[comp.id]
        installed = comp.installed() if task.status != "downloading" else False
        # File tải dở còn "mới" ngay sau khi chính nút này huỷ/lỗi — không phải dự án nào đang tải.
        own_recent = time.time() - task.ended_at < EXTERNAL_FRESH_S
        external = (not installed and not own_recent and task.status not in ("downloading", "queued")
                    and _fresh_partial(comp.watch()))
        total = task.total or comp.size_mb() * _MB
        items.append({
            "id": comp.id,
            "label": comp.label,
            "purpose": comp.purpose,
            "tech": comp.tech(),
            "installed": installed,
            "status": "done" if installed and task.status != "downloading" else task.status,
            "external_download": external,
            "external_mb": round(_size(comp.watch()) / _MB) if external else 0,
            "downloaded_mb": round(min(task.downloaded, total * 0.99 if task.status == "downloading" else total) / _MB, 1),
            "total_mb": round(total / _MB),
            "speed_mbps": round(task.speed / _MB, 2),
            "error": task.error,
            "error_tech": task.error_tech,
        })
    free_gb = shutil.disk_usage(config.models_dir()).free / 1024**3
    return {"busy": is_busy(), "items": items, "models_dir": str(config.models_dir()), "free_gb": round(free_gb, 1)}


# ------------------------------------------------------------------ Chuyển cache cũ ở ~/.cache
# Bản cũ (và lúc stage tự đổi HF_HOME giữa chừng) để lọt model sang
# C:\Users\<user>\.cache. Chuyển về thư mục model của app để khỏi tải lại và
# giải phóng ổ C. CHỈ đụng các repo app này dùng — cache HF/modelscope của
# chương trình khác trên máy giữ nguyên.

_LEGACY_HF_REPOS = (
    "models--adefossez--HTDemucs",
    "models--pnnbao-ump--VieNeu-TTS-v3-Turbo",
    "models--OpenMOSS-Team--MOSS-Audio-Tokenizer-Nano-ONNX",
)


def _legacy_moves() -> list[tuple[Path, Path]]:
    home = Path.home() / ".cache"
    hf_old = home / "huggingface" / "hub"
    moves: list[tuple[Path, Path]] = []
    if hf_old.is_dir():
        for d in hf_old.glob("models--*--faster-whisper-*"):
            moves.append((d, config.whisper_cache_dir() / d.name))
        for name in _LEGACY_HF_REPOS:
            moves.append((hf_old / name, _hf_hub() / name))
    ms_old = home / "modelscope" / "models"
    for name in ("iic--SenseVoiceSmall", "iic--speech_fsmn_vad_zh-cn-16k-common-pytorch"):
        moves.append((ms_old / name, _ms_root() / "models" / name))
    return [(src, dst) for src, dst in moves if src.is_dir()]


def migrate_legacy_cache() -> None:
    for src, dst in _legacy_moves():
        if dst.exists():
            # Bên app đã có bản riêng → bản ở ~/.cache là bản trùng, xoá cho nhẹ ổ C.
            if next(dst.glob("snapshots/*"), None) is not None:
                shutil.rmtree(src, ignore_errors=True)
                logger.info("model_setup: xoá cache trùng {} (đã có {})", src, dst)
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            logger.info("model_setup: chuyển {} → {}", src, dst)
        except OSError as err:
            logger.warning("model_setup: không chuyển được {} ({})", src, err)
    old_lama = Path.home() / ".cache" / "remove_hardsub" / "big-lama.pt"
    if old_lama.exists() and not config.lama_model_path().exists():
        try:
            shutil.copy2(old_lama, config.lama_model_path())  # copy: môi trường "Làm sạch video" cũ còn dùng file này
            logger.info("model_setup: copy {} → {}", old_lama, config.lama_model_path())
        except OSError as err:
            logger.warning("model_setup: không copy được big-lama.pt ({})", err)


def start_migration() -> None:
    def run() -> None:
        try:
            migrate_legacy_cache()
        except Exception:  # noqa: BLE001
            logger.exception("model_setup: chuyển cache cũ lỗi")

    threading.Thread(target=run, name="model-cache-migrate", daemon=True).start()
