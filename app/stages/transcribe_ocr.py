from __future__ import annotations

import difflib
import subprocess
import tempfile
import threading
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
    _engine = RapidOCR(params=_FAST_PARAMS)
    return _engine


# Cấu hình nhanh cho ảnh ĐÃ CROP sát dải phụ đề (xem chú thích trong _load_engine).
_FAST_PARAMS = {
    "EngineConfig.onnxruntime.intra_op_num_threads": 4,
    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
    "Det.limit_side_len": 224,
}

_visibility_engine = None
_visibility_engine_lock = threading.Lock()


def _load_visibility_engine():
    """Engine cho `detect_subtitle_visibility` — cùng cấu hình nhanh với
    `_engine` (ảnh vào cũng là dải phụ đề đã crop sát) nhưng là INSTANCE RIÊNG:
    lượt dò sớm sau ingest chạy song song với transcribe, RapidOCR không an
    toàn khi 2 thread gọi chung 1 engine.

    Trước đây dùng `_region_engine` (limit_side_len 736, dành cho khung hình
    đầy đủ): dải phụ đề dẹt (~600x90px) bị phóng cạnh ngắn lên 736 → ngang
    ~5000px/khung. Đo thật video 12 phút: 920 → 95 ms/khung (19,8 → 2,5 phút
    cả video); khoảng che gần như y hệt (544s vs 547s / 718s), soi từng đoạn
    lệch thấy bản nhanh còn bắt thêm 2 câu phụ đề thật bản cũ bỏ sót."""
    global _visibility_engine
    if _visibility_engine is not None:
        return _visibility_engine
    from rapidocr import RapidOCR

    _visibility_engine = RapidOCR(params=_FAST_PARAMS)
    return _visibility_engine


_region_engine = None
# RapidOCR không đảm bảo an toàn khi 2 thread gọi CHUNG 1 engine cùng lúc — lượt
# dò sớm sau ingest (main._start_early_blur_detect) và transcribe từng chạy
# song song trên cùng engine này. Khoá quanh mỗi lần gọi engine.
_region_engine_lock = threading.Lock()


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
#
# Sửa lần 2 (đo thật trên video 4K 3840x2160): bản cũ thu nhỏ vùng crop về
# 160x32 TRƯỚC rồi mới lọc pixel sáng — ở 4K co ~24 lần, nét chữ trắng mảnh bị
# trộn với viền đen thành xám, mặt nạ gần như luôn RỖNG kể cả khi có phụ đề →
# mọi khung "giống nhau" → dùng lại chữ cũ: 90s đầu sai 52/90 khung (mất câu,
# câu cũ kéo dài). Giờ lọc ở độ phân giải GỐC trước, chỉ giữ pixel sáng nằm
# sát viền tối (chữ phụ đề luôn có viền đen — trời/nền sáng không có, không
# làm nhiễu), rồi mới thu nhỏ; so theo tỉ lệ trên phần có chữ thay vì trên cả
# khung (câu ngắn chỉ chiếm vài % khung, so trên cả khung dễ lọt ngưỡng).
# Đo lại: 4K sai 52 → 0 câu, 720p sai 7 → 0, 1080p dọc/576p không đổi (0).
_SIG_SIZE = (320, 64)
_SIG_EMPTY_CELLS = 12  # ít hơn ngần này ô có chữ ở cả 2 khung = cùng "không có phụ đề"
_SIG_DIFF_RATIO = 0.3


#
# Sửa lần 3: đo độ sáng theo kênh màu SÁNG NHẤT (max R/G/B) thay vì grayscale
# — phụ đề màu (vàng/cam/đỏ tươi...) có độ sáng grayscale chỉ ~150-190, dưới
# ngưỡng 200, mặt nạ chữ luôn RỖNG → mọi khung "giống nhau" → câu đầu tiên bị
# dùng lại suốt 85s (đã xác nhận thật: kênh câu cá phụ đề vàng cam, chỉ 7/100
# khung được OCR thật). Viền đen vẫn là max(R,G,B) thấp nên không đổi.
_MAX_REUSE_FRAMES = 5  # dù khung "giống", tối đa ngần này khung liên tiếp là OCR thật lại 1 lần


