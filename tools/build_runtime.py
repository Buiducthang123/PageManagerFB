"""Dựng lớp Runtime (user-management-plan.md mục 12, 13.6).

    .venv\\Scripts\\python.exe tools\\build_runtime.py 1.0.0

Ra `dist\\runtime\\runtime-<bản>.zip` (vài GB) — giải nén vào `<cài đặt>\\runtime\\`:
    python.exe / pythonw.exe       Python nhúng chính thức (python.org), cùng bản với .venv
    Lib\\site-packages\\              thư viện trong requirements.txt
    ms-playwright\\                  Chromium cho đăng TikTok
    ffmpeg\\ffmpeg.exe, ffprobe.exe  lấy từ ffmpeg đang có trong PATH máy build
    douyin-downloader\\              repo douyin-downloader (DOUYIN_DL_DIR), bỏ .git
    runtime.json                    {"version": ...}

Runtime hiếm khi đổi; mỗi bản App ghi `min_runtime` để biết cần Runtime nào.
Bản torch trên PyPI cho Windows là bản CPU → chạy được trên máy không có card
NVIDIA; Whisper (ctranslate2) vẫn dùng GPU qua các gói nvidia-*-cu12 nếu có.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "runtime"
OUT = ROOT / "dist" / "runtime"

PY_VER = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
EMBED_URL = f"https://www.python.org/ftp/python/{PY_VER}/python-{PY_VER}-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"


def download(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    print(f"tải {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.rename(dest)
    return dest


def _build_env() -> dict:
    """Cache + file tạm của pip/playwright đặt cạnh thư mục build (ổ D) — mặc
    định nằm ở ổ C và đã từng làm đầy ổ C giữa chừng (torch + CUDA vài GB)."""
    cache, tmp = BUILD / "pip-cache", BUILD / "tmp"
    cache.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    return {**os.environ, "PIP_CACHE_DIR": str(cache), "TMP": str(tmp), "TEMP": str(tmp), "TMPDIR": str(tmp)}


def run(cmd: list[str], env: dict | None = None, **kw) -> None:
    print("$", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True, env={**_build_env(), **(env or {})}, **kw)


def build(version: str, skip_pip: bool) -> Path:
    rt = BUILD / "runtime"
    if not skip_pip:
        shutil.rmtree(rt, ignore_errors=True)
    rt.mkdir(parents=True, exist_ok=True)
    py = rt / "python.exe"

    if not py.exists():
        with zipfile.ZipFile(download(EMBED_URL, BUILD / "cache" / Path(EMBED_URL).name)) as zf:
            zf.extractall(rt)
        # Bản nhúng mặc định tắt site-packages: bật lại để pip cài được thư viện.
        pth = next(rt.glob("python3*._pth"))
        lines = [ln for ln in pth.read_text().splitlines() if ln.strip() and not ln.startswith("#import site")]
        lines += ["Lib\\site-packages", "import site"]
        pth.write_text("\n".join(lines) + "\n")
        run([py, download(GET_PIP_URL, BUILD / "cache" / "get-pip.py"), "--no-warn-script-location"])

    if not skip_pip:
        # Python nhúng không dựng được gói chỉ có source (vd jieba) trong môi
        # trường build tách biệt (._pth giới hạn sys.path) → cài sẵn công cụ
        # build và tắt build isolation.
        run([py, "-m", "pip", "install", "--no-warn-script-location", "setuptools", "wheel"])
        pip = [py, "-m", "pip", "install", "--no-warn-script-location", "--no-build-isolation"]
        run([*pip, "-r", ROOT / "requirements.txt"])
        run([*pip, "uvicorn[standard]"])

    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(rt / "ms-playwright")}
    run([py, "-m", "playwright", "install", "chromium"], env=env)

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("Máy build không có ffmpeg trong PATH")
    (rt / "ffmpeg").mkdir(exist_ok=True)
    for exe in ("ffmpeg.exe", "ffprobe.exe"):
        src = Path(ffmpeg).with_name(exe)
        if src.exists():
            shutil.copy2(src, rt / "ffmpeg" / exe)

    dl_dir = os.environ.get("DOUYIN_DL_DIR") or str(ROOT / "workspace" / "vendor" / "douyin-downloader")
    if (Path(dl_dir) / "run.py").exists():
        shutil.rmtree(rt / "douyin-downloader", ignore_errors=True)
        shutil.copytree(dl_dir, rt / "douyin-downloader",
                        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "downloads", "logs", "config.yml"))
        req = rt / "douyin-downloader" / "requirements.txt"
        if req.exists() and not skip_pip:
            run([py, "-m", "pip", "install", "--no-warn-script-location", "--no-build-isolation", "-r", req])
    else:
        print(f"! Không thấy douyin-downloader ở {dl_dir} — bỏ qua")

    (rt / "runtime.json").write_text(json.dumps({"version": version, "python": PY_VER}), encoding="utf-8")

    OUT.mkdir(parents=True, exist_ok=True)
    zip_path = OUT / f"runtime-{version}.zip"
    zip_path.unlink(missing_ok=True)
    print("nén runtime (lâu)…")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zf:
        for f in sorted(rt.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                zf.write(f, f.relative_to(rt).as_posix())
    return zip_path


# GitHub Releases giới hạn 2 GiB/file → chia zip thành các phần nhỏ hơn mốc này.
PART_SIZE = 1900 * 1024 * 1024


def split_parts(zip_path: Path) -> list[dict]:
    """Chia zip thành <tên>.part1, .part2... (ghép lại bằng `copy /b` theo thứ
    tự là ra zip gốc). Ghi kèm <tên>.parts.json: tên, kích thước, SHA-256 từng phần."""
    for old in zip_path.parent.glob(zip_path.name + ".part*"):
        old.unlink()
    parts: list[dict] = []
    with zip_path.open("rb") as src:
        i = 1
        while True:
            name = f"{zip_path.name}.part{i}"
            h, size = hashlib.sha256(), 0
            with (zip_path.parent / name).open("wb") as dst:
                while size < PART_SIZE:
                    chunk = src.read(min(1 << 20, PART_SIZE - size))
                    if not chunk:
                        break
                    dst.write(chunk)
                    h.update(chunk)
                    size += len(chunk)
            if size == 0:
                (zip_path.parent / name).unlink()
                break
            parts.append({"name": name, "size": size, "sha256": h.hexdigest()})
            i += 1
    (zip_path.parent / (zip_path.name + ".parts.json")).write_text(json.dumps(parts, indent=1), encoding="utf-8")
    return parts


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("--skip-pip", action="store_true", help="giữ thư viện đã cài ở lần build trước")
    args = ap.parse_args()
    zip_path = build(args.version, args.skip_pip)
    h = hashlib.sha256()
    with zip_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    size = zip_path.stat().st_size
    print(f"\nĐã build: {zip_path} ({size / 1e9:.2f} GB)\nSHA-256: {h.hexdigest()}")
    parts = split_parts(zip_path)
    print(f"Đã chia thành {len(parts)} phần (≤ {PART_SIZE // (1024 * 1024)} MB/phần) cho GitHub Releases.")
    print(f"Upload: .venv/Scripts/python.exe tools/publish_github.py runtime {args.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
