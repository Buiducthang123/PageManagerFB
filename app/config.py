from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .paths import DEFAULT_WORKSPACE, ENV_FILE

load_dotenv(ENV_FILE)

# Sau load_dotenv: nạp gói license là khởi tạo LicenseManager (đọc env).
from .license import constants as _license_constants  # noqa: E402

# Đặt SỚM nhất có thể (trước khi torch/numpy/mkl được import ở bất kỳ đâu
# trong app) — từng gặp Demucs (CPU) treo cứng toàn bộ process, 0% CPU, không
# phản hồi API nào luôn, nghi do xung đột nhiều bản OpenMP runtime nạp trùng
# (lỗi kinh điển của torch+numpy trên Windows). setdefault để không đè giá trị
# người dùng đã tự set trong .env/hệ thống.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


def _suppress_subprocess_windows() -> None:
    """Trên Windows, mỗi lần gọi ffmpeg/ffprobe/yt-dlp... qua subprocess sẽ bật
    một cửa sổ console đen chớp lên rồi tắt. Khi xuất video (gọi ffprobe/ffmpeg
    liên tục) nó nháy loạn màn hình. Thay vì rải `creationflags=CREATE_NO_WINDOW`
    ở từng chỗ (dễ sót, không phủ được thư viện bên thứ ba), patch luôn
    `subprocess.Popen` một lần ở đây — vì cả `subprocess.run` lẫn yt-dlp/demucs
    đều đi qua Popen. Chỉ áp dụng trên Windows; chạy idempotent (import 2 lần
    không chồng patch)."""
    import subprocess
    import sys

    if sys.platform != "win32" or getattr(subprocess.Popen, "_no_window_patched", False):
        return

    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    _OrigPopen = subprocess.Popen

    class _NoWindowPopen(_OrigPopen):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            # CHỈ OR thêm CREATE_NO_WINDOW (giữ nguyên cờ caller đã truyền, vd.
            # CREATE_NEW_PROCESS_GROUP dùng cho hủy tiến trình). Cờ này ẩn console
            # của app console (ffmpeg/ffprobe/yt-dlp) nhưng BỊ BỎ QUA với app GUI
            # -> KHÔNG được set startupinfo SW_HIDE ở đây, vì nó sẽ ẩn luôn cửa sổ
            # explorer.exe (nút "Mở thư mục") và các app GUI khác.
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | create_no_window
            super().__init__(*args, **kwargs)

    _NoWindowPopen._no_window_patched = True
    subprocess.Popen = _NoWindowPopen  # type: ignore[misc]


_suppress_subprocess_windows()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip().strip("'\"") or DEFAULT_GEMINI_MODEL

RETIRED_GEMINI_MODELS = {
    "gemini-2.0-flash": "gemini-3.6-flash",
    "gemini-2.0-flash-001": "gemini-3.6-flash",
    "gemini-2.0-flash-lite": "gemini-3.5-flash-lite",
    "gemini-2.0-flash-exp": "gemini-3.6-flash",
}


def resolve_gemini_model(name: str | None = None) -> str:
    raw = (name or os.environ.get("GEMINI_MODEL") or GEMINI_MODEL or DEFAULT_GEMINI_MODEL).strip().strip("'\"")
    if raw.startswith("models/"):
        raw = raw[len("models/") :]
    return RETIRED_GEMINI_MODELS.get(raw, raw) or DEFAULT_GEMINI_MODEL

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "medium").strip() or "medium"
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "auto").strip() or "auto"
WHISPER_LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "zh").strip() or "zh"

TRANSLATE_BLOCK_SIZE = 10
GEMINI_BLOCK_SLEEP_S = 4.0
GEMINI_TEMPERATURE = 0.1