def _frame_signature(frame_path: Path) -> np.ndarray:
    color = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
    if color is None:
        return np.zeros((_SIG_SIZE[1], _SIG_SIZE[0]), dtype=bool)
    img = color.max(axis=2)
    bright = (img >= 200).astype(np.uint8)
    dark = (img <= 70).astype(np.uint8)
    k = max(3, round(img.shape[0] / 60)) | 1  # độ dày viền tỉ lệ theo độ phân giải
    near_dark = cv2.dilate(dark, np.ones((k, k), np.uint8))
    text = (bright & near_dark) * 255
    small = cv2.resize(text, _SIG_SIZE, interpolation=cv2.INTER_AREA)
    return small > 20


def _frames_similar(a: np.ndarray, b: np.ndarray) -> bool:
    union = int(np.count_nonzero(a | b))
    if union < _SIG_EMPTY_CELLS:
        return True
    return np.count_nonzero(a ^ b) / union < _SIG_DIFF_RATIO


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


_RegionKey = tuple[str, int, int, Optional[tuple[float, float, float, float]]]
_region_cache: dict[_RegionKey, Optional[tuple[float, float, float, float]]] = {}
_region_key_locks: dict[_RegionKey, threading.Lock] = {}
_region_cache_guard = threading.Lock()


def detect_subtitle_region(
    video_path: Path,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    search_region: Optional[tuple[float, float, float, float]] = None,
) -> Optional[tuple[float, float, float, float]]:
    """Như `_detect_subtitle_region_uncached`, nhưng mỗi video (theo đường dẫn +
    kích thước + mtime + vùng quét) chỉ dò 1 lần mỗi phiên chạy: lượt dò sớm
    sau ingest và transcribe/export cùng cần vùng này, trước đây chạy SONG
    SONG 2 lượt (~3 phút mỗi lượt trên video 7 phút) — giờ lượt sau chờ và
    dùng lại kết quả lượt trước."""
    try:
        st = video_path.stat()
    except OSError:
        return _detect_subtitle_region_uncached(video_path, on_progress, search_region)
    key: _RegionKey = (str(video_path.resolve()), st.st_size, int(st.st_mtime), search_region)
    with _region_cache_guard:
        if key in _region_cache:
            return _region_cache[key]
        key_lock = _region_key_locks.setdefault(key, threading.Lock())
    with key_lock:
        with _region_cache_guard:
            if key in _region_cache:
                return _region_cache[key]
        result = _detect_subtitle_region_uncached(video_path, on_progress, search_region)
        with _region_cache_guard:
            _region_cache[key] = result
            _region_key_locks.pop(key, None)
        return result


