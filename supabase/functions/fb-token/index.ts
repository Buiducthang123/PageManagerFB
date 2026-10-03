// Đổi token Facebook thay cho app trên máy user (user-management-plan.md 13.1):
// FB_APP_SECRET chỉ nằm trong Supabase secrets, máy user không bao giờ thấy.
//
// Chỉ trả token khi session_status() ok (không bị khoá/đá/đúng máy/chưa hết
// hạn) VÀ user có quyền facebook_publish.
//
// Body:
//   { action: "exchange_code", code, redirect_uri }  -> user token ngắn hạn
//   { action: "extend", token }                      -> user token dài hạn
// Secrets: FB_APP_ID, FB_APP_SECRET, (tuỳ chọn) FB_GRAPH_VERSION.

import { createClient } from 'npm:@supabase/supabase-js@2'

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { ...CORS, 'Content-Type': 'application/json' } })
}

// Chỉ chấp nhận redirect về máy local — chặn dùng function này làm "máy đổi
// code" cho trang web lạ.
function redirectAllowed(uri: string): boolean {
  // Trạm chuyển tiếp HTTPS của chính project (fb-callback) — đường dùng cho bản cài.
  const relay = `${Deno.env.get('SUPABASE_URL')}/functions/v1/fb-callback`
  if (uri === relay) return true
  try {
    const u = new URL(uri)
    return ['localhost', '127.0.0.1'].includes(u.hostname) || u.hostname.endsWith('.ngrok-free.app') ||
      u.hostname.endsWith('.ngrok-free.dev') || u.hostname.endsWith('.ngrok.app') || u.hostname.endsWith('.ngrok.io')
  } catch {
    return false
  }
}

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS })
  if (req.method !== 'POST') return json(405, { error: 'method_not_allowed' })
  const authHeader = req.headers.get('Authorization') ?? ''
  if (!authHeader.startsWith('Bearer ')) return json(401, { error: 'no_auth' })

  const supabase = createClient(Deno.env.get('SUPABASE_URL')!, Deno.env.get('SUPABASE_ANON_KEY')!, {
    global: { headers: { Authorization: authHeader } },
    auth: { persistSession: false, autoRefreshToken: false },
  })
  const { data: st, error } = await supabase.rpc('session_status')
  if (error) {
    if (/^PGRST30/.test(error.code ?? '') || /jwt/i.test(error.message)) return json(401, { error: 'bad_jwt', message: error.message })
    return json(502, { error: 'rpc_failed', message: error.message })
  }
  if (!st?.ok) return json(403, { error: st?.code ?? 'denied', message: st?.message ?? 'Không có quyền' })
  if (!(st.features ?? []).includes('facebook_publish')) {
    return json(403, { error: 'no_feature', message: 'Tài khoản chưa được cấp quyền đăng Facebook' })
  }

  const appId = Deno.env.get('FB_APP_ID')
  const appSecret = Deno.env.get('FB_APP_SECRET')
  if (!appId || !appSecret) return json(500, { error: 'not_configured', message: 'Chưa cấu hình FB_APP_ID/FB_APP_SECRET' })
  const version = Deno.env.get('FB_GRAPH_VERSION') ?? 'v21.0'

  let body: Record<string, unknown>
  try {
    body = await req.json()
  } catch {
    return json(400, { error: 'bad_json' })
  }

  const params = new URLSearchParams({ client_id: appId, client_secret: appSecret })
  if (body.action === 'exchange_code') {
    const code = String(body.code ?? '')
    const redirectUri = String(body.redirect_uri ?? '')
    if (!code || !redirectAllowed(redirectUri)) return json(400, { error: 'bad_request' })
    params.set('code', code)
    params.set('redirect_uri', redirectUri)
  } else if (body.action === 'extend') {
    const token = String(body.token ?? '')
    if (!token) return json(400, { error: 'bad_request' })
    params.set('grant_type', 'fb_exchange_token')
    params.set('fb_exchange_token', token)
  } else {
    return json(400, { error: 'bad_action' })
  }

  const res = await fetch(`https://graph.facebook.com/${version}/oauth/access_token?${params}`)
  const data = await res.json().catch(() => ({}))
  // Trả nguyên lỗi của Facebook (không chứa secret) để app hiện lời nhắn dễ hiểu.
  return json(res.ok ? 200 : 400, data)
})