# Tốc độ đọc mục tiêu/ngưỡng nén lại (ký tự việt/giây) khi dịch — video gốc
# càng thoại dồn dập (gần như không có khoảng nghỉ giữa các câu) thì càng dễ
# phải đánh đổi giữa "đọc tự nhiên" và "dịch đủ ý". 3 mức người dùng tự chọn
# ở Settings — "natural" giữ tốc độ đọc chuẩn, chấp nhận cắt bớt ý ở câu quá
# dồn; "full_meaning" ưu tiên dịch đủ nghĩa hơn, chấp nhận đọc nhanh hơn/để
# timing trôi nhẹ (đã có cơ chế chậm video + tăng tốc giọng + đẩy lùi câu sau
# ở assemble.py xử lý phần dôi ra).
TRANSLATE_PACE_PROFILES: dict[str, tuple[float, float]] = {
    "natural": (15.5, 18.0),
    "balanced": (18.0, 23.0),
    "full_meaning": (20.0, 22.0),
}
DEFAULT_TRANSLATE_PACE = "full_meaning"
TRANSLATE_PACE = os.environ.get("TRANSLATE_PACE", DEFAULT_TRANSLATE_PACE).strip() or DEFAULT_TRANSLATE_PACE


def translate_cps(pace: str | None = None) -> tuple[float, float]:
    mode = (pace or os.environ.get("TRANSLATE_PACE") or TRANSLATE_PACE).strip()
    return TRANSLATE_PACE_PROFILES.get(mode, TRANSLATE_PACE_PROFILES[DEFAULT_TRANSLATE_PACE])


SENSEVOICE_DEVICE = os.environ.get("SENSEVOICE_DEVICE", "cpu").strip() or "cpu"

# Mặc định CPU — Whisper (transcribe) đã chiếm VRAM và model vẫn giữ resident
# suốt đời process (không unload), TTS chạy SAU trong cùng pipeline nên cộng
# dồn VRAM nếu cũng chạy CUDA. Máy dev 4GB VRAM khá sát — an toàn hơn để CPU,
# người dùng có nhiều VRAM hơn có thể tự set VIENEU_DEVICE=cuda.
VIENEU_DEVICE = os.environ.get("VIENEU_DEVICE", "cpu").strip() or "cpu"

try:
    TTS_CONCURRENCY = max(1, int(os.environ.get("TTS_CONCURRENCY", "3").strip() or "3"))
except ValueError:
    TTS_CONCURRENCY = 3


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default
    return max(lo, min(hi, value))


def tts_concurrency() -> int:
    """Đọc lại mỗi lần (không cache lúc import) để đổi trong Cài đặt có hiệu
    lực ngay ở job TTS kế tiếp."""
    return _env_int("TTS_CONCURRENCY", 3, 1, 16)


def demucs_timeout_s() -> int:
    return _env_int("DEMUCS_TIMEOUT_S", 1800, 300, 4 * 3600)


# Thiết bị xử lý AI chung cho cả máy (trang Cài đặt): "auto" = giữ mặc định
# an toàn của từng engine (Whisper thử CUDA rồi lùi CPU; SenseVoice/VieNeu chạy
# CPU — xem lý do ở SENSEVOICE_DEVICE/VIENEU_DEVICE), "cuda" = ép GPU cho tất
# cả (máy không có CUDA thì vẫn lùi CPU), "cpu" = ép CPU cho tất cả (máy không
# có card NVIDIA). Biến riêng từng engine trong .env chỉ có tác dụng ở "auto".
AI_DEVICE_MODES = ("auto", "cuda", "cpu")


def ai_device_mode() -> str:
    mode = (os.environ.get("AI_DEVICE") or "auto").strip().lower()
    return mode if mode in AI_DEVICE_MODES else "auto"


def _torch_cuda_ok() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def engine_device(env_var: str, auto_default: str) -> str:
    """Thiết bị cho 1 engine dùng torch (SenseVoice, VieNeu). Whisper không
    qua hàm này vì tự thử CUDA rồi lùi CPU (transcribe._load_model)."""
    mode = ai_device_mode()
    if mode == "cpu":
        return "cpu"
    if mode == "cuda":
        return "cuda" if _torch_cuda_ok() else "cpu"
    return (os.environ.get(env_var) or auto_default).strip().lower() or auto_default


def whisper_device() -> str:
    mode = ai_device_mode()
    if mode != "auto":
        return mode
    return (os.environ.get("WHISPER_DEVICE") or WHISPER_DEVICE).strip().lower() or "auto"

