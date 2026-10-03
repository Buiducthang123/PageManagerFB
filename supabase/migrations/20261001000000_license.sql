-- Quản lý user (user-management-plan.md mục 3, 4, 13.3).
-- Chạy 1 lần trên project Supabase: SQL Editor → dán toàn bộ file → Run
-- (hoặc `supabase db push`). Chạy lại được (idempotent ở mức bảng/hàm).
--
-- Nguyên tắc:
--   * User KHÔNG đọc/ghi thẳng bảng nào (trừ đọc app_config/app_releases).
--     Mọi thao tác đi qua hàm security definer, tự lấy auth.uid() và
--     session_id trong JWT — không nhận user_id từ app.
--   * Admin đọc/sửa tất cả qua RLS `is_admin()`.
--   * Mọi hàm security definer: search_path = '' và tên đầy đủ.

-- ------------------------------------------------------------------ Bảng

create table if not exists public.profiles (
  id                 uuid primary key references auth.users on delete cascade,
  email              text not null,
  display_name       text not null default '',
  role               text not null default 'user' check (role in ('admin', 'user')),
  -- Mặc định KHOÁ: tài khoản lọt vào bằng đường nào cũng không dùng được
  -- cho tới khi admin bật (13.10).
  enabled            boolean not null default false,
  features           text[] not null default '{}',
  session_ttl_hours  int check (session_ttl_hours is null or session_ttl_hours > 0),
  force_logout_at    timestamptz,
  -- Session Supabase DUY NHẤT hợp lệ (claim session_id trong JWT). Mã máy
  -- do app tự khai nên chỉ để hiển thị.
  active_session_id  uuid,
  active_device_id   text,
  active_device_name text,
  active_login_at    timestamptz,
  account_expires_at timestamptz,
  created_at         timestamptz not null default now()
);

create table if not exists public.admin_notes (
  user_id    uuid primary key references public.profiles on delete cascade,
  note       text not null default '',
  updated_at timestamptz not null default now()
);

create table if not exists public.usage_stats (
  user_id         uuid primary key references public.profiles on delete cascade,
  tiktok_accounts int not null default 0,
  facebook_pages  int not null default 0,
  app_version     text,
  last_seen_at    timestamptz
);

create table if not exists public.project_events (
  user_id    uuid not null references public.profiles on delete cascade,
  project_id text not null check (length(project_id) between 1 and 80),
  type       text not null check (type in ('created', 'completed')),
  at         timestamptz not null default now(),
  primary key (user_id, project_id, type)
);

create table if not exists public.device_events (
  id          bigint generated always as identity primary key,
  user_id     uuid not null references public.profiles on delete cascade,
  device_id   text not null,
  device_name text,
  session_id  uuid,
  at          timestamptz not null default now()
);
create index if not exists device_events_user_at on public.device_events (user_id, at desc);

create table if not exists public.app_config (
  id                    int primary key default 1 check (id = 1),
  min_app_version       text,
  offline_grace_minutes int not null default 360 check (offline_grace_minutes >= 0),
  heartbeat_seconds     int not null default 300 check (heartbeat_seconds between 60 and 3600),
  login_notice          text not null default ''
);
insert into public.app_config (id) values (1) on conflict (id) do nothing;

create table if not exists public.app_releases (
  layer        text not null default 'app' check (layer in ('app', 'runtime', 'model')),
  name         text not null default '',
  variant      text not null default '',
  version      text not null,
  url          text not null,
  sha256       text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes   bigint,
  notes        text not null default '',
  min_runtime  text,
  force        boolean not null default false,
  published_at timestamptz not null default now(),
  primary key (layer, name, variant, version)
);

-- ------------------------------------------------------------------ Tiện ích

-- '1.10.2' -> {1,10,2}; so mảng số nên '1.10' > '1.9' (so chuỗi thì sai).
create or replace function public.version_parts(v text)
returns int[]
language sql
immutable
set search_path = ''
as $$
  select coalesce(
    (select array_agg(coalesce(nullif(regexp_replace(p, '[^0-9]', '', 'g'), ''), '0')::int order by ord)
       from unnest(string_to_array(split_part(coalesce(v, ''), '-', 1), '.')) with ordinality as t(p, ord)),
    '{}'::int[]
  );
$$;

create or replace function public.jwt_session_id()
returns uuid
language sql
stable
set search_path = ''
as $$
  select nullif(auth.jwt() ->> 'session_id', '')::uuid;
$$;

create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.profiles
     where id = auth.uid() and role = 'admin' and enabled
  );
$$;

