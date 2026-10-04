#!/usr/bin/env python3
"""Worker xoá phụ đề cứng (chữ Hán in sẵn) khỏi video — trang "Làm sạch video".

Port từ script thử nghiệm D:\test_remove_text_in_video\remove_hardsub.py.
Chạy trong SUBPROCESS bằng Python của venv GPU riêng (config.HARDSUB_PYTHON),
KHÔNG import từ server: venv chính dùng torch CPU (bản CUDA từng crash), còn
LaMa cần torch CUDA + rapidocr_onnxruntime (API khác `rapidocr` của venv
chính). Vì vậy file này không import gì từ package `app`.

Quy trình:
  1. OCR thưa (RapidOCR) vài frame/giây, chỉ giữ box có chữ Hán
  2. OCR dày ở đoạn chữ đổi giữa 2 mẫu (chữ hiện/tắt/chạy)
  3. Vá vùng chữ, 2 chế độ (--engine):
     - sttn (mặc định, MƯỢT): STTN vá cả chuỗi frame mỗi cảnh (sttn_net.py) -> không giật
     - fast (NHANH): LaMa từng frame; cảnh camera + quanh chữ đứng yên thì lấp từ tấm nền
       nhớ (BgPlate), cảnh động lấy nền frame trước (Propagator) — vùng vá dễ nhảy
  4. Pipe frame thẳng vào ffmpeg, giữ nguyên audio gốc

Tiến độ in ra stdout dạng `@@PROGRESS {"phase","done","total"}`, lỗi dạng
`@@ERROR <msg>` — app/stages/hardsub_clean.py đọc 2 dòng này.

Cài venv GPU riêng (xem install.md):
  pip install opencv-python numpy rapidocr_onnxruntime
  pip install torch --index-url https://download.pytorch.org/whl/cu121
  pip uninstall -y onnxruntime && pip install onnxruntime-gpu==1.22.0

Dùng: python hardsub_worker.py input.mp4 -o output.mp4 [--icon-pad 0.8] [--all-text] [--nvenc]
"""
import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np


def _suppress_subprocess_windows() -> None:
    """Worker này chạy ở venv GPU riêng (KHÔNG import app.config nên không hưởng
    patch ẩn cửa sổ ở đó) nhưng lại tự gọi ffmpeg/ffprobe liên tục -> mỗi lần
    nháy một cửa sổ console đen. Patch subprocess.Popen ngay tại đây cho mọi lời
    gọi ffmpeg/ffprobe của worker đều chạy ẩn. Chỉ áp dụng trên Windows."""
    if sys.platform != "win32" or getattr(subprocess.Popen, "_no_window_patched", False):
        return
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    _OrigPopen = subprocess.Popen

    class _NoWindowPopen(_OrigPopen):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            # Chỉ OR CREATE_NO_WINDOW (xem giải thích ở app/config.py): KHÔNG set
            # startupinfo SW_HIDE để không ẩn nhầm cửa sổ app GUI.
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | create_no_window
            super().__init__(*args, **kwargs)

    _NoWindowPopen._no_window_patched = True
    subprocess.Popen = _NoWindowPopen  # type: ignore[misc]


_suppress_subprocess_windows()

LAMA_URL = "https://github.com/Sanster/models/releases/download/add_big_lama/big-lama.pt"
CJK_RE = re.compile(r"[㐀-鿿豈-﫿]")


class WorkerError(Exception):
    pass


def nvenc_error():
    """Thử encode 1 frame bằng h264_nvenc — None nếu dùng được, ngược lại trả
    dòng lỗi chính của ffmpeg. Gặp thật: driver 566 < 570 mà bản ffmpeg này
    cần, chỉ lộ ra lúc render (sau vài phút OCR) nếu không thử trước."""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "color=s=256x256",
         "-frames:v", "1", "-c:v", "h264_nvenc", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode == 0:
        return None
    lines = [l.split("] ", 1)[-1] for l in r.stderr.splitlines() if l.strip()]
    return lines[0] if lines else f"exit {r.returncode}"


def emit(phase, done, total):
    """Tiến độ cho server đọc (app/stages/hardsub_clean.py)."""
    print("@@PROGRESS " + json.dumps({"phase": phase, "done": done, "total": total}), flush=True)


# ----------------------------------------------------------------------------- video info
def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    streams = json.loads(out)["streams"]
    v = next(s for s in streams if s["codec_type"] == "video")
    num, den = map(int, v["r_frame_rate"].split("/"))
    return {
        "w": int(v["width"]), "h": int(v["height"]),
        "fps_str": v["r_frame_rate"], "fps": num / den,
        "has_audio": any(s["codec_type"] == "audio" for s in streams),
    }


# ----------------------------------------------------------------------------- pass 1: OCR
class Detector:
    def __init__(self, cjk_only, use_cuda):
        if use_cuda:
            # onnxruntime-gpu (bản CUDA 12) dùng chung cudart/cublas/cudnn đi kèm torch -> nạp torch trước
            try:
                import torch  # noqa: F401
                import onnxruntime as ort
                if hasattr(ort, "preload_dlls"):
                    ort.preload_dlls()
            except ImportError:
                use_cuda = False
        from rapidocr_onnxruntime import RapidOCR
        # Mặc định RapidOCR phóng cạnh NGẮN lên 736px -> dải phụ đề bị phóng to ~16x, chậm 20x.
        # Chỉ thu nhỏ theo cạnh dài, không phóng to.
        # Không có onnxruntime-gpu thì RapidOCR tự lùi về CPU.
        self.ocr = RapidOCR(det_limit_type="max", det_limit_side_len=960,
                            det_use_cuda=use_cuda, rec_use_cuda=use_cuda)
        self.cjk_only = cjk_only

    def boxes(self, img):
        if self.cjk_only:
            res, _ = self.ocr(img, use_det=True, use_cls=False, use_rec=True)
            return [r[0] for r in (res or []) if CJK_RE.search(r[1] or "")]
        res, _ = self.ocr(img, use_det=True, use_cls=False, use_rec=False)
        return res or []


def detect(args, info):
    det = Detector(args.cjk_only, args.device == "cuda")
    y0 = int(info["h"] * (1 - args.roi))
    step = max(1, round(info["fps"] / args.ocr_fps))
    max_h = info["h"] * args.max_h

    cap = cv2.VideoCapture(str(args.input))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    idx, t0 = 0, time.time()
    check_dir = args.check_dir
    if args.check:
        check_dir.mkdir(parents=True, exist_ok=True)

    def frame_polys(frame):
        polys = []
        for b in det.boxes(frame[y0:]):
            pts = np.array(b, np.int32)
            # đo theo khung xoay: chữ nghiêng có ptp(y) rất lớn dù dòng chữ mỏng
            (cx, cy), (rw, rh), ang = cv2.minAreaRect(pts)
            bw, bh = max(rw, rh), min(rw, rh)
            # phụ đề = dải ngang, không quá nhỏ/quá cao -> loại vật sáng, logo, nhiễu
            if args.min_h <= bh <= max_h and bw >= bh * args.min_aspect:
                polys.append(pts)                         # box gốc; nới (halo/icon) lúc vẽ mask
        return polys

    masks = Masks((info["h"] - y0, info["w"]), step)
    masks.configure(args)
    while cap.grab():
        if idx % step == 0:
            _, frame = cap.retrieve()
            polys = frame_polys(frame)
            if polys:
                masks.polys[idx] = polys
            if args.check and idx // step % 10 == 0:       # lưu overlay mỗi 10 mẫu
                m = masks.raster(polys)
                vis = frame.copy()
                cv2.line(vis, (0, y0), (info["w"], y0), (0, 255, 255), 2)
                if m is not None:
                    red = vis[y0:].copy(); red[m > 0] = (0, 0, 255)
                    vis[y0:] = cv2.addWeighted(vis[y0:], 0.5, red, 0.5, 0)
                cv2.imwrite(str(check_dir / f"{idx:06d}.jpg"), vis)
        idx += 1
        if idx % 100 == 0:
            emit("ocr", idx, total)
    cap.release()
    print(f"OCR thưa xong: {idx} frame, {len(masks.polys)} mẫu có chữ, {time.time() - t0:.0f}s")

    if not args.check:
        # Đoạn giữa 2 mẫu mà chữ đổi (hiện/tắt/bay/chạy chữ) -> OCR từng frame,
        # vì hợp 2 mẫu không phủ được chữ đang chuyển động.
        dense = set()
        for s in range(0, idx, step):
            if masks.changed(s, s + step):
                dense.update(range(s + 1, min(s + step, idx)))
        t1 = time.time()
        cap = cv2.VideoCapture(str(args.input))
        f, n_done = 0, 0
        emit("dense", 0, len(dense))
        while dense and cap.grab():
            if f in dense:
                _, frame = cap.retrieve()
                polys = frame_polys(frame)
                if polys:
                    masks.polys[f] = polys
                n_done += 1
                if n_done % 20 == 0:
                    emit("dense", n_done, len(dense))
            f += 1
            if f > max(dense):
                break
        cap.release()
        masks.dense = dense
        print(f"OCR dày xong: {len(dense)} frame chữ chuyển động ({len(dense) * 100 // max(idx, 1)}%), "
              f"{time.time() - t1:.0f}s")
    return masks, y0, idx


