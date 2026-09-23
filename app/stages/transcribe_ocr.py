from __future__ import annotations

import difflib
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np
from loguru import logger

from .. import config
from ..utils.srt import Cue, format_ts, parse_ts, write_srt

# Engine thứ 3 (bên cạnh Whisper/SenseVoice): đọc PHỤ ĐỀ CỨNG (burned-in) in
# sẵn trên khung hình video, không dựa vào âm thanh — phù hợp với video Douyin
# gắn phụ đề tiếng Trung khớp đúng lời thoại (đọc thẳng chữ đã render thường
# chính xác hơn đoán từ âm thanh), nhưng CHẬM HƠN Whisper nhiều lần vì phải
# OCR từng khung hình lấy mẫu (đã đo thực tế: ~0.6-1.2s/khung trên CPU) —
# không có cơ chế VAD như Whisper để biết trước lúc nào có thoại, phải tự suy
# ra mốc thời gian bằng cách so sánh chữ đọc được giữa các khung liên tiếp.

_engine = None


class TranscribeOCRError(RuntimeError):
    pass


def _load_engine():
    global _engine
    if _engine is not None:
        return _engine
    from rapidocr import RapidOCR

    logger.info("Đang tải model RapidOCR...")
    # Đã đo thực tế trên máy dev (CPU 16 luồng logic): mặc định (-1 = để
    # onnxruntime tự chọn, thường dùng hết luồng) chậm hơn rõ rệt so với giới
    # hạn ở mức vừa phải — model PP-OCR nhỏ, ảnh input bé, quá nhiều luồng gây
    # tranh chấp thay vì tăng tốc. intra=4 cho kết quả nhanh nhất/ổn định nhất
    # trong dải đã thử (1/2/4/6/8).
    #
    # Det.limit_side_len (mặc định 736, limit_type="min"): bước dò vùng chữ
    # tự động resize sao cho CẠNH NGẮN NHẤT của ảnh đạt tối thiểu giá trị này —
    # crop phụ đề của mình rất dẹt (cao vài trăm px, ngang cả nghìn px), nên
    # cạnh ngắn (chiều cao) luôn bị đây lên 736, kéo cả chiều ngang phình theo
    # tỷ lệ lên tới ~5000px/khung — đã xác nhận đây là nguyên nhân chính khiến
    # OCR chậm hơn cả thời lượng video trên nguồn có độ phân giải cao (video
    # ngang 1920x1080, crop mặc định không chọn tay). Hạ xuống 224 đo được
    # nhanh gấp ~6.7 lần (1100ms → 165ms/khung) trên cùng video, so từng khung
    # với baseline 736 không thấy mất nội dung câu nào — khác biệt chỉ là biến
    # thể phồn thể/giản thể vốn có sẵn của OCR (đã thấy hiện tượng này độc lập
    # với thay đổi này, giữa các khung giống hệt nhau ở config cũ).
    _engine = RapidOCR(
        params={
            "EngineConfig.onnxruntime.intra_op_num_threads": 4,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "Det.limit_side_len": 224,
        }
    )
    return _engine


_region_engine = None


def _load_region_engine():
    """Engine RIÊNG cho `detect_subtitle_region` — KHÔNG dùng chung `_engine`
    (Det.limit_side_len=224, xem `_load_engine`). Đã xác nhận thật: 224 chỉ
    hợp với crop hẹp có sẵn (phụ đề transcribe bình thường); chạy trên
    khung hình ĐẦY ĐỦ (không crop, vì chưa biết vùng chữ ở đâu) thì 224 ép
    downscale quá mạnh, model dò chữ mất hẳn độ chính xác, trả về box méo mó
    cao gần hết khung hình dù chữ thật chỉ chiếm 1 dải mỏng — đo trực tiếp
    trên video thật thấy box "cao 40-56% khung hình" cho 1 dòng phụ đề bình
    thường, sai rõ rệt. Giữ limit_side_len mặc định (736) cho engine này —
    chậm hơn nhưng đây là 1 lượt dò 1 lần, không phải OCR từng khung liên
    tục như transcribe."""
    global _region_engine
    if _region_engine is not None:
        return _region_engine
    from rapidocr import RapidOCR

    _region_engine = RapidOCR(
        params={
            "EngineConfig.onnxruntime.intra_op_num_threads": 4,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        }
    )
    return _region_engine


