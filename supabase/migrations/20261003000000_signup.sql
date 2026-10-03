-- User tự đăng ký ở màn đăng nhập → chờ admin duyệt + cấp quyền.
--
-- Tài khoản tự đăng ký được Edge Function `register` tạo với approval =
-- 'pending', enabled = false, features = '{}' — dùng không được gì cho tới khi
-- admin duyệt ở trang "Duyệt user". Cột mặc định 'approved' nên mọi user cũ và
-- user do admin tạo tay KHÔNG bị ảnh hưởng.
--
-- Chạy: npx supabase db query --linked --project-ref szmjvnhgolzeztcgvups -f supabase/migrations/20261003000000_signup.sql

alter table public.profiles
  add column if not exists approval text not null default 'approved'
    check (approval in ('pending', 'approved', 'rejected'));
alter table public.profiles add column if not exists signup_contact text not null default '';
alter table public.profiles add column if not exists signup_at timestamptz;

create index if not exists profiles_pending_idx on public.profiles (signup_at) where approval = 'pending';

-- Đăng nhập khi chưa được duyệt: báo rõ lý do thay vì "bị khoá" chung chung.
create or replace function public.claim_device(p_device_id text, p_device_name text)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  uid uuid := auth.uid();
  sid uuid := public.jwt_session_id();
  p public.profiles;
begin
  if uid is null or sid is null then
    raise exception 'Chưa đăng nhập' using errcode = '28000';
  end if;
  if length(coalesce(p_device_id, '')) not between 16 and 128 then
    raise exception 'Mã máy không hợp lệ' using errcode = '22023';
  end if;
  if not exists (
    select 1 from auth.sessions s
     where s.id = sid and s.user_id = uid and s.created_at > now() - interval '2 minutes'
  ) then
    raise exception 'Phiên đăng nhập quá cũ — đăng nhập lại bằng mật khẩu' using errcode = '28000';
  end if;

  select * into p from public.profiles where id = uid for update;
  if not found then
    raise exception 'Tài khoản chưa được cấp quyền' using errcode = '28000';
  end if;
  if p.approval = 'pending' then
    raise exception 'Tài khoản đang chờ admin duyệt — duyệt xong bạn đăng nhập lại bằng email và mật khẩu này' using errcode = '28000';
  end if;
  if p.approval = 'rejected' then
    raise exception 'Yêu cầu đăng ký đã bị từ chối — liên hệ admin' using errcode = '28000';
  end if;
  if not p.enabled then
    raise exception 'Tài khoản đã bị khoá — liên hệ admin' using errcode = '28000';
  end if;
  if p.account_expires_at is not null and p.account_expires_at < now() then
    raise exception 'Tài khoản đã hết hạn sử dụng — liên hệ admin' using errcode = '28000';
  end if;

  update public.profiles
     set active_session_id = sid,
         active_device_id = p_device_id,
         active_device_name = left(coalesce(p_device_name, ''), 80),
         active_login_at = now()
   where id = uid;

  insert into public.device_events (user_id, device_id, device_name, session_id)
  values (uid, p_device_id, left(coalesce(p_device_name, ''), 80), sid);

  -- Máy cũ mất luôn refresh token (refresh_tokens xoá theo session).
  delete from auth.sessions where user_id = uid and id <> sid;

  return public.session_status();
end;
$$;

-- Danh sách user cho trang admin: thêm 3 cột duyệt ở CUỐI (create or replace
-- view chỉ cho thêm cột vào cuối).
create or replace view public.admin_user_stats
with (security_invoker = true)
as
select
  p.id, p.email, p.display_name, p.role, p.enabled, p.features,
  p.session_ttl_hours, p.account_expires_at, p.force_logout_at,
  p.active_device_name, p.active_login_at, (p.active_session_id is not null) as signed_in,
  p.created_at,
  u.tiktok_accounts, u.facebook_pages, u.app_version, u.last_seen_at,
  coalesce(n.note, '') as note,
  (select count(*) from public.project_events e where e.user_id = p.id and e.type = 'created') as projects_created,
  (select count(*) from public.project_events e where e.user_id = p.id and e.type = 'completed') as projects_completed,
  (select count(*) from public.device_events d where d.user_id = p.id and d.at > now() - interval '7 days') as device_switches_7d,
  p.approval, p.signup_contact, p.signup_at
from public.profiles p
left join public.usage_stats u on u.user_id = p.id
left join public.admin_notes n on n.user_id = p.id;

revoke all on public.admin_user_stats from anon;
