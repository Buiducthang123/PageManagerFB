# Quản lý user qua Supabase — Kế hoạch

> Trạng thái: **bản nháp chờ duyệt** (đã rà soát lần 2 ngày 01/10 — xem mục 16). Chưa viết code phần Supabase. Đã làm: cài đặt thư mục draft CapCut (mục 14). Các mục đánh dấu ❓ cần bạn quyết; mục 🔴 ở phần 13 cần xử lý trước khi phát hành.

## 1. Mục tiêu

Phát hành tool cho nhiều người dùng, mỗi người **tự chạy tool trên máy của họ** như hiện tại. Bạn là admin duy nhất và điều khiển từ xa:

- Ai được đăng nhập, phiên đăng nhập kéo dài bao lâu, đá ai ra.
- Mỗi user được dùng chức năng nào.
- Xem thống kê sử dụng của từng user.

**Không đưa lên Supabase**: video, model AI, cookie/phiên đăng nhập TikTok/Facebook/Douyin, Gemini key, nội dung dự án. Toàn bộ xử lý video vẫn chạy local.

## 2. Kiến trúc

```
┌──────────────── Máy user ────────────────┐          ┌────────────── Supabase (free) ──────────────┐
│ React ──► FastAPI (1 cổng khi đóng gói)  │          │ Auth: email + mật khẩu, TẮT tự đăng ký      │
│                    │                     │  HTTPS   │ Postgres: profiles, admin_notes, usage_stats│
│                    ├─ đăng nhập ─────────┼─────────►│   project_events, device_events,            │
│                    │                     │          │   app_config, app_releases                  │
│                    ├─ heartbeat 5 phút ──┼─────────►│ Edge Function: heartbeat — trả phán quyết   │
│                    │   (gửi số liệu +    │◄─────────┤   CÓ CHỮ KÝ Ed25519 (mục 13.9)              │
│                    │    nonce, nhận      │          │ RPC security definer: claim_device,         │
│                    │    phán quyết ký)   │          │   report_usage, my_profile                  │
│                    │                     │          │ Hàm chung session_status() — xem mục 4      │
│                    ├─ đổi token Facebook ┼─────────►│ Edge Function: fb-token (giữ FB secret)     │
│                    └─ chặn API theo quyền│          │ Edge Function: admin-users (tạo/đặt lại MK) │
│   (chỉ tin phán quyết có chữ ký hợp lệ)  │          │ RLS: user KHÔNG đọc/ghi thẳng bảng nào      │
└──────────────────────────────────────────┘          └─────────────────────────────────────────────┘
File cập nhật app/runtime/model: Cloudflare R2 (mục 12), không nằm trên Supabase.
```

- **Đăng nhập**: màn hình login trong app. Backend local gọi Supabase Auth và lưu phiên vào `%LOCALAPPDATA%\ReupVideoVjpPro\auth.json` (không để trong workspace: đổi/copy workspace không mang phiên theo; token mã hoá DPAPI).
- **Heartbeat** khoảng 5 phút/lần: backend gửi số liệu thống kê + một `nonce` ngẫu nhiên lên, nhận về **phán quyết do server tính và ký** (còn dùng được không, lý do nếu không, quyền, hạn phiên, giờ server). App chỉ chấp nhận phán quyết có chữ ký hợp lệ và đúng `nonce` vừa gửi (mục 13.9).
- **Chặn quyền ở backend**: mọi API của chức năng chưa được cấp trả `403`. Frontend chỉ ẩn menu cho gọn, không dựa vào việc ẩn đó để bảo mật.

## 3. Dữ liệu trên Supabase

```sql
-- 1 dòng / user, tạo tự động khi admin tạo tài khoản
create table profiles (
  id               uuid primary key references auth.users on delete cascade,
  email            text not null,
  display_name     text default '',
  role             text not null default 'user' check (role in ('admin','user')),
  enabled          boolean not null default false,       -- false = khoá; mặc định KHOÁ, admin bật khi tạo user (13.10)
  features         text[] not null default '{}',         -- xem mục 5
  session_ttl_hours int,                                 -- null = vĩnh viễn
  force_logout_at  timestamptz,                          -- mọi phiên đăng nhập trước mốc này bị huỷ
  active_session_id uuid,                                -- session Supabase DUY NHẤT hợp lệ (claim session_id trong JWT, mục 4)
  active_device_id text,                                 -- mã máy, CHỈ để hiển thị/thống kê, không dùng để quyết định
  active_device_name text,                               -- tên máy, để hiện cho admin/user
  active_login_at  timestamptz,
  account_expires_at timestamptz,                        -- null = không hết hạn (❓ mục 15)
  created_at       timestamptz default now()
);

-- Ghi chú của admin về user — tách bảng riêng vì user KHÔNG được đọc
create table admin_notes (
  user_id    uuid primary key references profiles on delete cascade,
  note       text not null default '',
  updated_at timestamptz default now()
);

-- 1 dòng / user, app ghi đè mỗi lần heartbeat
create table usage_stats (
  user_id              uuid primary key references profiles on delete cascade,
  tiktok_accounts      int not null default 0,   -- số HIỆN TẠI trên máy
  facebook_pages       int not null default 0,   -- số HIỆN TẠI trên máy
  app_version          text,
  last_seen_at         timestamptz               -- tên máy: lấy profiles.active_device_name
);

-- Nhật ký mỗi lần claim_device — để phát hiện 1 tài khoản bị chia cho nhiều người
-- (2 máy luân phiên đăng nhập đá nhau). Admin xem số lần đổi máy / 7 ngày.
create table device_events (
  id          bigint generated always as identity primary key,
  user_id     uuid not null references profiles on delete cascade,
  device_id   text not null,
  device_name text,
  session_id  uuid,
  at          timestamptz not null default now()
);

-- Sự kiện cộng dồn — xoá dự án trên máy không làm giảm số (xem mục 6)
create table project_events (
  user_id     uuid not null references profiles on delete cascade,
  project_id  text not null,                 -- id dự án local
  type        text not null check (type in ('created','completed')),
  at          timestamptz not null default now(),
  primary key (user_id, project_id, type)    -- gửi trùng không đếm 2 lần
);
-- Số "đã tạo"/"hoàn thành" KHÔNG lưu thành cột (dễ lệch với sự kiện):
-- view admin_user_stats = usage_stats + count(*) project_events theo user + type.

-- Cấu hình chung, chỉ 1 dòng, admin sửa
create table app_config (
  id                     int primary key default 1 check (id = 1),
  min_app_version        text,              -- bản cũ hơn bị chặn, phải cập nhật (mục 12). So sánh SEMVER
                                            -- (tách số), KHÔNG so chuỗi: '1.10' < '1.9' nếu so text
  offline_grace_minutes  int not null default 360,   -- 6 giờ (mục 13.2)
  heartbeat_seconds      int not null default 300,
  login_notice           text default ''    -- thông báo hiện ở màn đăng nhập (bảo trì…)
);
```

- **RLS** (chi tiết ở 13.3): user **không đọc/ghi thẳng** bảng nào (trừ đọc `app_config`). Thông tin của chính mình (quyền, hạn phiên, tên máy) nằm trong phán quyết heartbeat có chữ ký — không cần RPC đọc profile riêng. Admin đọc/sửa được tất cả, kiểm tra bằng `is_admin()` phía server.
- **Mọi thao tác ghi của user** đi qua RPC `security definer` (`claim_device`, `report_usage`) hoặc Edge Function `heartbeat`. Tất cả tự lấy `auth.uid()` và `session_id` từ JWT, không nhận `user_id` từ app gửi lên.
- `report_usage` giới hạn tối đa 200 sự kiện/lần gọi và bỏ qua `project_id` dài bất thường, để app bị sửa không spam được bảng.
- Trigger trên `auth.users` tự tạo dòng `profiles` (+ `usage_stats`, `admin_notes`) khi admin tạo tài khoản.
- Dung lượng rất nhỏ (vài KB/user), gói free 500 MB DB là dư.

## 4. Phiên đăng nhập

