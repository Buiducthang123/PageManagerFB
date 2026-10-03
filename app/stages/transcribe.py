from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from .. import config
from ..utils.srt import Cue, format_ts, parse_ts, write_srt

_model = None
_model_device: Optional[str] = None
_cpu_model = None


class TranscribeError(RuntimeError):
    pass


def _nvidia_bin_dirs() -> list[Path]:
    import sysconfig

    root = Path(sysconfig.get_path("purelib")) / "nvidia"
    if not root.is_dir():
        return []
    return [p for p in root.glob("*/bin") if p.is_dir()]


def _preload_cuda_dlls() -> None:
    """ctranslate2 cần cublas64_12.dll. CUDA Toolkit thường chưa cài —
    pip `nvidia-cublas-cu12` để DLL trong site-packages."""
    dirs = _nvidia_bin_dirs()
    extra = os.pathsep.join(str(d) for d in dirs)
    if extra:
        os.environ["PATH"] = extra + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        for d in dirs:
            try:
                os.add_dll_directory(str(d))
            except OSError:
                continue
    if dirs:
        logger.info("CUDA DLL dirs: {}", ", ".join(str(d) for d in dirs))


def _cuda_ok() -> bool:
    try:
        import ctranslate2

        return "int8" in ctranslate2.get_supported_compute_types("cuda")
    except Exception:
        return False


def _load_model():
    global _model, _model_device
    if _model is not None:
        return _model

    cache = config.apply_whisper_cache_env()
    _preload_cuda_dlls()
    from faster_whisper import WhisperModel

    preferred = config.whisper_device()
    model_name = (os.environ.get("WHISPER_MODEL") or config.WHISPER_MODEL).strip()
    attempts: list[tuple[str, str]] = []
    if preferred in ("cuda", "auto") and _cuda_ok():
        attempts.append(("cuda", "int8"))
    attempts.append(("cpu", "int8"))

    last_err: Exception | None = None
    for device, compute in attempts:
        try:
            logger.info(
                "Tải Whisper {} trên {} ({}) cache={}",
                model_name,
                device,
                compute,
                cache,
            )
            _model = WhisperModel(
                model_name,
                device=device,
                compute_type=compute,
                download_root=str(cache),
            )
            _model_device = device
            logger.info("Whisper sẵn sàng trên {}", device)
            return _model
        except Exception as err:
            last_err = err
            logger.warning("Không tải được Whisper trên {}: {}", device, err)
            _model = None
            _model_device = None
    raise TranscribeError(
        f"Không khởi tạo được Whisper: {last_err}. "
        f"Cache đang để ở {cache} (ổ D). Ổ C đầy sẽ không còn dùng để tải model."
    )


def _load_cpu_model():
    """Model Whisper riêng chạy CPU, chỉ dùng làm phương án cuối khi vá
    khoảng trống (_fill_gaps) — đã xác nhận trực tiếp: CTranslate2 trên CUDA
    không tất định (cùng audio/tham số, gọi transcribe() nhiều lần cho kết
    quả khác nhau — lúc chia câu bình thường, lúc gộp hết thành 1 câu dài
    chồng lấn, lúc trống trơn), CPU decode ổn định và tái lập được hơn hẳn.
    Chỉ tải khi thật sự cần (gap-fill GPU thất bản hết) nên chi phí tải thêm
    ~10-20s là chấp nhận được, vì đây là trường hợp hiếm."""
    global _cpu_model
    if _cpu_model is not None:
        return _cpu_model
    if _model_device == "cpu":
        return _model

    cache = config.apply_whisper_cache_env()
    from faster_whisper import WhisperModel

    model_name = (os.environ.get("WHISPER_MODEL") or config.WHISPER_MODEL).strip()
    try:
        _cpu_model = WhisperModel(model_name, device="cpu", compute_type="int8", download_root=str(cache))
        logger.info("Whisper CPU fallback (vá khoảng trống) sẵn sàng")
    except Exception as err:
        logger.warning("Không tải được Whisper CPU fallback: {}", err)
        return None
    return _cpu_model