def _crop_filter(crop_region: Optional[tuple[float, float, float, float]]) -> str:
    """`crop_region` = (x, y, w, h) dạng phân số 0-1, người dùng tự khoanh
    trên preview video ở frontend — khoanh càng sát chữ phụ đề thật càng vừa
    NHANH (ít pixel hơn) vừa CHÍNH XÁC hơn (đã xác nhận trực tiếp: crop mù
    quáng/co nhỏ bừa bãi làm OCR đọc sai, còn crop đúng tay vào đúng vùng chữ
    thì không mất gì). Không có thì fallback về mặc định 25% đáy khung hình."""
    if crop_region:
        x, y, w, h = crop_region
        return f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y}"
    frac = config.OCR_CROP_BOTTOM_FRACTION
    return f"crop=iw:ih*{frac}:0:ih*{1 - frac}"


def _extract_frames(
    video_path: Path,
    out_dir: Path,
    fps: float,
    crop_region: Optional[tuple[float, float, float, float]],
) -> list[Path]:
    """1 lệnh ffmpeg duy nhất: lấy mẫu khung hình thưa (mặc định 1 khung/giây)
    + crop sẵn vùng phụ đề ngay trong ffmpeg — giảm hẳn số pixel phải OCR so
    với xử lý nguyên khung hình gốc. Không dùng pattern Popen+poll-để-cancel
    (như video_merge.py) — bước này thường nhanh hơn hẳn tổng thời gian OCR
    sau đó; cancel thật sự xảy ra ở vòng lặp OCR từng khung bên dưới, chi tiết
    hơn nhiều."""
    out_dir.mkdir(parents=True, exist_ok=True)
    vf = f"fps={fps},{_crop_filter(crop_region)}"
    pattern = str(out_dir / "frame_%06d.jpg")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path), "-vf", vf, "-qscale:v", "2", pattern],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise TranscribeOCRError(f"ffmpeg lấy mẫu khung hình lỗi: {(result.stderr or '')[-500:]}")
    return sorted(out_dir.glob("frame_*.jpg"))


# Phụ đề thường hiện liên tục nhiều giây (nhiều khung 1fps liên tiếp giống
# hệt nhau về nội dung) — so sánh nhanh bằng pixel (rẻ hơn OCR hàng trăm
# lần) trước, khung nào gần như giống hệt khung ngay trước thì dùng lại
# đúng text đã đọc, khỏi chạy lại OCR. Đã thử so toàn bộ pixel vùng crop
# (mean-abs-diff) trước — THẤT BẠI: cảnh quay nền phía sau chữ phụ đề vẫn
# chuyển động liên tục dù chữ không đổi, nên gần như khung nào cũng bị coi
# là "khác" (chỉ bỏ qua được 2/108 khung thực tế). Sửa lại: chữ phụ đề luôn
# là các pixel RẤT SÁNG (trắng/near-white, có viền đen dày để dễ đọc trên
# nền bất kỳ) — nhị phân hoá theo ngưỡng sáng rồi chỉ so khung "mặt nạ chữ"
# này, gần như miễn nhiễm với nền ảnh tự nhiên thay đổi liên tục.
_MASK_DIFF_FRACTION = 0.02