Supabase Auth chỉ cho đặt **một** thời hạn token chung cho cả project. Vì vậy hạn phiên theo từng user do **một hàm SQL chung `session_status()` phía server** quyết định, dùng `now()` của Postgres (13.2). App chỉ làm theo kết quả, không tự tính.

`session_status()` **không nhận tham số từ app**. Nó đọc `auth.uid()` và claim `session_id` trong JWT (Supabase tự cấp, app không giả được), so với `profiles.active_session_id`. Mã máy do app tự khai nên chỉ dùng để hiển thị — nếu dùng mã máy để quyết định, một app bị sửa chỉ cần gửi mã của máy khác là 2 máy dùng chung được 1 tài khoản.

Cùng một hàm này được gọi ở **mọi nơi** cần biết user còn hợp lệ không: Edge Function `heartbeat` (13.9), Edge Function `fb-token` (13.1), Edge Function admin. Nhờ vậy user đã bị đá ra hoặc bị khoá không thể vòng qua một đường khác (ví dụ vẫn lấy được token Facebook vì phiên Supabase chưa hết hạn).

Thứ tự kiểm tra:

| Điều kiện (tính ở server) | Kết quả trả về app |
|---|---|
| `enabled = false` | Đăng xuất, báo "Tài khoản đã bị khoá" |
| `account_expires_at < now()` | Đăng xuất, báo "Tài khoản đã hết hạn sử dụng — liên hệ admin" (❓ xem mục 15) |
| `session_id` trong JWT khác `active_session_id` | Đăng xuất, báo "Tài khoản đã đăng nhập trên máy khác (`active_device_name`)" |
| `force_logout_at > active_login_at` | Đăng xuất, báo "Admin đã đăng xuất phiên này" |
| `session_ttl_hours` có giá trị và `now() > active_login_at + ttl` | Đăng xuất, báo "Phiên đã hết hạn" |
| `session_ttl_hours = null` | Không tự hết hạn (vĩnh viễn) |
| App cũ hơn `app_config.min_app_version` | Không cho dùng, báo "Cần cập nhật lên bản mới" (mục 12) |

- **Đá user ra ngay**: admin bấm "Đăng xuất", hệ thống ghi `force_logout_at = now()`, đồng thời **xoá các session của user** trong `auth.sessions` (kéo theo refresh token) bằng một hàm `security definer` chỉ admin gọi được. Lưu ý: Admin API `signOut` của Supabase nhận JWT của chính user chứ không nhận user id, nên không dùng được cho việc này. Máy user bị đăng xuất **chậm nhất sau 1 chu kỳ heartbeat (~5 phút)**, và từ đó không gọi được gì lên Supabase nữa. Job đang chạy dở (render, đăng bài) ❓ cho chạy nốt hay dừng ngay? Đề xuất mặc định: **chạy nốt**, giống lúc bị khoá vì mất mạng (13.2), để không bỏ dở một video đang render; việc mới thì không nhận nữa.
  - **"Đá ra" không phải là "chặn"**: user biết mật khẩu thì đăng nhập lại ngay được (phiên mới có `active_login_at` mới hơn `force_logout_at`). Muốn chặn thật thì **khoá tài khoản** (`enabled = false`) hoặc **đặt lại mật khẩu**. Trang admin ghi rõ điều này cạnh nút.
- **User tự đăng xuất**: nút "Đăng xuất" trong app, xoá phiên trên máy và gỡ máy khỏi tài khoản (`active_session_id = null`) để đăng nhập máy khác ngay.
- **Mất mạng / Supabase lỗi**: cho dùng tiếp trong **thời gian ân hạn** (mặc định 6 giờ app chạy offline, không tính theo đồng hồ máy). Hết ân hạn thì hiện modal khoá cho tới khi kết nối lại được. Chi tiết cách tính và giao diện ở mục 13.2.
- **Mỗi tài khoản chỉ 1 máy tại 1 thời điểm** (đã chốt). Đăng nhập máy mới thì máy cũ bị đá:
  - **Mã máy** = SHA-256 của **MachineGuid** Windows (registry `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid`, Windows tự tạo lúc cài đặt) ghép với 1 chuỗi bí mật cố định của app, để không gửi nguyên mã gốc lên server. Không lưu file nào, nên copy thư mục `workspace` sang máy khác thì mã máy không đi theo (đã chốt).
    - Đổi ổ cứng, RAM, card hay cập nhật Windows: mã **không đổi**.
    - **Cài lại Windows**: mã **đổi**, nên user đăng nhập lại là xong. Máy mới tự thay máy cũ, **không cần admin gỡ**.
    - Ngoại lệ hiếm: nhiều máy bung từ cùng một bản ghost Windows có thể trùng MachineGuid. Gặp thì chạy lại sysprep hoặc đổi mã đó.
  - Khi đăng nhập, app gọi RPC `claim_device(mã máy, tên máy)`. RPC lấy `session_id` từ JWT, ghi đè `active_session_id`/`active_device_id`/`active_device_name`/`active_login_at`, ghi 1 dòng `device_events`, rồi **xoá các session Supabase khác** của user (máy cũ mất luôn refresh token). Máy vừa đăng nhập luôn thắng.
  - `claim_device` chỉ chấp nhận session **vừa tạo** (`auth.sessions.created_at` trong vòng 2 phút), để máy cũ còn access token không thể gọi lại `claim_device` mà "giành" lại phiên khi không nhập mật khẩu.
  - Mỗi lần heartbeat, máy nào có `session_id` không còn là `active_session_id` thì tự đăng xuất, kèm thông báo đang dùng ở máy nào. Máy cũ bị đá **chậm nhất ~5 phút**.
  - Trang admin hiện **số lần đổi máy trong 7 ngày** (từ `device_events`). Con số cao bất thường = tài khoản đang bị chia cho nhiều người dùng luân phiên.
  - Máy cũ đang **mất mạng** thì không biết mình bị đá, nên vẫn dùng được tới hết thời gian ân hạn (6 giờ). Có mạng lại là bị đá ngay.
  - Admin thấy mỗi user đang dùng ở máy nào. Có nút **"Gỡ máy"** để xoá `active_session_id` (và session Supabase tương ứng), bắt đăng nhập lại.

## 5. Danh sách quyền (đề xuất, theo các trang đang có)

| Mã quyền | Chức năng | Trang / API |
|---|---|---|
| `projects` | Tạo & xử lý dự án (tải, transcribe, dịch, TTS, xuất video, ảnh bìa) | `/`, `/projects/:id` |
| `capcut` | Dựng draft CapCut | mục CapCut trong dự án |
| `tiktok_publish` | Đăng TikTok | mục Đăng TikTok, `/accounts` |
| `facebook_publish` | Đăng Facebook Reels | mục Đăng Facebook, `/accounts` |
| `automated` | Dự án tự động (crawl + tự chạy + lịch đăng) | `/automated` |
| `download` | Tải video Douyin hàng loạt | `/download` |
| `merge` | Ghép video | `/merge` |
| `clean_video` | Xoá hardsub | `/clean-video` |
| `monitor` | Màn giám sát tiến trình | `/monitor` |
| `cleanup` | Dọn dữ liệu | `/cleanup` |

**Kiểm tra quyền theo HÀNH ĐỘNG, không theo trang.** Ví dụ user có `automated` nhưng không có `tiktok_publish`: Dự án tự động vẫn crawl và xử lý video, nhưng bước tự đăng TikTok phải bị chặn (và ghi rõ lý do trong hàng đợi). Tương tự, `/accounts` mở khi có ít nhất 1 trong 2 quyền đăng bài, và chỉ hiện đúng phần TikTok hoặc Facebook mà user được cấp.

`/settings` luôn mở. **Mỗi user tự nhập Gemini API key của họ** (đã chốt), không dùng key của admin. Key nằm trên máy user và không gửi lên Supabase.

## 6. Thống kê