def _detect_subtitle_region_uncached(
    video_path: Path,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    search_region: Optional[tuple[float, float, float, float]] = None,
) -> Optional[tuple[float, float, float, float]]:
    """Tự động dò vùng che phụ đề cũ cho tính năng "Xuất video trực tiếp" —
    OCR nguyên khung hình (không crop trước, vì chưa biết vùng chữ ở đâu) trên
    1 số khung lấy mẫu thưa (xem OCR_DETECT_REGION_FPS), gộp toạ độ mọi box
    chữ Hán phát hiện được (đã lọc độ tin cậy + logo tĩnh + phụ đề di động,
    xem các bước lọc bên dưới) thành 1 khung chữ nhật che suốt video. Trả
    None nếu: không phát hiện được chữ nào (video không có phụ đề cứng, chữ
    quá nhỏ/mờ), hoặc phụ đề phát hiện được là chữ DI ĐỘNG (không có 1 vùng
    cố định hợp lý để che). `search_region` (x,y,w,h phân số 0-1, tuỳ chọn) —
    thu hẹp vùng OCR quét TỪ ĐẦU (không phải lọc sau) — giảm hẳn khả năng đọc
    nhầm hình ảnh phức tạp (giáp/hoạ tiết nhân vật...) ở ngoài dải phụ đề
    thành 'chữ giả' (đã xác nhận thật: model OCR có thể đọc nhầm hoạ tiết góc
    cạnh phức tạp thành ký tự Hán với độ tin cậy CAO, không lọc được bằng
    ngưỡng confidence). None = quét nguyên khung hình như cũ."""
    if not video_path.exists():
        raise TranscribeOCRError(f"Không thấy video: {video_path}")

    engine = _load_region_engine()
    fps = config.OCR_DETECT_REGION_FPS
    # Ngưỡng chiều cao tối đa cho 1 box được tính vào vùng che — chặn 2 loại
    # không mong muốn: (1) box nhiễu méo mó chiếm gần hết khung (xem
    # _load_region_engine), (2) chữ cảm thán cỡ to kiểu hiệu ứng ("啊", "嘿"...)
    # cao tới ~40-56% khung hình, khác hẳn dải phụ đề hội thoại bình thường
    # (~5-10%) — đã xác nhận thật trên video mẫu, người dùng chốt: KHÔNG che
    # loại chữ hiệu ứng này (ít quan trọng hơn để dịch, che sẽ làm vùng che
    # rộng quá mức cần thiết).
    _MAX_BOX_HEIGHT_FRAC = 0.15
    # Ngưỡng độ tin cậy nhận diện chữ (RapidOCR trả `result.scores`, 0-1) —
    # box đọc từ khung hình đang GIỮA HIỆU ỨNG CHUYỂN ĐỘNG (mờ dần/nhoè khi
    # phụ đề xuất hiện/biến mất) luôn có score thấp hẳn so với khung đã rõ
    # nét, VÀ hình dạng box cũng méo/to hơn thật — bỏ trước khi tính vùng che
    # thay vì tự dựng cơ chế chọn "khung nét nhất" (tốn kém hơn nhiều, đạt
    # hiệu quả tương đương cho mục đích riêng của hàm này: chỉ cần TOẠ ĐỘ
    # đúng, không cần đọc đúng NỘI DUNG chữ).
    _MIN_CONFIDENCE = 0.7

    # LƯU Ý: `crop_region=None` cho `_extract_frames` KHÔNG có nghĩa "không
    # crop" — `_crop_filter` coi None là "chưa chọn tay, dùng mặc định crop
    # đáy 25%" (đúng ý cho transcribe_video, sai hoàn toàn ở đây nếu không
    # truyền `search_region` vì mình đang cần quét NGUYÊN khung hình để tìm
    # vị trí chữ). Phải truyền rõ (0,0,1,1) khi không có `search_region` —
    # vùng full-frame thật, không phải "để trống" — mới tắt được crop mặc
    # định (đã xác nhận thật: thiếu dòng này khiến mọi toạ độ box tính sai
    # theo tỉ lệ dải crop 25% đáy thay vì theo khung hình gốc).
    crop_used = search_region or (0.0, 0.0, 1.0, 1.0)
    sx, sy, sw, sh = crop_used

    with tempfile.TemporaryDirectory(prefix="ocr_region_") as tmp:
        frames = _extract_frames(video_path, Path(tmp), fps, crop_region=crop_used)
        total = len(frames)
        if total == 0:
            raise TranscribeOCRError("Không lấy được khung hình nào từ video")

        boxes: list[tuple[float, float, float, float, str]] = []  # (bx0, bx1, by0, by1, text) — toạ độ FULL-FRAME
        for i, frame in enumerate(frames):
            if on_progress:
                on_progress(i, total, f"Dò vùng chữ {i + 1}/{total}")
            img = cv2.imread(str(frame))
            if img is None:
                continue
            h, w = img.shape[:2]
            with _region_engine_lock:
                result = engine(str(frame))
            if not result.txts:
                continue
            scores = result.scores or [1.0] * len(result.txts)
            for box, text, score in zip(result.boxes, result.txts, scores):
                text = text.strip()
                if not _has_cjk(text):
                    continue
                if score is not None and score < _MIN_CONFIDENCE:
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
                # Quy đổi từ toạ độ TRONG VÙNG QUÉT (đã crop bởi ffmpeg) sang
                # toạ độ FULL-FRAME thật — mọi nơi khác trong hàm (và giá trị
                # trả về) đều tính theo full-frame.
                boxes.append((sx + bx0 * sw, sx + bx1 * sw, sy + by0 * sh, sy + by1 * sh, text, float(score) if score is not None else 1.0))

    if not boxes:
        return None

    # Nhiễu KHÔNG bị lọc bởi ngưỡng chiều cao ở trên vẫn còn (chữ Hán giả đọc
    # nhầm từ hoạ tiết cảnh/UI game, kích thước bình thường nhưng rải rác khắp
    # khung hình — đã xác nhận thật, vd đọc nhầm '中'/'新'/'江' ở nhiều vị trí y
    # khác hẳn nhau) — phụ đề THẬT luôn lặp lại ở gần đúng 1 DẢI Y cố định
    # xuyên suốt video, còn nhiễu thì rải rác không lặp lại đúng chỗ. Gộp theo
    # cụm y-center (histogram 20 bin) thay vì hợp mù quáng mọi box.
    _NUM_BINS = 20
    bin_counts = [0] * _NUM_BINS
    for bx0, bx1, by0, by1, text, _score in boxes:
        yc = (by0 + by1) / 2
        bin_idx = min(_NUM_BINS - 1, int(yc * _NUM_BINS))
        bin_counts[bin_idx] += 1
    # Cửa sổ dung sai quanh 1 bin — đủ rộng để không cắt rời phụ đề THẬT rơi ở
    # bin liền kề do sai số làm tròn, không đủ rộng để lẫn nhiễu (đã xác nhận
    # thật trên video mẫu: dải phụ đề thật cực kỳ ổn định, y-center chỉ dao
    # động ~0.005 giữa các khung, rơi gọn trong 1 bin).
    _TOLERANCE_BINS = 1

    def _boxes_in_bin(bin_idx: int) -> list[tuple[float, float, float, float, str]]:
        lo = (bin_idx - _TOLERANCE_BINS) / _NUM_BINS
        hi = (bin_idx + 1 + _TOLERANCE_BINS) / _NUM_BINS
        return [b for b in boxes if lo <= (b[2] + b[3]) / 2 <= hi]

    # Logo/watermark tĩnh (tên kênh, tag studio...) thường in y NGUYÊN VĂN ở
    # MỌI khung hình lấy mẫu — khác phụ đề lời thoại LUÔN đổi nội dung theo
    # từng câu. Xét từng bin theo thứ tự đông nhất trước, bỏ qua bin nào chữ
    # bị lặp lại gần như y hệt (logo), lấy bin ĐẦU TIÊN có nội dung đủ đa dạng
    # (phụ đề thật) — tránh nhầm che logo thay vì phụ đề (đã gặp thật: logo
    # xuất hiện ở MỌI khung nên đếm còn đông hơn cả phụ đề, nếu chỉ đếm số
    # lượng thô sẽ chọn nhầm logo làm "dải chữ chính").
    _MAX_REPEAT_FRAC = 0.6  # > 60% box cùng 1 nội dung y hệt → coi là logo tĩnh
    # Phụ đề DI ĐỘNG (chữ hiệu ứng bay/nhảy khắp khung hình, karaoke động...)
    # và CHỮ TRONG CẢNH (biển hiệu cửa hàng, bảng giá...) khác phụ đề hội
    # thoại thường: dù đổi độ dài câu, TÂM ngang (center-x) của phụ đề hội
    # thoại vẫn dao động RẤT ít quanh 1 vị trí quen thuộc (thường giữa khung
    # hoặc lề trái cố định) — còn 2 loại kia thì tâm-x trải rộng/rải rác.
    _MAX_CENTER_X_STD = 0.12  # phân số chiều rộng khung hình
    # Dải được chọn phải có ít nhất ngần này box — 1-2 box lẻ ở 1 dải hiếm
    # hoi (nhiễu) không đủ tin để lấy làm vùng phụ đề cho cả video.
    _MIN_GROUP_BOXES = 3
    # Hộp thấp hơn ngần này × cỡ chữ trung vị của dải VÀ tâm lệch xa hơn
    # _OUTLIER_CENTER_DX so với tâm trung vị = chữ khác loại (watermark, chú
    # thích nhỏ), không phải phụ đề. Đo thật: watermark cao 0.55-0.73 lần trung
    # vị, tâm lệch ~0.4; phụ đề hơi nhỏ nhất gặp được 0.80 lần nhưng tâm lệch 0.
    _MIN_HEIGHT_RATIO = 0.8
    _OUTLIER_CENTER_DX = 0.1
    candidate_bins = sorted(range(_NUM_BINS), key=lambda b: bin_counts[b], reverse=True)
    matched: list[tuple[float, float, float, float, str]] = []
    for bin_idx in candidate_bins:
        if bin_counts[bin_idx] == 0:
            break
        group = _boxes_in_bin(bin_idx)
        if len(group) < _MIN_GROUP_BOXES:
            continue
        texts = [b[4] for b in group]
        most_common_count = max(texts.count(t) for t in set(texts))
        if most_common_count / len(texts) > _MAX_REPEAT_FRAC:
            continue  # nghi logo tĩnh — thử bin đông kế tiếp
        # Tâm-x rải rác → chữ trong cảnh/di động, thử dải đông KẾ TIẾP thay
        # vì bỏ cuộc cả video. Đã xác nhận thật (kênh câu cá quay ngoài phố):
        # biển hiệu cửa hàng ở y≈0.33 cho NHIỀU box hơn cả dải phụ đề thật ở
        # y≈0.74 — bản cũ chọn dải biển hiệu, thấy tâm-x rải rác rồi trả None
        # luôn, transcribe lùi về crop 25% đáy cắt đôi dòng phụ đề → OCR ra
        # chữ rác, 1 câu kéo dài 85s.
        # Bỏ chữ NHỎ hẳn so với cỡ chữ phổ biến của dải trước khi xét vị trí —
        # watermark di động (vd "@老黑说趣" trôi khắp khung, có lúc xuống đúng
        # dải phụ đề) cao chỉ ~0.6 lần phụ đề. Đã gặp thật: 3 hộp watermark ở
        # tâm x≈0.1 lẫn vào 23 hộp phụ đề căn giữa (tâm 0.5) đẩy std tâm lên
        # 0.123 → bỏ cả dải, video xuất không che gì; nới ngưỡng thì vùng che lại
        # kéo rộng sang trái tới chỗ watermark.
        # Chỉ bỏ khi VỪA nhỏ VỪA lệch xa vị trí ngang chung: chỉ xét cỡ chữ thì
        # loại nhầm phụ đề thật hơi nhỏ (đo thật: 1 câu ở 0.80 lần trung vị,
        # sát ngưỡng) — câu đó vẫn nằm đúng giữa dải, watermark thì không.
        median_h = float(np.median([b[3] - b[2] for b in group]))
        median_cx = float(np.median([(b[0] + b[1]) / 2 for b in group]))
        group = [
            b for b in group
            if (b[3] - b[2]) >= _MIN_HEIGHT_RATIO * median_h or abs((b[0] + b[1]) / 2 - median_cx) <= _OUTLIER_CENTER_DX
        ]
        if len(group) < _MIN_GROUP_BOXES:
            continue
        std_x = float(np.std([(b[0] + b[1]) / 2 for b in group]))
        if std_x > _MAX_CENTER_X_STD:
            logger.info(
                "detect_subtitle_region: bỏ dải y≈{:.2f} (tâm-X rải rác, std={:.3f}) — thử dải kế tiếp",
                (bin_idx + 0.5) / _NUM_BINS, std_x,
            )
            continue
        matched = group
        break

    if not matched:
        logger.warning("detect_subtitle_region: không có dải chữ nào giống phụ đề hội thoại — bỏ qua auto-dò")
        return None

    def _percentile(values: list[float], p: float) -> float:
        s = sorted(values)
        idx = min(len(s) - 1, max(0, round(p * (len(s) - 1))))
        return s[idx]

    # Che KHÍT theo phân vị 10-90% thay vì hợp min/max thô — 1 khung lấy mẫu
    # hoạ hoằn có câu dài/lệch bất thường trước đây kéo giãn cả vùng che cho
    # SUỐT video, dù đa số khung hình chữ hẹp hơn nhiều — nhìn "che quá to"
    # đúng như người dùng phản ánh. Đổi coordinate thấp nhất/cao nhất thành
    # phân vị 10%/90% giữ vùng che sát với kích cỡ chữ THỰC TẾ ở phần lớn
    # khung hình, chấp nhận có thể hụt vài khung ngoại lệ (đổi lấy khít hơn).
    # Đệm DỌC theo chiều cao dòng chữ thật (không theo chiều cao khung hình):
    # đệm cũ 1.5% khung hình MỖI cạnh = ~61px tổng trên video cao 2048px, gần
    # bằng cả dòng chữ (~73px) — người dùng phản ánh dải che "quá rộng về
    # chiều cao so với text cũ". Đo thật trên video xam-xi-du: dải che 155px =
    # 2.1x dòng chữ; đệm 15% chiều cao chữ → 118px (1.6x), vẫn che trọn 94%
    # box chữ gốc (các phương án chặt hơn lọt chữ nhiều hơn: 86-88%).
    # Sửa lần 2 → 0: box OCR vốn đã rộng hơn nét chữ thật ~1% khung hình mỗi
    # cạnh (đo thật trên kênh câu cá: box 0.709-0.782, chữ kể cả viền chỉ
    # 0.718-0.774), cộng thêm đệm làm lõi dày gần gấp đôi dòng chữ; mép mềm
    # (export_direct.BLUR_FEATHER_RATIO) đã phủ phần lệch nhỏ còn lại. Người
    # dùng phản ánh vùng mờ "chiều cao lớn quá, muốn sát text".
    # CHIỀU CAO: canh theo DÒNG CHỮ TIÊU BIỂU (trung vị) thay vì phân vị 10-90%
    # của mép trên/dưới MỌI box. Phụ đề chữ MÀU (vàng/cam, tương phản thấp + có
    # quầng/viền trắng dày) làm bộ dò chữ RapidOCR trả box LỎNG, chiều cao dao
    # động mạnh giữa các khung — lấy 10-90% thì chỉ vài box "phình" đã kéo dải
    # che cao hơn hẳn dòng chữ thật (người dùng phản ánh "mờ quá rộng, không che
    # sát"). Mọi dòng phụ đề nằm cùng 1 baseline nên canh giữa theo tâm-y trung
    # vị + cao bằng chiều cao chữ trung vị là ÔM SÁT nhất mà vẫn phủ dòng điển
    # hình; box phình bất thường không kéo giãn được nữa.
    # Đệm nhỏ 8% chiều cao chữ mỗi phía — đủ phủ viền/quầng mà không "quá rộng"
    # như đệm 15% cũ (mép mềm export_direct.BLUR_FEATHER_RATIO phủ nốt phần lệch).
    # TÁCH phụ đề khỏi CHỮ TRONG CẢNH (chữ Hán in trong khung: trang sách, biển
    # hiệu, bìa sản phẩm...). Đã gặp thật (video con thú đặt trên quyển sách):
    # OCR đọc nguyên đoạn văn in trên sách (5-6 dòng, điểm 0.8-0.94) nằm NGAY
    # DƯỚI phụ đề, tâm-x xấp xỉ giữa nên bộ lọc std-x không cắt sạch → kéo dải
    # che xuống trùm cả chữ sách. Phụ đề hội thoại khác hẳn: luôn là dòng RÕ NÉT
    # NHẤT (font chuẩn + viền → điểm OCR cao nhất) và CĂN GIỮA ỔN ĐỊNH. Neo vào
    # box tin cậy CAO NHẤT rồi chỉ giữ các box sát nó theo CẢ y (cùng dòng) LẪN
    # tâm-x (cùng căn lề) — loại các dòng đoạn văn khác dòng / tâm trôi.
    anchor = max(matched, key=lambda b: b[5])
    anchor_cy = (anchor[2] + anchor[3]) / 2
    anchor_cx = (anchor[0] + anchor[1]) / 2
    anchor_h = anchor[3] - anchor[2]
    refined = [
        b for b in matched
        if abs((b[2] + b[3]) / 2 - anchor_cy) <= 0.7 * anchor_h
        and abs((b[0] + b[1]) / 2 - anchor_cx) <= _OUTLIER_CENTER_DX
    ]
    if refined:
        matched = refined

    _PAD_Y_TEXT_FRAC = 0.08
    heights = [b[3] - b[2] for b in matched]
    centers_y = [(b[2] + b[3]) / 2 for b in matched]
    median_h = float(np.median(heights))
    median_cy = float(np.median(centers_y))
    half_h = median_h * (0.5 + _PAD_Y_TEXT_FRAC)
    # CHIỀU NGANG: vẫn phủ theo câu dài (phân vị 10-90% mép trái/phải) — siết
    # hẹp hơn thì câu dài nhất sẽ bị LÒI CHỮ ra 2 bên, tệ hơn là che hơi rộng.
    # Bỏ đệm ngang thừa (_PAD_FRAC) cho sát 2 mép hơn một chút.
    x0 = max(0.0, _percentile([b[0] for b in matched], 0.10))
    x1 = min(1.0, _percentile([b[1] for b in matched], 0.90))
    y0 = max(0.0, median_cy - half_h)
    y1 = min(1.0, median_cy + half_h)
    return (x0, y0, x1 - x0, y1 - y0)


