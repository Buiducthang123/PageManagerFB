"""Hai thư mục gốc (user-management-plan.md mục 12):

- CODE_ROOT: nơi chứa code app + frontend build. Chạy từ source = thư mục repo;
  bản đóng gói = `<cài đặt>\\app\\<phiên bản>\\` (mỗi bản 1 thư mục, cập nhật
  không ghi đè bản đang chạy).
- INSTALL_ROOT: nơi chứa dữ liệu user (`workspace\\`, `.env`) — KHÔNG BAO GIỜ
  bị đụng khi cập nhật. Launcher đặt qua biến môi trường REUP_INSTALL_ROOT;
  chạy từ source thì trùng CODE_ROOT như trước giờ.
"""

from __future__ import annotations

import os
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent.parent

_install = os.environ.get("REUP_INSTALL_ROOT", "").strip()
INSTALL_ROOT = Path(_install).resolve() if _install else CODE_ROOT

ENV_FILE = INSTALL_ROOT / ".env"
DEFAULT_WORKSPACE = INSTALL_ROOT / "workspace"
