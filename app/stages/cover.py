from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from loguru import logger
from PIL import Image, ImageDraw, ImageFont

from .. import config
from ..jobs import current_job
from . import transcribe_ocr as ocr_stage

# Ảnh bìa tiếng Việt. Nhiều video Douyin mở đầu bằng 2-4 khung "ảnh bìa" (tiêu
# đề chữ Trung to) trước khi cắt sang cảnh thật — khoảng 0.1s, mắt thường
# không kịp thấy nhưng TikTok lấy khung ĐẦU làm ảnh bìa mặc định (đã xác nhận
# thật trên TikTok Studio). Ở đây: nhận diện các khung đó, tìm chữ TIÊU ĐỀ chèn
# lên (bỏ chữ trong cảnh như biển hiệu), xoá tiêu đề bằng LaMa (lama_inpaint —
# người dùng chốt sau khi so với làm mờ và chép nền từ cảnh trùng trong video),
# đặt 1 khung "viên thuốc" màu với tiêu đề Việt cỡ chữ cố định, rồi
# export_direct phủ ảnh này lên đúng các khung ảnh bìa — thời lượng video không đổi.

FONT_PATH = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "Baloo2-ExtraBold.ttf"
DEFAULT_BG = "#F2555A"
DEFAULT_FG = "#FFFFFF"
# Cỡ chữ CỐ ĐỊNH = tỉ lệ theo CẠNH NGẮN khung hình (video dọc/ngang cùng cỡ).
FONT_SIZE_RATIO = 0.06
MAX_LINES = 2
# Chênh lệch trung bình (thang xám, ảnh thu nhỏ) giữa 2 khung liền nhau coi là
# CẮT CẢNH. Đo thật trên 21 video: ảnh bìa → cảnh thật 38-88, trong cảnh <25.
_CUT_DIFF = 25.0
_MAX_COVER_FRAMES = 12  # ảnh bìa đo được 2-4 khung; tìm trong ~0.4s đầu

# Khoảng cách màu (Lab) tối thiểu tới MỌI màu nền để 1 pixel được coi là nét
# chữ tiêu đề — xem _glyph_mask.
_GLYPH_BG_DIST = 22.0

VIET_MARKS ="ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ"


class CoverError(RuntimeError):
    pass


@dataclass
class TitleBox:
    x0: int
    y0: int
    x1: int
    y1: int
    text: str

    @property
    def thickness(self) -> int:
        """Cỡ chữ ≈ cạnh ngắn của hộp — đúng cho cả tiêu đề ngang lẫn dọc."""
        return min(self.x1 - self.x0, self.y1 - self.y0)