| Chỉ số | Cách đếm (local, không gửi nội dung) |
|---|---|
| Tài khoản liên kết | Số tài khoản TikTok (`workspace/accounts`) + số Page Facebook đã thêm, gửi riêng từng loại. Đây là số **hiện tại** (xoá tài khoản thì giảm). |
| Dự án đã tạo | **Cộng dồn** (đã chốt): mỗi lần tạo dự án ghi 1 sự kiện `created`. Xoá dự án **không** làm giảm. |
| Dự án hoàn thành | **Cộng dồn** (đã chốt): dự án **dựng CapCut xong** HOẶC **xuất video (không qua CapCut) xong**. Mỗi dự án chỉ tính 1 lần (sự kiện `completed`), dù sau đó dựng/xuất lại nhiều lần hay làm cả hai. |
| Hoạt động gần nhất | Thời điểm heartbeat cuối, kèm tên máy và phiên bản app |

Cách ghi: sự kiện lưu tạm vào hàng đợi trên máy (`workspace/usage_outbox.json`) rồi gửi lên kèm heartbeat. Mất mạng thì gửi bù sau, không mất số. Bảng `project_events` có khoá duy nhất `(user_id, project_id, type)` nên gửi trùng cũng không đếm 2 lần.

Dự án đã có sẵn trước khi bật tính năng này: **đề xuất chỉ đếm từ lúc bật** (đơn giản, không phải đoán dự án cũ đã "hoàn thành" hay chưa). ❓ Đổi nếu bạn muốn tính cả dự án cũ.

## 7. Trang admin

Một trang `/admin` trong chính app, chỉ hiện khi đăng nhập bằng tài khoản `role = admin`:

- **Danh sách user**: email, tên, trạng thái (đang hoạt động / quá 1 ngày không thấy / bị khoá), lần thấy cuối, phiên bản app, 4 chỉ số thống kê, số lần đổi máy 7 ngày (mục 4).
- **Tạo user**: email + mật khẩu ban đầu. Việc này cần quyền service role, xem mục 9.
- **Sửa user**: bật/tắt từng quyền, chọn thời hạn phiên (24 giờ / 7 ngày / 30 ngày / tuỳ chỉnh / vĩnh viễn), khoá/mở khoá, ghi chú.
- **Nút "Đăng xuất ngay"**, **"Gỡ máy"** và **"Đặt lại mật khẩu"**. Danh sách user có thêm cột "Đang dùng trên máy".
- **Phát hành bản mới**: xem mục 12.

Giai đoạn đầu có thể quản lý thẳng trong Table Editor của Supabase, chưa cần trang admin.

## 8. Chống bẻ khoá

**Đã chốt: đóng gói app để không lộ source.**

- Dùng **Nuitka** cho code của mình (`app/`). Nuitka dịch Python sang C rồi biên dịch thành mã máy (`.pyd`), nên rất khó dịch ngược.
- **Không dùng PyInstaller cho phần này.** PyInstaller chỉ nén file `.pyc`, có công cụ giải nén và dịch ngược ra gần đúng source trong vài phút, gần như không che được gì.
- Thư viện bên thứ 3 (torch, onnxruntime…) giữ nguyên dạng thường, vì không cần giấu và biên dịch chúng rất lâu.
- Frontend build ra file tĩnh (JS đã nén). JS luôn đọc được, nhưng mọi kiểm tra quyền thật đều nằm ở backend đã biên dịch, nên sửa JS cũng không mở được chức năng.
- Mức này chặn được người dùng bình thường và người biết chút code. Người chuyên dịch ngược mã máy vẫn có thể bẻ, nhưng tốn công hơn nhiều. Không có cách nào chặn tuyệt đối khi app chạy trên máy người khác.
- **Nuitka chỉ chống đọc source, không chống được việc sửa dữ liệu đi qua mạng hay file trên máy.** Các cách bẻ "rẻ" không cần dịch ngược (chặn/sửa phản hồi Supabase, chặn tên miền Supabase, chép đè file `auth.json` cũ) được xử lý riêng ở 13.2, 13.9.
- Mọi bí mật nhúng trong app (chuỗi ghép mã máy, khoá HMAC) đều **coi như sẽ lộ**: chúng chỉ làm khó thêm, không được là lớp bảo vệ duy nhất. Thứ duy nhất app được tin là **chữ ký của server** (13.9).

Các giới hạn khác:
1. **Project Supabase free bị tạm dừng sau 7 ngày không có hoạt động.** Có user dùng hằng ngày (heartbeat) thì không sao. Nếu bị dừng thì user chỉ dùng được trong thời gian ân hạn.
2. Gói free giới hạn khoảng 50.000 user hoạt động/tháng và 2 project. Với nhu cầu này là dư.
3. File `.exe` chưa ký số sẽ bị Windows SmartScreen cảnh báo "Unknown publisher", và đôi khi bị antivirus báo nhầm. Mua chứng chỉ ký code (~$100–400/năm) thì hết, chưa cần ngay.

## 9. Bảo mật

- App của user chỉ chứa **anon key** (key công khai, an toàn khi RLS bật đúng) và **khoá công khai** Ed25519 để kiểm tra chữ ký heartbeat (13.9).
- **Tắt tự đăng ký** trong Supabase Auth (13.10). Tài khoản chỉ được tạo qua Edge Function `admin-users`.
- **Service role key** (key toàn quyền) **tuyệt đối không** đưa vào bản phát hành cho user. Thao tác cần quyền admin (tạo user, đặt lại mật khẩu) đi qua một Edge Function chỉ nhận yêu cầu từ tài khoản admin.
- Token phiên lưu ở `workspace/auth.json` trên máy user. Không ghi ra log.

## 10. Lộ trình đề xuất

| Giai đoạn | Nội dung | Ước lượng |
|---|---|---|
| 0 | Cài đặt theo máy (mục 14) + màn Kiểm tra hệ thống — làm được ngay, không phụ thuộc Supabase | 1 buổi |
| 1 | Tắt tự đăng ký (13.10); bảng + RLS + RPC trên Supabase (không cho user đọc/ghi thẳng, 13.3); hàm `session_status()` theo `session_id` trong JWT (mục 4); Edge Function `heartbeat` ký phán quyết Ed25519 + app kiểm tra chữ ký (13.9); kiểm tra hạn theo giờ server, chống chặn Supabase (13.2); đổi mật khẩu/đăng xuất phía user (15.3); chế độ dev (15.2); màn đăng nhập; hết hạn/đăng xuất từ xa; 1 tài khoản/1 máy; thời gian ân hạn | 2–3 buổi |
| 2 | Chặn quyền ở backend + ẩn menu frontend | 1 buổi |
| 3 | Gửi thống kê (sự kiện cộng dồn + hàng đợi offline) | nửa buổi |
| 4 | Trang `/admin` (thay cho Table Editor) + Edge Function tạo user | 1–2 buổi |
| 4b | Edge Function đổi token Facebook, gỡ `FB_APP_SECRET` khỏi máy user (13.1) | nửa buổi |
| 5 | Đóng gói: FastAPI phục vụ luôn frontend đã build (bỏ vite), biên dịch `app/` bằng Nuitka, xử lý các script chạy theo đường dẫn file (13.6), đăng ký lại redirect Facebook (13.8), launcher, bộ cài lần đầu, thử trên máy Windows sạch | 4–6 buổi |
| 6 | Tự cập nhật (mục 12, kể cả cập nhật khi đang bị chặn vì bản cũ) + trang phát hành bản mới | 1–2 buổi |

## 11. Cần bạn cung cấp / quyết định

1. **Tạo project Supabase** (free) và gửi mình:
   - **Project URL** (dạng `https://xxxx.supabase.co`)
   - **anon key**
   - Service role key: **chưa cần gửi**. Tới giai đoạn 4, bạn dán vào Supabase secrets cho Edge Function, không gửi qua chat.
