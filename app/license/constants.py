"""Thông tin công khai nhúng vào app (user-management-plan.md mục 9).

Chỉ chứa thứ LỘ CŨNG KHÔNG SAO: URL project, anon key (an toàn khi RLS đúng),
khoá công khai kiểm tra chữ ký heartbeat. Không bao giờ đặt service role key
hay khoá bí mật ở đây.
"""

APP_VERSION = "1.0.0"

SUPABASE_URL = "https://szmjvnhgolzeztcgvups.supabase.co"
SUPABASE_ANON_KEY = "sb_publishable_Kw_o-qCbEapct4KHwftM_w_YY118PH7"

# Ed25519, 32 byte base64 — tools/gen_license_keys.py ghi vào đây.
HEARTBEAT_PUBLIC_KEY = "ueND9bM9wp+n9yErEZRlfrn58gJ3huRlyVO4hEPy1XQ="

# Ghép với MachineGuid trước khi băm, để không gửi nguyên mã gốc lên server.
# Coi như sẽ lộ (mục 8) — chỉ làm khó thêm, không phải lớp bảo vệ chính.
DEVICE_SALT = "reupvjp-device-v1"

# App ID Facebook là thông tin công khai (nằm trong mọi URL đăng nhập
# Facebook). Secret KHÔNG ở đây — nằm ở Edge Function fb-token (13.1).
FB_APP_ID = "1048640917780452"