WINDOW_S = 25.0
OVERLAP_S = 6.0
STEP_S = WINDOW_S - OVERLAP_S
SAMPLE_RATE = 16000

# Whisper đôi khi tự dừng decode giữa chừng 1 khối 25s dù audio phía sau vẫn
# có thoại thật (đã xác nhận bằng debug trực tiếp: 1 khối chỉ ra segment cho
# nửa đầu rồi im, khối kế tiếp thì trust-zone lại bắt đầu sau đó vài giây —
# để lại "vùng chết" không khối nào bao phủ). Coi khoảng trống giữa 2 cue
# liền nhau lớn hơn ngần này là bất thường (phân biệt với khoảng lặng tự
# nhiên ngắn) và decode lại riêng đúng đoạn đó.
GAP_FILL_THRESHOLD_S = 3.0
# Decode lại đúng bằng đệm sát mép khoảng trống (~10-12s) đã thử và thường bị
# CỤT GIỮA CHỪNG dù cùng audio, cùng model — chunk phải neo đủ dài, gần
# WINDOW_S, Whisper mới decode trọn vẹn (đã tự test trực tiếp xác nhận: chunk
# ngắn ra rỗng, chunk 25s neo lùi 10s trước khoảng trống ra đúng nội dung).
GAP_FILL_LEAD_S = 10.0


def _dedup_cues(raw: list[tuple[float, float, str]]) -> list[Cue]:
    """Loại cue trùng lặp sinh ra ở vùng overlap giữa 2 khối kề nhau.

    Ngoài ngưỡng tỷ lệ (overlap/dur > 0.5, hợp với cue ngắn bình thường),
    còn cần ngưỡng CHỒNG LẤN TUYỆT ĐỐI — đã xác nhận trực tiếp: khi Whisper
    (do nondeterminism GPU) gộp nhiều câu thành 1 segment dài bất thường,
    2 segment liền nhau có thể chồng lấn tới ~6s (đúng bằng OVERLAP_S thiết
    kế giữa 2 khối) nhưng tỷ lệ trên tổng độ dài rất dài của chúng lại nhỏ
    (<50%) nên lọt qua ngưỡng tỷ lệ — để lại cue trùng nội dung mà cũng che
    mất khoảng trống thật lẽ ra phải được _fill_gaps phát hiện lại."""
    kept: list[tuple[float, float, str]] = []
    for s, e, text in raw:
        if kept:
            ps, pe, _ = kept[-1]
            overlap = min(e, pe) - max(s, ps)
            dur = min(e - s, pe - ps)
            if dur > 0 and (overlap / dur > 0.5 or overlap > OVERLAP_S - 1.0):
                continue
        kept.append((s, e, text))
    return [Cue(id=i, start=format_ts(s), end=format_ts(e), text=text) for i, (s, e, text) in enumerate(kept, 1)]


def _decode_window_for_gap(
    model, audio, kwargs: dict, s: float, e: float, gap_start: float, gap_end: float,
) -> list[tuple[float, float, str]]:
    chunk = audio[int(s * SAMPLE_RATE) : int(e * SAMPLE_RATE)]
    segments, _ = model.transcribe(chunk, **kwargs)
    found: list[tuple[float, float, str]] = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        s_abs, e_abs = seg.start + s, seg.end + s
        if s_abs >= gap_start - 1.0 and e_abs <= gap_end + 1.0:
            found.append((s_abs, e_abs, text))
    return found