2. **Email tài khoản admin** của bạn.
3. Đã chốt:
   - 1 tài khoản/1 máy, máy mới đá máy cũ, mã máy theo MachineGuid Windows (mục 4).
   - User tự dùng Gemini key của họ (mục 5).
   - Hoàn thành = dựng CapCut xong hoặc xuất video xong; đếm cộng dồn (mục 6).
   - Đóng gói bằng Nuitka (mục 8).
   - Thư mục draft CapCut chỉnh trong Cài đặt — **đã làm** (mục 14).
   - Facebook: app đã Live; không làm App Review, admin thêm tay từng user làm Tester; vẫn chuyển secret lên Edge Function (13.1).
   - Mất mạng: dùng tiếp tối đa 6 giờ chạy offline (chỉnh được), hết thì hiện modal khoá, có mạng tự mở (13.2).
   - Rà soát lần 2 (mục 16): phán quyết heartbeat có chữ ký, 1 máy theo `session_id`, tắt tự đăng ký, chống chặn Supabase. Đây là thiết kế bảo mật, không cần bạn chọn.
4. Còn chờ bạn trả lời (đều **có đề xuất mặc định, không chặn giai đoạn 1**):
   - Job đang chạy khi bị đá ra: chạy nốt (đề xuất) hay dừng ngay? (mục 4)
   - Danh sách quyền ở mục 5 đã đủ chưa? Có tách "ảnh bìa" khỏi `projects` không?
   - Dự án có sẵn trước khi bật thống kê: có tính không? (mục 6)
   - Giai đoạn đầu dùng Table Editor là đủ, hay cần trang `/admin` ngay?
   - Nơi đặt file bản cập nhật: GitHub Releases hay Cloudflare R2? (mục 12)
   - Máy user có GPU NVIDIA không, hay phải hỗ trợ cả máy chỉ có CPU? (mục 12)
   - Phiên bản CapCut nào user sẽ dùng? (13.7)
   - Có cần "Tự khởi động cùng Windows" và "Proxy mạng" không? (mục 14)
   - Có cho dùng theo thời hạn (hết hạn tài khoản theo ngày) không? (15.1)
   - Có cần nhật ký hành động admin không? (15.6)

## 12. Đóng gói & cập nhật phiên bản

### Vấn đề

Bản đóng gói đầy đủ rất nặng: thư viện AI (torch, CUDA, onnxruntime, faster-whisper, demucs, Chromium cho Playwright) cộng model (`workspace/models` hiện **6.3 GB**) lên tới **vài GB**. Nếu mỗi lần sửa một dòng code lại bắt user tải lại vài GB thì không dùng được. Code của chính app thì nhỏ: `app/` sau khi biên dịch cộng frontend build chỉ khoảng **10–40 MB**.

### Giải pháp: tách 3 lớp, mỗi lớp cập nhật riêng

| Lớp | Gồm | Dung lượng | Bao lâu đổi 1 lần |
|---|---|---|---|
| **Runtime** | Python + thư viện bên thứ 3 + ffmpeg + Chromium | ~2–4 GB | Hiếm (đổi phiên bản torch, thêm thư viện mới) |
| **Models** | big-lama, whisper, sensevoice, OCR… | ~6 GB | Hiếm, và **tải riêng từng model** khi cần |
| **App** | `app/` biên dịch bằng Nuitka + frontend build | ~10–40 MB | Thường xuyên (mỗi lần sửa code) |

Cấu trúc thư mục trên máy user:

```
ReupVideoVjpPro\
  launcher.exe          ← user mở cái này; rất ít khi đổi
  runtime\              ← lớp Runtime
  models\               ← lớp Models
  app\
    1.4.2\              ← bản đang chạy
    1.4.1\              ← bản trước, giữ lại để quay lui
  workspace\            ← dữ liệu user (dự án, tài khoản…) — KHÔNG BAO GIỜ bị đụng khi cập nhật
```

### Luồng cập nhật

1. Admin build bản mới, upload file zip lớp App lên nơi lưu file, rồi thêm 1 dòng vào bảng `app_releases` trên Supabase.
2. Mỗi lần user mở app (và kèm heartbeat), app so phiên bản đang chạy với bản mới nhất trong `app_releases` (so semver, không so chuỗi).
   - Kiểm tra bản mới **không cần đăng nhập** (`app_releases` cho phép đọc công khai). Nếu không, bản cũ hơn `min_app_version` sẽ kẹt: bị chặn đăng nhập mà cũng không cập nhật được.
