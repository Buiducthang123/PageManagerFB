from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import set_key, unset_key

from . import config, projects as pj, system_check
from .schemas import AppSettingsResponse, ModelOption
from .stages import tts as tts_stage
from .stages import tts_vieneu as tts_vieneu_stage

GEMINI_MODELS = [
    ModelOption(id="gemini-3.5-flash-lite", label="3.5 Flash Lite (mặc định — rẻ, quota free cao)"),
    ModelOption(id="gemini-flash-lite-latest", label="Flash Lite latest"),
    ModelOption(id="gemini-3.5-flash", label="3.5 Flash"),
    ModelOption(id="gemini-flash-latest", label="Flash latest"),
    ModelOption(id="gemini-3.6-flash", label="3.6 Flash (đắt — free chỉ 20 req/ngày)"),
]

WHISPER_MODELS = [
    # Nhãn theo độ chính xác, không nêu tên model (tiny/medium...) — user không cần biết.
    ModelOption(id="tiny", label="Nhanh nhất — kém chính xác (~75 MB)"),
    ModelOption(id="base", label="Nhanh (~150 MB)"),
    ModelOption(id="small", label="Cân bằng (~500 MB)"),
    ModelOption(id="medium", label="Chính xác — khuyên dùng, hợp card 4 GB (~1,5 GB)"),
    ModelOption(id="large-v3", label="Chính xác nhất — nặng, cần card mạnh (~3 GB)"),
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

TRANSLATE_PACES = [
    ModelOption(id="natural", label="Đọc tự nhiên (15.5 CPS) — có thể cắt bớt ý ở câu quá dồn"),
    ModelOption(id="balanced", label="Cân bằng (18 CPS)"),
    ModelOption(id="full_meaning", label="Đủ ý hơn (20-22 CPS, mặc định) — chấp nhận đọc nhanh/trôi timing nhẹ"),
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
    translate_pace = (os.environ.get("TRANSLATE_PACE") or config.TRANSLATE_PACE).strip()
    return AppSettingsResponse(
        workspace_dir=str(pj.WORKSPACE_DIR),
        gemini_api_key_masked=_mask(os.environ.get("GEMINI_API_KEY", "")),
        gemini_model=gemini_model,
        whisper_model=whisper_model,
        whisper_device=os.environ.get("WHISPER_DEVICE", config.WHISPER_DEVICE),
        whisper_language=whisper_language,
        translate_pace=translate_pace,
        gemini_models=_with_current(GEMINI_MODELS, gemini_model),
        whisper_models=_with_current(WHISPER_MODELS, whisper_model),
        whisper_languages=_with_current(WHISPER_LANGUAGES, whisper_language),
        translate_paces=TRANSLATE_PACES,
        tts_voices=[ModelOption(id=v["id"], label=v["label"]) for v in tts_stage.VOICES],
        tts_voices_vieneu=[ModelOption(id=v["id"], label=v["label"]) for v in tts_vieneu_stage.VOICES],
        whisper_cache_dir=str(config.whisper_cache_dir()),
        **_capcut_fields(),
        **_machine_fields(),
    )


def _machine_fields() -> dict:
    gpu = system_check.detect_gpu()
    free_gb, _total = system_check.disk_free(pj.WORKSPACE_DIR)
    return {
        "workspace_free_gb": round(free_gb, 1),
        "workspace_disk_status": system_check.disk_status(free_gb),
        "ai_device": config.ai_device_mode(),
        "gpu_available": gpu.available,
        "gpu_name": gpu.name,
        "gpu_memory_mb": gpu.memory_mb,
        "models_dir": str(config.models_dir()),
        "models_dir_custom": os.environ.get("MODELS_DIR", "").strip(),
        "temp_dir": str(config.temp_dir()),
        "temp_dir_custom": os.environ.get("TEMP_DIR", "").strip(),
        "tts_concurrency": config.tts_concurrency(),
        "demucs_timeout_s": config.demucs_timeout_s(),
    }


def _check_dir_writable(label: str, raw: str) -> None:
    """Thư mục tự đặt phải tạo/ghi được — báo lỗi ngay lúc Lưu thay vì để job
    sau đó hỏng giữa chừng."""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label}: cần đường dẫn đầy đủ (vd D:/ReupData/models)")
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as err:
        raise ValueError(f"{label}: không ghi được vào {path} ({err})") from err


def _capcut_fields() -> dict:
    path, source = config.capcut_drafts_dir_setting()
    status, message = config.check_capcut_drafts_dir(path) if path else ("error", "Chưa cài thư mục draft CapCut")
    return {
        "capcut_drafts_dir": str(path) if path else "",
        "capcut_drafts_source": source,
        "capcut_drafts_status": status,
        "capcut_drafts_message": message,
        "capcut_drafts_detected": [str(p) for p in config.detect_capcut_drafts_dirs()],
    }


def check_capcut_drafts_dir(raw: str) -> tuple[str, str]:
    raw = (raw or "").strip()
    if not raw:
        return "error", "Chưa nhập đường dẫn"
    return config.check_capcut_drafts_dir(Path(raw).expanduser())


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
    translate_pace: Optional[str] = None,
    capcut_drafts_dir: Optional[str] = None,
    ai_device: Optional[str] = None,
    models_dir: Optional[str] = None,
    temp_dir: Optional[str] = None,
    tts_concurrency: Optional[int] = None,
    demucs_timeout_s: Optional[int] = None,
) -> AppSettingsResponse:
    # Kiểm tra hết trước khi ghi .env — lỗi ở 1 ô thì không lưu dở dang.
    if ai_device is not None and ai_device not in config.AI_DEVICE_MODES:
        raise ValueError(f'Thiết bị xử lý AI "{ai_device}" không hợp lệ')
    if models_dir and models_dir.strip():
        _check_dir_writable("Thư mục model", models_dir.strip())
    if temp_dir and temp_dir.strip():
        _check_dir_writable("Thư mục tạm", temp_dir.strip())
    if tts_concurrency is not None and not 1 <= tts_concurrency <= 16:
        raise ValueError("Số luồng TTS phải từ 1 đến 16")
    if demucs_timeout_s is not None and not 300 <= demucs_timeout_s <= 4 * 3600:
        raise ValueError("Thời gian chờ Demucs phải từ 300 đến 14400 giây")
    if capcut_drafts_dir and capcut_drafts_dir.strip():
        status, message = check_capcut_drafts_dir(capcut_drafts_dir)
        if status == "error":
            raise ValueError(f"Thư mục draft CapCut: {message}")
    _set_var("WORKSPACE_DIR", workspace_dir, empty_clears=True)
    # Để trống = bỏ đường dẫn tự đặt, quay về tự dò thư mục mặc định của CapCut.
    _set_var("CAPCUT_DRAFTS_DIR", capcut_drafts_dir, empty_clears=True)
    if gemini_api_key:
        _set_var("GEMINI_API_KEY", gemini_api_key, empty_clears=False)
    _set_var("GEMINI_MODEL", gemini_model, empty_clears=False)
    _set_var("WHISPER_MODEL", whisper_model, empty_clears=False)
    _set_var("WHISPER_DEVICE", whisper_device, empty_clears=False)
    _set_var("WHISPER_LANGUAGE", whisper_language, empty_clears=False)
    if translate_pace and translate_pace not in config.TRANSLATE_PACE_PROFILES:
        raise ValueError(f'Mức độ dịch "{translate_pace}" không hợp lệ')
    _set_var("TRANSLATE_PACE", translate_pace, empty_clears=False)
    _set_var("AI_DEVICE", ai_device, empty_clears=False)
    _set_var("MODELS_DIR", models_dir, empty_clears=True)
    _set_var("TEMP_DIR", temp_dir, empty_clears=True)
    if tts_concurrency is not None:
        _set_var("TTS_CONCURRENCY", str(tts_concurrency), empty_clears=False)
    if demucs_timeout_s is not None:
        _set_var("DEMUCS_TIMEOUT_S", str(demucs_timeout_s), empty_clears=False)
    if models_dir is not None or temp_dir is not None:
        # HF_HOME/TEMP của process đặt lại ngay; model đã nạp trong RAM vẫn
        # dùng tiếp tới khi khởi động lại app.
        config.apply_whisper_cache_env()
    return get_settings()
