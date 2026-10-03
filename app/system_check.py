"""Màn "Kiểm tra hệ thống": liệt kê những thứ khác nhau giữa các máy user
(card NVIDIA, Gemini key, thư mục draft CapCut, dung lượng ổ, model đã tải,
công cụ ngoài) kèm trạng thái ok/warning/error, để user mới cài tự làm theo.

Không import torch/model nào ở đây — trang này phải mở nhanh kể cả trên máy
chưa tải model."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import config, projects as pj, updater

# Dưới mốc này thì cảnh báo: 1 dự án video dài (video gốc + tách nhạc + TTS +
# bản xuất) dễ chiếm 2-5 GB.
DISK_WARN_GB = 20.0
DISK_ERROR_GB = 5.0

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class GpuInfo:
    available: bool
    name: str = ""
    memory_mb: int = 0
    driver: str = ""


_gpu_cache: tuple[float, GpuInfo] | None = None


def detect_gpu() -> GpuInfo:
    """Dò card NVIDIA bằng `nvidia-smi` (đi kèm driver) — không cần torch.
    Cache 5 phút vì gọi tiến trình con mất ~0.3s."""
    global _gpu_cache
    now = time.monotonic()
    if _gpu_cache and now - _gpu_cache[0] < 300:
        return _gpu_cache[1]
    info = GpuInfo(available=False)
    exe = shutil.which("nvidia-smi")
    if exe:
        try:
            out = subprocess.run(
                [exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10, creationflags=_CREATE_NO_WINDOW,
            )
            line = (out.stdout or "").strip().splitlines()[0] if out.returncode == 0 and out.stdout.strip() else ""
            if line:
                parts = [p.strip() for p in line.split(",")]
                info = GpuInfo(
                    available=True,
                    name=parts[0],
                    memory_mb=int(float(parts[1])) if len(parts) > 1 and parts[1] else 0,
                    driver=parts[2] if len(parts) > 2 else "",
                )
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            pass
    _gpu_cache = (now, info)
    return info


def disk_free(path: Path) -> tuple[float, float]:
    """(GB trống, GB tổng) của ổ chứa `path` (đi ngược lên tới thư mục có thật)."""
    p = path
    while not p.exists() and p.parent != p:
        p = p.parent
    usage = shutil.disk_usage(p)
    return usage.free / 1024**3, usage.total / 1024**3


def disk_status(free_gb: float) -> str:
    if free_gb < DISK_ERROR_GB:
        return "error"
    if free_gb < DISK_WARN_GB:
        return "warning"
    return "ok"


def test_gemini_key(api_key: Optional[str] = None) -> tuple[str, str]:
    """Gọi Gemini 1 lần với prompt rất ngắn. ("ok"|"error", lời nhắn).
    `api_key` None = thử key đang lưu."""
    key = (api_key or os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        return "error", "Chưa nhập Gemini API key"
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return "error", "Thiếu thư viện google-genai"
    model = config.resolve_gemini_model()
    try:
        client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=30_000))
        client.models.generate_content(
            model=model,
            contents="Reply with the single word: ok",
            config=types.GenerateContentConfig(max_output_tokens=16),
        )
    except Exception as err:  # noqa: BLE001 — lỗi SDK nhiều kiểu, chỉ cần phân loại theo nội dung
        return "error", _gemini_error_text(err)
    return "ok", f"Key dùng được (model {model})"


def _gemini_error_text(err: BaseException) -> str:
    text = str(err)
    low = text.lower()
    if "api_key_invalid" in low or "api key not valid" in low or "permission_denied" in low:
        return "Key sai hoặc đã bị thu hồi — tạo key mới ở aistudio.google.com"
    if "resource_exhausted" in low or "429" in low or "quota" in low:
        return "Key đúng nhưng đã hết quota (thử lại sau, hoặc bật Billing cho project của key)"
    if "not_found" in low or "404" in low:
        return f"Key đúng nhưng model \"{config.resolve_gemini_model()}\" không dùng được — đổi model trong Cài đặt"
    if "deadline" in low or "timeout" in low or "timed out" in low:
        return "Không kết nối được Gemini (quá thời gian chờ) — kiểm tra mạng"
    return f"Gemini báo lỗi: {text[:300]}"


def _item(id_: str, label: str, status: str, message: str, *, hint: str = "", link: str = "", tech: str = "") -> dict:
    """label/message/hint viết cho user thường — KHÔNG nêu tên engine/thư viện/dịch
    vụ (Whisper, ffmpeg, viesnap...). Chi tiết kỹ thuật để gỡ lỗi để ở `tech`:
    giao diện chỉ hiện cho admin, file chẩn đoán luôn có.
    link: trang trong app để sửa mục này (vd "/settings") — giao diện hiện nút đi tới."""
    return {"id": id_, "label": label, "status": status, "message": message, "hint": hint, "link": link, "tech": tech}


_REINSTALL_HINT = "Cài lại app bằng file Setup mới nhất (dữ liệu dự án không mất)"


def _ffmpeg_item() -> dict:
    label = "Công cụ xử lý video"
    exe = shutil.which("ffmpeg")
    if not exe:
        return _item("ffmpeg", label, "error", "Bị thiếu — không xử lý/xuất được video",
                     hint=_REINSTALL_HINT, tech="không thấy ffmpeg trong PATH")
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=10,
                             creationflags=_CREATE_NO_WINDOW)
        first = (out.stdout or "").splitlines()[0] if out.stdout else ""
    except (OSError, subprocess.SubprocessError):
        first = ""
    m = re.search(r"version (?:n)?(\d+)", first)
    major = int(m.group(1)) if m else 0
    v = re.search(r"version (\S+)", first)
    tech = f"ffmpeg {v.group(1) if v else '?'} · {exe}"
    # Xuất video dùng cú pháp `-/filter_complex <file>` — chỉ có từ ffmpeg 7.
    if major and major < 7:
        return _item("ffmpeg", label, "error", "Phiên bản quá cũ — xuất video sẽ lỗi",
                     hint=_REINSTALL_HINT, tech=f"{tech} (cần ffmpeg ≥ 7)")
    return _item("ffmpeg", label, "ok", "Sẵn sàng", tech=tech)


VCREDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"


def _vcrt_item() -> dict:
    """onnxruntime (OCR, giọng đọc trên máy) cần Visual C++ Runtime >= 14.40 —
    bản cũ làm app crash (access violation). App tự kèm bản mới (app/vcrt.py);
    mục này báo khi cả bản đi kèm cũng không dùng được."""
    from . import vcrt

    st = vcrt.status()
    label = "Thư viện Microsoft Visual C++"
    tech = f"System32 msvcp140 {st['system'] or 'không có'} · đi kèm {st['bundled'] or 'không có'}" + (
        f" · System32 thiếu {', '.join(st['missing'])}" if st.get("missing") else "") + (
        " · đã nạp bản đi kèm" if st["preloaded"] else "") + (f" · {st['error']}" if st["error"] else "")
    if st["system_ok"]:
        return _item("vcrt", label, "ok", "Sẵn sàng", tech=tech)
    if st["preloaded"]:
        return _item("vcrt", label, "ok", "Máy có bản cũ — app đã tự dùng bản mới đi kèm", tech=tech,
                     hint=f"Muốn chắc chắn: cài bản mới của Microsoft ở {VCREDIST_URL}")
    return _item("vcrt", label, "error", "Bản trên máy quá cũ — nhận diện bằng hình ảnh có thể làm app tự tắt",
                 hint=f"Tải và cài: {VCREDIST_URL}, rồi mở lại app", tech=tech)


def _douyin_items() -> list[dict]:
    # Đọc thẳng file đánh dấu (= douyin_browser.LOGIN_MARKER) — import douyin_browser
    # kéo theo playwright, trang này phải mở nhanh.
    try:
        marker = pj.WORKSPACE_DIR / "douyin_browser_login.json"
        logged_in = bool(json.loads(marker.read_text(encoding="utf-8")).get("logged_in"))
    except (OSError, ValueError):
        logged_in = False
    tech = "tải link Douyin: viesnap → snaptiktok → Chrome" + (" → douyin-downloader" if config.douyin_dl_available() else "")
    if not config.chrome_installed():
        return [_item("chrome", "Google Chrome", "warning", "Chưa cài — một số video Douyin có thể không tải được",
                      hint="Cài Chrome ở google.com/chrome rồi bấm Kiểm tra lại", tech=tech)]
    return [_item(
        "douyin_login", "Đăng nhập Douyin", "ok" if logged_in else "warning",
        "Đã đăng nhập" if logged_in else "Chưa đăng nhập — nên đăng nhập để tải video Douyin ổn định hơn",
        hint="" if logged_in else "Vào Cài đặt → Đăng nhập Douyin, quét mã QR bằng app Douyin",
        link="" if logged_in else "/settings", tech=tech,
    )]


def run_checks() -> list[dict]:
    items: list[dict] = []

    gpu = detect_gpu()
    mode = config.ai_device_mode()
    if gpu.available:
        gb = round(gpu.memory_mb / 1024) if gpu.memory_mb else 0
        msg = f"{gpu.name.replace('NVIDIA GeForce ', 'NVIDIA ')}" + (f" ({gb} GB)" if gb else "") + " — xử lý AI nhanh"
        status = "ok"
        if mode == "cpu":
            msg += ", nhưng Cài đặt đang tắt dùng card này"
            status = "warning"
        items.append(_item("gpu", "Card đồ hoạ", status, msg,
                           tech=f"{gpu.name} · {gpu.memory_mb} MB VRAM · driver {gpu.driver} · chế độ {mode}"))
    else:
        status = "warning" if mode != "cpu" else "ok"
        items.append(_item(
            "gpu", "Card đồ hoạ", status,
            "Không có card NVIDIA — AI vẫn chạy nhưng chậm hơn",
            hint="" if mode == "cpu" else "Nên chọn \"Không dùng card đồ hoạ\" ở Cài đặt → Thiết bị xử lý AI",
            tech=f"nvidia-smi không trả về card nào · chế độ {mode}",
        ))

    if (os.environ.get("GEMINI_API_KEY") or "").strip():
        items.append(_item("gemini", "Gemini API key", "ok", "Đã nhập — bấm \"Thử key\" để kiểm tra còn dùng được không",
                           tech=f"model {config.resolve_gemini_model()}"))
    else:
        items.append(_item("gemini", "Gemini API key", "error", "Chưa nhập — bước dịch không chạy được",
                           hint="Lấy key ở aistudio.google.com/apikey rồi dán vào Cài đặt"))

    path, _source = config.capcut_drafts_dir_setting()
    if path is None:
        items.append(_item("capcut", "Thư mục draft CapCut", "error", "Chưa cài — không dựng draft CapCut được",
                           hint="Cài CapCut, mở 1 lần, rồi vào Cài đặt bấm \"Tự dò\"", link="/settings"))
    else:
        status, message = config.check_capcut_drafts_dir(path)
        hint = ""
        if status == "warning":
            hint = ("Đúng thư mục CapCut nhưng chưa có dự án nào: mở CapCut, tạo 1 dự án bất kỳ rồi bấm \"Kiểm tra lại\". "
                    "CapCut lưu draft ở chỗ khác thì chọn lại trong Cài đặt")
        items.append(_item("capcut", "Thư mục draft CapCut", status, f"{message} — {path}",
                           hint=hint, link="/settings" if status != "ok" else ""))

    free_gb, total_gb = disk_free(pj.WORKSPACE_DIR)
    items.append(_item(
        "disk", "Dung lượng ổ workspace", disk_status(free_gb),
        f"Còn trống {free_gb:.1f} / {total_gb:.0f} GB ({pj.WORKSPACE_DIR})",
        hint="" if free_gb >= DISK_WARN_GB else "Dọn bớt ở trang \"Tự dọn ổ đĩa\" hoặc chuyển workspace sang ổ khác",
    ))
    models = config.models_dir()
    if models.drive.upper() != Path(pj.WORKSPACE_DIR).drive.upper():
        m_free, m_total = disk_free(models)
        items.append(_item("disk_models", "Dung lượng ổ chứa dữ liệu AI", disk_status(m_free),
                           f"Còn trống {m_free:.1f} / {m_total:.0f} GB ({models.drive})", tech=str(models)))

    items.append(_ffmpeg_item())
    items.append(_vcrt_item())

    items.extend(_douyin_items())

    label = "Làm sạch video"
    if config.HARDSUB_PYTHON and not Path(config.HARDSUB_PYTHON).exists():
        items.append(_item("hardsub", label, "error", "Thiết lập bị hỏng — chức năng này không chạy được",
                           hint=_REINSTALL_HINT, tech=f"không thấy HARDSUB_PYTHON: {config.HARDSUB_PYTHON}"))
    elif config.HARDSUB_PYTHON:
        items.append(_item("hardsub", label, "ok", "Sẵn sàng", tech=f"HARDSUB_PYTHON={config.HARDSUB_PYTHON}"))
    elif updater.packaged():
        items.append(_item("hardsub", label, "warning",
                           "Bản cài này chưa hỗ trợ — các chức năng khác không ảnh hưởng",
                           tech="bản đóng gói không kèm môi trường GPU riêng (HARDSUB_PYTHON trống)"))
    else:
        items.append(_item("hardsub", label, "warning",
                           "Chưa cài riêng — chỉ chạy được nếu môi trường chính đủ thư viện GPU",
                           tech="HARDSUB_PYTHON trống, dùng môi trường chính"))

    return items
