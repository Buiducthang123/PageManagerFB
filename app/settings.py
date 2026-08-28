from __future__ import annotations

import os
from typing import Optional

from dotenv import set_key, unset_key

from . import config, projects as pj
from .schemas import AppSettingsResponse, ModelOption
from .stages import tts as tts_stage

GEMINI_MODELS = [
    ModelOption(id="gemini-3.5-flash-lite", label="3.5 Flash Lite (mặc định — rẻ, quota free cao)"),
    ModelOption(id="gemini-flash-lite-latest", label="Flash Lite latest"),
    ModelOption(id="gemini-3.5-flash", label="3.5 Flash"),
    ModelOption(id="gemini-flash-latest", label="Flash latest"),
    ModelOption(id="gemini-3.6-flash", label="3.6 Flash (đắt — free chỉ 20 req/ngày)"),
]

WHISPER_MODELS = [
    ModelOption(id="tiny", label="tiny — nhanh, kém chính xác"),
    ModelOption(id="base", label="base"),
    ModelOption(id="small", label="small"),
    ModelOption(id="medium", label="medium (khuyên dùng — 4GB VRAM)"),
    ModelOption(id="large-v3", label="large-v3 — nặng, cần VRAM lớn"),
]

WHISPER_LANGUAGES = [
    ModelOption(id="zh", label="Tiếng Trung (mặc định)"),
    ModelOption(id="en", label="Tiếng Anh"),
    ModelOption(id="yue", label="Tiếng Quảng Đông (Cantonese)"),
    ModelOption(id="ja", label="Tiếng Nhật"),
    ModelOption(id="ko", label="Tiếng Hàn"),
    ModelOption(id="vi", label="Tiếng Việt"),
    ModelOption(id="auto", label="Tự động nhận diện"),
]


def _with_current(options: list[ModelOption], current: str) -> list[ModelOption]:
    current = (current or "").strip()
    if current and all(o.id != current for o in options):
        return [ModelOption(id=current, label=f"{current} (đang dùng)"), *options]
    return options


ENV_PATH = pj.ROOT_DIR / ".env"


def _mask(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    return "••••••" + raw[-4:]


def get_settings() -> AppSettingsResponse:
    gemini_model = config.resolve_gemini_model()
    whisper_model = (os.environ.get("WHISPER_MODEL") or config.WHISPER_MODEL).strip()
    whisper_language = (os.environ.get("WHISPER_LANGUAGE") or config.WHISPER_LANGUAGE).strip()
    return AppSettingsResponse(
        workspace_dir=str(pj.WORKSPACE_DIR),
        gemini_api_key_masked=_mask(os.environ.get("GEMINI_API_KEY", "")),
        gemini_model=gemini_model,
        whisper_model=whisper_model,
        whisper_device=os.environ.get("WHISPER_DEVICE", config.WHISPER_DEVICE),
        whisper_language=whisper_language,
        gemini_models=_with_current(GEMINI_MODELS, gemini_model),
        whisper_models=_with_current(WHISPER_MODELS, whisper_model),
        whisper_languages=_with_current(WHISPER_LANGUAGES, whisper_language),
        tts_voices=[ModelOption(id=v["id"], label=v["label"]) for v in tts_stage.VOICES],
        whisper_cache_dir=str(config.whisper_cache_dir()),
        capcut_drafts_dir=str(config.capcut_drafts_dir()),
    )


def _set_var(env_var: str, value: Optional[str], *, empty_clears: bool) -> None:
    if value is None:
        return
    stripped = value.strip()
    ENV_PATH.touch(exist_ok=True)
    if stripped:
        set_key(str(ENV_PATH), env_var, stripped)
        os.environ[env_var] = stripped
    elif empty_clears:
        unset_key(str(ENV_PATH), env_var)
        os.environ.pop(env_var, None)


def update_settings(
    *,
    workspace_dir: Optional[str] = None,
    gemini_api_key: Optional[str] = None,
    gemini_model: Optional[str] = None,
    whisper_model: Optional[str] = None,
    whisper_device: Optional[str] = None,
    whisper_language: Optional[str] = None,
) -> AppSettingsResponse:
    _set_var("WORKSPACE_DIR", workspace_dir, empty_clears=True)
    if gemini_api_key:
        _set_var("GEMINI_API_KEY", gemini_api_key, empty_clears=False)
    _set_var("GEMINI_MODEL", gemini_model, empty_clears=False)
    _set_var("WHISPER_MODEL", whisper_model, empty_clears=False)
    _set_var("WHISPER_DEVICE", whisper_device, empty_clears=False)
    _set_var("WHISPER_LANGUAGE", whisper_language, empty_clears=False)
    return get_settings()