def _fill_gaps(
    model,
    audio,
    kwargs: dict,
    cues: list[Cue],
    total_s: float,
    video_path: Optional[Path] = None,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> list[Cue]:
    """Dò khoảng trống bất thường giữa 2 cue liền nhau (kể cả đầu/cuối video)
    và decode lại riêng đúng đoạn đó — xem giải thích ở GAP_FILL_THRESHOLD_S."""
    boundaries = [(format_ts(0.0), cues[0].start if cues else format_ts(total_s))]
    for i in range(len(cues) - 1):
        boundaries.append((cues[i].end, cues[i + 1].start))
    if cues:
        boundaries.append((cues[-1].end, format_ts(total_s)))

    extra: list[tuple[float, float, str]] = []
    for start_ts, end_ts in boundaries:
        gap_start, gap_end = parse_ts(start_ts), parse_ts(end_ts)
        if gap_end - gap_start < GAP_FILL_THRESHOLD_S:
            continue
        # faster-whisper/CTranslate2 trên CUDA không tất định (beam search
        # reduction order khác nhau giữa các lần gọi dù CÙNG audio/tham số) —
        # đã xác nhận trực tiếp: cùng 1 khối, gọi transcribe() nhiều lần cho
        # ra lúc thì chia câu ngắn bình thường, lúc gộp hết thành 1 câu dài
        # chồng lấn, lúc trống trơn. 1 lần thử không đủ tin cậy — thử vài
        # window khác nhau, lặp lại window nếu cần, để tăng xác suất bắt
        # đúng nội dung thật thay vì bỏ cuộc ngay ở lần đầu.
        def _windows_for_gap() -> list[tuple[float, float]]:
            candidates = []
            for lead in (GAP_FILL_LEAD_S, 0.0, GAP_FILL_LEAD_S):
                cs = max(0.0, gap_start - lead)
                ce = min(total_s, cs + WINDOW_S)
                if ce - cs < WINDOW_S:
                    cs = max(0.0, ce - WINDOW_S)
                candidates.append((cs, ce))
            return candidates

        found: list[tuple[float, float, str]] = []
        for cs, ce in _windows_for_gap():
            found = _decode_window_for_gap(model, audio, kwargs, cs, ce, gap_start, gap_end)
            if found:
                break

        if not found:
            # Hết cách trên GPU — thử CPU (decode tất định hơn, đã xác nhận
            # trực tiếp bắt đúng nội dung mà GPU liên tục bỏ lỡ ở cùng 1 gap).
            cpu_model = _load_cpu_model()
            if cpu_model is not None:
                s, e = _windows_for_gap()[0]
                found = _decode_window_for_gap(cpu_model, audio, kwargs, s, e, gap_start, gap_end)

        if not found and video_path is not None:
            # Cả GPU lẫn CPU đều không tin cậy tuyệt đối cho đúng loại nội
            # dung khó (nhạc nền lớn, tạp âm) — thử Gemini nghe lại đúng
            # khoảng trống này làm phương án cuối, đệm thêm 2s mỗi đầu để
            # không cắt cụt câu ngay tại ranh giới.
            found = _gemini_transcribe_window(
                video_path, max(0.0, gap_start - 2.0), min(total_s, gap_end + 2.0),
                context=f"đoạn {gap_start:.0f}s-{gap_end:.0f}s",
            ) or []

        extra.extend(found)
        if on_progress:
            on_progress(0, 0, f"vá khoảng trống {gap_start:.0f}s-{gap_end:.0f}s")

    if not extra:
        return cues

    logger.info("Whisper: vá được {} câu ở các khoảng trống bất thường", len(extra))
    all_raw = [(parse_ts(c.start), parse_ts(c.end), c.text) for c in cues] + extra
    all_raw.sort(key=lambda c: c[0])
    return _dedup_cues(all_raw)


# Lưới an toàn cuối cùng: bất kể nguồn nào tạo ra cue (Whisper decode thường,
# CTranslate2/CUDA nondeterminism đôi khi tự gộp nhiều câu thành 1 segment dài
# — xem comment ở _fill_gaps, hay Gemini nghe lại ở gap-fill/opening-fix cũng
# có xu hướng gộp nhiều câu bằng dấu phẩy/chấm tiếng Trung), tách cue theo 2
# tiêu chí ĐỘC LẬP (chỉ cần 1 trong 2 đúng là tách):
# 1. Đọc quá dồn dập so với tốc độ nói tiếng Trung tự nhiên (~7-8 ký tự/giây).
# 2. Cue quá dài (>DENSE_DURATION_S) VÀ bị nhồi từ nhiều mệnh đề trở lên
#    (>=DENSE_MIN_CLAUSES đoạn ngăn bởi dấu câu) — đã gặp thực tế: 1 cue dài
#    10.44s, CPS chỉ ~5.6 (dưới ngưỡng 1, không bị bắt) nhưng gộp tới 6 mệnh đề
#    khác nhau ("不过嘛" mở đầu ý mới) thành 1 khối phụ đề duy nhất — sai chuẩn
#    sub (mỗi khối nên là 1 ý, không phải nhiều câu ghép) dù tốc độ đọc bình
#    thường. Chia thời lượng theo tỷ lệ ký tự mỗi đoạn (không có word-timestamps
#    đáng tin cậy cho nguồn Gemini nên đây là xấp xỉ tốt nhất, cùng kỹ thuật đã
#    dùng ở _gemini_transcribe_window khi Gemini dồn timestamp bất thường).
DENSE_CPS_THRESHOLD = 8.0
DENSE_DURATION_S = 6.0
DENSE_MIN_CLAUSES = 3
_SPLIT_PUNCT = set("，,。.！!？?；;")


def _split_dense_cue(cue: Cue) -> list[tuple[float, float, str]]:
    start_s, end_s = parse_ts(cue.start), parse_ts(cue.end)
    dur = end_s - start_s
    text = cue.text.strip()
    if dur <= 0 or not text:
        return [(start_s, end_s, text)]

    parts: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in _SPLIT_PUNCT:
            parts.append(buf)
            buf = ""
    if buf.strip():
        parts.append(buf)
    parts = [p.strip() for p in parts if p.strip()]

    too_fast = len(text) / dur >= DENSE_CPS_THRESHOLD
    too_crowded = dur >= DENSE_DURATION_S and len(parts) >= DENSE_MIN_CLAUSES
    if len(parts) < 2 or not (too_fast or too_crowded):
        return [(start_s, end_s, text)]

    total_chars = sum(len(p) for p in parts) or 1
    cum = start_s
    out: list[tuple[float, float, str]] = []
    for p in parts:
        piece_dur = dur * (len(p) / total_chars)
        out.append((cum, cum + piece_dur, p))
        cum += piece_dur
    return out


def _split_dense_cues(cues: list[Cue]) -> list[Cue]:
    raw: list[tuple[float, float, str]] = []
    n_split = 0
    for cue in cues:
        pieces = _split_dense_cue(cue)
        if len(pieces) > 1:
            n_split += 1
        raw.extend(pieces)
    if not n_split:
        return cues
    logger.info("Tách lại {} cue đọc quá dồn dập theo dấu câu tiếng Trung", n_split)
    return [Cue(id=i, start=format_ts(s), end=format_ts(e), text=t) for i, (s, e, t) in enumerate(raw, 1)]


# Whisper hay bịa nội dung nhất ở đoạn mở đầu video (chưa có giọng nói rõ
# ràng — nhạc intro/hiệu ứng/im lặng), đây là hallucination kinh điển đã biết
# rộng rãi (SYSTRAN/faster-whisper#474). Gửi riêng đoạn này cho Gemini nghe
# lại (ít tốn quota — chỉ 1 lần gọi thêm/video) để thay thế nếu Whisper bịa.
OPENING_FIX_WINDOW_S = 20.0


def _extract_wav_window(video_path: Path, start_s: float, duration_s: float) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", str(max(0.0, start_s)),
                "-i", str(video_path),
                "-t", str(duration_s),
                "-vn", "-ac", "1", "-ar", "16000",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not tmp_path.exists():
            raise TranscribeError(f"ffmpeg trích đoạn audio lỗi: {(result.stderr or '')[-300:]}")
        return tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)