def _frame_signature(frame_path: Path) -> np.ndarray:
    img = cv2.imread(str(frame_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return np.zeros((32, 160), dtype=np.uint8)
    small = cv2.resize(img, (160, 32), interpolation=cv2.INTER_AREA)
    _, mask = cv2.threshold(small, 200, 255, cv2.THRESH_BINARY)
    return mask


def _frames_similar(a: np.ndarray, b: np.ndarray) -> bool:
    diff_frac = float(np.count_nonzero(a != b)) / a.size
    return diff_frac < _MASK_DIFF_FRACTION


def _has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def _ocr_text(engine, frame_path: Path) -> str:
    result = engine(str(frame_path))
    if not result.txts:
        return ""
    # Đây là engine chuyên đọc phụ đề TIẾNG TRUNG — câu phụ đề thật luôn có
    # ít nhất 1 ký tự Hán. Đã xác nhận thực tế (Det.limit_side_len hạ xuống
    # 224 để tăng tốc — xem _load_engine) làm lộ ra các box "chữ" giả từ
    # watermark/hiệu ứng game ở đúng đoạn video KHÔNG có phụ đề (vd đọc nhầm
    # thành "WV", "26", "YY", hoặc dính thêm 1 ký tự lạ vào cuối câu thật) —
    # box nào không có ký tự Hán gần như chắc chắn là nhiễu, loại trước khi
    # ghép thay vì cố nâng ngưỡng tin cậy (nhiễu này vẫn được model chấm điểm
    # tin cậy cao, tăng threshold không lọc được).
    boxes = [(b, t.strip()) for b, t in zip(result.boxes, result.txts) if _has_cjk(t)]
    if not boxes:
        return ""
    if len(boxes) == 1:
        return boxes[0][1]
    # Hiếm khi phụ đề dài bị tách thành nhiều box — sắp theo toạ độ x trái
    # nhất của mỗi box rồi ghép lại đúng thứ tự đọc trái→phải.
    boxes.sort(key=lambda p: float(p[0][:, 0].min()))
    return "".join(t for _, t in boxes)


def detect_subtitle_region(
    video_path: Path,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> Optional[tuple[float, float, float, float]]:
    """Tự động dò vùng che phụ đề cũ cho tính năng "Xuất video trực tiếp" —
    OCR nguyên khung hình (không crop trước, vì chưa biết vùng chữ ở đâu) trên
    1 số khung lấy mẫu thưa (xem OCR_DETECT_REGION_FPS), gộp (hợp) toạ độ mọi
    box chữ Hán phát hiện được thành 1 khung chữ nhật duy nhất che suốt video —
    đã chốt với người dùng: chấp nhận che dư nếu phụ đề cũ nhảy vị trí, đổi
    lấy đơn giản (không cần vùng che đổi theo thời gian). Trả None nếu không
    phát hiện được chữ nào (video không có phụ đề cứng, hoặc chữ quá nhỏ/mờ)."""
    if not video_path.exists():
        raise TranscribeOCRError(f"Không thấy video: {video_path}")

    engine = _load_region_engine()
    fps = config.OCR_DETECT_REGION_FPS
    _PAD_FRAC = 0.015  # đệm thêm mỗi cạnh — box OCR đôi khi sát chữ hơi chặt
    # Ngưỡng chiều cao tối đa cho 1 box được tính vào vùng che — chặn 2 loại
    # không mong muốn: (1) box nhiễu méo mó chiếm gần hết khung (xem
    # _load_region_engine), (2) chữ cảm thán cỡ to kiểu hiệu ứng ("啊", "嘿"...)
    # cao tới ~40-56% khung hình, khác hẳn dải phụ đề hội thoại bình thường
    # (~5-10%) — đã xác nhận thật trên video mẫu, người dùng chốt: KHÔNG che
    # loại chữ hiệu ứng này (ít quan trọng hơn để dịch, che sẽ làm vùng che
    # rộng quá mức cần thiết).
    _MAX_BOX_HEIGHT_FRAC = 0.15

    with tempfile.TemporaryDirectory(prefix="ocr_region_") as tmp:
        # LƯU Ý: `crop_region=None` cho `_extract_frames` KHÔNG có nghĩa "không
        # crop" — `_crop_filter` coi None là "chưa chọn tay, dùng mặc định crop
        # đáy 25%" (đúng ý cho transcribe_video, sai hoàn toàn ở đây vì mình
        # đang cần quét NGUYÊN khung hình để tìm vị trí chữ). Phải truyền rõ
        # (0,0,1,1) — vùng full-frame thật, không phải "để trống" — mới tắt
        # được crop mặc định (đã xác nhận thật: thiếu dòng này khiến mọi toạ độ
        # box tính sai theo tỉ lệ dải crop 25% đáy thay vì theo khung hình gốc,
        # ra kết quả vùng che sai be bét).
        frames = _extract_frames(video_path, Path(tmp), fps, crop_region=(0.0, 0.0, 1.0, 1.0))
        total = len(frames)
        if total == 0:
            raise TranscribeOCRError("Không lấy được khung hình nào từ video")

        boxes: list[tuple[float, float, float, float]] = []  # (bx0, bx1, by0, by1)
        for i, frame in enumerate(frames):
            if on_progress:
                on_progress(i, total, f"Dò vùng chữ {i + 1}/{total}")
            img = cv2.imread(str(frame))
            if img is None:
                continue
            h, w = img.shape[:2]
            result = engine(str(frame))
            if not result.txts:
                continue
            for box, text in zip(result.boxes, result.txts):
                if not _has_cjk(text.strip()):
                    continue
                bx0, bx1 = float(box[:, 0].min()) / w, float(box[:, 0].max()) / w
                by0, by1 = float(box[:, 1].min()) / h, float(box[:, 1].max()) / h
                # Nhiễu tin cậy thấp (thường 1 ký tự đơn lẻ đọc nhầm từ hoạ tiết
                # nền) đôi khi được model trả về box gần như CHIẾM TRỌN khung
                # hình (đã xác nhận thật: text='上'/'一' với box phủ x=[0.0,1.0]
                # y=[0.0,1.0]) — 1 box như vậy đủ phá hỏng cả phép hợp, làm vùng
                # che rơi về gần như toàn khung. Phụ đề thật luôn là 1 dải ngang
                # dẹt, không bao giờ chiếm gần hết chiều cao khung hình. Cũng
                # loại luôn chữ hiệu ứng cỡ to (xem docstring hàm) qua cùng
                # ngưỡng này.
                if (bx1 - bx0) > 0.9 or (by1 - by0) > _MAX_BOX_HEIGHT_FRAC:
                    continue
                boxes.append((bx0, bx1, by0, by1))

    if not boxes:
        return None

    # Nhiễu KHÔNG bị lọc bởi ngưỡng chiều cao ở trên vẫn còn (chữ Hán giả đọc
    # nhầm từ hoạ tiết cảnh/UI game, kích thước bình thường nhưng rải rác khắp
    # khung hình — đã xác nhận thật, vd đọc nhầm '中'/'新'/'江' ở nhiều vị trí y
    # khác hẳn nhau) — phụ đề THẬT luôn lặp lại ở gần đúng 1 DẢI Y cố định
    # xuyên suốt video, còn nhiễu thì rải rác không lặp lại đúng chỗ. Gộp theo
    # cụm y-center đông nhất (histogram 20 bin) thay vì hợp mù quáng mọi box.
    _NUM_BINS = 20
    bin_counts = [0] * _NUM_BINS
    for bx0, bx1, by0, by1 in boxes:
        yc = (by0 + by1) / 2
        bin_idx = min(_NUM_BINS - 1, int(yc * _NUM_BINS))
        bin_counts[bin_idx] += 1
    dominant_bin = max(range(_NUM_BINS), key=lambda b: bin_counts[b])
    # Cửa sổ dung sai quanh bin đông nhất — đủ rộng để không cắt rời phụ đề
    # THẬT rơi ở bin liền kề do sai số làm tròn, không đủ rộng để lẫn nhiễu.
    # Đã xác nhận thật trên video mẫu: dải phụ đề thật cực kỳ ổn định
    # (y-center chỉ dao động ~0.005 giữa các khung, rơi gọn trong 1 bin), còn
    # cụm nhiễu gần nhất cũng đã cách xa hơn 1 bin — dung sai 2 bin (thử ban
    # đầu) đủ rộng để NUỐT LUÔN cụm nhiễu liền kề, phải hạ xuống 1.
    _TOLERANCE_BINS = 1
    lo = (dominant_bin - _TOLERANCE_BINS) / _NUM_BINS
    hi = (dominant_bin + 1 + _TOLERANCE_BINS) / _NUM_BINS

    min_x = min_y = 1.0
    max_x = max_y = 0.0
    found_any = False
    for bx0, bx1, by0, by1 in boxes:
        yc = (by0 + by1) / 2
        if not (lo <= yc <= hi):
            continue
        found_any = True
        min_x = min(min_x, bx0)
        max_x = max(max_x, bx1)
        min_y = min(min_y, by0)
        max_y = max(max_y, by1)

    if not found_any:
        return None

    x0 = max(0.0, min_x - _PAD_FRAC)
    y0 = max(0.0, min_y - _PAD_FRAC)
    x1 = min(1.0, max_x + _PAD_FRAC)
    y1 = min(1.0, max_y + _PAD_FRAC)
    return (x0, y0, x1 - x0, y1 - y0)


def transcribe_video(
    video_path: Path,
    output_srt: Path,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    crop_region: Optional[tuple[float, float, float, float]] = None,
) -> tuple[list[Cue], str]:
    if not video_path.exists():
        raise TranscribeOCRError(f"Không thấy video: {video_path}")

    engine = _load_engine()
    fps = config.OCR_SAMPLE_FPS
    frame_dur = 1.0 / fps

    with tempfile.TemporaryDirectory(prefix="ocr_frames_") as tmp:
        frames = _extract_frames(video_path, Path(tmp), fps, crop_region)
        total = len(frames)
        if total == 0:
            raise TranscribeOCRError("Không lấy được khung hình nào từ video")

        raw: list[tuple[float, str]] = []
        prev_sig: Optional[np.ndarray] = None
        prev_text = ""
        ocr_calls = 0
        for i, frame in enumerate(frames):
            if on_progress:
                on_progress(i, total, f"OCR khung {i + 1}/{total}")
            sig = _frame_signature(frame)
            if prev_sig is not None and _frames_similar(sig, prev_sig):
                text = prev_text  # khung gần như giống hệt khung trước — khỏi OCR lại
            else:
                text = _ocr_text(engine, frame)
                ocr_calls += 1
            raw.append((i * frame_dur, text))
            prev_sig = sig
            prev_text = text
        if total:
            logger.info("OCR: chạy thật {}/{} khung (bỏ qua {} khung trùng lặp)", ocr_calls, total, total - ocr_calls)

    # Diff tuần tự: text đổi (kể cả về rỗng) là ranh giới cue. Cue đang mở
    # được nới `end` ra tới hết khung có text giống hệt/gần giống hệt.
    #
    # So khớp GẦN ĐÚNG (không phải tuyệt đối từng ký tự): OCR đôi khi đọc
    # lệch 1-2 ký tự giữa các khung liên tiếp của CÙNG 1 câu phụ đề thật (hay
    # gặp nhất là biến thể phồn thể/giản thể như 拋/抛, 隕/陨 — bản chất nhiễu
    # nhận dạng ký tự, không phải câu đổi) — nếu so tuyệt đối sẽ bị tách nhầm
    # thành 2 cue trùng lặp gần như giống hệt nhau. Ngưỡng 0.85 đủ chặt để
    # không gộp nhầm 2 câu phụ đề THẬT SỰ khác nhau (dù có chung vài từ khoá,
    # ví dụ cùng nhắc tên nhân vật) — 1-2 ký tự lệch trong câu ~15-25 ký tự
    # cho tỉ lệ giống ~0.9+, còn 2 câu khác nội dung thường dưới 0.5.
    _SIMILARITY_THRESHOLD = 0.85

    def _same_subtitle(a: str, b: str) -> bool:
        if not a or not b:
            return a == b
        return difflib.SequenceMatcher(None, a, b).ratio() >= _SIMILARITY_THRESHOLD

    cues: list[Cue] = []
    cur_text: Optional[str] = None
    cur_start = 0.0

    def flush(end_ts: float) -> None:
        nonlocal cur_text
        if cur_text:
            cues.append(Cue(id=len(cues) + 1, start=format_ts(cur_start), end=format_ts(end_ts), text=cur_text))
        cur_text = None

    for ts, text in raw:
        if text == cur_text or (cur_text and text and _same_subtitle(text, cur_text)):
            continue  # vẫn là câu đang hiện — chỉ là đọc lệch vài ký tự, khỏi tách cue
        flush(ts)
        cur_text = text or None
        cur_start = ts
    if raw:
        flush(raw[-1][0] + frame_dur)

    # Lọc cue quá ngắn — thường là OCR đọc lệch 1 khung đơn lẻ (nhiễu), không
    # phải phụ đề thật (phụ đề thật luôn hiện đủ lâu để người xem đọc được).
    filtered = [c for c in cues if parse_ts(c.end) - parse_ts(c.start) >= config.OCR_MIN_CUE_DURATION_S]
    for i, c in enumerate(filtered, 1):
        c.id = i

    if not filtered:
        raise TranscribeOCRError(
            "Không phát hiện được phụ đề cứng nào trong video — có thể video này không có "
            "phụ đề in sẵn trên khung hình, thử Whisper hoặc SenseVoice thay"
        )

    output_srt.parent.mkdir(parents=True, exist_ok=True)
    write_srt(output_srt, filtered)
    return filtered, f"ocr · {ocr_calls}/{total} khung thật đọc ({fps:.0f}fps) · {config.OCR_DEVICE}"