class Masks:
    """Lưu đa giác chữ theo frame (nhẹ RAM), chỉ vẽ ra mask khi cần."""
    def __init__(self, shape, step):
        self.shape, self.step = shape, step
        self.polys, self.dense = {}, set()
        self.grow = np.ones((17, 17), np.uint8)

    def configure(self, args):
        """Độ nới vùng xoá — áp lúc vẽ mask (không lúc OCR) để đổi tham số không phải OCR lại."""
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * args.dilate + 1,) * 2)
        self.halo, self.icon_pad = args.halo, args.icon_pad

    def expand(self, pts):
        (cx, cy), (rw, rh), ang = cv2.minAreaRect(pts)
        bh = min(rw, rh)
        # nới theo CỠ CHỮ (không phải số px cố định): chữ tiêu đề to có viền +
        # quầng sáng dày, nới cố định vài px thì còn sót dải sáng quanh chữ
        g = 2 * self.halo * bh
        # kéo dài 2 đầu dòng chữ để ăn luôn icon trang trí sát chữ (✦, ✓, !!)
        e = 2 * self.icon_pad * bh
        size = (rw + e + g, rh + g) if rw >= rh else (rw + g, rh + e + g)
        return cv2.boxPoints(((cx, cy), size, ang)).astype(np.int32)

    def raster(self, polys):
        if not polys:
            return None
        m = np.zeros(self.shape, np.uint8)
        for p in polys:                                   # tô từng cái: fillPoly nhiều đa giác 1 lần
            cv2.fillPoly(m, [self.expand(p)], 255)        # dùng luật chẵn-lẻ -> chỗ chồng nhau bị khoét
        return cv2.dilate(m, self.kernel)

    def changed(self, a, b):
        pa, pb = self.polys.get(a, []), self.polys.get(b, [])
        if not pa and not pb:
            return False
        ma, mb = self.raster(pa), self.raster(pb)
        if ma is None or mb is None:                      # chữ vừa hiện / vừa tắt
            return True
        # có vùng chữ mới/mất (dù chỉ 1 chữ) nằm ngoài mask kia nới 8px (bỏ qua box OCR rung vài px)
        ga, gb = cv2.dilate(ma, self.grow), cv2.dilate(mb, self.grow)
        return max(np.count_nonzero(mb & ~ga), np.count_nonzero(ma & ~gb)) > 400

    def __len__(self):
        return len(self.polys)

    def _keys(self, f):
        if f in self.dense:
            # frame đã OCR riêng: hợp với 2 frame kề -> đỡ sót khi OCR hụt 1 frame
            return (f - 1, f, f + 1)
        # hợp mẫu trước & sau -> phủ cả lúc chữ vừa hiện/vừa tắt
        s0 = f - f % self.step
        return (s0, s0 + self.step)

    def mask_for(self, f):
        return self.raster([p for k in self._keys(f) for p in self.polys.get(k, [])])

    def window_bbox(self, f, k):
        """Khung bao (x1,y1,x2,y2) mọi box chữ trong [f-k, f+k], None nếu không có chữ."""
        pts = [p for key in range(f - k, f + k + 1) for p in self.polys.get(key, [])]
        if not pts:
            return None
        a = np.concatenate([self.expand(p) for p in pts])
        return a[:, 0].min(), a[:, 1].min(), a[:, 0].max() + 1, a[:, 1].max() + 1

    def window_mask(self, f, k):
        """Hợp vùng chữ của mọi frame trong [f-k, f+k] (+ các mẫu mask_for dùng ở 2 đầu)."""
        keys = set(range(f - k, f + k + 1)) | set(self._keys(f - k)) | set(self._keys(f + k)) | set(self._keys(f))
        return self.raster([p for key in keys for p in self.polys.get(key, [])])


# ----------------------------------------------------------------------------- inpainters
class LamaInpainter:
    def __init__(self, device, max_side=None):
        self.max_side = max_side
        import torch
        self.torch = torch
        path = Path.home() / ".cache" / "remove_hardsub" / "big-lama.pt"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            print("Tải model LaMa (~200MB)...")
            urllib.request.urlretrieve(LAMA_URL, path)
        if device == "cuda" and not torch.cuda.is_available():
            print("⚠ Không thấy CUDA, chuyển sang CPU (sẽ chậm).")
            device = "cpu"
        self.device = device
        self.model = torch.jit.load(str(path), map_location=device).eval()

    def __call__(self, bgr, mask):
        h, w = mask.shape
        if self.max_side and max(h, w) > self.max_side:
            # LaMa học ở ~512px: chạy crop lớn ở độ phân giải gốc vừa chậm (crop ~1000px
            # mất ~1s/lần trên GTX 1650) vừa hay ra vân lặp -> thu nhỏ, vá, phóng lại
            s = self.max_side / max(h, w)
            sw, sh = max(8, round(w * s)), max(8, round(h * s))
            small = self(cv2.resize(bgr, (sw, sh), interpolation=cv2.INTER_AREA),
                         cv2.resize(mask, (sw, sh), interpolation=cv2.INTER_NEAREST))
            return cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
        ph, pw = (-h) % 8, (-w) % 8                       # LaMa cần kích thước chia hết 8
        img = np.pad(bgr, ((0, ph), (0, pw), (0, 0)), mode="symmetric")
        msk = np.pad(mask, ((0, ph), (0, pw)), mode="symmetric")
        t = self.torch
        # Tắt tối ưu JIT: với big-lama bản TorchScript, profiling executor cứ tối ưu lại mỗi
        # lần gọi -> đo trên GTX 1650: 1430ms/lần, tắt đi còn 144ms (cùng kết quả).
        with t.inference_mode(), t.jit.optimized_execution(False):
            x = t.from_numpy(img[:, :, ::-1].copy()).permute(2, 0, 1)[None].float().div(255)
            m = t.from_numpy((msk > 0).astype(np.float32))[None, None]
            out = self.model(x.to(self.device), m.to(self.device))
            out = out[0].permute(1, 2, 0).clamp(0, 1).mul(255).byte().cpu().numpy()
        return out[:h, :w, ::-1]


