"""Đánh giá chất lượng "Làm sạch video" (app/stages/hardsub_worker.py) — đo cùng một cách
cho mọi phiên bản, để quyết định bằng số liệu thay vì nhìn vài khung.

Đo cho từng video đã làm sạch (so với video gốc):
  - Chữ Hán còn sót: OCR lại mỗi N frame, đếm khung còn đọc ra chữ Hán và tổng số chữ.
  - Độ nháy vùng vá: độ thay đổi giữa 2 frame liền nhau TRONG vùng chữ chia cho ở VÀNH
    quanh chữ (chuyển động tự nhiên của cảnh). ~1.0 = êm như xung quanh; >1 = nháy/giật.
  - Số khung giật mạnh: khung mà vùng vá đổi nhiều hơn vành quanh > 8 mức xám.
  - Cú nhảy: như trên nhưng ngưỡng 5 và BỎ các khung chuyển cảnh (cả khung hình đổi thì vùng vá
    đổi theo là đúng) — đây là chỉ số khớp với cảm giác "vùng vá giật"; trung bình độ nháy thì
    không (v1 và bản kết hợp đều ~1.0 dù nhảy ~1 lần/giây).
  - Ảnh so sánh: các mốc có chữ rải đều cả video, cột = gốc | từng phiên bản.
Chỉ số không bắt được lỗi "dán nền sai chỗ" — phải xem ảnh so sánh.

Chạy bằng Python của venv GPU (HARDSUB_PYTHON — cần rapidocr_onnxruntime):
  python tools/hardsub_eval.py --original input.mp4 --masks ocr_masks.pkl --out eval_dir \\
      v1=clean_old.mp4 v2=clean_new.mp4
`--masks` là file OCR worker đã lưu (workspace/hardsub/<id>/ocr_masks.pkl).
Ra: <out>/metrics.json, <out>/report.md, <out>/sheet_*.png
"""
import argparse
import json
import pickle
import re
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "stages"))
import hardsub_worker as hw  # noqa: E402

CJK = re.compile(r"[㐀-鿿豈-﫿]")


def load_masks(path, halo, icon_pad, dilate):
    import __main__
    __main__.Masks = hw.Masks                             # file cache được pickle từ worker chạy dạng __main__
    data = pickle.loads(Path(path).read_bytes())
    masks = data["masks"]

    class Cfg:
        pass
    cfg = Cfg()
    cfg.halo, cfg.icon_pad, cfg.dilate = halo, icon_pad, dilate
    masks.configure(cfg)
    return masks, data["y0"], data["n"]


def find_cuts(path, cut_diff=30):
    """Các khung chuyển cảnh của video gốc (độ chênh sáng trung bình với khung trước > cut_diff)."""
    cap = cv2.VideoCapture(str(path))
    cuts, prev, f = set(), None, 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(fr, (135, 240), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.int16)
        if prev is not None and np.abs(g - prev).mean() > cut_diff:
            cuts.add(f)
        prev, f = g, f + 1
    cap.release()
    return cuts


def measure(path, masks, y0, n, ocr, ocr_every, W, H, cuts=frozenset()):
    """Chữ còn sót + độ nháy vùng vá cho 1 video."""
    cap = cv2.VideoCapture(str(path))
    sw, sh = W // 2, H // 2
    prev, f = None, 0
    left_frames = left_chars = sampled = 0
    samples, fin, fring, fidx = [], [], [], []
    ring_k = np.ones((25, 25), np.uint8)
    prev_m = None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if f % ocr_every == 0:
            sampled += 1
            res, _ = ocr(frame, use_det=True, use_cls=False, use_rec=True)
            txt = "".join(r[1] for r in (res or []) if CJK.search(r[1] or ""))
            k = len(CJK.findall(txt))
            if k:
                left_frames += 1
                left_chars += k
                if len(samples) < 15:
                    samples.append([f, txt[:24]])
        small = cv2.resize(frame, (sw, sh), interpolation=cv2.INTER_AREA).astype(np.int16)
        m = masks.mask_for(f) if f < n else None
        if m is not None:
            full = np.zeros((H, W), np.uint8)
            full[y0:] = m
            m = cv2.resize(full, (sw, sh), interpolation=cv2.INTER_NEAREST) > 0
        if prev is not None and m is not None and prev_m is not None:
            ring = (cv2.dilate(m.astype(np.uint8), ring_k) > 0) & ~m
            if ring.sum() > 100:
                d = np.abs(small - prev).mean(2)
                fin.append(d[m].mean())
                fring.append(d[ring].mean())
                fidx.append(f)
        prev, prev_m = small, m
        f += 1
    cap.release()
    fin, fring = np.array(fin), np.array(fring)
    excess = fin - fring
    jumps = sorted(((float(e), k) for e, k in zip(excess, fidx) if e > 5 and k not in cuts), reverse=True)
    return {
        "cu_nhay": len(jumps),
        "cu_nhay_lon_nhat": [[round(k / 30, 1), round(e, 1)] for e, k in jumps[:10]],
        "khung_con_chu": left_frames, "khung_da_ocr": sampled, "so_chu_con": left_chars,
        "do_nhay": round(float(fin.mean() / max(fring.mean(), 1e-6)), 2) if len(fin) else None,
        "khung_giat": int(((fin - fring) > 8).sum()) if len(fin) else 0,
        "vi_du_chu_con": samples,
    }


def pick_frames(masks, n, count):
    """Các mốc có chữ rải đều cả video."""
    keys = sorted(k for k in masks.polys if k < n)
    if not keys:
        return []
    idx = np.linspace(0, len(keys) - 1, min(count, len(keys))).round().astype(int)
    return sorted({keys[i] for i in idx})


