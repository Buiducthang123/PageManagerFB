from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

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


SENSEVOICE_DEVICE = os.environ.get("SENSEVOICE_DEVICE", "cpu").strip() or "cpu"


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