class OpenCVInpainter:
    """Dự phòng / test nhanh, chất lượng thấp hơn LaMa nhiều."""
    def __call__(self, bgr, mask):
        return cv2.inpaint(bgr, mask, 5, cv2.INPAINT_TELEA)


# ----------------------------------------------------------------------------- camera tracking
TRACK_S = 0.5                                             # tracking ở nửa độ phân giải cho nhanh
_D = np.diag([TRACK_S, TRACK_S, 1.0])
_EXCL_K = np.ones((9, 9), np.uint8)


def small_gray(frame, mask, s=TRACK_S):
    """Ảnh xám thu nhỏ + vùng chữ (nới) để loại khỏi điểm bám — chữ đứng yên trên màn hình."""
    gray = cv2.cvtColor(cv2.resize(frame, None, fx=s, fy=s, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    excl = None if mask is None else cv2.dilate(
        cv2.resize(mask, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_NEAREST), _EXCL_K)
    return gray, excl


def track_step(prev_gray, gray, prev_excl, excl, relaxed=False):
    """Homography frame trước -> frame này (toạ độ ảnh thu nhỏ), chỉ dùng điểm ngoài vùng chữ.
    relaxed: cho tấm nền cảnh tĩnh — nhận tịnh tiến theo trung vị khi đa số điểm dịch như nhau
    (chữ đang bay/hiện dần làm homography rớt ngưỡng inlier dù camera đứng yên)."""
    allow = np.full_like(gray, 255)
    for e in (prev_excl, excl):
        if e is not None:
            allow[e > 0] = 0
    p0 = cv2.goodFeaturesToTrack(prev_gray, 400, 0.01, 8, mask=allow)
    if p0 is None or len(p0) < 20:
        return None
    p1, st, _ = cv2.calcOpticalFlowPyrLK(prev_gray, gray, p0, None, winSize=(21, 21), maxLevel=3)
    ok = st.ravel() == 1
    if ok.sum() < 20:
        return None
    if relaxed:
        flow = (p1[ok] - p0[ok]).reshape(-1, 2)
        med = np.median(flow, axis=0)
        if np.mean(np.linalg.norm(flow - med, axis=1) < 0.5) >= 0.6:
            return np.array([[1, 0, med[0]], [0, 1, med[1]], [0, 0, 1]], np.float64)
    Hs, inl = cv2.findHomography(p0[ok], p1[ok], cv2.RANSAC, 2.0)
    if Hs is None or inl.sum() < 15 or inl.mean() < 0.5:
        return None
    return Hs


def analyze_shots(args, info, masks, y0, n_frames):
    """Chia video thành các cảnh và đánh dấu cảnh nào camera ĐỨNG YÊN.

    Tấm nền (BgPlate) chỉ đúng khi camera đứng yên: nó bám camera bằng 1 phép biến đổi phẳng
    cho cả khung hình, cảnh quay lia/cận sàn có thị sai (vật ở nhiều độ sâu) thì dán nền lệch
    thành mảng chữ nhật sai chỗ (đo trên test30: chân bàn hồng dán giữa sàn ở giây 26). Cảnh
    di chuyển dùng cách cũ (Propagator + LaMa), vốn ổn định hơn ở đó.
    Trả list bool theo frame: True = frame thuộc cảnh tĩnh."""
    s = 0.25
    cap = cv2.VideoCapture(str(args.input))
    prev = prev_excl = None
    shots, cur = [], []                    # cur: list (vectơ dịch px, bám được?, độ đổi quanh chữ)
    band_k = np.ones((21, 21), np.uint8)
    f = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        m = masks.mask_for(f)
        full = None
        if m is not None:
            full = np.zeros((info["h"], info["w"]), np.uint8)
            full[y0:] = m
        gray, excl = small_gray(frame, full, s)
        if prev is None:
            cur = []
        else:
            cut = np.abs(gray.astype(np.int16) - prev.astype(np.int16)).mean() > args.cut_diff
            Hs = None if cut else track_step(prev, gray, prev_excl, excl, relaxed=True)
            if cut:
                shots.append(cur)
                cur = []
            else:
                # vật quanh chữ có chuyển động không (tay, bàn đang bị kéo...): độ đổi trung bình
                # giữa 2 frame ở dải ~40px quanh chữ
                ring = None
                if excl is not None:
                    band = (cv2.dilate(excl, band_k) > 0) & (excl == 0)
                    if band.any():
                        ring = float(np.abs(gray.astype(np.int16) - prev.astype(np.int16))[band].mean())
                if Hs is None:
                    cur.append((np.zeros(2), False, ring))
                else:
                    c = np.array([gray.shape[1] / 2, gray.shape[0] / 2, 1.0])
                    d = Hs @ c
                    cur.append(((d[:2] / d[2] - c[:2]) / s, True, ring))   # vectơ px ở độ phân giải gốc
        prev, prev_excl = gray, excl
        f += 1
        if f % 50 == 0:
            emit("shots", f, n_frames)
    cap.release()
    shots.append(cur)

    static, n_static, start, fps = [], 0, 0, info["fps"]
    for sh in shots:
        vecs = np.array([d for d, ok, _ in sh if ok]).reshape(-1, 2)
        ok_ratio = len(vecs) / max(len(sh), 1)
        steps = np.linalg.norm(vecs, axis=1)
        # phân vị 90% (không phải max): 1 frame bám nhiễu không làm hỏng cả cảnh. Độ trôi tính theo
        # VECTƠ tổng — cộng độ lớn từng frame thì rung 0.1px x 70 frame đã thành 7px, cảnh tiêu đề
        # đứng yên hẳn (đo: dịch 0px cả cảnh) bị xếp nhầm là cảnh động.
        is_static = len(vecs) > 0 and ok_ratio >= 0.9 and np.percentile(steps, 90) <= args.static_step \
            and np.linalg.norm(vecs.sum(0)) <= args.static_drift
        print(f"  cảnh {start / fps:.1f}-{(start + len(sh) + 1) / fps:.1f}s: "
              f"{'camera đứng yên' if is_static else 'camera động'} "
              f"(p90 {np.percentile(steps, 90) if len(steps) else 0:.1f}px/frame, "
              f"trôi {np.linalg.norm(vecs.sum(0)) if len(vecs) else 0:.1f}px)", flush=True)
        start += len(sh) + 1
        n_static += is_static
        static.extend([is_static] * (len(sh) + 1))       # +1: frame đầu cảnh
    static = np.array((static + [False] * n_frames)[:n_frames])

    # Camera đứng yên nhưng VẬT quanh chữ chuyển động (tay đi qua, kéo bàn...) thì nền nhớ cũng
    # cũ -> dán sai (test30 giây 8). Đo trên test30: lúc có tay độ đổi quanh chữ 2-8 mức xám,
    # lúc yên (kể cả tiêu đề đang hiện dần) 0-1. Trung vị cửa sổ 11 frame để bỏ đỉnh đơn lẻ
    # ở chỗ chuyển cảnh; đoạn bận nới thêm 8 frame 2 bên -> dùng cách vá cảnh động.
    ring = [0.0 if r is None else r for sh in shots for r in [None] + [x for _, _, x in sh]]
    ring = np.array((ring + [0.0] * n_frames)[:n_frames])     # cùng thứ tự frame với `static`
    med = np.array([np.median(ring[max(0, i - 5):i + 6]) for i in range(n_frames)])
    busy = med > args.static_scene
    busy = np.convolve(busy.astype(int), np.ones(17, int), mode="same") > 0
    n_busy = int((static & busy).sum())
    static = list(static & ~busy)
    print(f"Phân tích cảnh: {len(shots)} cảnh, {n_static} cảnh camera đứng yên; dùng tấm nền cho "
          f"{sum(static) * 100 // max(n_frames, 1)}% số frame (bỏ {n_busy} frame có vật chuyển động quanh chữ)",
          flush=True)
    return static


# ----------------------------------------------------------------------------- temporal propagation
class Propagator:
    """CẢNH CAMERA DI CHUYỂN (cách của bản đầu): giữ 1 frame nền đã sạch chữ (ref), bám theo
    camera (homography) để lấp vùng chữ các frame sau. Không bám được hoặc vành quanh chữ lệch
    -> False để gọi LaMa, và frame vừa vá thành ref mới (ổn định nền giữa các frame)."""

    def __init__(self, max_err):
        self.max_err = max_err
        self.ref = self.refH = self.prev_gray = self.prev_excl = None
        self.ring_k = np.ones((15, 15), np.uint8)

    def fill(self, frame, mask, gray, excl):
        """Lấp vùng mask bằng ref đã dịch theo camera. Trả True nếu thành công (frame đã sửa)."""
        if self.ref is None or self.prev_gray is None:
            return False
        Hs = track_step(self.prev_gray, gray, self.prev_excl, excl)
        if Hs is None:
            return False
        refH = (np.linalg.inv(_D) @ Hs @ _D) @ self.refH
        # chỉ warp trong khung bao quanh vùng chữ (+ vành 7px để kiểm tra độ khớp)
        ring_all = cv2.dilate(mask, self.ring_k)
        ys, xs = np.nonzero(ring_all)
        x1, x2, y1, y2 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        T = np.array([[1, 0, -x1], [0, 1, -y1], [0, 0, 1]], np.float64)
        size = (int(x2 - x1), int(y2 - y1))
        warped = cv2.warpPerspective(self.ref, T @ refH, size, flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
        inside = cv2.warpPerspective(np.full(self.ref.shape[:2], 255, np.uint8), T @ refH, size,
                                     flags=cv2.INTER_NEAREST, borderValue=0)
        m = mask[y1:y2, x1:x2] > 0
        if np.any(m & (inside == 0)):                     # vùng chữ rơi ra ngoài ref -> không đủ nền
            return False
        crop = frame[y1:y2, x1:x2]
        ring = (ring_all[y1:y2, x1:x2] > 0) & ~m & (inside > 0)
        if ring.sum() < 50:
            return False
        fr, wr = crop[ring].astype(np.float32), warped[ring].astype(np.float32)
        gain = np.clip(fr.mean(0) / np.maximum(wr.mean(0), 1), 0.85, 1.15)   # bù sáng/auto-exposure
        if np.abs(fr - wr * gain).mean() > self.max_err:  # lệch (vật chuyển động / bám sai) -> LaMa
            return False
        feather_paste(crop, cv2.multiply(warped, tuple(float(g) for g in gain) + (0.0,)), m)
        self.refH = refH
        return True

    def reset(self, clean=None):
        if clean is None:                                 # sang cảnh mới
            self.ref = self.prev_gray = self.prev_excl = None
        else:
            self.ref, self.refH = clean.copy(), np.eye(3)

    def advance(self, gray, excl):
        self.prev_gray, self.prev_excl = gray, excl


class BgPlate:
    """CẢNH CAMERA ĐỨNG YÊN: tấm nền theo TỪNG PIXEL.

    Mỗi pixel nhớ lần gần nhất nó lộ ra sạch (V=REAL); vùng chữ lấp từ đây -> nền thật (vd
    tiêu đề hiện dần trên tường: phần tường trước khi chữ hiện được giữ lại, không phải đoán).
    Chỗ chưa từng thấy nền mới gọi LaMa, kết quả lưu tạm (V=SYNTH) để frame sau dùng lại cho
    khỏi nháy, gặp nền thật thì bị ghi đè. Canvas neo theo frame đầu cảnh, có đệm cho rung nhẹ."""
    REAL, SYNTH = 2, 1

    def __init__(self, W, H, max_err):
        self.W, self.H, self.max_err = W, H, max_err
        self.px, self.py = W // 8, H // 8
        self.CW, self.CH = W + 2 * self.px, H + 2 * self.py
        self.B = np.zeros((self.CH, self.CW, 3), np.uint8)
        self.V = np.zeros((self.CH, self.CW), np.uint8)
        self.prev_gray = self.prev_excl = None
        self.reset()

    def reset(self):
        """Sang cảnh mới / mất bám: bỏ hết nền đã nhớ, neo canvas lại theo frame hiện tại."""
        self.V[:] = 0
        self.A = np.array([[1, 0, self.px], [0, 1, self.py], [0, 0, 1]], np.float64)
        self.prev_gray = self.prev_excl = None

    def advance(self, gray, excl):
        """Cập nhật A (toạ độ frame hiện tại -> canvas) theo rung nhẹ của camera."""
        if self.prev_gray is not None:
            Hs = track_step(self.prev_gray, gray, self.prev_excl, excl, relaxed=True)
            if Hs is None:
                self.reset()
            else:
                self.A = self.A @ np.linalg.inv(np.linalg.inv(_D) @ Hs @ _D)
        self.prev_gray, self.prev_excl = gray, excl

    def read(self, x1, y1, x2, y2):
        """Nền (ảnh, mức V) cho khung [x1,x2)x[y1,y2) của frame hiện tại."""
        M = self.A @ np.array([[1, 0, x1], [0, 1, y1], [0, 0, 1]], np.float64)
        size = (int(x2 - x1), int(y2 - y1))
        img = cv2.warpPerspective(self.B, M, size, flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                  borderMode=cv2.BORDER_CONSTANT)
        lvl = cv2.warpPerspective(self.V, M, size, flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP,
                                  borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        return img, lvl

    def write(self, img, sel, x1=0, y1=0, level=REAL):
        """Ghi các pixel `sel` của ảnh `img` (đặt tại (x1,y1) trong frame) vào canvas.
        Mức SYNTH không đè lên pixel đã có nền thật."""
        if not sel.any():
            return
        ys, xs = np.nonzero(sel)
        bx1, bx2, by1, by2 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        M = self.A @ np.array([[1, 0, x1 + bx1], [0, 1, y1 + by1], [0, 0, 1]], np.float64)
        corners = np.array([[0, 0, 1], [bx2 - bx1, 0, 1], [0, by2 - by1, 1], [bx2 - bx1, by2 - by1, 1]],
                           np.float64).T
        c = M @ corners
        c = c[:2] / c[2]
        cx1, cy1 = max(int(np.floor(c[0].min())) - 1, 0), max(int(np.floor(c[1].min())) - 1, 0)
        cx2, cy2 = min(int(np.ceil(c[0].max())) + 2, self.CW), min(int(np.ceil(c[1].max())) + 2, self.CH)
        if cx2 <= cx1 or cy2 <= cy1:
            return
        Mc = np.array([[1, 0, -cx1], [0, 1, -cy1], [0, 0, 1]], np.float64) @ M
        size = (cx2 - cx1, cy2 - cy1)
        w_img = cv2.warpPerspective(img[by1:by2, bx1:bx2], Mc, size, flags=cv2.INTER_LINEAR)
        # mask uint8 + cv2.copyTo: gán kiểu numpy (a[bool] = b[bool]) trên vùng ~1000x1100 mất
        # ~39ms, copyTo 0.6ms — ghi nền mỗi frame nên chỗ này quyết định tốc độ
        w_sel = cv2.warpPerspective(sel[by1:by2, bx1:bx2].astype(np.uint8) * 255, Mc, size,
                                    flags=cv2.INTER_NEAREST, borderValue=0)
        Vc = self.V[cy1:cy2, cx1:cx2]
        if level == self.SYNTH:
            w_sel[Vc >= self.REAL] = 0
        else:
            # nền thật: bỏ 1px mép (nội suy ở mép lẫn màu vùng chữ bên cạnh). Nền tạm thì ghi
            # đủ — gọt mép sẽ để lại viền "chưa thấy nền" -> frame nào cũng gọi LaMa
            w_sel = cv2.erode(w_sel, np.ones((3, 3), np.uint8))
        if not cv2.countNonZero(w_sel):
            return
        cv2.copyTo(w_img, w_sel, self.B[cy1:cy2, cx1:cx2])
        Vc[:] = cv2.max(cv2.bitwise_and(Vc, cv2.bitwise_not(w_sel)),
                        cv2.bitwise_and(np.full_like(Vc, level), w_sel))


class Laps:
    """Cộng dồn thời gian từng bước render — in ra cuối để biết chậm ở đâu."""
    def __init__(self):
        self.t, self.sum = time.perf_counter(), {}

    def __call__(self, name):
        now = time.perf_counter()
        self.sum[name] = self.sum.get(name, 0.0) + now - self.t
        self.t = now

    def report(self):
        return ", ".join(f"{k} {v:.0f}s" for k, v in sorted(self.sum.items(), key=lambda kv: -kv[1]))


def feather_paste(crop, fill, m, px=3):
    """Dán `fill` (uint8) vào `crop` trong mask `m`, hoà mép `px` pixel cho khỏi lộ đường ghép."""
    a = np.minimum(cv2.distanceTransform(m.astype(np.uint8), cv2.DIST_L2, 3) / px, 1).astype(np.float32)
    crop[:] = cv2.blendLinear(fill, crop, a, 1 - a)


# ----------------------------------------------------------------------------- pass 2: inpaint
def start_inpainter(args):
    """Nạp model vá (LaMa ~20-30s / STTN) ở luồng nền SONG SONG lúc OCR, thay vì chờ OCR xong."""
    import threading
    box = {}

    def load():
        try:
            if args.engine == "sttn":
                box["v"] = SttnInpainter(args.device, step=args.sttn_step)
            elif args.backend == "lama":
                box["v"] = LamaInpainter(args.device, args.lama_size)
            else:
                box["v"] = OpenCVInpainter()
        except Exception as e:                            # báo lại ở luồng chính lúc render
            box["e"] = e

    t = threading.Thread(target=load, daemon=True)
    t.start()

    def get():
        t.join()
        if "e" in box:
            raise box["e"]
        return box["v"]
    return get


def open_writer(args, info):
    """ffmpeg nhận frame BGR qua stdin, ghép audio gốc. Ghi ra file tạm (.part), xong mới đổi
    tên -> bị tắt giữa chừng thì lần sau không tưởng là đã xong. Không dùng -shortest: audio
    thường ngắn hơn video vài chục ms, cắt theo audio làm mất mấy frame cuối."""
    W, H = info["w"], info["h"]
    part = args.output.with_name(args.output.stem + ".part.mp4")
    vcodec = ["-c:v", "h264_nvenc", "-cq", "20", "-preset", "p5"] if args.nvenc \
        else ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast"]
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", info["fps_str"], "-i", "-",
           "-i", str(args.input), "-map", "0:v", "-map", "1:a?",
           *vcodec, "-pix_fmt", "yuv420p", "-c:a", "copy", str(part)]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE), part


def write_frame(ff, frame, args):
    try:
        ff.stdin.write(frame.tobytes())
    except OSError:                                       # ffmpeg đã chết (vd. nvenc không mở được)
        ff.wait()
        raise WorkerError("ffmpeg lỗi khi encode." + (" Thử tắt encode GPU (NVENC)." if args.nvenc else ""))


def close_writer(ff, part, args):
    ff.stdin.close()
    if ff.wait() != 0:
        raise WorkerError("ffmpeg lỗi khi encode.")
    part.replace(args.output)


def render(args, info, masks, y0, n_frames, get_inpainter, static):
    """Chế độ NHANH: LaMa từng frame + lấy nền frame trước (cảnh động) / tấm nền (cảnh tĩnh)."""
    emit("render", 0, n_frames)
    inpaint = get_inpainter()
    W, H = info["w"], info["h"]
    ff, part = open_writer(args, info)

    def full_mask(m):
        if m is None:
            return None
        out = np.zeros((H, W), np.uint8)
        out[y0:] = m
        return out

    def lama_groups(frame, mask, ctx):
        """Vá mask bằng LaMa theo từng cụm chữ (cụm cách nhau > 2*ctx crop riêng)."""
        groups = cv2.dilate(mask, np.ones((2 * ctx + 1, 2 * ctx + 1), np.uint8))
        _, _, stats, _ = cv2.connectedComponentsWithStats(groups)
        for x1, y1, bw, bh, _ in stats[1:]:
            crop, cm = frame[y1:y1 + bh, x1:x1 + bw], mask[y1:y1 + bh, x1:x1 + bw]
            if not cm.any():
                continue
            res = inpaint(crop, cm)
            sel = cm > 0
            crop[sel] = res[sel]

    cap = cv2.VideoCapture(str(args.input))
    use_plate = not args.no_propagate and args.static_plate
    prop = None if args.no_propagate else Propagator(args.prop_err)
    plate = BgPlate(W, H, args.prop_err) if use_plate else None
    ctx, f, t0 = args.context, 0, time.time()
    n = {"nền nhớ (cảnh tĩnh)": 0, "nền frame trước (cảnh động)": 0, "LaMa": 0}
    hole_k = np.ones((5, 5), np.uint8)
    lap = Laps()
    prev_static = None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        lap("đọc video")
        full = full_mask(masks.mask_for(f))
        is_static = bool(plate) and static[f]
        if is_static != prev_static:                      # đổi chế độ = sang cảnh khác
            if plate:
                plate.reset()
            if prop:
                prop.reset()
            prev_static = is_static
        lap("vẽ mask")

        if is_static:
            gray, excl = small_gray(frame, full)
            plate.advance(gray, excl)
            # KHÔNG ghi nền ở vùng sắp có chữ / vừa hết chữ: chữ đang mờ dần hiện/tắt
            # thì OCR chưa bắt được, ghi vào sẽ thành bóng chữ trên nền.
            forbid = full_mask(masks.window_mask(f, args.plate_guard))
            orig = frame.copy()
            lap("bám camera")
            if full is not None:
                groups = cv2.dilate(full, np.ones((2 * ctx + 1, 2 * ctx + 1), np.uint8))
                _, _, stats, _ = cv2.connectedComponentsWithStats(groups)
                for x1, y1, bw, bh, _ in stats[1:]:
                    x2, y2 = x1 + bw, y1 + bh
                    crop, m = frame[y1:y2, x1:x2], full[y1:y2, x1:x2] > 0
                    if not m.any():
                        continue
                    bg, lvl = plate.read(x1, y1, x2, y2)
                    have = m & (lvl > 0)
                    # vành ngay ngoài vùng không ghi nền: so nền nhớ còn khớp không
                    occ = m if forbid is None else m | (forbid[y1:y2, x1:x2] > 0)
                    ring = (cv2.dilate(occ.astype(np.uint8), np.ones((15, 15), np.uint8)) > 0) \
                        & ~occ & (lvl == BgPlate.REAL)
                    usable = have.any()
                    gain = np.ones(3, np.float32)
                    if usable and ring.sum() >= 50:
                        fr, wr = crop[ring].astype(np.float32), bg[ring].astype(np.float32)
                        gain = np.clip(np.median(fr, 0) / np.maximum(np.median(wr, 0), 1), 0.85, 1.15)
                        usable = np.abs(fr - wr * gain).mean() <= args.prop_err
                    hole = m
                    if usable:
                        feather_paste(crop, cv2.multiply(bg, tuple(float(g) for g in gain) + (0.0,)), have)
                        hole = m & ~have
                        n["nền nhớ (cảnh tĩnh)"] += 1
                    lap("lấp từ nền")
                    if hole.any():
                        # LaMa chỉ vá chỗ chưa từng thấy nền; xung quanh đã được lấp nền thật
                        hm = cv2.dilate(hole.astype(np.uint8), hole_k) & m.astype(np.uint8)
                        if np.count_nonzero(hole) < args.small_hole:
                            res = cv2.inpaint(crop, hm * 255, 3, cv2.INPAINT_TELEA)   # vài sợi lẻ
                        else:
                            res = inpaint(crop, hm * 255)
                            n["LaMa"] += 1
                        sel = hm > 0
                        crop[sel] = res[sel]
                        plate.write(crop, sel, x1, y1, BgPlate.SYNTH)
                        lap("LaMa")
            # chỉ nhớ nền quanh chỗ sắp có chữ (~1.5s tới) — ghi cả khung hình tốn ~150ms/frame
            bb = masks.window_bbox(f, args.plate_ahead)
            if bb is not None:
                mx, my = W // 8, H // 8
                bx1, by1 = max(int(bb[0]) - mx, 0), max(int(bb[1]) + y0 - my, 0)
                bx2, by2 = min(int(bb[2]) + mx, W), min(int(bb[3]) + y0 + my, H)
                clean = np.ones((by2 - by1, bx2 - bx1), bool) if forbid is None \
                    else forbid[by1:by2, bx1:bx2] == 0
                plate.write(orig[by1:by2, bx1:bx2], clean, bx1, by1, BgPlate.REAL)
            lap("ghi nền")
        else:
            if prop:
                gray, excl = small_gray(frame, full)
                lap("bám camera")
            if full is None:
                if prop:
                    prop.reset(frame)                     # frame không chữ = nền thật
            elif prop and prop.fill(frame, full, gray, excl):
                n["nền frame trước (cảnh động)"] += 1
                lap("lấp từ nền")
            else:
                lama_groups(frame, full, ctx)
                n["LaMa"] += 1
                if prop:
                    prop.reset(frame)
                lap("LaMa")
            if prop:
                prop.advance(gray, excl)

        write_frame(ff, frame, args)
        lap("encode")
        f += 1
        if f % 25 == 0:
            emit("render", f, n_frames)
    cap.release()
    close_writer(ff, part, args)
    stats = ", ".join(f"{k} {v}" for k, v in n.items())
    print(f"Render xong: {f} frame ({stats}), {time.time() - t0:.0f}s -> {args.output}")
    print(f"Thời gian render: {lap.report()}", flush=True)


# ----------------------------------------------------------------------------- STTN (chế độ MƯỢT)
STTN_URL = "https://raw.githubusercontent.com/YaoFANGUK/video-subtitle-remover/main/backend/models/sttn-det/sttn.pth"


class SttnInpainter:
    """Vá VIDEO bằng STTN (app/stages/sttn_net.py): nhìn ~11 frame lân cận + frame tham chiếu rải
    trong đoạn nên vùng vá liền mạch theo thời gian. LaMa vá từng ảnh riêng -> chữ bay/đổi câu,
    tay đi qua chữ, camera lia là vùng vá "nhảy" ~1 lần/giây (đo trên test30: 29-36 cú nhảy/30s,
    STTN còn 4; video review 21s: 56 -> 11). Đổi lại: chạy ở 432x240 nên nền nhiều chi tiết (thảm
    lông, chân bàn) ra mềm/mờ hơn, và chậm hơn (~165ms/frame/ô trên GTX 1650)."""

    def __init__(self, device, stride=5, ref_len=10, step=10):
        import torch
        import sttn_net
        self.torch, self.net_mod = torch, sttn_net
        path = Path.home() / ".cache" / "remove_hardsub" / "sttn.pth"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            print("Tải model STTN (~66MB)...", flush=True)
            tmp = path.with_suffix(".part")
            urllib.request.urlretrieve(STTN_URL, tmp)
            tmp.replace(path)
        if device == "cuda" and not torch.cuda.is_available():
            print("⚠ Không thấy CUDA, chuyển sang CPU (sẽ rất chậm).")
            device = "cpu"
        self.device = device
        # Tắt cuDNN: workspace cuDNN cho các conv của STTN đẩy VRAM lên ~2.9GB/cửa sổ bất kể số
        # frame, vượt phần VRAM còn trống của card 4GB (màn hình + app khác giữ ~1.5GB) -> driver
        # NVIDIA trên Windows lặng lẽ tràn sang RAM hệ thống: tiến trình phình ~7GB, máy dev chạm
        # giới hạn commit và lỗi hết bộ nhớ. Tắt đi: ~1.35GB VRAM, tốc độ gần như không đổi.
        torch.backends.cudnn.enabled = False
        self.net = sttn_net.load_generator(path, device)
        # fp32: đã thử fp16 trên GTX 1650 — chậm hơn (9s vs 1.8s/cửa sổ) và ra NaN.
        # step: cửa sổ ±stride trượt mỗi lần `step` frame. video-subtitle-remover dùng step=stride
        # (mỗi frame tính 2 lần rồi lấy trung bình); step=2*stride chỉ chồng 1 frame, nhanh gần
        # gấp đôi mà nhìn không khác.
        self.stride, self.ref_len, self.step = stride, ref_len, step

    def __call__(self, frames, masks, n_ctx=0):
        """frames: list ảnh RGB 432x240 uint8; masks: list 240x432 {0,1}. n_ctx frame đầu là frame
        ĐÃ VÁ của đoạn trước (mask 0) — chỉ làm tham chiếu cho 2 đoạn liền mạch.
        Trả list ảnh đã vá cho các frame còn lại."""
        torch = self.torch
        T = len(frames)
        with torch.no_grad():
            x = torch.from_numpy(np.stack(frames)).to(self.device).permute(0, 3, 1, 2).float().div(127.5).sub(1)
            m = torch.from_numpy(np.stack(masks)[:, None].astype(np.float32)).to(self.device)
            # encoder theo lô 8 frame: đưa cả đoạn (~70 frame) một lần thì activation vượt VRAM
            feats = torch.cat([self.net.encoder(x[i:i + 8] * (1 - m[i:i + 8])) for i in range(0, T, 8)])
            acc = [None] * T
            for f in range(n_ctx + self.stride, T + self.stride, self.step):
                f = min(f, T - 1)
                nb = list(range(max(0, f - self.stride), min(T, f + self.stride + 1)))
                refs = [i for i in range(0, T, self.ref_len) if i not in nb]
                ids = nb + refs
                pred = torch.tanh(self.net.decoder(self.net.infer(feats[ids], m[ids])[:len(nb)]))
                # uint8 ngay trên GPU: giữ float32 cả đoạn tốn RAM (máy dev sát giới hạn commit)
                pred = ((pred + 1) * 127.5).clamp(0, 255).byte().permute(0, 2, 3, 1).cpu().numpy()
                for i, idx in enumerate(nb):
                    if idx >= n_ctx:
                        acc[idx] = pred[i] if acc[idx] is None else cv2.addWeighted(acc[idx], 0.5, pred[i], 0.5, 0)
        return [np.where(masks[i][..., None] > 0, acc[i], frames[i]) for i in range(n_ctx, T)]


def find_cuts(args, n_frames):
    """Frame bắt đầu mỗi cảnh (+ tổng số frame ở cuối) — STTN không được trộn 2 cảnh với nhau."""
    cap = cv2.VideoCapture(str(args.input))
    starts, prev, f = [0], None, 0
    while cap.grab():
        _, fr = cap.retrieve()
        g = cv2.cvtColor(cv2.resize(fr, (135, 240), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.int16)
        if prev is not None and np.abs(g - prev).mean() > args.cut_diff:
            starts.append(f)
        prev, f = g, f + 1
        if f % 100 == 0:
            emit("shots", f, n_frames)
    cap.release()
    return starts + [f]


def plan_tiles(union, W, H, margin=24):
    """Chia vùng chữ gộp của cả cảnh thành các ô tỉ lệ 432:240, càng gần độ phân giải gốc càng tốt:
    ô cao >= 240px thật (không phóng to); phụ đề dài thì nhiều ô chồng nhau thay vì thu nhỏ cả
    dải (thu nhỏ làm vùng vá mờ hơn)."""
    from sttn_net import INPUT_W, INPUT_H
    aspect = INPUT_W / INPUT_H
    blobs = cv2.dilate(union, np.ones((61, 61), np.uint8))
    _, _, stats, _ = cv2.connectedComponentsWithStats(blobs)
    tiles = []
    for x, y, bw, bh, _ in stats[1:]:
        x1, y1 = max(x - margin, 0), max(y - margin, 0)
        x2, y2 = min(x + bw + margin, W), min(y + bh + margin, H)
        bw, bh = x2 - x1, y2 - y1
        th = max(bh, INPUT_H)
        tw = int(round(th * aspect))
        if tw > W:                                        # vùng quá cao: ô rộng hết khung, méo tỉ lệ nhẹ
            tw = W
            th = max(bh, int(round(W / aspect)))
        th = min(th, H)
        ty = int(np.clip((y1 + y2) // 2 - th // 2, 0, H - th))
        if bw <= tw:
            xs = [int(np.clip((x1 + x2) // 2 - tw // 2, 0, W - tw))]
        else:
            k = int(np.ceil((bw - tw) / (0.75 * tw))) + 1
            xs = [int(round(v)) for v in np.linspace(x1, x2 - tw, k)]
        tiles += [(tx, ty, tw, th) for tx in xs]
    return tiles


def tile_weight(tw, th, ramp=48):
    """Trọng số hoà các ô chồng nhau: giảm dần về mép ô (chỉ để hoà, không phải độ phủ)."""
    wx = np.minimum(np.arange(tw) + 1, np.arange(tw)[::-1] + 1).astype(np.float32)
    wy = np.minimum(np.arange(th) + 1, np.arange(th)[::-1] + 1).astype(np.float32)
    return np.minimum(np.minimum.outer(wy, wx) / ramp, 1.0) + 1e-3


def render_sttn(args, info, masks, y0, n_frames, get_inpainter):
    """Chế độ MƯỢT: mỗi cảnh chia vùng chữ thành ô, chạy STTN theo đoạn `sttn_chunk` frame, đầu mỗi
    đoạn nối `sttn_ctx` frame ĐÃ VÁ của đoạn trước làm tham chiếu (2 đoạn không bị nhảy)."""
    from sttn_net import INPUT_W as MW, INPUT_H as MH
    W, H = info["w"], info["h"]

    def full_mask(f):
        m = masks.mask_for(f) if f < n_frames else None
        if m is None:
            return None
        out = np.zeros((H, W), np.uint8)
        out[y0:] = m
        return out

    starts = find_cuts(args, n_frames)
    emit("render", 0, n_frames)
    sttn = get_inpainter()
    ff, part = open_writer(args, info)
    cap = cv2.VideoCapture(str(args.input))
    t0, t_model, n_tiles, done_frames = time.time(), 0.0, 0, 0
    dil = np.ones((5, 5), np.uint8)
    for s, e in zip(starts[:-1], starts[1:]):
        union = np.zeros((H, W), np.uint8)
        for f in range(s, e):
            fm = full_mask(f)
            if fm is not None:
                union |= fm
        tiles = plan_tiles(union, W, H) if union.any() else []
        n_tiles += len(tiles)
        if tiles:                                         # vùng bao mọi ô: chỉ ghép trong vùng này
            rx1, ry1 = min(t[0] for t in tiles), min(t[1] for t in tiles)
            rx2, ry2 = max(t[0] + t[2] for t in tiles), max(t[1] + t[3] for t in tiles)
            wgts = [tile_weight(tw, th) for _, _, tw, th in tiles]
        prev_done = {}                                    # ô -> ảnh 432x240 đã vá cuối đoạn trước
        for c0 in range(s, e, args.sttn_chunk):
            c1 = min(c0 + args.sttn_chunk, e)
            frames = [cap.read()[1] for _ in range(c0, c1)]
            fmasks = [full_mask(f) for f in range(c0, c1)]
            # kết quả model giữ ở dạng ảnh nhỏ (~0.3MB/ô/frame), ghép từng frame lúc ghi ra —
            # bộ đệm float cả khung hình cho mọi frame của đoạn làm hết RAM máy dev
            comps = {}
            for ti, (tx, ty, tw, th) in enumerate(tiles):
                small, smask = [], []
                for fr, fm in zip(frames, fmasks):
                    small.append(cv2.cvtColor(cv2.resize(fr[ty:ty + th, tx:tx + tw], (MW, MH),
                                                         interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB))
                    mc = np.zeros((th, tw), np.uint8) if fm is None else fm[ty:ty + th, tx:tx + tw]
                    smask.append((cv2.dilate(cv2.resize(mc, (MW, MH), interpolation=cv2.INTER_NEAREST), dil) > 0)
                                 .astype(np.uint8))
                if not any(mk.any() for mk in smask):
                    prev_done[ti] = small[-args.sttn_ctx:]
                    continue
                ctx = prev_done.get(ti, [])
                tm = time.time()
                done = sttn(ctx + small, [np.zeros((MH, MW), np.uint8)] * len(ctx) + smask, n_ctx=len(ctx))
                t_model += time.time() - tm
                prev_done[ti] = (ctx + done)[-args.sttn_ctx:]
                comps[ti] = done
            for j, (fr, fm) in enumerate(zip(frames, fmasks)):
                if comps and fm is not None:
                    num = np.zeros((ry2 - ry1, rx2 - rx1, 3), np.float32)
                    den = np.zeros((ry2 - ry1, rx2 - rx1), np.float32)
                    for ti, done in comps.items():
                        tx, ty, tw, th = tiles[ti]
                        if not fm[ty:ty + th, tx:tx + tw].any():
                            continue
                        big = cv2.cvtColor(cv2.resize(done[j], (tw, th), interpolation=cv2.INTER_CUBIC),
                                           cv2.COLOR_RGB2BGR)
                        oy, ox = ty - ry1, tx - rx1
                        num[oy:oy + th, ox:ox + tw] += big.astype(np.float32) * wgts[ti][..., None]
                        den[oy:oy + th, ox:ox + tw] += wgts[ti]
                    if den.any():
                        reg = fr[ry1:ry2, rx1:rx2]
                        blended = num / np.maximum(den, 1e-6)[..., None]
                        # độ phủ theo mask gốc (hoà mép 3px); chỗ không ô nào phủ thì giữ nguyên
                        alpha = np.minimum(cv2.distanceTransform(fm[ry1:ry2, rx1:rx2], cv2.DIST_L2, 3) / 3, 1)
                        alpha = (alpha * (den > 0))[..., None].astype(np.float32)
                        reg[:] = (blended * alpha + reg * (1 - alpha)).clip(0, 255).astype(np.uint8)
                write_frame(ff, fr, args)
                done_frames += 1
                if done_frames % 10 == 0:
                    emit("render", done_frames, n_frames)
    cap.release()
    close_writer(ff, part, args)
    print(f"Render xong (STTN): {done_frames} frame, {len(starts) - 1} cảnh, {n_tiles} ô, "
          f"model {t_model:.0f}s, tổng {time.time() - t0:.0f}s -> {args.output}", flush=True)


# ----------------------------------------------------------------------------- mask cache
MASK_KEYS = ("roi", "ocr_fps", "min_h", "max_h", "min_aspect", "cjk_only")


def load_or_detect(args, info):
    """OCR tốn vài phút; chỉ đổi cách vá nền thì dùng lại kết quả OCR lần trước."""
    import pickle
    key = {k: getattr(args, k) for k in MASK_KEYS}
    key["input"] = [str(args.input.resolve()), args.input.stat().st_size, args.input.stat().st_mtime]
    cache = args.mask_cache
    if cache and cache.exists() and not args.check:
        try:
            data = pickle.loads(cache.read_bytes())
            if data["key"] == key:
                print("Dùng lại kết quả OCR đã lưu.", flush=True)
                data["masks"].configure(args)
                return data["masks"], data["y0"], data["n"]
        except Exception:
            pass
    masks, y0, n = detect(args, info)
    if cache and not args.check:
        cache.write_bytes(pickle.dumps({"key": key, "masks": masks, "y0": y0, "n": n}))
    return masks, y0, n


# ----------------------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser(description="Xóa phụ đề cứng khỏi video (OCR + STTN/LaMa)")
    p.add_argument("input", type=Path)
    p.add_argument("-o", "--output", type=Path, required=True)
    p.add_argument("--icon-pad", type=float, default=0.0,
                   help="Kéo dài vùng xóa 2 đầu dòng chữ N lần chiều cao chữ để xóa icon sát chữ (vd. 0.8)")
    p.add_argument("--engine", choices=["sttn", "fast"], default="sttn",
                   help="sttn = MƯỢT (vá cả chuỗi frame, không giật, chậm hơn); "
                        "fast = NHANH (LaMa từng frame, vùng vá dễ nhảy)")
    p.add_argument("--sttn-chunk", type=int, default=60, help="STTN: số frame mỗi lần chạy (RAM/VRAM)")
    p.add_argument("--sttn-ctx", type=int, default=10, help="STTN: số frame đã vá của đoạn trước làm tham chiếu")
    p.add_argument("--sttn-step", type=int, default=10, help="STTN: bước trượt cửa sổ (5 = mỗi frame tính 2 lần)")
    p.add_argument("--halo", type=float, default=0.15,
                   help="Nới vùng xoá mỗi phía N lần chiều cao chữ (ăn viền + quầng sáng của chữ to)")
    p.add_argument("--lama-size", type=int, default=0,
                   help="Cạnh dài tối đa của vùng đưa vào LaMa (lớn hơn thì thu nhỏ rồi phóng lại). 0 = không giới hạn")
    p.add_argument("--plate-ahead", type=int, default=45,
                   help="Chỉ nhớ nền quanh chỗ có chữ trong ±N frame (nhanh hơn ghi cả khung hình)")
    p.add_argument("--cut-diff", type=float, default=30,
                   help="Độ chênh sáng trung bình giữa 2 frame (0-255) coi là chuyển cảnh")
    p.add_argument("--static-step", type=float, default=0.7,
                   help="Cảnh tĩnh: 90% số frame camera dịch <= N px/frame (độ phân giải gốc)")
    p.add_argument("--static-drift", type=float, default=5,
                   help="Cảnh tĩnh: độ trôi thực (vectơ) cả cảnh <= N px")
    p.add_argument("--static-scene", type=float, default=1.5,
                   help="Độ đổi quanh chữ giữa 2 frame (mức xám) > N = có vật chuyển động -> không dùng tấm nền")
    p.add_argument("--no-static-plate", dest="static_plate", action="store_false",
                   help="Tắt tấm nền cho cảnh tĩnh (mọi cảnh vá như cảnh động)")
    p.add_argument("--small-hole", type=int, default=400,
                   help="Lỗ chưa có nền nhỏ hơn N px thì vá bằng OpenCV thay vì LaMa (nhanh)")
    p.add_argument("--plate-guard", type=int, default=8,
                   help="Không ghi nền nhớ ở vùng có chữ trong ±N frame (chữ mờ dần hiện/tắt OCR chưa bắt được)")
    p.add_argument("--mask-cache", type=Path, help="Lưu/đọc kết quả OCR ở file này (chạy lại render không cần OCR)")
    p.add_argument("--roi", type=float, default=1.0, help="Phần dưới khung hình để tìm chữ (1 = cả khung, 0.25 = 1/4 dưới)")
    p.add_argument("--ocr-fps", type=float, default=4, help="Số lần OCR mỗi giây (mặc định 4)")
    p.add_argument("--dilate", type=int, default=6, help="Nới mask thêm N px để xóa viền/bóng chữ")
    p.add_argument("--min-h", type=int, default=12, help="Bỏ box chữ cao dưới N px")
    p.add_argument("--max-h", type=float, default=0.12, help="Bỏ box cao hơn tỉ lệ này của khung hình")
    p.add_argument("--min-aspect", type=float, default=1.0, help="Bỏ box có rộng/cao nhỏ hơn giá trị này")
    p.add_argument("--context", type=int, default=160,
                   help="Lấy thêm N px xung quanh cho LaMa (rộng = LaMa hiểu cảnh hơn, ít vệt/bóng mờ)")
    p.add_argument("--all-text", dest="cjk_only", action="store_false",
                   help="Xóa mọi chữ (cả Latin/số). Mặc định chỉ xóa chữ Hán để không xóa nhầm vật thể")
    p.add_argument("--no-propagate", action="store_true",
                   help="Tắt lấy nền từ frame trước (mỗi frame LaMa riêng -> dễ nháy)")
    p.add_argument("--prop-err", type=float, default=10,
                   help="Sai lệch tối đa quanh chữ để còn lấy nền đã nhớ (cao = ít gọi LaMa hơn)")
    p.add_argument("--backend", choices=["lama", "opencv"], default="lama")
    p.add_argument("--device", default="cuda")
    p.add_argument("--nvenc", action="store_true", help="Encode bằng GPU NVIDIA (nhanh hơn)")
    p.add_argument("--check", action="store_true", help="Chỉ chạy OCR, xuất ảnh overlay vào --check-dir")
    p.add_argument("--check-dir", type=Path, default=Path("check"))
    args = p.parse_args()
    try:
        process(args)
    except WorkerError as e:
        print(f"@@ERROR {e}", flush=True)
        sys.exit(1)


def process(args):
    info = probe(args.input)
    print(f"Video {info['w']}x{info['h']} @ {info['fps']:.2f}fps", flush=True)
    if args.nvenc:
        err = nvenc_error()
        if err:
            args.nvenc = False
            print(f"@@WARN Không dùng được encode GPU (NVENC), đã tự chuyển sang CPU: {err}", flush=True)
    t0 = time.time()
    get_inpainter = None if args.check else start_inpainter(args)
    masks, y0, n = load_or_detect(args, info)
    if args.check:
        print(f"Xem ảnh trong {args.check_dir}/: vạch vàng = ranh giới ROI, đỏ = vùng sẽ xóa.")
        return
    if not masks:
        raise WorkerError("Không phát hiện chữ Hán nào trong video.")
    if args.engine == "sttn":
        render_sttn(args, info, masks, y0, n, get_inpainter)
    else:
        if args.static_plate and not args.no_propagate:
            static = analyze_shots(args, info, masks, y0, n)
        else:
            static = [False] * n
        render(args, info, masks, y0, n, get_inpainter, static)
    print(f"Tổng thời gian: {(time.time() - t0) / 60:.1f} phút")


if __name__ == "__main__":
    main()
