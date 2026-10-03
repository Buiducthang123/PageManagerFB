// User tự đăng ký ở màn đăng nhập của app → tài khoản CHỜ DUYỆT.
//
// Gọi KHÔNG cần đăng nhập (deploy --no-verify-jwt). Thay cho bật "Enable sign
// ups" của Supabase Auth: bật cái đó thì ai cũng tạo được user đã kích hoạt
// qua thẳng Auth API, còn ở đây tài khoản luôn bị khoá (enabled=false,
// approval='pending', không quyền nào) cho tới khi admin duyệt ở trang
// "Duyệt user" trong app. Service role key chỉ nằm ở đây.
//
// Body: { email, password, display_name, contact? }
// Deploy: npx supabase functions deploy register --project-ref szmjvnhgolzeztcgvups --use-api --no-verify-jwt

import { createClient } from 'npm:@supabase/supabase-js@2'

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
}

// Chống spam đơn giản: quá nhiều yêu cầu đang chờ duyệt trong 1 giờ thì tạm từ chối.
const MAX_PENDING_PER_HOUR = 15

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { ...CORS, 'Content-Type': 'application/json' } })
}

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS })
  if (req.method !== 'POST') return json(405, { error: 'method_not_allowed' })

  let body: Record<string, unknown>
  try {
    body = await req.json()
  } catch {
    return json(400, { error: 'bad_json', message: 'Dữ liệu gửi lên không hợp lệ' })
  }

  const email = String(body.email ?? '').trim().toLowerCase()
  const password = String(body.password ?? '')
  const displayName = String(body.display_name ?? '').trim().slice(0, 80)
  const contact = String(body.contact ?? '').trim().slice(0, 120)
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email) || email.length > 200) {
    return json(400, { error: 'bad_email', message: 'Email không hợp lệ' })
  }
  if (password.length < 8 || password.length > 72) {
    return json(400, { error: 'weak_password', message: 'Mật khẩu từ 8 đến 72 ký tự' })
  }
  if (!displayName) return json(400, { error: 'no_name', message: 'Nhập tên của bạn' })

  const admin = createClient(Deno.env.get('SUPABASE_URL')!, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!, {
    auth: { persistSession: false, autoRefreshToken: false },
  })

  const since = new Date(Date.now() - 3600_000).toISOString()
  const { count } = await admin
    .from('profiles')
    .select('id', { count: 'exact', head: true })
    .eq('approval', 'pending')
    .gte('signup_at', since)
  if ((count ?? 0) >= MAX_PENDING_PER_HOUR) {
    return json(429, { error: 'too_many', message: 'Đang có quá nhiều yêu cầu đăng ký — thử lại sau ít phút' })
  }

  // email_confirm: không gửi mail xác nhận — admin duyệt từng tài khoản rồi mới dùng được.
  const { data, error } = await admin.auth.admin.createUser({ email, password, email_confirm: true })
  if (error || !data.user) {
    const msg = (error?.message ?? '').toLowerCase()
    if (msg.includes('already') || msg.includes('registered') || msg.includes('exists')) {
      return json(409, {
        error: 'email_taken',
        message: 'Email này đã có tài khoản — đăng nhập, hoặc chờ admin duyệt nếu bạn vừa đăng ký',
      })
    }
    return json(400, { error: 'create_failed', message: 'Không tạo được tài khoản — thử lại sau' })
  }

  // Trigger handle_new_user đã tạo dòng profiles — đánh dấu chờ duyệt.
  const { error: upErr } = await admin.from('profiles').update({
    display_name: displayName,
    signup_contact: contact,
    signup_at: new Date().toISOString(),
    approval: 'pending',
    enabled: false,
    features: [],
  }).eq('id', data.user.id)
  if (upErr) {
    // Không để lại tài khoản "nửa vời" (approval mặc định 'approved').
    await admin.auth.admin.deleteUser(data.user.id)
    return json(500, { error: 'profile_failed', message: 'Không tạo được tài khoản — thử lại sau' })
  }
  return json(200, { ok: true })
})
