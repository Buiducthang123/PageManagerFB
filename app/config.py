from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Đặt SỚM nhất có thể (trước khi torch/numpy/mkl được import ở bất kỳ đâu
# trong app) — từng gặp Demucs (CPU) treo cứng toàn bộ process, 0% CPU, không
# phản hồi API nào luôn, nghi do xung đột nhiều bản OpenMP runtime nạp trùng
# (lỗi kinh điển của torch+numpy trên Windows). setdefault để không đè giá trị
# người dùng đã tự set trong .env/hệ thống.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip().strip("'\"") or DEFAULT_GEMINI_MODEL

RETIRED_GEMINI_MODELS = {
    "gemini-2.0-flash": "gemini-3.6-flash",
    "gemini-2.0-flash-001": "gemini-3.6-flash",
    "gemini-2.0-flash-lite": "gemini-3.5-flash-lite",
    "gemini-2.0-flash-exp": "gemini-3.6-flash",
}


def resolve_gemini_model(name: str | None = None) -> str:
    raw = (name or os.environ.get("GEMINI_MODEL") or GEMINI_MODEL or DEFAULT_GEMINI_MODEL).strip().strip("'\"")
    if raw.startswith("models/"):
        raw = raw[len("models/") :]
    return RETIRED_GEMINI_MODELS.get(raw, raw) or DEFAULT_GEMINI_MODEL

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "medium").strip() or "medium"
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "auto").strip() or "auto"
WHISPER_LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "zh").strip() or "zh"

TRANSLATE_BLOCK_SIZE = 10
GEMINI_BLOCK_SLEEP_S = 4.0
GEMINI_TEMPERATURE = 0.1

# Tốc độ đọc mục tiêu/ngưỡng nén lại (ký tự việt/giây) khi dịch — video gốc
# càng thoại dồn dập (gần như không có khoảng nghỉ giữa các câu) thì càng dễ
# phải đánh đổi giữa "đọc tự nhiên" và "dịch đủ ý". 3 mức người dùng tự chọn
# ở Settings — "natural" giữ tốc độ đọc chuẩn, chấp nhận cắt bớt ý ở câu quá
# dồn; "full_meaning" ưu tiên dịch đủ nghĩa hơn, chấp nhận đọc nhanh hơn/để
# timing trôi nhẹ (đã có cơ chế chậm video + tăng tốc giọng + đẩy lùi câu sau
# ở assemble.py xử lý phần dôi ra).
TRANSLATE_PACE_PROFILES: dict[str, tuple[float, float]] = {
    "natural": (15.5, 18.0),
    "balanced": (18.0, 23.0),
    "full_meaning": (20.0, 22.0),
}
DEFAULT_TRANSLATE_PACE = "full_meaning"
TRANSLATE_PACE = os.environ.get("TRANSLATE_PACE", DEFAULT_TRANSLATE_PACE).strip() or DEFAULT_TRANSLATE_PACE


def translate_cps(pace: str | None = None) -> tuple[float, float]:
    mode = (pace or os.environ.get("TRANSLATE_PACE") or TRANSLATE_PACE).strip()
    return TRANSLATE_PACE_PROFILES.get(mode, TRANSLATE_PACE_PROFILES[DEFAULT_TRANSLATE_PACE])


SENSEVOICE_DEVICE = os.environ.get("SENSEVOICE_DEVICE", "cpu").strip() or "cpu"

# Mặc định CPU — Whisper (transcribe) đã chiếm VRAM và model vẫn giữ resident
# suốt đời process (không unload), TTS chạy SAU trong cùng pipeline nên cộng
# dồn VRAM nếu cũng chạy CUDA. Máy dev 4GB VRAM khá sát — an toàn hơn để CPU,
# người dùng có nhiều VRAM hơn có thể tự set VIENEU_DEVICE=cuda.
VIENEU_DEVICE = os.environ.get("VIENEU_DEVICE", "cpu").strip() or "cpu"

try:
    TTS_CONCURRENCY = max(1, int(os.environ.get("TTS_CONCURRENCY", "3").strip() or "3"))
except ValueError:
    TTS_CONCURRENCY = 3


def sensevoice_cache_dir() -> Path:
    custom = os.environ.get("SENSEVOICE_CACHE_DIR", "").strip()
    if custom:
        path = Path(custom).expanduser().resolve()
    else:
        workspace = os.environ.get("WORKSPACE_DIR", "").strip()
        base = Path(workspace).expanduser().resolve() if workspace else Path(__file__).resolve().parent.parent / "workspace"
        path = base / "models" / "sensevoice"
    path.mkdir(parents=True, exist_ok=True)
    return path


def whisper_cache_dir() -> Path:
    custom = os.environ.get("WHISPER_CACHE_DIR", "").strip()
    if custom:
        path = Path(custom).expanduser().resolve()
    else:
        workspace = os.environ.get("WORKSPACE_DIR", "").strip()
        base = Path(workspace).expanduser().resolve() if workspace else Path(__file__).resolve().parent.parent / "workspace"
        path = base / "models" / "whisper"
    path.mkdir(parents=True, exist_ok=True)
    return path


def capcut_drafts_dir() -> Path:
    """Thư mục draft CapCut THẬT trên máy — assemble ghi draft thẳng vào đây
    để CapCut tự nhận ra. Cùng biến env `CAPCUT_DRAFTS_DIR` với CapcutSupperTool
    (D:\\CapcutSupperTool) — máy này đã set sẵn 'D:\\Capcut Data\\CapCut Drafts'.
    Không set thì fallback vào workspace (vẫn ghi được, chỉ là CapCut không tự
    thấy — phải tự copy thư mục qua tay)."""
    custom = os.environ.get("CAPCUT_DRAFTS_DIR", "").strip()
    if custom:
        path = Path(custom).expanduser().resolve()
    else:
        workspace = os.environ.get("WORKSPACE_DIR", "").strip()
        base = Path(workspace).expanduser().resolve() if workspace else Path(__file__).resolve().parent.parent / "workspace"
        path = base / "capcut_drafts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def apply_whisper_cache_env() -> Path:
    """Đưa cache HuggingFace + TEMP sang workspace trên ổ D — ổ C thường hết chỗ."""
    cache = whisper_cache_dir()
    hf = cache / "huggingface"
    tmp = cache / "tmp"
    hf.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(hf)
    os.environ["HF_HUB_CACHE"] = str(hf / "hub")
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(hf / "hub")
    os.environ["TEMP"] = str(tmp)
    os.environ["TMP"] = str(tmp)
    os.environ["TMPDIR"] = str(tmp)
    return cache


apply_whisper_cache_env()
