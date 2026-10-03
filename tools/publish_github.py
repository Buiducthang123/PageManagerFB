"""Phát hành lên GitHub Releases (thay R2 khi chưa có thẻ thanh toán).

Repo phát hành PUBLIC, chỉ chứa file build — KHÔNG có source:
    https://github.com/<RELEASES_REPO>/releases

    .venv\\Scripts\\python.exe tools\\publish_github.py setup              # tạo repo phát hành (1 lần)
    .venv\\Scripts\\python.exe tools\\publish_github.py runtime 1.0.0      # upload các phần runtime + sinh installer\\runtime_parts.iss
    .venv\\Scripts\\python.exe tools\\publish_github.py app 1.0.1          # upload app-1.0.1.zip + ghi vào bảng app_releases
    .venv\\Scripts\\python.exe tools\\publish_github.py installer 1.0.1    # upload Setup.exe

Cần: GitHub CLI đã `gh auth login`; Supabase CLI đã `npx supabase login` (bước app).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASES_REPO = os.environ.get("RELEASES_REPO", "Buiducthang123/oddlylab-reup-releases")
SUPABASE_REF = "szmjvnhgolzeztcgvups"


def gh_exe() -> str:
    found = shutil.which("gh")
    if found:
        return found
    default = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "GitHub CLI" / "gh.exe"
    if default.exists():
        return str(default)
    raise SystemExit("Chưa cài GitHub CLI (winget install GitHub.cli) hoặc chưa gh auth login")


def gh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    print("$ gh", " ".join(args))
    return subprocess.run([gh_exe(), *args], check=check, text=True, capture_output=not check)


def asset_url(tag: str, name: str) -> str:
    return f"https://github.com/{RELEASES_REPO}/releases/download/{tag}/{name}"


def ensure_release(tag: str, title: str, notes: str) -> None:
    if gh("release", "view", tag, "--repo", RELEASES_REPO, check=False).returncode != 0:
        gh("release", "create", tag, "--repo", RELEASES_REPO, "--title", title, "--notes", notes or title)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_setup() -> None:
    if gh("repo", "view", RELEASES_REPO, check=False).returncode == 0:
        print(f"Repo {RELEASES_REPO} đã có.")
        return
    gh("repo", "create", RELEASES_REPO, "--public",
       "--description", "Bản phát hành OddlyLab Reup (chỉ file build, không có source)", "--add-readme")


def cmd_runtime(version: str) -> None:
    manifest = ROOT / "dist" / "runtime" / f"runtime-{version}.zip.parts.json"
    if not manifest.exists():
        raise SystemExit(f"Chưa có {manifest} — chạy tools/build_runtime.py {version} trước")
    parts = json.loads(manifest.read_text(encoding="utf-8"))
    tag = f"runtime-{version}"
    ensure_release(tag, f"Runtime {version}", "Thành phần chạy (Python + thư viện + Chromium + ffmpeg). Bộ cài tự tải.")
    for p in parts:
        f = manifest.parent / p["name"]
        if sha256(f) != p["sha256"]:
            raise SystemExit(f"{f.name} sai SHA-256 so với manifest — build lại runtime")
        gh("release", "upload", tag, str(f), "--repo", RELEASES_REPO, "--clobber")

    # Sinh đoạn Pascal cho Inno Setup: thêm từng phần vào trang tải (kèm SHA-256)
    # và lệnh ghép các phần lại thành runtime.zip.
    adds = "\n".join(
        f"  DownloadPage.Add('{asset_url(tag, p['name'])}', 'rt.part{i}', '{p['sha256']}');"
        for i, p in enumerate(parts, 1)
    )
    concat = "+".join(f"rt.part{i}" for i in range(1, len(parts) + 1))
    inc = ROOT / "installer" / "runtime_parts.iss"
    inc.write_text(
        "; SINH TỰ ĐỘNG bởi tools/publish_github.py — không sửa tay.\n"
        f"#define RuntimeVersion \"{version}\"\n\n"
        "[Code]\n"
        "procedure AddRuntimeParts(DownloadPage: TDownloadWizardPage);\nbegin\n"
        f"{adds}\nend;\n\n"
        "function RuntimeConcatArgs(): String;\nbegin\n"
        f"  Result := '/c copy /b /y {concat} runtime.zip';\nend;\n",
        encoding="utf-8-sig",
    )
    print(f"\nĐã upload {len(parts)} phần. Đã ghi {inc} — build lại Setup.exe.")


def cmd_app(version: str) -> None:
    z = ROOT / "dist" / "releases" / f"app-{version}.zip"
    rel_json = ROOT / "build" / "release" / "stage" / version / "release.json"
    if not z.exists():
        raise SystemExit(f"Chưa có {z} — chạy tools/build_release.py {version} trước")
    meta = json.loads(rel_json.read_text(encoding="utf-8")) if rel_json.exists() else {}
    tag = f"app-{version}"
    ensure_release(tag, f"App {version}", meta.get("notes") or f"Bản {version}")
    gh("release", "upload", tag, str(z), "--repo", RELEASES_REPO, "--clobber")
    digest, size = sha256(z), z.stat().st_size
    notes = (meta.get("notes") or "").replace("'", "''")
    sql = (
        "insert into public.app_releases (layer, version, url, sha256, size_bytes, notes, min_runtime) values "
        f"('app', '{version}', '{asset_url(tag, z.name)}', '{digest}', {size}, '{notes}', '{meta.get('min_runtime') or '1.0.0'}') "
        "on conflict (layer, name, variant, version) do update set url = excluded.url, sha256 = excluded.sha256, "
        "size_bytes = excluded.size_bytes, notes = excluded.notes, min_runtime = excluded.min_runtime;"
    )
    npx = shutil.which("npx") or "npx"
    print("$ supabase db query (ghi app_releases)")
    subprocess.run([npx, "--yes", "supabase", "db", "query", "--linked", "--project-ref", SUPABASE_REF, sql],
                   check=True, shell=sys.platform == "win32")
    print(f"\nĐã phát hành bản {version}. Máy user thấy \"Có bản mới\" trong ≤ 30 phút.")


def cmd_installer(version: str) -> None:
    exe = ROOT / "dist" / "installer" / f"OddlyLabReup-Setup-{version}.exe"
    if not exe.exists():
        raise SystemExit(f"Chưa có {exe}")
    tag = f"app-{version}"
    ensure_release(tag, f"App {version}", f"Bản {version}")
    gh("release", "upload", tag, str(exe), "--repo", RELEASES_REPO, "--clobber")
    print(f"\nLink bộ cài gửi cho user: {asset_url(tag, exe.name)}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["setup", "runtime", "app", "installer"])
    ap.add_argument("version", nargs="?")
    a = ap.parse_args()
    if a.what == "setup":
        cmd_setup()
    elif not a.version:
        raise SystemExit("Thiếu số phiên bản")
    else:
        {"runtime": cmd_runtime, "app": cmd_app, "installer": cmd_installer}[a.what](a.version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