def detect_subtitle_visibility(
    video_path: Path,
    region: tuple[float, float, float, float],
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> list[tuple[float, float]]:
    """Dò các khoảng thời gian [start_s, end_s] mà phụ đề cứng THẬT SỰ hiện
    trên khung hình trong `region` đã dò được (`detect_subtitle_region`) —
    chỉ cần biết CÓ/KHÔNG có chữ, KHÔNG cần đọc đúng nội dung. Cố tình TÁCH
    HẲN khỏi `transcribe_video` (đọc lời THOẠI) — đã xác nhận thật có video
    lời thoại kể 1 chuyện, chữ trên màn hình lại là chuyện khác hẳn (audio
    và phụ đề cứng không đồng bộ nội dung) — nên KHÔNG dùng mốc thời gian
    của transcribe (dù bằng Whisper hay OCR) để suy ra lúc chữ hiện/ẩn, phải
    tự dò riêng. Dùng cho tính năng che phụ đề THEO THỜI GIAN, hoạt động với
    MỌI engine transcribe (khác hẳn hạn chế trước đây: chỉ áp dụng được khi
    engine transcribe là OCR). Trả [] nếu không dò được khoảng nào (không
    chặn export — gọi nơi dùng tự hiểu [] = che suốt video như cũ)."""
    if not video_path.exists():
        raise TranscribeOCRError(f"Không thấy video: {video_path}")

    engine = _load_visibility_engine()
    fps = config.OCR_SAMPLE_FPS
    frame_dur = 1.0 / fps
    _MIN_CONFIDENCE = 0.7

    with tempfile.TemporaryDirectory(prefix="ocr_visibility_") as tmp:
        # Crop THẲNG vào region đã dò được — vừa nhanh (ít pixel hơn hẳn quét
        # full-frame) vừa chính xác hơn (không lẫn chữ/hoạ tiết ngoài vùng).
        frames = _extract_frames(video_path, Path(tmp), fps, crop_region=region)
        total = len(frames)
        if total == 0:
            return []

        presence: list[bool] = []
        for i, frame in enumerate(frames):
            if on_progress:
                on_progress(i, total, f"Dò thời điểm hiện chữ {i + 1}/{total}")
            img = cv2.imread(str(frame))
            if img is None:
                presence.append(False)
                continue
            with _visibility_engine_lock:
                result = engine(str(frame))
            has_text = False
            if result.txts:
                scores = result.scores or [1.0] * len(result.txts)
                for text, score in zip(result.txts, scores):
                    if _has_cjk(text.strip()) and (score is None or score >= _MIN_CONFIDENCE):
                        has_text = True
                        break
            presence.append(has_text)

    windows: list[tuple[float, float]] = []
    start_idx: Optional[int] = None
    for i, has in enumerate(presence):
        if has and start_idx is None:
            start_idx = i
        elif not has and start_idx is not None:
            windows.append((start_idx * frame_dur, i * frame_dur))
            start_idx = None
    if start_idx is not None:
        windows.append((start_idx * frame_dur, len(presence) * frame_dur))
    if not windows:
        return []

    # Đệm nhỏ 2 đầu mỗi khoảng — OCR đôi khi trễ đúng 1 khung mẫu lúc chữ
    # bắt đầu/kết thúc hiện (cùng lý do `_PAD_FRAC` ở detect_subtitle_region).
    pad_s = frame_dur * 0.5
    padded = [(max(0.0, s - pad_s), e + pad_s) for s, e in windows]

    # Gộp các khoảng cách nhau dưới 1 khung mẫu — tránh chớp tắt liên tục do
    # OCR đọc trượt đúng 1 khung giữa 2 câu phụ đề liền kề (ở fps lấy mẫu
    # này không đủ để phân biệt khoảng trống thật sự ngắn).
    merged: list[tuple[float, float]] = []
    for s, e in padded:
        if merged and s - merged[-1][1] <= frame_dur:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged


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
        reused = 0
        # Mặt nạ chữ chỉ "thấy" phụ đề chữ sáng có viền tối. Kiểu phụ đề khác
        # (vd chữ vàng nhạt viền cam + viền ngoài trắng, KHÔNG có viền tối —
        # đã gặp thật) cho mặt nạ RỖNG dù đang có chữ → mọi khung bị coi là
        # giống nhau. OCR thật đọc ra chữ mà mặt nạ trống = mặt nạ mù với kiểu
        # phụ đề của video này → tắt hẳn bỏ-qua-khung, OCR mọi khung còn lại.
        sig_blind = False
        for i, frame in enumerate(frames):
            if on_progress:
                on_progress(i, total, f"Đọc phụ đề khung {i + 1}/{total}")
            sig = _frame_signature(frame)
            if (
                not sig_blind
                and prev_sig is not None
                and _frames_similar(sig, prev_sig)
                and reused < _MAX_REUSE_FRAMES
            ):
                text = prev_text  # khung gần như giống hệt khung trước — khỏi OCR lại
                reused += 1
            else:
                text = _ocr_text(engine, frame)
                ocr_calls += 1
                reused = 0
                if text and not sig_blind and int(np.count_nonzero(sig)) < _SIG_EMPTY_CELLS:
                    sig_blind = True
                    logger.info("OCR: mặt nạ chữ không nhận ra kiểu phụ đề của video này — OCR mọi khung từ khung {}", i + 1)
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