# Engine transcribe thứ 3 (đọc phụ đề CỨNG in sẵn trên khung hình, không dựa
# âm thanh — xem app/stages/transcribe_ocr.py). Đã đo thực tế: ~0.6-1.2s/khung
# trên CPU (RapidOCR/onnxruntime) — 1 khung/giây là mốc cân bằng tốc độ/độ
# chính xác chấp nhận được, 2 khung/giây thử nghiệm chậm gần gấp đôi mà phụ đề
# thường hiển thị đủ lâu (>1s) để 1fps không bỏ sót câu nào.
OCR_SAMPLE_FPS = 1.0
# Vùng đáy khung hình để crop trước khi OCR (giảm hẳn pixel phải xử lý) — thử
# crop hẹp hơn (12%) kèm resize nhỏ cho nhanh thì bị đọc sai chữ, nên giữ crop
# rộng rãi (25%) đổi lấy độ chính xác đúng.
OCR_CROP_BOTTOM_FRACTION = 0.25
# Cue ngắn hơn mốc này bị loại — thường là OCR đọc lệch đúng 1 khung đơn lẻ
# (nhiễu), không phải phụ đề thật (phụ đề thật luôn hiện đủ lâu để đọc được).
OCR_MIN_CUE_DURATION_S = 0.3
OCR_DEVICE = os.environ.get("OCR_DEVICE", "cpu").strip() or "cpu"
# Phát hiện tự động vùng che phụ đề cũ (export trực tiếp) — chỉ cần TOẠ ĐỘ
# vùng chữ, không cần đọc đúng nội dung câu, nên lấy mẫu thưa hơn hẳn OCR
# transcribe bình thường (1fps) để nhanh trên video dài.
OCR_DETECT_REGION_FPS = 0.3

# "Làm sạch video" (xoá phụ đề cứng, app/stages/hardsub_worker.py) cần torch
# CUDA + rapidocr_onnxruntime — KHÔNG có trong venv chính (torch CPU), nên chạy
# bằng Python của 1 venv GPU riêng, trỏ tới qua biến này. Để trống = dùng
# chính Python đang chạy server (chỉ chạy được nếu venv này đủ thư viện).
HARDSUB_PYTHON = os.environ.get("HARDSUB_PYTHON", "").strip().strip("'\"")

# Đường dẫn thư mục đã clone thủ công github.com/jiji262/douyin-downloader
# (mặc định workspace/vendor/douyin-downloader/, cùng chỗ với capcut-tts-api
# — xem install.md) — dùng để ingest link Douyin qua CLI (subprocess) thay vì
# snaptiktok.to. Không có default tự động — chưa set thì ingest Douyin
# fallback về snaptiktok.to như cũ (xem app/stages/fetch_url.py), TikTok luôn
# dùng snaptiktok.to vì tool này không hỗ trợ TikTok.
DOUYIN_DL_DIR = os.environ.get("DOUYIN_DL_DIR", "").strip()