def hex_rgb(value: str, fallback: str) -> tuple[int, int, int]:
    v = (value or fallback).strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    try:
        return tuple(int(v[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return hex_rgb(fallback, fallback)


def detect_cover(video_path: Path) -> tuple[int, float, Optional[np.ndarray]]:
    """(số khung ảnh bìa ở đầu video — 0 = không có, fps, ảnh khung đầu)."""
    cap = cv2.VideoCapture(str(video_path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frames: list[np.ndarray] = []
        for _ in range(_MAX_COVER_FRAMES + 1):
            ok, f = cap.read()
            if not ok:
                break
            frames.append(f)
    finally:
        cap.release()
    if not frames:
        return 0, fps, None
    small = [cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (64, 112)).astype(np.float32) for f in frames]
    for i in range(len(small) - 1):
        if float(np.abs(small[i] - small[i + 1]).mean()) > _CUT_DIFF:
            return i + 1, fps, frames[0]
    return 0, fps, frames[0]


def find_title_boxes(img: np.ndarray) -> list[TitleBox]:
    """Tiêu đề = dòng chữ Hán TO NHẤT trên ảnh bìa + các dòng thẳng hàng với
    nó (tâm ngang lệch < 15% khung) cách không quá 1.5 lần cỡ chữ dòng chính.
    Chữ ở xa/lệch (biển hiệu, poster trong cảnh) bị bỏ — đã thử "so với khung
    cảnh thật ngay sau" nhưng hỏng vì phụ đề đầu video hay trùng chữ với tiêu
    đề. Phụ đề của chính video nằm sát dưới tiêu đề cũng được gộp — cố ý: xoá
    luôn để ảnh bìa không còn chữ Trung. Dòng chính đứng đầu."""
    H, W = img.shape[:2]
    engine = ocr_stage._load_region_engine()
    with ocr_stage._region_engine_lock:
        res = engine(img)
    boxes: list[TitleBox] = []
    if res.txts:
        for box, text, score in zip(res.boxes, res.txts, res.scores):
            text = text.strip()
            if not ocr_stage._has_cjk(text) or score < 0.6:
                continue
            b = TitleBox(int(box[:, 0].min()), int(box[:, 1].min()), int(box[:, 0].max()), int(box[:, 1].max()), text)
            if b.y1 - b.y0 >= 0.02 * H:
                boxes.append(b)
    if not boxes:
        return []
    main = max(boxes, key=lambda b: b.thickness)
    mh, mcx = main.thickness, (main.x0 + main.x1) / 2
    group = [main]
    for b in boxes:
        if b is main:
            continue
        gap = max(b.y0 - main.y1, main.y0 - b.y1, 0)
        if gap < 1.5 * mh and abs((b.x0 + b.x1) / 2 - mcx) < 0.15 * W :
            group.append(b)
    return group


def write_headline(zh_lines: list[str], video_title: str, context: str, avoid: Optional[str] = None) -> str:
    """Tiêu đề Việt giật tít nhưng ĐÚNG NGHĨA (người dùng yêu cầu), có dấu.
    `avoid`: tiêu đề cũ người dùng muốn đổi — yêu cầu viết cách khác."""
    from . import translate as tr

    avoid_line = f"\nViết KHÁC HẲN tiêu đề cũ này (đổi cách diễn đạt/góc giật tít): {avoid}" if avoid else ""
    prompt = (
        "Viết 1 tiêu đề ảnh bìa bằng tiếng Việt CÓ ĐẦY ĐỦ DẤU cho video dưới đây. Yêu cầu: giật tít, gây tò mò "
        "nhưng PHẢI ĐÚNG NGHĨA — chỉ dùng ý có trong chữ ảnh bìa gốc và nội dung video, tuyệt đối không bịa thêm "
        "chi tiết/con số không có. 4-9 chữ, viết hoa chữ cái đầu câu như câu bình thường (KHÔNG in hoa toàn bộ), "
        "được dùng 1 dấu ! hoặc ? ở cuối, không emoji/hashtag/ngoặc kép. Tên riêng dùng đúng bảng tên riêng; "
        "tên quốc tế (Pokémon, iPhone...) giữ tên tiếng Anh, không phiên âm Hán Việt; tên loài vật/cây dùng tên "
        "tiếng Việt thông dụng.\n"
        f"Chữ trên ảnh bìa gốc (tiếng Trung, dòng đầu là dòng chính): {' / '.join(zh_lines) or '(không có)'}\n"
        f"Tiêu đề video: {video_title}\n{context}{avoid_line}"
    )
    schema = {"type": "object", "properties": {"vi": {"type": "string"}}, "required": ["vi"]}
    vi = ""
    for attempt in range(2):
        p = prompt if attempt == 0 else prompt + "\nLần trước bạn viết thiếu dấu — NHỚ VIẾT CÓ DẤU."
        vi = str(tr._generate(p, schema=schema).get("vi") or "").strip().strip('"“”')
        if any(c in VIET_MARKS for c in vi.lower()):
            break
    if not vi:
        raise CoverError("Gemini không trả về tiêu đề")
    return vi


def _glyph_mask(img: np.ndarray, boxes: list[TitleBox]) -> np.ndarray:
    """Mặt nạ đúng NÉT chữ (+ viền, quầng sáng) thay vì cả hộp chữ nhật — LaMa
    chỉ phải lấp nét, nền lộ ra giữa các chữ giữ nguyên làm ngữ cảnh. Đã so
    thật: mặt nạ hộp chữ nhật làm LaMa vẽ bù cả mảng lớn → loang xám, méo mặt
    người gần tiêu đề. Tách nét: lấy BẢNG MÀU NỀN từ dải bao quanh cả nhóm chữ
    (trừ mọi hộp chữ — dải của riêng từng hộp bị lẫn chữ hộp bên cạnh, tiêu đề
    2 dòng/phụ đề nằm sát nhau), pixel trong hộp khác xa mọi màu nền = nét chữ,
    rồi nới ra phủ viền/quầng mờ (sót viền là LaMa vẽ lại bóng chữ). Hộp tách
    không ra (nét quá ít/quá nhiều) thì lùi về che cả hộp."""
    H, W = img.shape[:2]
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    rects = []
    for b in boxes:
        pad = int(b.thickness * 0.12) + 4
        rects.append((max(0, b.x0 - pad), max(0, b.y0 - pad), min(W, b.x1 + pad), min(H, b.y1 + pad)))
    union = np.zeros((H, W), np.uint8)
    for x0, y0, x1, y1 in rects:
        union[y0:y1, x0:x1] = 255
    r = int(max(b.thickness for b in boxes) * 0.3) + 8
    ring = (cv2.dilate(union, np.ones((2 * r + 1, 2 * r + 1), np.uint8)) > 0) & (union == 0)
    px_bg = lab[ring]
    mask = np.zeros((H, W), np.uint8)
    centers = None
    if len(px_bg) > 200:
        rng = np.random.default_rng(0)
        sample = px_bg[rng.choice(len(px_bg), min(len(px_bg), 20000), replace=False)]
        _, _, centers = cv2.kmeans(
            sample, 12, None, (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0), 2, cv2.KMEANS_PP_CENTERS
        )
    for b, (x0, y0, x1, y1) in zip(boxes, rects):
        t = b.thickness
        glyph = None
        if centers is not None and x1 > x0 and y1 > y0:
            region = lab[y0:y1, x0:x1].reshape(-1, 3)
            d = np.sqrt(((region[:, None, :] - centers[None]) ** 2).sum(-1)).min(1)
            g = (d > _GLYPH_BG_DIST).reshape(y1 - y0, x1 - x0).astype(np.uint8) * 255
            frac = float((g > 0).mean())
            if 0.08 <= frac <= 0.92:
                close = max(3, int(t * 0.06)) | 1
                g = cv2.morphologyEx(g, cv2.MORPH_CLOSE, np.ones((close, close), np.uint8))
                grow = max(5, int(t * 0.12)) | 1
                glyph = np.zeros((H, W), np.uint8)
                glyph[y0:y1, x0:x1] = g
                glyph = cv2.dilate(glyph, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (grow, grow)))
        if glyph is None:
            cv2.rectangle(mask, (x0, y0), (x1, y1), 255, -1)
        else:
            mask = np.maximum(mask, glyph)
    return mask


def _title_mask(shape: tuple[int, int], boxes: list[TitleBox]) -> np.ndarray:
    """Mặt nạ vùng tiêu đề cũ — mỗi dòng nới 25% CỠ CHỮ để phủ cả viền
    trắng/bóng đổ dày của kiểu chữ ảnh bìa Douyin. Theo cỡ chữ chứ không theo
    chiều cao hộp: tiêu đề DỌC có hộp cao ~75% khung, nới theo chiều cao thì
    lấn sang cả vùng phụ đề (đã gặp thật)."""
    H, W = shape
    mask = np.zeros((H, W), np.uint8)
    for b in boxes:
        pad = int(b.thickness * 0.25) + 6
        cv2.rectangle(mask, (max(0, b.x0 - pad), max(0, b.y0 - pad)), (min(W, b.x1 + pad), min(H, b.y1 + pad)), 255, -1)
    return mask


# Cạnh dài tối đa của vùng cắt đưa vào LaMa. Đã so thật 640/768/1024/1536:
# big-lama train ở ~512px — từ 1536 trở lên phần vẽ bù thành mảng xám nhiễu
# (không "nhìn" đủ xa để lấy nền); 1024 + mặt nạ theo nét chữ (_glyph_mask)
# cho kết quả sạch nhất, giữ mặt người gần tiêu đề ít méo nhất. CPU ~10-20s.
_LAMA_MAX_SIDE = 1024
_lama_model = None
_lama_lock = threading.Lock()


def _load_lama():
    global _lama_model
    with _lama_lock:
        if _lama_model is None:
            lama_path = config.lama_model_path()
            if not lama_path.exists():
                raise CoverError(f"Thiếu model LaMa: {lama_path}")
            import torch

            _lama_model = torch.jit.load(str(lama_path), map_location="cpu").eval()
        return _lama_model


def lama_inpaint(img: np.ndarray, boxes: list[TitleBox]) -> np.ndarray:
    """Xoá tiêu đề cũ bằng LaMa (big-lama, người dùng chốt sau khi so với làm
    mờ và chép nền cảnh trùng). Chạy trên vùng cắt quanh mặt nạ + ngữ cảnh
    xung quanh (LaMa cần thấy nền để vẽ bù), chỉ thu nhỏ khi vùng cắt vượt
    _LAMA_MAX_SIDE, rồi CHỈ thay đúng vùng mặt nạ (mép mềm) — ngoài mặt nạ
    giữ nguyên pixel gốc, không bị nhoè do thu/phóng."""
    import torch

    H, W = img.shape[:2]
    mask = _glyph_mask(img, boxes)
    ys, xs = np.where(mask > 0)
    mx0, mx1, my0, my1 = xs.min(), xs.max(), ys.min(), ys.max()
    ctx = int(max(mx1 - mx0, my1 - my0) * 0.5) + 32
    cx0, cy0 = max(0, mx0 - ctx), max(0, my0 - ctx)
    cx1, cy1 = min(W, mx1 + ctx), min(H, my1 + ctx)
    crop, cmask = img[cy0:cy1, cx0:cx1], mask[cy0:cy1, cx0:cx1]
    scale = min(1.0, _LAMA_MAX_SIDE / max(crop.shape[:2]))
    if scale < 1:
        crop_s = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        mask_s = cv2.resize(cmask, (crop_s.shape[1], crop_s.shape[0]), interpolation=cv2.INTER_NEAREST)
    else:
        crop_s, mask_s = crop, cmask
    h, w = crop_s.shape[:2]
    ph, pw = (8 - h % 8) % 8, (8 - w % 8) % 8
    rgb = np.pad(cv2.cvtColor(crop_s, cv2.COLOR_BGR2RGB), ((0, ph), (0, pw), (0, 0)), mode="symmetric")
    m = np.pad(mask_s, ((0, ph), (0, pw)), mode="symmetric")
    t_img = torch.from_numpy(rgb).permute(2, 0, 1).float().unsqueeze(0) / 255.0
    t_mask = (torch.from_numpy(m).float()[None, None] > 0).float()
    model = _load_lama()
    job = current_job()
    if job is not None:
        job.raise_if_cancelled()
    with _lama_lock, torch.inference_mode():
        out = model(t_img, t_mask)[0].permute(1, 2, 0).numpy()
    out = cv2.cvtColor(np.clip(out * 255, 0, 255).astype(np.uint8)[:h, :w], cv2.COLOR_RGB2BGR)
    if scale < 1:
        out = cv2.resize(out, (crop.shape[1], crop.shape[0]), interpolation=cv2.INTER_LANCZOS4)
    soft = cv2.GaussianBlur(cmask.astype(np.float32) / 255, (0, 0), 3)[..., None]
    res = img.copy()
    res[cy0:cy1, cx0:cx1] = (crop * (1 - soft) + out * soft).astype(np.uint8)
    return res


def _sparkle(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color) -> None:
    k = 0.22
    draw.polygon(
        [(cx, cy - r), (cx + r * k, cy - r * k), (cx + r, cy), (cx + r * k, cy + r * k),
         (cx, cy + r), (cx - r * k, cy + r * k), (cx - r, cy), (cx - r * k, cy - r * k)],
        fill=color,
    )


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: float) -> list[str]:
    words = text.split()
    lines = [text]
    for n in range(1, MAX_LINES + 1):
        per = -(-len(words) // n)  # chia đều số chữ mỗi dòng cho cân
        lines = [" ".join(words[i : i + per]) for i in range(0, len(words), per)]
        if max(draw.textlength(ln, font=font) for ln in lines) <= max_w:
            return lines
    return lines


def render_cover(clean: np.ndarray, anchor: Optional[TitleBox], title: str, bg: str, fg: str) -> Image.Image:
    """Khung viên thuốc (mẫu người dùng chọn) đặt ở vị trí dòng tiêu đề chính
    cũ, trên nền ĐÃ xoá tiêu đề (`clean`)."""
    H, W = clean.shape[:2]
    pil = Image.fromarray(cv2.cvtColor(clean, cv2.COLOR_BGR2RGB)).convert("RGBA")
    size = max(16, int(min(W, H) * FONT_SIZE_RATIO))
    font = ImageFont.truetype(str(FONT_PATH), size)
    measure = ImageDraw.Draw(pil)
    pad_x, pad_y = int(size * 1.1), int(size * 0.4)
    lines = _wrap(measure, title, font, W * 0.86 - 2 * pad_x)
    asc, desc = font.getmetrics()
    line_h = int((asc + desc) * 0.95)
    tw = max(measure.textlength(ln, font=font) for ln in lines)
    box_w, box_h = int(min(W * 0.92, tw + 2 * pad_x)), int(line_h * len(lines) + 2 * pad_y)
    if anchor is not None:
        cx, cy = (anchor.x0 + anchor.x1) / 2, (anchor.y0 + anchor.y1) / 2
    else:
        cx, cy = W / 2, H * 0.72
    m = int(W * 0.04)
    bx0 = int(min(max(cx - box_w / 2, m), W - m - box_w))
    by0 = int(min(max(cy - box_h / 2, m), H - m - box_h))
    radius = box_h // 2 if len(lines) == 1 else int(size * 0.9)
    shadow = Image.new("RGBA", pil.size, (0, 0, 0, 0))
    off = int(size * 0.12)
    ImageDraw.Draw(shadow).rounded_rectangle((bx0, by0 + off, bx0 + box_w, by0 + box_h + off), radius=radius, fill=(0, 0, 0, 70))
    pil = Image.alpha_composite(pil, shadow)
    draw = ImageDraw.Draw(pil)
    fg_rgb = hex_rgb(fg, DEFAULT_FG)
    draw.rounded_rectangle((bx0, by0, bx0 + box_w, by0 + box_h), radius=radius, fill=hex_rgb(bg, DEFAULT_BG))
    ty = by0 + pad_y + line_h / 2
    for i, ln in enumerate(lines):
        draw.text((bx0 + box_w / 2, ty), ln, font=font, fill=fg_rgb, anchor="mm")
        if i == len(lines) - 1:
            lw = draw.textlength(ln, font=font)
            r = size * 0.22
            for sx in (bx0 + box_w / 2 - lw / 2 - size * 0.55, bx0 + box_w / 2 + lw / 2 + size * 0.55):
                _sparkle(draw, sx, ty, r, fg_rgb)
        ty += line_h
    return pil.convert("RGB")


@dataclass
class CoverResult:
    frames: int
    end_s: float
    zh_lines: list[str]
    title: Optional[str]
    # Khác rỗng = KHÔNG tạo ảnh bìa Việt (giữ ảnh bìa gốc), kèm lý do.
    # Thường là ảnh bìa không có chữ tiêu đề nhận ra được.
    skipped: str = ""


def build_cover(
    video_path: Path,
    out_dir: Path,
    *,
    title: Optional[str],
    bg: str,
    fg: str,
    video_title: str = "",
    context: str = "",
    reuse_background: bool = True,
    avoid_title: Optional[str] = None,
) -> Optional[CoverResult]:
    """Tạo `out_dir/cover.png` (+ original.png, clean.png, meta.json). Trả None
    nếu video không có ảnh bìa; `skipped` khác rỗng nếu có ảnh bìa nhưng không
    nhận ra chữ tiêu đề (giữ ảnh bìa gốc). `title` None = nhờ Gemini viết
    (`avoid_title` = tiêu đề cũ cần viết khác đi). `reuse_background`: dùng
    lại clean.png (đã xoá tiêu đề) lần trước — đổi tiêu đề/màu không phải
    chạy LaMa lại. cover.png cũ chỉ bị thay khi tạo xong ảnh mới —
    Gemini lỗi giữa chừng thì ảnh cũ vẫn còn."""
    import json

    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path, clean_path, cover_path = out_dir / "meta.json", out_dir / "clean.png", out_dir / "cover.png"
    meta = None
    if reuse_background and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = None
    if meta is None:
        for stale in (clean_path, out_dir / "original.png"):
            stale.unlink(missing_ok=True)
        frames, fps, first = detect_cover(video_path)
        if not frames or first is None:
            meta = {"frames": 0, "fps": fps, "boxes": [], "skipped": ""}
        else:
            cv2.imwrite(str(out_dir / "original.png"), first)
            boxes = find_title_boxes(first)
            meta = {"frames": frames, "fps": fps, "boxes": [b.__dict__ for b in boxes], "skipped": ""}
            if not boxes:
                meta["skipped"] = "ảnh bìa không có chữ tiêu đề cần thay"
            else:
                cv2.imwrite(str(clean_path), lama_inpaint(first, boxes))
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    if not meta["frames"]:
        cover_path.unlink(missing_ok=True)
        return None
    boxes = [TitleBox(**b) for b in meta["boxes"]]
    zh_lines = [b.text for b in boxes]
    end_s = meta["frames"] / (meta["fps"] or 30.0)
    if meta["skipped"] or not clean_path.exists():
        cover_path.unlink(missing_ok=True)
        logger.info("cover: giữ ảnh bìa gốc — {}", meta["skipped"] or "thiếu nền sạch")
        return CoverResult(meta["frames"], end_s, zh_lines, title, skipped=meta["skipped"] or "thiếu nền sạch")
    if not title:
        title = write_headline(zh_lines, video_title, context, avoid=avoid_title)
    clean = cv2.imread(str(clean_path))
    render_cover(clean, boxes[0] if boxes else None, title, bg, fg).save(cover_path)
    logger.info("cover: {} khung ảnh bìa, tiêu đề cũ {} → {!r}", meta["frames"], zh_lines, title)
    return CoverResult(meta["frames"], end_s, zh_lines, title)
