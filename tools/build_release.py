"""Build lớp App để phát hành (user-management-plan.md mục 8, 12).

    .venv\\Scripts\\python.exe tools\\build_release.py 1.0.1 [--notes "..."] [--min-runtime 1.0.0] [--skip-frontend]

Ra `dist\\releases\\app-<bản>.zip` gồm:
    release.json
    app.cp313-win_amd64.pyd        cả gói `app` biên dịch Nuitka (C → mã máy)
    app\\assets\\...                 font, ảnh (dữ liệu, không phải code)
    app\\stages\\<worker>.py         worker chạy bằng tiến trình con theo đường
                                    dẫn file (hardsub, demucs) — dạng source,
                                    không chứa logic kiểm tra quyền
    frontend\\dist\\...              giao diện đã build

Thư mục `app\\` trong zip KHÔNG có __init__.py, nên Python nạp gói `app` từ
file .pyd chứ không từ thư mục đó.

Cần: Nuitka (pip) + MSVC Build Tools (Python 3.13 không dùng được MinGW).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "release"
OUT = ROOT / "dist" / "releases"

# Chạy bằng tiến trình con theo ĐƯỜNG DẪN FILE → phải có file .py thật.
SOURCE_WORKERS = [
    "stages/hardsub_worker.py", "stages/sttn_net.py", "stages/_demucs_worker.py", "stages/_model_fetch_worker.py",
]
DATA_DIRS = ["assets"]


def run(cmd: list[str], cwd: Path, env: dict | None = None) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd), check=True, env=env,
                   shell=sys.platform == "win32" and cmd[0] in ("npm", "npx"))


def _latest_dir(base: Path) -> Path:
    dirs = sorted((d for d in base.iterdir() if d.is_dir() and d.name[0].isdigit()),
                  key=lambda d: tuple(int(x) for x in re.findall(r"\d+", d.name)))
    if not dirs:
        raise SystemExit(f"Không thấy phiên bản nào trong {base}")
    return dirs[-1]


def msvc_env() -> dict:
    """Môi trường biên dịch MSVC x64 dựng thẳng từ thư mục cài (thay cho
    vcvars64.bat — trên máy build từng gặp vcvars dừng giữa chừng vì không gọi
    được vswhere, khiến Nuitka/SCons báo "CC is not set")."""
    pf86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    vswhere = pf86 / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    out = subprocess.run(
        [str(vswhere), "-latest", "-products", "*", "-requires",
         "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if not out:
        raise SystemExit("Chưa cài Visual Studio Build Tools (workload C++)")
    vc = _latest_dir(Path(out) / "VC" / "Tools" / "MSVC")
    kits = pf86 / "Windows Kits" / "10"
    sdk_ver = _latest_dir(kits / "Include").name
    env = dict(os.environ)
    env["INCLUDE"] = ";".join(str(p) for p in [
        vc / "include", *(kits / "Include" / sdk_ver / s for s in ("ucrt", "shared", "um", "winrt", "cppwinrt")),
    ])
    env["LIB"] = ";".join(str(p) for p in [
        vc / "lib" / "x64", *(kits / "Lib" / sdk_ver / s / "x64" for s in ("ucrt", "um")),
    ])
    env["PATH"] = ";".join([str(vc / "bin" / "Hostx64" / "x64"), str(kits / "bin" / sdk_ver / "x64"), env.get("PATH", "")])
    env.update({
        "VCINSTALLDIR": str(Path(out) / "VC") + "\\",
        "VCToolsInstallDir": str(vc) + "\\",
        "WindowsSdkDir": str(kits) + "\\",
        "WindowsSDKVersion": sdk_ver + "\\",
        "VSCMD_ARG_TGT_ARCH": "x64",
        "Platform": "x64",
    })
    return env


def stage_source(version: str) -> Path:
    """Chép gói app sang thư mục tạm, ghi số phiên bản vào constants."""
    src_stage = BUILD / "src"
    shutil.rmtree(src_stage, ignore_errors=True)
    shutil.copytree(ROOT / "app", src_stage / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    const = src_stage / "app" / "license" / "constants.py"
    text = const.read_text(encoding="utf-8")
    text, n = re.subn(r'^APP_VERSION = ".*"$', f'APP_VERSION = "{version}"', text, flags=re.M)
    if n != 1:
        raise SystemExit("Không ghi được APP_VERSION vào constants.py")
    for key in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "HEARTBEAT_PUBLIC_KEY"):
        if re.search(rf'^{key} = ""$', text, flags=re.M):
            raise SystemExit(f"constants.py thiếu {key} — bản đóng gói sẽ bị khoá (misconfigured)")
    const.write_text(text, encoding="utf-8")
    return src_stage


def compile_app(src_stage: Path) -> Path:
    out_dir = BUILD / "nuitka"
    shutil.rmtree(out_dir, ignore_errors=True)
    cmd = [
        sys.executable, "-m", "nuitka",
        "--module", "app",
        "--include-package=app",
        # Worker chạy riêng bằng tiến trình con — không cần (và không nên) biên dịch vào gói.
        "--nofollow-import-to=app.stages.hardsub_worker",
        "--nofollow-import-to=app.stages.sttn_net",
        "--nofollow-import-to=app.stages._demucs_worker",
        "--nofollow-import-to=app.stages._model_fetch_worker",
        # KHÔNG truyền --msvc: khi cl.exe đã có trong PATH (msvc_env), Nuitka
        # dùng thẳng môi trường đó; truyền --msvc là SCons tự dò lại và hỏng.
        "--assume-yes-for-downloads",
        "--remove-output",
        f"--output-dir={out_dir}",
    ]
    run(cmd, src_stage, env=msvc_env())
    pyds = list(out_dir.glob("app.*.pyd"))
    if len(pyds) != 1:
        raise SystemExit(f"Không thấy file .pyd sau khi biên dịch: {pyds}")
    return pyds[0]


def build_frontend() -> Path:
    run(["npm", "run", "build"], ROOT / "frontend")
    dist = ROOT / "frontend" / "dist"
    if not (dist / "index.html").exists():
        raise SystemExit("Build frontend lỗi: thiếu dist/index.html")
    return dist


def assemble(version: str, pyd: Path, frontend_dist: Path, min_runtime: str, notes: str) -> Path:
    stage = BUILD / "stage" / version
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    shutil.copy2(pyd, stage / pyd.name)
    for rel in SOURCE_WORKERS:
        dst = stage / "app" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "app" / rel, dst)
    for rel in DATA_DIRS:
        shutil.copytree(ROOT / "app" / rel, stage / "app" / rel, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(frontend_dist, stage / "frontend" / "dist")
    # App tự chép đè lên <cài đặt>\launcher.pyw lúc khởi động (updater.sync_launcher).
    shutil.copy2(ROOT / "tools" / "launcher" / "launcher.py", stage / "launcher.py")
    # Không được có __init__.py nào trong thư mục app/ — nếu có, Python nạp
    # source thay vì .pyd.
    leaked = list((stage / "app").rglob("__init__.py"))
    if leaked:
        raise SystemExit(f"Lọt __init__.py vào gói phát hành: {leaked}")
    (stage / "release.json").write_text(json.dumps({
        "version": version,
        "min_runtime": min_runtime,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "notes": notes,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    OUT.mkdir(parents=True, exist_ok=True)
    zip_path = OUT / f"app-{version}.zip"
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for f in sorted(stage.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(stage).as_posix())
    return zip_path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("--notes", default="")
    ap.add_argument("--min-runtime", default="1.0.0")
    ap.add_argument("--skip-frontend", action="store_true", help="dùng frontend/dist đã build sẵn")
    args = ap.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        raise SystemExit("Phiên bản dạng 1.2.3")

    src_stage = stage_source(args.version)
    pyd = compile_app(src_stage)
    dist = (ROOT / "frontend" / "dist") if args.skip_frontend else build_frontend()
    zip_path = assemble(args.version, pyd, dist, args.min_runtime, args.notes)
    digest, size = sha256(zip_path), zip_path.stat().st_size
    print(f"\nĐã build: {zip_path} ({size / 1e6:.1f} MB)\nSHA-256: {digest}")
    notes_sql = args.notes.replace("'", "''")
    print("\nUpload zip lên nơi lưu file (R2/GitHub Releases), rồi chạy trong SQL Editor (thay <URL>):")
    print(
        "insert into public.app_releases (layer, version, url, sha256, size_bytes, notes, min_runtime)\n"
        f"values ('app', '{args.version}', '<URL>', '{digest}', {size}, '{notes_sql}', '{args.min_runtime}');"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