3. Có bản mới: hiện thông báo "Có bản 1.4.3 — Cập nhật". User bấm thì app tải zip về thư mục tạm, **kiểm tra SHA-256** (chống file hỏng hoặc bị tráo), rồi giải nén vào `app\1.4.3\`.
4. Khởi động lại: `launcher.exe` chạy bản mới nhất. Không ghi đè lên bản đang chạy, vì Windows khoá file đang chạy và vì như vậy cập nhật hỏng giữa chừng cũng không làm hỏng bản cũ.
5. Bản mới **lỗi ngay khi khởi động** (không lên được trong ~60 giây): launcher tự quay về bản trước và ghi lỗi ra file log (gửi qua "Xuất file chẩn đoán", 13.8 — launcher không giữ phiên đăng nhập nên không gửi thẳng lên Supabase).
   - Bản mới phải **chưa chuyển đổi dữ liệu** trước khi được coi là khởi động thành công (xem "Dữ liệu user khi nâng cấp"), để quay lui không gặp dữ liệu bản cũ không đọc được.
6. Dọn dẹp: chỉ giữ 2 bản gần nhất.

```sql
create table app_releases (
  layer          text not null default 'app' check (layer in ('app','runtime','model')),
  name           text not null default '', -- '' cho app/runtime; tên model cho layer='model' (vd 'big-lama')
  version        text not null,           -- '1.4.3'
  variant        text not null default '', -- '' | 'gpu' | 'cpu' (Runtime 2 bản, mục "Bộ cài lần đầu")
  url            text not null,           -- link tải zip
  sha256         text not null,
  size_bytes     bigint,
  notes          text default '',         -- ghi chú thay đổi, hiện cho user
  min_runtime    text,                    -- bản App này cần Runtime tối thiểu bao nhiêu
  force          boolean default false,   -- true = bắt buộc cập nhật mới cho dùng tiếp
  published_at   timestamptz default now(),
  primary key (layer, name, variant, version)  -- app 1.0 và runtime 1.0 không trùng khoá
);
-- RLS: ai cũng đọc được (kể cả chưa đăng nhập), chỉ admin ghi.
-- Trong bảng cấu hình chung: min_app_version — bản cũ hơn bị chặn, phải cập nhật
```

- **Bắt buộc cập nhật**: đặt `min_app_version`. Bản cũ hơn sẽ không đăng nhập được cho tới khi cập nhật. Dùng khi sửa lỗi nghiêm trọng hoặc vá lỗ hổng bẻ khoá.
- **Cập nhật Runtime** (hiếm): cùng cơ chế nhưng file to. Bản App mới khai báo `min_runtime`; máy nào có Runtime cũ thì được nhắc tải Runtime mới trước.
- **Models**: không đóng vào bộ cài. Chức năng nào cần model mà máy chưa có thì app tải đúng model đó lúc dùng lần đầu, kèm thanh tiến trình và kiểm tra SHA-256. Ví dụ: chỉ dùng TTS CapCut thì không phải tải whisper.

### Dữ liệu user khi nâng cấp

- `workspace` nằm ngoài thư mục app nên cập nhật không xoá gì.
- Thêm field mới vào `project.json`… vẫn tương thích ngược nhờ giá trị mặc định của pydantic, như code đang làm.
- Đổi cấu trúc dữ liệu kiểu khó tương thích: bản mới tự chạy bước chuyển đổi lần đầu khởi động và sao lưu file cũ trước khi đổi.
- `workspace\schema_version.json` ghi phiên bản cấu trúc dữ liệu. Bản app gặp `schema_version` **mới hơn** mức nó hiểu thì từ chối chạy và báo rõ (thay vì đọc sai, ghi đè hỏng dữ liệu). Launcher khi quay lui bản cũ sẽ khôi phục bản sao lưu trước bước chuyển đổi.
- Bước chuyển đổi chỉ chạy **sau khi** bản mới đã khởi động ổn định (qua mốc 60 giây ở bước 5), để bản lỗi khởi động không kịp đụng vào dữ liệu.

### Nơi đặt file cập nhật

Supabase Storage **không dùng được**: gói free chỉ có 1 GB và giới hạn 50 MB/file, còn file Runtime/Models nặng vài GB. Hai lựa chọn miễn phí:

| | GitHub Releases | Cloudflare R2 |
|---|---|---|
| Chi phí | Miễn phí | Miễn phí 10 GB lưu trữ, **không tính phí băng thông tải xuống** |
| Giới hạn file | 2 GB/file (Runtime phải chia nhiều phần) | 5 GB/file mỗi lần upload thường |
| Ghi chú | Muốn giữ kín thì repo private cần token, phức tạp hơn | Link tải có thể đặt hạn dùng hoặc giới hạn |

Đề xuất: **Cloudflare R2**. Lớp App vẫn đặt trên GitHub Releases cũng được, vì file nhỏ.

### Bộ cài lần đầu

- Một file `Setup.exe` nhỏ (Inno Setup, miễn phí) cài `launcher.exe`, rồi tải Runtime về và giải nén. Không nhét vài GB vào một file cài.
- ❓ **GPU**: hiện `requirements.txt` có thư viện CUDA của NVIDIA (~1 GB+). Nếu có user dùng máy **không có card NVIDIA** thì cần 2 bản Runtime (GPU/CPU), hoặc 1 bản CPU chạy chậm hơn. Cần biết máy user thường là loại nào.

## 13. Rà soát: lỗ hổng & điểm cần lưu ý thêm

Sắp xếp theo mức quan trọng. Mục 🔴 nên xử lý **trước khi phát hành** cho người khác.

### 🔴 13.1 Facebook App Secret đang nằm trên máy user

- Hiện `FB_APP_ID` / `FB_APP_SECRET` nằm trong `.env`, và code dùng secret **ngay trên máy** để đổi mã đăng nhập Facebook lấy token ([facebook_publish.py:163](app/stages/facebook_publish.py#L163), [:185](app/stages/facebook_publish.py#L185)).
- Phát hành cho user nghĩa là phải nhét secret vào app. Nuitka **không giấu được chuỗi ký tự**: mở file `.pyd` là tìm thấy. Ai có secret có thể giả danh app Facebook của bạn, và Meta có thể khoá app.
- **Cách sửa**: chuyển 2 bước đổi token sang một **Supabase Edge Function** giữ secret trong Supabase secrets. App trên máy user chỉ gửi mã đăng nhập lên và nhận token về, không bao giờ thấy secret. Đây đúng là việc Edge Function làm tốt, và miễn phí.
- **Kèm theo — App Review của Meta**: khi app Facebook ở chế độ Development, chỉ những tài khoản được thêm làm admin/developer/tester của app mới đăng nhập được. Muốn user bất kỳ dùng thì app phải qua **App Review** cho đúng 5 quyền code đang xin (`pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `pages_manage_engagement`, `pages_manage_metadata` — [config.py:247](app/config.py#L247)), đồng thời có chính sách quyền riêng tư, xác minh doanh nghiệp… Việc này tốn thời gian.
- **Đã chốt: không làm App Review, admin thêm tay từng user làm Tester** (user ít). Quy trình cho mỗi user mới:
  1. User gửi link tài khoản Facebook cá nhân (tài khoản quản lý Page họ muốn đăng).
  2. Admin vào Meta App Dashboard → **App roles** → thêm người đó với vai trò **Tester**.
  3. User chấp nhận lời mời (thông báo Facebook hoặc trang lời mời dành cho developer), rồi mới bấm "Đăng nhập Facebook" trong tool.
  4. Bật quyền `facebook_publish` cho user trong trang admin.
  - Người chưa được thêm Tester bấm đăng nhập sẽ bị Facebook báo lỗi app không khả dụng. Tool nên bắt lỗi này và hiện câu dễ hiểu: *"Tài khoản Facebook này chưa được admin thêm vào app — liên hệ admin"*.
  - **App đang ở chế độ Live** (đã xác nhận), nên bài đăng công khai bình thường. Chưa qua App Review thì các quyền Page chỉ ở mức Standard Access, chỉ cấp được cho người có vai trò trong app. Vì vậy vẫn phải thêm user làm Tester như trên.
- **Đã chốt: secret không được nằm trên máy user.** Chuyển lên Edge Function dù user ít, vì secret lộ ra một lần là ai cũng dùng được, không phụ thuộc số user.
  - Edge Function `fb-token` giữ `FB_APP_SECRET` trong Supabase secrets. Có 2 thao tác, đúng bằng 2 chỗ code đang dùng secret: đổi mã đăng nhập lấy token, và đổi token ngắn hạn lấy token dài hạn.
  - Function gọi `session_status()` (mục 4): chỉ trả token khi user còn hợp lệ (không bị khoá, không bị đá, đúng máy, chưa hết hạn) **và** có quyền `facebook_publish`. Chỉ có phiên Supabase thôi là chưa đủ, kể cả khi app đã bị sửa.
  - Trên máy user chỉ còn `FB_APP_ID` (thông tin công khai, nhúng vào app được) và địa chỉ redirect `localhost`. Token Page sau khi lấy về vẫn lưu trên máy user như hiện tại.
  - Đã kiểm tra (01/10): `.env` nằm trong `.gitignore` và **chưa từng được commit**; `.env.example` không có thông tin Facebook. Secret hiện chưa lộ, chưa cần đổi.
  - Lưu ý: `.env` của tool ghi là copy từ **PagesManagerSupperTool**, nghĩa là tool đó cũng giữ cùng secret. Nếu sau này phát hành tool đó cho người khác thì cũng phải xử lý tương tự.

### 🔴 13.2 Kiểm tra hạn phải theo giờ server, không theo giờ máy user

User chỉnh đồng hồ Windows lùi lại là kéo dài được phiên 24 giờ, nếu app so với giờ máy. Cách làm:
- Edge Function `heartbeat` tự tính hạn bằng `now()` của Postgres, trả về `expires_at` và `server_now` trong phán quyết đã ký (13.9). App chỉ làm theo kết quả đó.
- **Khi mất mạng** (không bắt buộc luôn có mạng, nhưng có giới hạn):
  - App lưu `server_now` của lần heartbeat thành công cuối, và **cộng dồn số phút app thực sự chạy offline** bằng bộ đếm monotonic (không đọc đồng hồ Windows). Số phút này ghi vào `workspace/auth.json` kèm HMAC, **và** ghi thêm một bản thứ hai trong registry (`HKCU\Software\ReupVideoVjpPro`). Lúc đọc lấy **giá trị lớn hơn** của hai nơi.
    - Vì sao cần 2 nơi: khoá HMAC nằm trong app nên coi như sẽ lộ (mục 8), và HMAC không chặn được việc **chép đè lại bản `auth.json` cũ** (lúc số phút offline còn 0) để reset ân hạn mãi mãi. Muốn reset phải sửa đồng thời cả file lẫn registry, khó hơn hẳn.
    - Phán quyết đã ký gần nhất (13.9) cũng lưu trong `auth.json`. Lúc offline, hạn phiên và quyền tính theo phán quyết đã ký này, không theo dữ liệu nào app tự ghi.
  - **Chống chặn Supabase có chủ đích**: user có thể thêm `*.supabase.co` vào file hosts (hoặc chặn bằng tường lửa) để app tưởng mất mạng, trong khi Gemini/TikTok/Douyin vẫn chạy bình thường. Khi heartbeat lỗi, app thử thêm 1–2 địa chỉ phổ biến (ví dụ `generativelanguage.googleapis.com`). Nếu **các địa chỉ đó vào được mà Supabase thì không** trong hơn 30 phút liên tục, coi như bị chặn: không cho ân hạn nữa, hiện modal khoá với nội dung "Không kết nối được máy chủ xác thực — kiểm tra tường lửa/file hosts". (Supabase sập thật thì hiếm và ngắn; trường hợp đó admin có thể tăng `offline_grace_minutes` sau.)
  - "Giờ hiện tại" lúc offline = `server_now` lần cuối + số phút offline đã chạy. Hạn phiên (TTL) và thời gian ân hạn đều tính theo giờ này.
  - Đồng hồ Windows bị lùi về trước `server_now` lần cuối → khoá ngay.
  - **Thời gian ân hạn** mặc định **6 giờ chạy offline**, admin chỉnh được trong cấu hình chung (`offline_grace_minutes`). `0` = bắt buộc luôn có mạng.
- **Giao diện khi offline**:
  - Còn trong ân hạn: dải nhỏ ở góc màn hình *"Đang offline — còn 4 giờ 20 phút"*.
  - Hết ân hạn: **modal phủ toàn màn hình** (không chuyển trang) *"Mất kết nối tới máy chủ — đang thử lại…"*. App tự thử lại mỗi 30 giây, kèm nút "Thử lại ngay". Có mạng là modal **tự đóng**, mọi thứ đang mở vẫn giữ nguyên.
  - Trong lúc bị khoá: job đang chạy dở được chạy nốt; "Dự án tự động" ngừng nhận việc mới; mọi API khác trả `423 Locked`.
  - Lần đăng nhập đầu tiên (hoặc sau khi đăng xuất): bắt buộc có mạng.
- Thực tế ít ảnh hưởng: các chức năng chính (dịch Gemini, TTS CapCut, tải Douyin, đăng TikTok/Facebook) vốn đã cần mạng.

### 🔴 13.3 RLS: user không được đọc/ghi thẳng vào bảng nào

- **Không** tạo policy `update` hay `insert` cho user trên `profiles`, `admin_notes`, `usage_stats`, `project_events`, `device_events`. Mọi thao tác ghi của user đi qua RPC `security definer` (`claim_device`, `report_usage`) hoặc Edge Function `heartbeat`, và tất cả tự lấy `auth.uid()`/`session_id` từ JWT, không nhận `user_id` từ app gửi lên.
- Cũng **không** cho user `select` thẳng `profiles`: dùng RPC `my_profile()` trả đúng các cột được thấy. `admin_notes` user không đọc được dưới bất kỳ hình thức nào.
- Mọi hàm `security definer` đặt `set search_path = ''` (dùng tên đầy đủ `public.profiles`…) để tránh bị chiếm qua search_path, và `revoke execute … from public, anon` rồi chỉ `grant` cho `authenticated` (hàm admin chỉ chạy khi `is_admin()`).
- Nếu không làm vậy, user cầm anon key và token của chính mình có thể gọi thẳng REST API của Supabase để tự bật `features` hay xoá `force_logout_at`. Đóng gói app không chặn được việc này, vì đây là API công khai.
- Quyền admin kiểm tra bằng hàm `is_admin()` đọc `profiles.role` phía server, không tin vào bất cứ gì app gửi lên.

### 🟠 13.4 Bị đá ra phải dừng cả phần chạy nền

"Dự án tự động" có bộ lập lịch chạy nền (tự crawl, tự xử lý, tự đăng) kể cả khi không ai mở giao diện. Khi phiên hết hạn hoặc bị đá ra, **bộ lập lịch phải dừng theo**. Nếu không, chặn giao diện mà máy vẫn tự đăng bài. Job đang chạy dở thì tuỳ câu trả lời ❓ ở mục 4.

### 🟠 13.5 Phiên Supabase & quên mật khẩu

- Access token của Supabase sống 1 giờ. App phải dùng **refresh token** để tự làm mới (thư viện supabase-py làm sẵn). Phiên "vĩnh viễn" của mình nghĩa là app không tự đăng xuất; phía Supabase vẫn làm mới token đều đặn.
- **Quên mật khẩu**: email gửi sẵn của Supabase free bị giới hạn vài email mỗi giờ, và hay vào thư rác. Đề xuất giai đoạn đầu: user báo admin, admin bấm "Đặt lại mật khẩu" và gửi mật khẩu tạm qua Zalo. Sau này cấu hình SMTP riêng thì bật tự đặt lại.

### 🟠 13.6 Công cụ ngoài phải đóng gói theo

App đang gọi những thứ nằm **ngoài** thư mục code, cấu hình bằng đường dẫn tuyệt đối trên máy bạn:

| Thành phần | Hiện tại | Khi đóng gói |
|---|---|---|
| douyin-downloader | `DOUYIN_DL_DIR` trỏ tới repo clone riêng | Đưa vào lớp Runtime. Bỏ biến này khỏi tầm nhìn của user |
| Môi trường Python riêng cho "Làm sạch video" | `HARDSUB_PYTHON` trỏ tới venv GPU riêng | Gộp vào Runtime (nếu dùng chung được phiên bản torch), hoặc là một lớp Runtime phụ |
| ffmpeg | `imageio-ffmpeg` trong thư viện | Đã nằm trong Runtime |
| Chromium cho Playwright | Tải về thư mục cache của Playwright | Đưa vào Runtime, đặt đường dẫn cố định |
| Worker "Làm sạch video" | [hardsub_clean.py:19](app/stages/hardsub_clean.py#L19) chạy **theo đường dẫn file** `hardsub_worker.py` bằng Python khác (`HARDSUB_PYTHON`) | Nuitka biên dịch xong thì file `.py` này không còn. Phải: (a) đổi sang chạy dạng `-m` với module đã biên dịch **cho đúng phiên bản Python của venv đó**, hoặc (b) chấp nhận phát hành worker dạng source (nó không chứa logic kiểm tra quyền nên lộ cũng không sao). Đề xuất (b) cho nhanh, kèm `sttn_net.py` nếu worker import |
| Worker tách nguồn demucs | [dub_audio.py:85](app/stages/dub_audio.py#L85) chạy `-m app.stages._demucs_worker` bằng chính Python của app | Chạy được với module đã biên dịch, miễn là Runtime import được gói `app`. Cần thử lại sau khi đóng gói |
| douyin-downloader | [douyin_dl.py:62](app/stages/douyin_dl.py#L62) chạy `run.py` của repo ngoài | Đưa repo đó vào Runtime dạng source (không phải code của mình) |

### 🟠 13.7 Tương thích phiên bản CapCut

Draft được ghi theo định dạng mà thư viện `pycapcut` hiểu. Các bản CapCut/Jianying mới đôi khi đổi hoặc mã hoá định dạng draft, nên user dùng bản CapCut khác bạn có thể mở draft lỗi. Cần ghi rõ **phiên bản CapCut đã kiểm tra chạy được**, và cho app báo khi phát hiện CapCut quá mới/quá cũ (❓ cần thử trên vài phiên bản).

### 🟡 13.8 Các điểm nhỏ hơn

- **Cổng mạng cố định 8001/5175** có thể trùng với phần mềm khác trên máy user. Bản đóng gói chỉ cần 1 cổng (FastAPI phục vụ luôn giao diện), và launcher tự tìm cổng trống nếu cổng mặc định bị chiếm. Đường dẫn redirect của Facebook đang gắn với `localhost:5175` (`FRONTEND_URL`, `FB_REDIRECT_URI`): đổi cổng thì phải đăng ký lại với app Facebook, nên đây là lý do nữa để giữ cổng cố định và chỉ tìm cổng khác khi bắt buộc.
  - Bản đóng gói bỏ vite, giao diện chạy chung cổng 8001, nên địa chỉ redirect **chắc chắn đổi** so với `localhost:5175` hiện tại. Phải thêm URI mới vào "Valid OAuth Redirect URIs" của app Facebook, và **thử thật** đăng nhập với `http://localhost:8001/...` khi app ở chế độ Live trước khi phát hành (Meta có thể đòi HTTPS với một số cấu hình). Edge Function `fb-token` phải gửi đúng `redirect_uri` này khi đổi mã.
  - Có thể đăng ký sẵn 2–3 cổng dự phòng (8001, 8011, 8021) để launcher đổi cổng mà Facebook vẫn chạy.
- **Backend chỉ nghe trên `127.0.0.1`** (uvicorn mặc định đã vậy; launcher phải giữ nguyên, không dùng `0.0.0.0`). Nếu mở ra mạng LAN, máy khác cùng mạng gọi được API local, lấy được token TikTok/Facebook đã lưu và dùng ké phiên đăng nhập.
- **Thông báo cho user về thống kê**: ghi rõ app gửi những gì (số tài khoản, số dự án, tên máy, phiên bản) và không gửi gì (video, nội dung, mật khẩu, key). Có một dòng trong màn đăng nhập là đủ.
- **Hỗ trợ từ xa**: thêm nút "Xuất file chẩn đoán" (log gần nhất + cấu hình, **đã xoá key/token**) để user gửi cho bạn khi lỗi. Đỡ phải hỏi qua lại.
- **Antivirus báo nhầm**: Nuitka cũng có thể bị báo nhầm như PyInstaller, nhất là bản chưa ký số. Nên gửi file cho các hãng antivirus phổ biến để họ đánh dấu an toàn (miễn phí, nhưng mỗi bản cập nhật phải gửi lại).

### 🔴 13.9 Phán quyết heartbeat phải có chữ ký

- **Lỗ hổng**: máy là của user, nên họ cài được chứng chỉ gốc của công cụ chặn HTTPS (mitmproxy, Fiddler — vài phút, không cần biết code). Khi đó họ đọc và **sửa được phản hồi** từ Supabase, ví dụ đổi `features` thành đủ mọi quyền, `enabled = true`, xoá lý do bị đá. Kiểm tra quyền chạy ở backend local nên tin luôn dữ liệu đã sửa. Nuitka không chặn được việc này vì không cần đụng vào code.
- **Cách sửa**:
  - Heartbeat chạy qua **Edge Function `heartbeat`** (không phải RPC thường). Function gọi `session_status()`, ghi số liệu, rồi **ký phán quyết bằng khoá bí mật Ed25519** giữ trong Supabase secrets.
  - Nội dung được ký: `user_id`, `session_id`, `enabled/lý do`, `features`, `expires_at`, `server_now`, `offline_grace_minutes`, `min_app_version` và **`nonce` do app gửi lên** trong lần gọi đó.
  - App nhúng **khoá công khai** (lộ cũng không sao), kiểm tra chữ ký + `nonce` + `user_id` khớp phiên đang đăng nhập. Sai bất kỳ điều gì → coi như heartbeat thất bại (rơi vào chế độ offline/ân hạn, không phải "được phép").
  - `nonce` chặn việc phát lại một phán quyết cũ (ví dụ phán quyết lúc còn đủ quyền) khi đã bị hạ quyền.
  - Phán quyết đã ký lưu vào `auth.json` để dùng khi offline (13.2). Phán quyết lúc offline vẫn có hạn theo `server_now` + số phút offline, nên không dùng mãi được.
- Chi phí: Edge Function free có 500.000 lượt gọi/tháng. 1 user heartbeat 5 phút/lần chạy 24/7 ≈ 8.640 lượt/tháng → đủ cho ~50 user chạy liên tục, nhiều hơn nếu không chạy 24/7. Khi đông hơn thì tăng `heartbeat_seconds` lên 10 phút.
- Không tránh được hoàn toàn: người dịch ngược được mã máy có thể sửa luôn chỗ kiểm tra chữ ký (mục 8). Nhưng mức này đã loại toàn bộ các cách bẻ "không cần biết code".

### 🔴 13.10 Tắt tự đăng ký tài khoản

- Anon key nằm sẵn trong app, nên nếu Supabase Auth còn bật **"Allow new users to sign up"** (mặc định là bật) thì ai cũng tự tạo được tài khoản bằng một lệnh gọi API.
- **Cách sửa**: tắt ở Authentication → Sign In / Providers → Email → bỏ "Allow new users to sign up". Tài khoản chỉ tạo qua Edge Function `admin-users` (dùng service role, không bị ảnh hưởng bởi công tắc này).
- Thêm một lớp nữa: `profiles.enabled` mặc định `false`. Lỡ có tài khoản lọt vào bằng đường nào đó cũng bị khoá cho tới khi admin bật.

## 14. Cài đặt theo từng máy user

Mục tiêu: mọi thứ khác nhau giữa các máy phải chỉnh được trong trang Cài đặt, **không bắt user sửa file `.env`**. Bản đóng gói còn không cho user thấy file này.

| Cài đặt | Hiện tại | Đề xuất |
|---|---|---|
| Workspace (nơi lưu dự án) | ✅ Có trong Cài đặt | Giữ. Thêm cảnh báo khi ổ đĩa sắp đầy |
| Gemini API key / model | ✅ Có | Giữ. Thêm nút **"Thử key"** gọi Gemini 1 lần, báo key đúng/sai/hết quota |
| Whisper model / thiết bị / ngôn ngữ | ✅ Có | Giữ |
| Mức độ dịch | ✅ Có | Giữ |
| **Thư mục draft CapCut** | ✅ **Vừa thêm**: tự dò, kiểm tra, chặn dựng khi chưa cài | Xong |
| **Thiết bị xử lý AI** (SenseVoice, VieNeu TTS, OCR, LaMa) | ❌ Chỉ chỉnh được qua `.env` (`SENSEVOICE_DEVICE`, `VIENEU_DEVICE`, `OCR_DEVICE`) | **1 lựa chọn chung "Tự động / GPU / CPU"** áp cho tất cả, kèm dòng hiển thị máy có card NVIDIA hay không. Quan trọng với user không có GPU |
| Thư mục cache model + thư mục tạm | ⚠️ Chỉ hiển thị, không sửa được | Cho sửa. Mặc định đặt cạnh workspace (máy user hay chỉ có ổ C, hoặc C sắp đầy) |
| Số luồng TTS chạy song song, thời gian chờ tối đa của demucs | ❌ Chỉ `.env` (`TTS_CONCURRENCY`, `DEMUCS_TIMEOUT_S`) | Đưa vào nhóm **"Nâng cao"** (thu gọn mặc định). Máy yếu cần giảm |
| Facebook App ID / Secret / Redirect | ❌ `.env` | **Bỏ khỏi máy user** (xem 13.1). App ID là thông tin công khai, nhúng vào app được; Secret nằm ở Edge Function |
| douyin-downloader, Python của "Làm sạch video" | ❌ `.env` (`DOUYIN_DL_DIR`, `HARDSUB_PYTHON`) | **Không cho chỉnh**: đi kèm Runtime (13.6) |
| Cổng chạy app | ❌ Cố định trong `run.bat` | Lưu trong cấu hình của launcher, tự đổi khi bị trùng (13.8) |
| Tự khởi động cùng Windows | ❌ Chưa có | ❓ Cần nếu user muốn "Dự án tự động" chạy 24/7 mà không phải tự mở app |
| Proxy mạng | ❌ Chưa có | ❓ Chỉ cần nếu có user phải đi qua proxy để vào Douyin/TikTok |

**Màn "Kiểm tra hệ thống"** (đề xuất thêm): một trang hoặc một bước trong lần chạy đầu, liệt kê kèm dấu ✅/❌: có card NVIDIA không, Gemini key hợp lệ, thư mục draft CapCut, dung lượng trống của ổ workspace, model nào đã tải, kết nối Supabase. User mới cài làm theo danh sách là chạy được, bạn đỡ phải hỗ trợ từng người.

## 15. Còn thiếu — cần bạn quyết có làm không

Những thứ chưa bàn tới, nhưng một tool phát hành cho người khác dùng thường sẽ cần. Mỗi mục kèm đề xuất.

| # | Việc | Vì sao cần | Đề xuất |
|---|---|---|---|
| 15.1 | **Hạn dùng tài khoản** (`account_expires_at`) — khác với hạn phiên đăng nhập | Hạn phiên 24 giờ chỉ bắt đăng nhập lại. Nếu bạn cho dùng theo tháng/gói, cần một mốc "tới ngày X thì tài khoản ngừng hoạt động" | ❓ Có bán/cho dùng theo thời hạn không? Có thì thêm vào trang admin: ô "Hết hạn ngày" + nút gia hạn nhanh +30 ngày. Cột đã có sẵn trong `profiles` |
| 15.2 | **Chế độ dev cho bạn** | Bạn vẫn chạy tool từ source để phát triển. Không lẽ mỗi lần chạy thử đều phải đăng nhập, heartbeat, bị khoá khi mất mạng | Chạy từ source (không phải bản đóng gói) thì đăng nhập vẫn chạy bình thường bằng tài khoản admin; chỉ bỏ qua kiểm tra phiên bản và cho phép chỉnh `heartbeat`. Nhận biết "chạy từ source" bằng biến `__compiled__` mà Nuitka tự gắn, **không** dựa vào biến môi trường hay file cấu hình (user tự đặt được). **Bản đóng gói không có công tắc tắt kiểm tra**, để không ai bật được |
| 15.3 | **User tự đổi mật khẩu** | Mật khẩu tạm admin gửi qua Zalo nên được đổi ngay | Ô "Đổi mật khẩu" trong Cài đặt. Supabase hỗ trợ sẵn, ít công |
| 15.4 | **Sao lưu dữ liệu Supabase** | Gói free không có bản sao lưu tự động tải về được. Lỡ xoá nhầm bảng là mất danh sách user và thống kê | Script xuất các bảng ra file CSV/JSON, bạn chạy tay mỗi tuần (hoặc nút "Xuất dữ liệu" trong trang admin). Dữ liệu chỉ vài KB/user |
| 15.5 | **Thông báo từ admin** | Báo bảo trì, báo có bản mới quan trọng, nhắc gia hạn | Ô `login_notice` trong `app_config` (đã thêm), hiện ở màn đăng nhập và dải trên cùng của app |
| 15.6 | **Nhật ký hành động admin** | Biết ai bị khoá/đá/đổi quyền lúc nào, để giải thích khi user hỏi | Bảng `admin_audit` (thời điểm, user bị tác động, hành động). Chỉ cần nếu sau này có nhiều hơn 1 admin; hiện chỉ có bạn nên ❓ có thể bỏ |
| 15.7 | **Hướng dẫn cài đặt cho user** | Bộ cài + Kiểm tra hệ thống giảm hỏi đáp, nhưng user vẫn cần biết bước đầu (tải Setup, đăng nhập, nhập Gemini key, cài CapCut, thêm Tester Facebook) | 1 trang hướng dẫn ngắn có ảnh chụp màn hình, viết khi gần phát hành |

## 16. Rà soát lần 2 (01/10) — đã sửa vào kế hoạch

| Mức | Vấn đề | Đã sửa ở |
|---|---|---|
| 🔴 | Phán quyết heartbeat không có chữ ký: user chặn HTTPS bằng mitmproxy và sửa phản hồi để bật đủ quyền, không cần dịch ngược | 13.9 (Edge Function ký Ed25519 + nonce), mục 2 |
| 🔴 | "1 tài khoản/1 máy" dựa vào mã máy do app tự khai, nên app bị sửa có thể gửi mã của máy khác | Mục 4: gắn theo `session_id` trong JWT, `claim_device` chỉ nhận session vừa tạo, xoá session cũ |
| 🔴 | Chưa tắt tự đăng ký, trong khi anon key nằm sẵn trong app | 13.10, `enabled` mặc định `false` |
| 🔴 | Chặn `*.supabase.co` bằng file hosts để dùng chế độ offline; chép đè `auth.json` cũ để reset ân hạn (HMAC không chặn được) | 13.2: phát hiện chặn có chủ đích, bộ đếm offline lưu cả registry |
| 🟠 | Admin API `signOut` không nhận user id, không dùng để đá user ra được | Mục 4: xoá `auth.sessions` bằng hàm admin |
| 🟠 | "Đá ra" không chặn được user đăng nhập lại | Mục 4: ghi rõ phải khoá hoặc đặt lại mật khẩu; thêm `device_events` để phát hiện tài khoản dùng chung |
| 🟠 | Ghi chú admin (`profiles.note`) user đọc được | Tách bảng `admin_notes`; user chỉ đọc qua `my_profile()` |
| 🟠 | `app_releases` khoá chính chỉ là `version`, nên app 1.0 trùng runtime 1.0, không phân biệt được model | Khoá `(layer, name, variant, version)` |
| 🟠 | `min_app_version` so chuỗi sai (`'1.10' < '1.9'`) | So semver |
| 🟠 | Bản cũ bị chặn đăng nhập thì không cập nhật được | `app_releases` đọc công khai, không cần đăng nhập |
| 🟠 | Quay lui bản cũ sau khi bản mới đã chuyển đổi dữ liệu | `schema_version` + chuyển đổi chỉ sau khi khởi động ổn định |
| 🟠 | Nuitka không chạy được các worker chạy theo đường dẫn file `.py` | 13.6 (hardsub worker, demucs, douyin-downloader) |
| 🟠 | Redirect Facebook đổi khi bỏ vite | 13.8 |
| 🟡 | Backend phải chỉ nghe `127.0.0.1`; hàm `security definer` cần `search_path` cố định; giới hạn `report_usage`; chế độ dev nhận biết bằng `__compiled__` | 13.8, 13.3, mục 3, 15.2 |
| 🟡 | Ước lượng thời gian giai đoạn 1 và 5 quá lạc quan | Mục 10 |

### Đánh giá an toàn sau khi sửa

| Kiểu tấn công | Người thực hiện cần biết gì | Kết quả |
|---|---|---|
| Sửa JS frontend, gọi thẳng API local | Biết chút web | ❌ Chặn — quyền kiểm tra ở backend |
| Gọi REST Supabase bằng anon key + token của mình để tự bật quyền | Biết chút API | ❌ Chặn — RLS không cho đọc/ghi thẳng (13.3) |
| Tự tạo tài khoản | Biết chút API | ❌ Chặn — tắt signup (13.10) |
| Chặn HTTPS, sửa phản hồi heartbeat | Làm theo hướng dẫn mạng | ❌ Chặn — chữ ký Ed25519 + nonce (13.9) |
| Phát lại phản hồi heartbeat cũ | Làm theo hướng dẫn mạng | ❌ Chặn — nonce |
| Chỉnh đồng hồ Windows | Ai cũng làm được | ❌ Chặn — tính theo giờ server + bộ đếm monotonic (13.2) |
| Chặn Supabase bằng file hosts | Ai cũng làm được | ⚠️ Dùng được tối đa ~30 phút rồi bị khoá (13.2) |
| Chép đè `auth.json` cũ để reset ân hạn | Biết chút máy tính | ⚠️ Khó — phải sửa cả registry; tối đa 6 giờ mỗi lần |
| 2 máy dùng chung 1 tài khoản | Ai cũng làm được | ⚠️ Chỉ luân phiên được (đăng nhập là đá máy kia), admin thấy qua `device_events` |
| Lấy FB App Secret | Mở file app | ❌ Chặn — secret nằm ở Edge Function (13.1) |
| Dịch ngược `.pyd` Nuitka, gỡ chỗ kiểm tra chữ ký | Chuyên gia dịch ngược | ✅ Bẻ được — giới hạn chung của mọi app chạy trên máy user (mục 8). Ứng phó: `min_app_version` buộc cập nhật, đổi cách kiểm tra mỗi bản |

**Kết luận**: sau các sửa đổi trên, thiết kế đủ an toàn cho mục tiêu (chặn người dùng thường và người biết chút kỹ thuật). Điểm yếu còn lại chỉ là dịch ngược mã máy, không tránh được với app chạy local.

### Có triển khai ngay được không

- **Giai đoạn 0**: làm được ngay.
- **Giai đoạn 1**: thiết kế đã đủ, **làm được ngay khi có**: Project URL, anon key, email admin (mục 11.1–11.2). Các câu ❓ còn lại dùng đề xuất mặc định, không chặn.
- Cặp khoá Ed25519: mình tạo khi bắt đầu giai đoạn 1. Khoá bí mật bạn dán vào Supabase secrets, không gửi qua chat và không lưu trong repo.