-- ------------------------------------------------------------------ Tự tạo profile

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, email) values (new.id, coalesce(new.email, ''))
    on conflict (id) do nothing;
  insert into public.usage_stats (user_id) values (new.id) on conflict do nothing;
  insert into public.admin_notes (user_id) values (new.id) on conflict do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ------------------------------------------------------------------ Phán quyết phiên (mục 4)

-- Hàm DUY NHẤT quyết định user còn dùng được không. Gọi từ heartbeat,
-- fb-token, admin. Không nhận tham số từ app.
create or replace function public.session_status()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  uid uuid := auth.uid();
  sid uuid := public.jwt_session_id();
  p public.profiles;
  cfg public.app_config;
  expires timestamptz;
  code text := 'ok';
  msg text := '';
begin
  select * into cfg from public.app_config where id = 1;
  if uid is null then
    return jsonb_build_object('ok', false, 'code', 'no_auth', 'message', 'Chưa đăng nhập',
                              'server_now', now());
  end if;
  select * into p from public.profiles where id = uid;
  if not found then
    return jsonb_build_object('ok', false, 'code', 'no_profile', 'message', 'Tài khoản chưa được cấp quyền',
                              'server_now', now());
  end if;

  if p.session_ttl_hours is not null and p.active_login_at is not null then
    expires := p.active_login_at + make_interval(hours => p.session_ttl_hours);
  end if;
  if p.account_expires_at is not null and (expires is null or p.account_expires_at < expires) then
    expires := p.account_expires_at;
  end if;

  if not p.enabled then
    code := 'locked'; msg := 'Tài khoản đã bị khoá — liên hệ admin';
  elsif p.account_expires_at is not null and p.account_expires_at < now() then
    code := 'account_expired'; msg := 'Tài khoản đã hết hạn sử dụng — liên hệ admin';
  elsif sid is null or p.active_session_id is null then
    code := 'signed_out'; msg := 'Phiên đăng nhập đã kết thúc — đăng nhập lại';
  elsif p.active_session_id <> sid then
    code := 'other_device';
    msg := 'Tài khoản đã đăng nhập trên máy khác (' || coalesce(nullif(p.active_device_name, ''), 'không rõ tên') || ')';
  elsif p.force_logout_at is not null and p.active_login_at is not null and p.force_logout_at > p.active_login_at then
    code := 'forced_logout'; msg := 'Admin đã đăng xuất phiên này';
  elsif p.session_ttl_hours is not null and expires is not null and now() > expires then
    code := 'session_expired'; msg := 'Phiên đăng nhập đã hết hạn — đăng nhập lại';
  end if;

  return jsonb_build_object(
    'ok', code = 'ok',
    'code', code,
    'message', msg,
    'user_id', uid,
    'session_id', sid,
    'email', p.email,
    'display_name', p.display_name,
    'role', p.role,
    'features', to_jsonb(p.features),
    'expires_at', expires,
    'device_name', p.active_device_name,
    'server_now', now(),
    'offline_grace_minutes', cfg.offline_grace_minutes,
    'heartbeat_seconds', cfg.heartbeat_seconds,
    'min_app_version', cfg.min_app_version,
    'login_notice', cfg.login_notice
  );
end;
$$;

-- Gọi ngay sau khi đăng nhập bằng mật khẩu. Chỉ nhận session VỪA tạo (2 phút)
-- để máy cũ còn access token không "giành" lại phiên mà không nhập mật khẩu.
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

-- User tự đăng xuất: gỡ máy khỏi tài khoản để đăng nhập máy khác ngay.
create or replace function public.release_device()
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
begin
  update public.profiles
     set active_session_id = null
   where id = auth.uid() and active_session_id = public.jwt_session_id();
end;
$$;