def contact_sheets(out, frames, videos, masks, y0, W, H, per_sheet=6, row_h=260):
    caps = [(label, cv2.VideoCapture(str(p))) for label, p in videos]
    sheets, rows = [], []
    for f in frames:
        m = masks.mask_for(f)
        ys, xs = np.nonzero(m)
        x1, x2 = max(xs.min() - 120, 0), min(xs.max() + 120, W)
        y1, y2 = max(ys.min() + y0 - 120, 0), min(ys.max() + y0 + 120, H)
        tiles = []
        for label, cap in caps:
            cap.set(cv2.CAP_PROP_POS_FRAMES, f)
            ok, fr = cap.read()
            t = fr[y1:y2, x1:x2] if ok else np.zeros((y2 - y1, x2 - x1, 3), np.uint8)
            t = cv2.resize(t, (max(1, int(t.shape[1] * row_h / t.shape[0])), row_h))
            cv2.putText(t, label, (6, row_h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4)
            cv2.putText(t, label, (6, row_h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            tiles.append(cv2.copyMakeBorder(t, 0, 0, 0, 6, cv2.BORDER_CONSTANT, value=(40, 40, 40)))
        row = np.hstack(tiles)
        cv2.putText(row, f"frame {f} ({f / 30:.1f}s)", (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4)
        cv2.putText(row, f"frame {f} ({f / 30:.1f}s)", (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        rows.append(row)
        if len(rows) == per_sheet:
            sheets.append(rows)
            rows = []
    if rows:
        sheets.append(rows)
    paths = []
    for i, rs in enumerate(sheets, 1):
        wmax = max(r.shape[1] for r in rs)
        rs = [cv2.copyMakeBorder(r, 0, 6, 0, wmax - r.shape[1], cv2.BORDER_CONSTANT, value=(40, 40, 40))
              for r in rs]
        p = out / f"sheet_{i}.png"
        cv2.imwrite(str(p), np.vstack(rs))
        paths.append(p)
    for _, c in caps:
        c.release()
    return paths


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--original", type=Path, required=True)
    ap.add_argument("--masks", type=Path, required=True, help="ocr_masks.pkl worker đã lưu")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("versions", nargs="+", help="nhãn=đường_dẫn_video_đã_làm_sạch")
    ap.add_argument("--ocr-every", type=int, default=6, help="OCR lại mỗi N frame")
    ap.add_argument("--moments", type=int, default=12, help="Số mốc trong ảnh so sánh")
    ap.add_argument("--frames", help="Tự chọn mốc: danh sách frame, vd 208,296,784")
    ap.add_argument("--halo", type=float, default=0.15)
    ap.add_argument("--icon-pad", type=float, default=0.8)
    ap.add_argument("--dilate", type=int, default=6)
    ap.add_argument("--skip-metrics", action="store_true", help="Chỉ vẽ ảnh so sánh")
    args = ap.parse_args()

    versions = []
    for v in args.versions:
        label, _, path = v.partition("=")
        if not path:
            sys.exit(f"Sai dạng '{v}', cần nhãn=đường_dẫn")
        versions.append((label, Path(path)))
    args.out.mkdir(parents=True, exist_ok=True)
    masks, y0, n = load_masks(args.masks, args.halo, args.icon_pad, args.dilate)
    info = hw.probe(args.original)
    W, H = info["w"], info["h"]

    frames = [int(x) for x in args.frames.split(",")] if args.frames else pick_frames(masks, n, args.moments)
    sheets = contact_sheets(args.out, frames, [("goc", args.original)] + versions, masks, y0, W, H)
    print("Ảnh so sánh:", ", ".join(str(p) for p in sheets), flush=True)
    if args.skip_metrics:
        return

    # onnxruntime-gpu dùng chung cudart/cublas đi kèm torch — phải nạp torch trước, không thì
    # OCR lặng lẽ lùi về CPU (chậm gấp nhiều lần, tranh CPU với render đang chạy)
    import torch  # noqa: F401
    import onnxruntime as ort
    if hasattr(ort, "preload_dlls"):
        ort.preload_dlls()
    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR(det_limit_type="max", det_limit_side_len=960, det_use_cuda=True, rec_use_cuda=True)
    results = {}
    cuts = find_cuts(args.original)
    for label, path in [("goc", args.original)] + versions:
        results[label] = measure(path, masks, y0, n, ocr, args.ocr_every, W, H, cuts)
        r = results[label]
        print(f"{label}: chữ còn {r['so_chu_con']} ({r['khung_con_chu']}/{r['khung_da_ocr']} khung), "
              f"cú nhảy {r['cu_nhay']}, độ nháy {r['do_nhay']}, khung giật {r['khung_giat']}", flush=True)
    (args.out / "metrics.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = ["| Bản | Chữ Hán còn (khung) | Cú nhảy (bỏ chuyển cảnh) | Độ nháy TB | Khung giật |",
             "|---|---|---|---|---|"]
    for label, r in results.items():
        lines.append(f"| {label} | {r['so_chu_con']} ({r['khung_con_chu']}/{r['khung_da_ocr']}) "
                     f"| {r['cu_nhay']} | {r['do_nhay']} | {r['khung_giat']} |")
    lines += ["", "Độ nháy ~1.0 = vùng vá thay đổi như cảnh xung quanh; > 1 = nháy/giật.",
              "Ảnh so sánh: " + ", ".join(p.name for p in sheets)]
    (args.out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
