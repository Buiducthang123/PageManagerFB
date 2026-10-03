// Thao tác cần service role (user-management-plan.md mục 7, 9): tạo user,
// đặt lại mật khẩu, xoá user. Chỉ nhận yêu cầu từ tài khoản admin (kiểm tra
// bằng is_admin() với CHÍNH JWT người gọi). Service role key chỉ nằm ở đây.
//
// Body:
//   { action: "create", email, password, display_name?, features?, enabled?, session_ttl_hours? }
//   { action: "reset_password", user_id, password }
//   { action: "delete", user_id }

import { createClient } from 'npm:@supabase/supabase-js@2'

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
}

const FEATURES = new Set([
  'projects', 'capcut', 'tiktok_publish', 'facebook_publish', 'automated',
  'download', 'merge', 'clean_video', 'monitor', 'cleanup',
])

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { ...CORS, 'Content-Type': 'application/json' } })
}

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS })
  if (req.method !== 'POST') return json(405, { error: 'method_not_allowed' })
  const authHeader = req.headers.get('Authorization') ?? ''
  if (!authHeader.startsWith('Bearer ')) return json(401, { error: 'no_auth' })

  const url = Deno.env.get('SUPABASE_URL')!
  const caller = createClient(url, Deno.env.get('SUPABASE_ANON_KEY')!, {
    global: { headers: { Authorization: authHeader } },
    auth: { persistSession: false, autoRefreshToken: false },
  })
  // Admin cũng phải có phiên hợp lệ (đúng máy, chưa bị đá) — không chỉ role.
  const { data: st } = await caller.rpc('session_status')
  const { data: isAdmin } = await caller.rpc('is_admin')
  if (!st?.ok || isAdmin !== true) return json(403, { error: 'forbidden', message: 'Chỉ admin' })

  const admin = createClient(url, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!, {
    auth: { persistSession: false, autoRefreshToken: false },
  })

  let body: Record<string, unknown>
  try {
    body = await req.json()
  } catch {
    return json(400, { error: 'bad_json' })
  }

  const password = String(body.password ?? '')
  if ((body.action === 'create' || body.action === 'reset_password') && password.length < 8) {
    return json(400, { error: 'weak_password', message: 'Mật khẩu tối thiểu 8 ký tự' })
  }

  if (body.action === 'create') {
    const email = String(body.email ?? '').trim().toLowerCase()
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return json(400, { error: 'bad_email', message: 'Email không hợp lệ' })
    const { data, error } = await admin.auth.admin.createUser({ email, password, email_confirm: true })
    if (error || !data.user) return json(400, { error: 'create_failed', message: error?.message ?? 'Không tạo được' })
    const features = Array.isArray(body.features) ? body.features.map(String).filter((f) => FEATURES.has(f)) : []
    const ttl = body.session_ttl_hours == null ? null : Math.max(1, Number(body.session_ttl_hours) | 0)
    // Trigger handle_new_user đã tạo dòng profiles (mặc định khoá) — cập nhật theo form.
    const { error: upErr } = await admin.from('profiles').update({
      display_name: String(body.display_name ?? '').slice(0, 80),
      features,
      enabled: body.enabled !== false,
      session_ttl_hours: ttl,
    }).eq('id', data.user.id)
    if (upErr) return json(500, { error: 'profile_failed', message: upErr.message })
    return json(200, { user_id: data.user.id })
  }

  const userId = String(body.user_id ?? '')
  if (!/^[0-9a-f-]{36}$/.test(userId)) return json(400, { error: 'bad_user_id' })

  if (body.action === 'reset_password') {
    const { error } = await admin.auth.admin.updateUserById(userId, { password })
    if (error) return json(400, { error: 'reset_failed', message: error.message })
    // Mật khẩu mới => phiên cũ hết hiệu lực, user phải đăng nhập lại.
    await caller.rpc('admin_unbind_device', { p_user: userId })
    return json(200, { ok: true })
  }

  if (body.action === 'delete') {
    if (userId === st.user_id) return json(400, { error: 'self_delete', message: 'Không tự xoá tài khoản admin đang dùng' })
    const { error } = await admin.auth.admin.deleteUser(userId)
    if (error) return json(400, { error: 'delete_failed', message: error.message })
    return json(200, { ok: true })
  }

  return json(400, { error: 'bad_action' })
})