-- Heartbeat: ghi số liệu + trả phán quyết. App KHÔNG gọi thẳng hàm này mà
-- qua Edge Function `heartbeat` (ký phán quyết) — gọi thẳng thì chỉ được bản
-- không chữ ký, app không chấp nhận.
create or replace function public.heartbeat(
  p_app_version text,
  p_tiktok_accounts int,
  p_facebook_pages int,
  p_events jsonb default '[]'::jsonb
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  uid uuid := auth.uid();
  st jsonb := public.session_status();
  min_v text;
  accepted int := 0;
begin
  if uid is null or not (st ->> 'ok')::boolean then
    return st;
  end if;

  insert into public.usage_stats (user_id, tiktok_accounts, facebook_pages, app_version, last_seen_at)
  values (uid, greatest(0, least(coalesce(p_tiktok_accounts, 0), 10000)),
          greatest(0, least(coalesce(p_facebook_pages, 0), 10000)),
          left(coalesce(p_app_version, ''), 32), now())
  on conflict (user_id) do update
    set tiktok_accounts = excluded.tiktok_accounts,
        facebook_pages = excluded.facebook_pages,
        app_version = excluded.app_version,
        last_seen_at = excluded.last_seen_at;

  -- Tối đa 200 sự kiện/lần; gửi trùng không đếm 2 lần (khoá chính).
  if jsonb_typeof(p_events) = 'array' then
    with ev as (
      select e ->> 'project_id' as project_id, e ->> 'type' as type,
             coalesce(nullif(e ->> 'at', '')::timestamptz, now()) as at
        from jsonb_array_elements(p_events) with ordinality as t(e, i)
       where i <= 200
    ), ins as (
      insert into public.project_events (user_id, project_id, type, at)
      select uid, project_id, type, least(at, now())
        from ev
       where type in ('created', 'completed') and length(coalesce(project_id, '')) between 1 and 80
      on conflict do nothing
      returning 1
    )
    select count(*) into accepted from ins;
  end if;

  min_v := st ->> 'min_app_version';
  if coalesce(min_v, '') <> '' and public.version_parts(p_app_version) < public.version_parts(min_v) then
    st := st || jsonb_build_object('ok', false, 'code', 'update_required',
                                   'message', 'Cần cập nhật lên bản ' || min_v || ' trở lên');
  end if;

  return st || jsonb_build_object('events_accepted', accepted);
end;
$$;

-- ------------------------------------------------------------------ Admin

create or replace function public.admin_force_logout(p_user uuid)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
begin
  if not public.is_admin() then
    raise exception 'Chỉ admin' using errcode = '42501';
  end if;
  update public.profiles
     set force_logout_at = now(), active_session_id = null
   where id = p_user;
  delete from auth.sessions where user_id = p_user;
end;
$$;

-- "Gỡ máy": bắt đăng nhập lại, không ghi force_logout_at.
create or replace function public.admin_unbind_device(p_user uuid)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
begin
  if not public.is_admin() then
    raise exception 'Chỉ admin' using errcode = '42501';
  end if;
  update public.profiles set active_session_id = null where id = p_user;
  delete from auth.sessions where user_id = p_user;
end;
$$;

-- Danh sách user cho trang admin. security_invoker => RLS của người gọi áp
-- dụng: chỉ admin thấy dòng nào.
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
  (select count(*) from public.device_events d where d.user_id = p.id and d.at > now() - interval '7 days') as device_switches_7d
from public.profiles p
left join public.usage_stats u on u.user_id = p.id
left join public.admin_notes n on n.user_id = p.id;

-- ------------------------------------------------------------------ RLS + quyền

alter table public.profiles enable row level security;
alter table public.admin_notes enable row level security;
alter table public.usage_stats enable row level security;
alter table public.project_events enable row level security;
alter table public.device_events enable row level security;
alter table public.app_config enable row level security;
alter table public.app_releases enable row level security;

do $$
declare t text;
begin
  foreach t in array array['profiles', 'admin_notes', 'usage_stats', 'project_events', 'device_events', 'app_config', 'app_releases']
  loop
    execute format('drop policy if exists admin_all on public.%I', t);
    execute format('create policy admin_all on public.%I for all to authenticated using (public.is_admin()) with check (public.is_admin())', t);
  end loop;
end $$;

drop policy if exists read_all on public.app_config;
create policy read_all on public.app_config for select to anon, authenticated using (true);
-- Đọc được khi CHƯA đăng nhập: bản cũ bị chặn vì min_app_version vẫn tự cập nhật được.
drop policy if exists read_all on public.app_releases;
create policy read_all on public.app_releases for select to anon, authenticated using (true);

-- anon không cần gì ngoài 2 bảng công khai.
revoke all on public.profiles, public.admin_notes, public.usage_stats, public.project_events,
              public.device_events, public.admin_user_stats from anon;
revoke insert, update, delete on public.app_config, public.app_releases from anon;

revoke execute on function public.session_status(), public.claim_device(text, text), public.release_device(),
  public.heartbeat(text, int, int, jsonb), public.admin_force_logout(uuid), public.admin_unbind_device(uuid),
  public.is_admin(), public.handle_new_user() from public, anon;
grant execute on function public.session_status(), public.claim_device(text, text), public.release_device(),
  public.heartbeat(text, int, int, jsonb), public.admin_force_logout(uuid), public.admin_unbind_device(uuid),
  public.is_admin() to authenticated;