def _gemini_transcribe_window(
    video_path: Path, start_s: float, end_s: float, *, context: str,
) -> Optional[list[tuple[float, float, str]]]:
    """Gửi 1 khoảng audio [start_s, end_s] cho Gemini nghe lại, trả về cues
    với timestamp TUYỆT ĐỐI (đã cộng lại start_s). Trả None nếu có bất kỳ lỗi
    gì (không cấu hình key, hết quota, lỗi mạng...) — best-effort, không được
    để lỗi ở đây làm hỏng cả bước Whisper."""
    api_key = (os.environ.get("GEMINI_API_KEY") or config.GEMINI_API_KEY).strip()
    if not api_key:
        return None

    duration_s = end_s - start_s
    try:
        audio_bytes = _extract_wav_window(video_path, start_s, duration_s)
    except Exception as err:
        logger.warning("Không trích được audio ({}) cho Gemini: {}", context, err)
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        model = config.resolve_gemini_model()
        prompt = (
            f"Đây là {duration_s:.0f} giây audio tiếng Trung phổ thông (Mandarin), "
            f"trích từ {context} của 1 video. Các công cụ nhận diện giọng nói tự động "
            "hay BỊA nội dung sai hoặc bỏ sót ở những đoạn khó nghe (nhạc nền lớn, tạp "
            "âm, giọng nói bị đè) — hãy nghe thật kỹ, chỉ ghi lại CHÍNH XÁC những gì "
            "thực sự là lời thoại con người nói ra. Đoạn nào chỉ có nhạc/tiếng động/im "
            "lặng, không có lời thoại rõ ràng thì bỏ qua hoàn toàn, TUYỆT ĐỐI không bịa "
            "chữ.\n\n"
            "Trả về mỗi câu 1 dòng, đúng định dạng "
            '"giây_bắt_đầu-giây_kết_thúc: văn bản tiếng Trung giản thể" '
            '(ví dụ "2.10-4.00: 你好", tính theo giây kể từ ĐẦU đoạn audio này). '
            "Không thêm giải thích, không đánh số, nếu không có lời thoại nào thì trả "
            "về chuỗi rỗng."
        )
        response = client.models.generate_content(
            model=model,
            contents=[types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"), prompt],
            config=types.GenerateContentConfig(temperature=0.0),
        )
        text = (response.text or "").strip()
    except Exception as err:
        logger.warning("Gemini sửa audio ({}) lỗi, giữ nguyên Whisper: {}", context, err)
        return None

    parsed: list[tuple[float, float, str]] = []
    for line in text.splitlines():
        m = re.match(r"\s*([\d.]+)\s*-\s*([\d.]+)\s*:\s*(.+)", line.strip())
        if not m:
            continue
        rel_s, rel_e = float(m.group(1)), float(m.group(2))
        txt = m.group(3).strip().strip("「」\"'")
        if not txt:
            continue
        parsed.append((rel_s, max(rel_e, rel_s + 0.1), txt))

    if not parsed:
        return []

    # Đôi khi Gemini trả về kiểu "giả thuyết tăng dần" (mỗi dòng là phần đầu
    # của dòng kế tiếp, giống ASR streaming) thay vì câu hoàn chỉnh riêng
    # biệt — đã xác nhận trực tiếp: 33 dòng gần như trùng lặp cho 1 đoạn 20s
    # chỉ có ~9 câu thật. Gộp mỗi chuỗi "tăng dần" lại thành 1 dòng duy nhất
    # (bản dài nhất/cuối cùng), lấy mốc bắt đầu của dòng ĐẦU chuỗi và mốc kết
    # thúc của dòng CUỐI chuỗi.
    collapsed: list[tuple[float, float, str]] = []
    i = 0
    while i < len(parsed):
        chain_start_s = parsed[i][0]
        j = i
        while j + 1 < len(parsed) and parsed[j + 1][2].startswith(parsed[j][2]):
            j += 1
        collapsed.append((chain_start_s, parsed[j][1], parsed[j][2]))
        i = j + 1
    parsed = collapsed

    # Gemini không đưa ra timestamp chính xác tuyệt đối như Whisper — đôi khi
    # dồn hết N câu vào vài giây đầu dù audio thật dài hơn hẳn (đã xác nhận
    # trực tiếp: 9 câu ước tính trong 20s bị dồn vào 0-3.87s). Hậu quả: phần
    # audio "trống" phía sau (dù có lời thật) bị coi như gap, coi như MẤT nội
    # dung ở bước TTS/assemble dù text vẫn đủ. Phát hiện dồn bất thường (tổng
    # thời lượng phủ được < 1 nửa cửa sổ thật) và tính lại thời gian theo tỷ
    # lệ số ký tự mỗi câu, trải đều hết phần cửa sổ còn lại.
    covered = parsed[-1][1] - parsed[0][0]
    if len(parsed) >= 2 and covered < duration_s * 0.5:
        total_chars = sum(len(txt) for _, _, txt in parsed) or 1
        available = duration_s - parsed[0][0]
        cum = parsed[0][0]
        rescaled: list[tuple[float, float, str]] = []
        for _, _, txt in parsed:
            dur = available * (len(txt) / total_chars)
            rescaled.append((cum, cum + dur, txt))
            cum += dur
        parsed = rescaled
        logger.warning("Gemini ({}) dồn timestamp bất thường ({:.1f}s cho {:.1f}s audio) — đã tính lại theo tỷ lệ ký tự", context, covered, duration_s)

    fixed: list[tuple[float, float, str]] = []
    for rel_s, rel_e, txt in parsed:
        abs_s = start_s + rel_s
        abs_e = min(start_s + max(rel_e, rel_s + 0.5), end_s)
        if abs_e > abs_s:
            fixed.append((abs_s, abs_e, txt))

    return fixed


def transcribe_video(
    video_path: Path,
    output_srt: Path,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> tuple[list[Cue], str]:
    if not video_path.exists():
        raise TranscribeError(f"Không thấy video: {video_path}")

    model = _load_model()
    from faster_whisper.audio import decode_audio

    language = (os.environ.get("WHISPER_LANGUAGE") or config.WHISPER_LANGUAGE).strip().lower()
    kwargs = dict(
        language=None if language == "auto" else language,
        beam_size=5,
        condition_on_previous_text=False,
        # Không dùng vad_filter: faster-whisper có bug đã biết (SYSTRAN/faster-whisper#1355,
        # #1270) tự cắt bỏ nội dung khi VAD gộp 1 segment dài hơn ~30s (giới hạn kiến trúc
        # Whisper), và coi nhầm thoại bị nhạc nền lớn đè lên là "không phải giọng nói". Thay
        # vào đó tự chia audio thành khối cố định ngắn hơn 30s (có overlap để không cắt cụt
        # câu ở ranh giới) rồi để Whisper decode toàn bộ từng khối.
        vad_filter=False,
        word_timestamps=True,
        hallucination_silence_threshold=2.0,
    )

    device = _model_device or "?"
    audio = decode_audio(str(video_path), sampling_rate=SAMPLE_RATE)
    total_s = len(audio) / SAMPLE_RATE

    # decode_audio() ở trên là lệnh block DUY NHẤT không có điểm kiểm tra huỷ
    # nào — với video vài giờ/vài GB có thể mất nhiều phút để giải mã hết
    # audio. Nếu người dùng đã bấm Dừng ngay trong lúc đó, on_progress() gọi
    # job.raise_if_cancelled() ở đây sẽ dừng NGAY khi decode xong, thay vì
    # tiếp tục chạy hết cả vòng lặp decode Whisper (có thể mất hàng giờ) rồi
    # mới nhận ra cần dừng.
    if on_progress:
        on_progress(0, max(1, int(total_s)), f"đã giải mã audio ({total_s:.0f}s)")

    offsets: list[float] = []
    o = 0.0
    while o < total_s:
        offsets.append(o)
        o += STEP_S
    if not offsets:
        offsets = [0.0]

    n_window = int(WINDOW_S * SAMPLE_RATE)
    raw_cues: list[tuple[float, float, str]] = []
    lang_label: Optional[str] = None

    for idx, offset in enumerate(offsets):
        start_sample = int(offset * SAMPLE_RATE)
        end_sample = min(len(audio), start_sample + n_window)
        chunk = audio[start_sample:end_sample]
        segments, info = model.transcribe(chunk, **kwargs)
        if lang_label is None:
            lang_label = f"{info.language} ({(info.language_probability or 0):.0%})"

        trust_start = offset + (0.0 if idx == 0 else OVERLAP_S / 2)
        trust_end = offset + WINDOW_S - (0.0 if idx == len(offsets) - 1 else OVERLAP_S / 2)
        for seg in segments:
            text = (seg.text or "").strip()
            if not text:
                continue
            s_abs, e_abs = seg.start + offset, seg.end + offset
            center = (s_abs + e_abs) / 2
            if trust_start <= center <= trust_end:
                raw_cues.append((s_abs, e_abs, text))

        if on_progress:
            done = min(int(offset + WINDOW_S), int(total_s))
            on_progress(done, max(1, int(total_s)), f"{device} · {done}s/{int(total_s)}s")

    raw_cues.sort(key=lambda c: c[0])
    cues = _dedup_cues(raw_cues)
    cues = _fill_gaps(model, audio, kwargs, cues, total_s, video_path=video_path, on_progress=on_progress)
    lang = lang_label or "?"
    logger.info("Whisper language={} duration={:.1f}s device={} câu={}", lang, total_s, device, len(cues))

    window_s = min(OPENING_FIX_WINDOW_S, total_s)
    # Chỉ nhờ Gemini "sửa" đoạn mở đầu khi Whisper thật sự có dấu hiệu bịa/bỏ
    # sót (đoạn đầu gần như im lặng với Whisper — bao phủ được rất ít so với
    # window_s, đúng kịch bản hallucination nêu ở OPENING_FIX_WINDOW_S).
    # Nếu Whisper đã nghe ra thoại đầy đủ ngay từ đầu (video không có
    # nhạc intro/khoảng lặng), Gemini nghe lại thường GỘP nhiều câu liền nhau
    # thành 1 dòng (kèm timing sai, dồn cả câu dài vào khung ngắn của câu đầu)
    # — đã xác nhận trực tiếp bằng cách gọi lại y hệt hàm này trên 1 video
    # thoại dồn dập: Whisper tách đúng 3 câu 0-2.12/2.12-3.5/3.5-5.1s, Gemini
    # gộp lại thành 1-2 câu dài sai timing. Nên GIỮ Whisper nếu nó đã bao phủ
    # đủ, chỉ thay khi Whisper thực sự bỏ trống phần lớn đoạn đầu.
    opening_covered_s = sum(
        min(parse_ts(c.end), window_s) - parse_ts(c.start)
        for c in cues if parse_ts(c.start) < window_s
    )
    if opening_covered_s < window_s * 0.4:
        opening_fixed = _gemini_transcribe_window(video_path, 0.0, window_s, context="đoạn mở đầu")
        if opening_fixed is not None:
            n_replaced = sum(1 for c in cues if parse_ts(c.start) < window_s)
            logger.info("Gemini sửa đoạn đầu: {} câu (thay {} câu Whisper trong {:.0f}s đầu)",
                        len(opening_fixed), n_replaced, window_s)
            kept = [(parse_ts(c.start), parse_ts(c.end), c.text) for c in cues if parse_ts(c.start) >= window_s]
            cues = _dedup_cues(sorted(opening_fixed + kept, key=lambda c: c[0]))

    cues = _split_dense_cues(cues)

    output_srt.parent.mkdir(parents=True, exist_ok=True)
    write_srt(output_srt, cues)
    return cues, f"{lang} · {device}"