def chrome_installed() -> bool:
    """douyin_browser mở Chrome THẬT đã cài (channel="chrome"), không dùng
    Chromium đi kèm playwright — máy không cài Chrome thì tầng tải đó không chạy."""
    roots = [os.environ.get(k, "") for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    return any(r and (Path(r) / "Google" / "Chrome" / "Application" / "chrome.exe").exists() for r in roots)


def douyin_dl_available() -> bool:
    """Có repo douyin-downloader VÀ có config.yml (chứa cookie Douyin). Bản cài
    gửi khách có sẵn repo nhưng cố ý không kèm config.yml (cookie của admin) —
    trước đây chỉ kiểm tra run.py nên máy khách luôn chọn tầng này rồi hỏng."""
    root = Path(DOUYIN_DL_DIR) if DOUYIN_DL_DIR else None
    return bool(root) and (root / "run.py").exists() and (root / "config.yml").exists()


def _workspace_base() -> Path:
    workspace = os.environ.get("WORKSPACE_DIR", "").strip()
    return Path(workspace).expanduser().resolve() if workspace else DEFAULT_WORKSPACE


def models_dir() -> Path:
    """Thư mục gốc chứa model AI (whisper/, sensevoice/, big-lama.pt). Mặc định
    cạnh workspace; đổi được trong Cài đặt (env `MODELS_DIR`) — máy user hay
    chỉ có ổ C nhỏ, model nặng vài GB."""
    custom = os.environ.get("MODELS_DIR", "").strip()
    path = Path(custom).expanduser().resolve() if custom else _workspace_base() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sensevoice_cache_dir() -> Path:
    custom = os.environ.get("SENSEVOICE_CACHE_DIR", "").strip()
    path = Path(custom).expanduser().resolve() if custom else models_dir() / "sensevoice"
    path.mkdir(parents=True, exist_ok=True)
    return path


def whisper_cache_dir() -> Path:
    custom = os.environ.get("WHISPER_CACHE_DIR", "").strip()
    path = Path(custom).expanduser().resolve() if custom else models_dir() / "whisper"
    path.mkdir(parents=True, exist_ok=True)
    return path


def lama_model_path() -> Path:
    return models_dir() / "big-lama.pt"


def temp_dir() -> Path:
    """Thư mục tạm (TEMP/TMP của cả process). Mặc định giữ chỗ cũ
    (`<cache whisper>/tmp`); đổi được trong Cài đặt (env `TEMP_DIR`)."""
    custom = os.environ.get("TEMP_DIR", "").strip()
    path = Path(custom).expanduser().resolve() if custom else whisper_cache_dir() / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


class CapcutDraftsDirError(RuntimeError):
    """Chưa cài / cài sai thư mục draft CapCut — chặn dựng draft thay vì ghi
    vào chỗ CapCut không thấy."""


def default_capcut_drafts_candidates() -> list[Path]:
    """Thư mục draft MẶC ĐỊNH của CapCut quốc tế và Jianying (CapCut Trung) trên
    Windows. Người dùng đổi vị trí lưu trong cài đặt CapCut thì không nằm ở đây
    — phải nhập tay trong trang Cài đặt."""
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        return []
    base = Path(local)
    return [
        base / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft",
        base / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft",
    ]


def detect_capcut_drafts_dirs() -> list[Path]:
    return [p for p in default_capcut_drafts_candidates() if p.is_dir()]


def check_capcut_drafts_dir(path: Path) -> tuple[str, str]:
    """("ok"|"warning"|"error", lời nhắn). warning = thư mục có thật nhưng chưa
    thấy dấu hiệu của CapCut (vd CapCut mới cài, chưa có draft nào) — vẫn cho
    dùng."""
    if not path.exists():
        return "error", "Thư mục không tồn tại"
    if not path.is_dir():
        return "error", "Đường dẫn không phải thư mục"
    if (path / "root_meta_info.json").exists():
        return "ok", "Đúng thư mục draft CapCut"
    try:
        for child in path.iterdir():
            if child.is_dir() and ((child / "draft_content.json").exists() or (child / "draft_info.json").exists()):
                return "ok", "Đúng thư mục draft CapCut"
    except OSError as err:
        return "error", f"Không đọc được thư mục: {err}"
    return "warning", "Chưa thấy draft CapCut nào trong thư mục này — kiểm tra lại nếu CapCut đã có draft"


def capcut_drafts_dir_setting() -> tuple[Optional[Path], str]:
    """(thư mục draft, nguồn): nguồn "env" = người dùng tự đặt trong Cài đặt,
    "auto" = tự dò thấy thư mục mặc định của CapCut, "" = chưa có."""
    custom = os.environ.get("CAPCUT_DRAFTS_DIR", "").strip()
    if custom:
        return Path(custom).expanduser().resolve(), "env"
    found = detect_capcut_drafts_dirs()
    if found:
        return found[0], "auto"
    return None, ""


def capcut_drafts_dir() -> Path:
    """Thư mục draft CapCut THẬT trên máy — assemble ghi draft thẳng vào đây
    để CapCut tự nhận ra. Đặt trong trang Cài đặt (env `CAPCUT_DRAFTS_DIR`,
    dùng chung với CapcutSupperTool), không đặt thì tự dò thư mục mặc định.
    Trước đây không đặt thì ghi vào workspace/capcut_drafts — dựng "thành
    công" nhưng CapCut không thấy draft, người dùng không biết vì sao; giờ báo
    lỗi rõ để vào Cài đặt sửa."""
    path, _ = capcut_drafts_dir_setting()
    if path is None:
        raise CapcutDraftsDirError(
            "Chưa cài thư mục draft CapCut — mở Cài đặt, bấm \"Tự dò\" hoặc dán đường dẫn "
            "thư mục lưu draft của CapCut"
        )
    status, message = check_capcut_drafts_dir(path)
    if status == "error":
        raise CapcutDraftsDirError(f"Thư mục draft CapCut không dùng được ({path}): {message} — sửa trong Cài đặt")
    return path


def apply_whisper_cache_env() -> Path:
    """Đưa MỌI cache model (HuggingFace, modelscope, torch hub) + TEMP vào thư
    mục model — ổ C thường hết chỗ. Đặt 1 chỗ duy nhất cho cả app: từng có
    stage tự đổi HF_HOME sang chỗ khác, model bị tải trùng / lọt sang ~/.cache."""
    cache = whisper_cache_dir()
    hf = cache / "huggingface"
    tmp = temp_dir()
    hf.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(hf)
    os.environ["HF_HUB_CACHE"] = str(hf / "hub")
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(hf / "hub")
    os.environ["MODELSCOPE_CACHE"] = str(sensevoice_cache_dir() / "modelscope")
    os.environ["TORCH_HOME"] = str(models_dir() / "torch")
    os.environ["TEMP"] = str(tmp)
    os.environ["TMP"] = str(tmp)
    os.environ["TMPDIR"] = str(tmp)
    return cache


apply_whisper_cache_env()

# Facebook Page (Reels) — Graph API. App ID/Secret CHỈ cần khi dán user token
# ngắn hạn (~1-2h) và muốn tool tự đổi sang token dài hạn — không bắt buộc
# (dán token đã "Extend" sẵn ở Access Token Debugger thì không cần), và không
# được đóng gói vào bản build gửi khách.
FB_GRAPH_VERSION = os.environ.get("FB_GRAPH_VERSION", "v21.0").strip() or "v21.0"
# Không có trong .env (bản đóng gói) → dùng App ID công khai nhúng sẵn.
FB_APP_ID = os.environ.get("FB_APP_ID", "").strip() or _license_constants.FB_APP_ID
FB_APP_SECRET = os.environ.get("FB_APP_SECRET", "").strip()
# Đăng nhập Facebook (OAuth) ngay trong tool: ngrok trỏ vào cổng frontend
# (5175, Vite chuyển /api sang backend). FB_REDIRECT_URI="auto" (mặc định) =
# tự hỏi ngrok đang chạy tên miền hiện tại (xem facebook_publish.resolve_redirect_uri);
# URI https://<tên-miền>/api/facebook/callback phải khai báo y hệt trong
# Meta App > Facebook Login > Valid OAuth Redirect URIs.
FB_REDIRECT_URI = os.environ.get("FB_REDIRECT_URI", "auto").strip()
FB_LOGIN_SCOPES = os.environ.get(
    "FB_LOGIN_SCOPES",
    "pages_show_list,pages_read_engagement,pages_manage_posts,pages_manage_engagement,pages_manage_metadata",
).strip()
# Đăng nhập xong quay về đây (không ở lại tên miền ngrok).
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5175").strip().rstrip("/")
# File pages.json của PagesManagerSupperTool (đã đăng nhập Facebook qua OAuth
# + ngrok) — nút "Nhập từ PagesManager" đọc token Page từ đây.
PAGES_MANAGER_PAGES_JSON = os.environ.get(
    "PAGES_MANAGER_PAGES_JSON", r"D:\PagesManagerSupperTool\backend\data\pages.json"
).strip()
