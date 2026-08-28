from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

# torch Storage.clone() access-violation trên Windows khi load checkpoint nếu chạy đa luồng —
# phải set trước khi import torch.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

from loguru import logger

from .. import config
from ..utils.srt import Cue, format_ts, write_srt

_model = None

SUPPORTED_LANGUAGES = {"zh", "en", "yue", "ja", "ko", "auto"}


class SenseVoiceError(RuntimeError):
    pass


def _apply_cache_env() -> Path:
    cache = config.sensevoice_cache_dir()
    os.environ["MODELSCOPE_CACHE"] = str(cache / "modelscope")
    os.environ["HF_HOME"] = str(cache / "huggingface")
    os.environ["HF_HUB_CACHE"] = str(cache / "huggingface" / "hub")
    return cache


def _load_model():
    global _model
    if _model is not None:
        return _model

    cache = _apply_cache_env()
    import torch

    torch.set_num_threads(1)
    from funasr import AutoModel

    device = (os.environ.get("SENSEVOICE_DEVICE") or config.SENSEVOICE_DEVICE).strip().lower()
    logger.info("Tải SenseVoiceSmall trên {} cache={}", device, cache)
    try:
        _model = AutoModel(
            model="iic/SenseVoiceSmall",
            vad_model="fsmn-vad",
            vad_kwargs={"max_single_segment_time": 30000},
            device=device,
            disable_update=True,
        )
    except Exception as err:
        raise SenseVoiceError(f"Không khởi tạo được SenseVoice: {err}") from err
    logger.info("SenseVoice sẵn sàng trên {}", device)
    return _model


def transcribe_video(
    video_path: Path,
    output_srt: Path,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> tuple[list[Cue], str]:
    if not video_path.exists():
        raise SenseVoiceError(f"Không thấy video: {video_path}")

    model = _load_model()
    from funasr.utils.postprocess_utils import rich_transcription_postprocess

    language = (os.environ.get("WHISPER_LANGUAGE") or config.WHISPER_LANGUAGE).strip().lower()
    if language not in SUPPORTED_LANGUAGES:
        language = "auto"

    if on_progress:
        on_progress(0, 1, "SenseVoice · đang xử lý")

    try:
        res = model.generate(
            input=str(video_path),
            cache={},
            language=language,
            use_itn=True,
            batch_size_s=60,
            merge_vad=True,
            merge_length_s=15,
            sentence_timestamp=True,
        )
    except Exception as err:
        raise SenseVoiceError(f"SenseVoice lỗi khi xử lý: {err}") from err

    sentences = (res[0].get("sentence_info") if res else None) or []
    cues: list[Cue] = []
    for i, s in enumerate(sentences, 1):
        text = rich_transcription_postprocess(s.get("text", "")).strip()
        if not text:
            continue
        start = float(s.get("start", 0)) / 1000.0
        end = float(s.get("end", 0)) / 1000.0
        cues.append(Cue(id=i, start=format_ts(start), end=format_ts(end), text=text))

    if on_progress:
        on_progress(1, 1, "SenseVoice · xong")

    device = (os.environ.get("SENSEVOICE_DEVICE") or config.SENSEVOICE_DEVICE).strip().lower()
    output_srt.parent.mkdir(parents=True, exist_ok=True)
    write_srt(output_srt, cues)
    return cues, f"sensevoice · {language} · {device}"
