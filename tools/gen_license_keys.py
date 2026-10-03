"""Tạo cặp khoá Ed25519 cho heartbeat có chữ ký (user-management-plan.md 13.9).

    .venv\\Scripts\\python.exe tools\\gen_license_keys.py

- Khoá BÍ MẬT (PKCS8 base64) ghi vào secrets/heartbeat_signing_key.txt (đã
  .gitignore). Dán vào Supabase → Edge Functions → Secrets, tên
  HEARTBEAT_SIGNING_KEY. KHÔNG commit, không gửi qua chat, không nhúng vào app.
- Khoá CÔNG KHAI (32 byte base64) ghi thẳng vào app/license/constants.py
  (HEARTBEAT_PUBLIC_KEY) — lộ cũng không sao.

Chạy lại = đổi khoá: mọi bản app cũ sẽ không kiểm tra được chữ ký mới nữa
(rơi vào ân hạn offline rồi khoá) — chỉ làm khi khoá bí mật bị lộ.
"""

from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent.parent
SECRET_FILE = ROOT / "secrets" / "heartbeat_signing_key.txt"
CONSTANTS = ROOT / "app" / "license" / "constants.py"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if SECRET_FILE.exists() and "--force" not in sys.argv:
        print(f"Đã có {SECRET_FILE} — thêm --force nếu thật sự muốn đổi khoá.")
        return 1
    key = Ed25519PrivateKey.generate()
    private_b64 = base64.b64encode(
        key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    ).decode()
    public_b64 = base64.b64encode(
        key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()

    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    SECRET_FILE.write_text(private_b64 + "\n", encoding="utf-8")

    text = CONSTANTS.read_text(encoding="utf-8")
    text, n = re.subn(r'^HEARTBEAT_PUBLIC_KEY = ".*"$', f'HEARTBEAT_PUBLIC_KEY = "{public_b64}"', text, flags=re.M)
    if n != 1:
        print("Không tìm thấy dòng HEARTBEAT_PUBLIC_KEY trong constants.py")
        return 1
    CONSTANTS.write_text(text, encoding="utf-8")
    print(f"Khoá bí mật: {SECRET_FILE}  (dán vào Supabase secret HEARTBEAT_SIGNING_KEY)")
    print(f"Khoá công khai đã ghi vào {CONSTANTS}: {public_b64}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
