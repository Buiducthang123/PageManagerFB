// Heartbeat có chữ ký (user-management-plan.md 13.9).
//
// App gửi số liệu + `nonce` ngẫu nhiên; function gọi RPC `heartbeat` bằng
// CHÍNH JWT của user (RLS/session_status áp dụng như user gọi), rồi ký phán
// quyết bằng khoá Ed25519 giữ trong secret HEARTBEAT_SIGNING_KEY. App kiểm
// tra chữ ký bằng khoá công khai nhúng sẵn + nonce vừa gửi → không giả/phát
// lại được phản hồi kể cả khi user chặn HTTPS trên máy mình.
//
// Secret cần đặt: HEARTBEAT_SIGNING_KEY = khoá bí mật Ed25519 dạng PKCS8 base64
// (tạo bằng tools/gen_license_keys.py). SUPABASE_URL / SUPABASE_ANON_KEY có sẵn.

import { createClient } from 'npm:@supabase/supabase-js@2'

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
}

function b64(bytes: Uint8Array): string {
  let s = ''
  for (const b of bytes) s += String.fromCharCode(b)
  return btoa(s)
}

function unb64(text: string): Uint8Array {
  const raw = atob(text.trim())
  const out = new Uint8Array(raw.length)
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}

let signingKey: CryptoKey | null = null
async function getSigningKey(): Promise<CryptoKey> {
  if (signingKey) return signingKey
  const pkcs8 = Deno.env.get('HEARTBEAT_SIGNING_KEY')
  if (!pkcs8) throw new Error('HEARTBEAT_SIGNING_KEY chưa đặt')
  signingKey = await crypto.subtle.importKey('pkcs8', unb64(pkcs8), { name: 'Ed25519' }, false, ['sign'])
  return signingKey
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { ...CORS, 'Content-Type': 'application/json' } })
}

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS })
  if (req.method !== 'POST') return json(405, { error: 'method_not_allowed' })

  const authHeader = req.headers.get('Authorization') ?? ''
  if (!authHeader.startsWith('Bearer ')) return json(401, { error: 'no_auth' })

  let body: Record<string, unknown>
  try {
    body = await req.json()
  } catch {
    return json(400, { error: 'bad_json' })
  }
  const nonce = String(body.nonce ?? '')
  if (!/^[A-Za-z0-9_-]{16,128}$/.test(nonce)) return json(400, { error: 'bad_nonce' })
  const events = Array.isArray(body.events) ? body.events.slice(0, 200) : []

  const supabase = createClient(Deno.env.get('SUPABASE_URL')!, Deno.env.get('SUPABASE_ANON_KEY')!, {
    global: { headers: { Authorization: authHeader } },
    auth: { persistSession: false, autoRefreshToken: false },
  })
  const { data, error } = await supabase.rpc('heartbeat', {
    p_app_version: String(body.app_version ?? '').slice(0, 32),
    p_tiktok_accounts: Number(body.tiktok_accounts ?? 0) | 0,
    p_facebook_pages: Number(body.facebook_pages ?? 0) | 0,
    p_events: events,
  })
  if (error) {
    // JWT hết hạn/bị thu hồi (PostgREST PGRST301/303) → 401 để app biết phiên đã mất.
    if (/^PGRST30/.test(error.code ?? '') || /jwt/i.test(error.message)) return json(401, { error: 'bad_jwt', message: error.message })
    return json(502, { error: 'rpc_failed', message: error.message })
  }

  const verdict = { ...(data as Record<string, unknown>), nonce, issued_at: new Date().toISOString() }
  const payload = new TextEncoder().encode(JSON.stringify(verdict))
  const signature = new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, await getSigningKey(), payload))
  return json(200, { payload: b64(payload), signature: b64(signature) })
})
