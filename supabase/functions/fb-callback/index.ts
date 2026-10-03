// Trạm chuyển tiếp HTTPS cho đăng nhập Facebook (user-management-plan.md 13.8).
//
// App Facebook ở chế độ Live bắt redirect_uri phải là HTTPS — không dùng được
// http://localhost:<cổng> của tool chạy trên máy user. URI đăng ký với
// Facebook là chính function này; Facebook chuyển trình duyệt về đây kèm
// code + state, function chuyển tiếp ngay về tool trên máy user:
//     http://localhost:<cổng>/api/facebook/callback?code=...&state=...
// Cổng nằm trong state ("<cổng>-<chuỗi ngẫu nhiên>") do tool tự tạo.
//
// Không giữ secret, không đổi code lấy token (việc đó ở fb-token, cần đăng
// nhập). Chỉ chuyển về localhost/127.0.0.1 → không thể bị dùng để chuyển
// hướng người dùng sang trang lạ.
//
// Deploy với --no-verify-jwt: trình duyệt quay về từ Facebook không có JWT.

Deno.serve((req) => {
  const url = new URL(req.url)
  const state = url.searchParams.get('state') ?? ''
  const m = /^(\d{2,5})-[0-9a-f]{16,64}$/.exec(state)
  const port = m ? Number(m[1]) : 0
  if (!m || port < 1024 || port > 65535) {
    return new Response('Yêu cầu đăng nhập Facebook không hợp lệ — mở lại tool và bấm "Kết nối Facebook".', {
      status: 400,
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    })
  }
  const target = new URL(`http://localhost:${port}/api/facebook/callback`)
  for (const key of ['code', 'state', 'error', 'error_reason', 'error_description']) {
    const v = url.searchParams.get(key)
    if (v) target.searchParams.set(key, v)
  }
  return new Response(null, { status: 302, headers: { Location: target.toString(), 'Cache-Control': 'no-store' } })
})
