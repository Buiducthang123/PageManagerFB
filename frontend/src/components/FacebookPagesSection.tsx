import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type FacebookPage } from '../lib/api'
import { secondaryButtonClass } from '../lib/ui'

// Facebook Page đăng Reels qua Graph API — token Page lưu ở máy (app/fb_pages.py),
// không bao giờ trả về trình duyệt (chỉ hiện 6 ký tự cuối). 2 cách thêm Page:
// nhập từ PagesManagerSupperTool (đã đăng nhập Facebook qua OAuth + ngrok), hoặc
// dán tay token lấy ở Graph API Explorer (máy khác không chạy được OAuth).

const STATUS_LABEL: Record<FacebookPage['status'], string> = {
  ok: 'Token còn dùng được',
  expired: 'Token hết hạn / bị thu hồi',
  error: 'Không kiểm tra được',
  unknown: 'Chưa kiểm tra',
}

function expiryText(p: FacebookPage): string {
  if (p.token_expires_at === 0) return 'Token không hết hạn'
  if (!p.token_expires_at) return 'Chưa rõ hạn token'
  const d = new Date(p.token_expires_at * 1000)
  return `Token hết hạn ${d.toLocaleString('vi-VN')}`
}

function PageRow({ page, onChanged }: { page: FacebookPage; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const run = (fn: () => Promise<unknown>) => {
    setError(null)
    setBusy(true)
    fn()
      .then(onChanged)
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false))
  }
  const problem = page.status === 'expired'
  const expiringSoon = !!page.token_expires_at && page.token_expires_at * 1000 - Date.now() < 7 * 86_400_000

  return (
    <li className={`card space-y-3 p-4 ${problem ? 'border-danger-300' : ''}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          {page.picture_url ? (
            <img src={page.picture_url} alt="" className="h-10 w-10 shrink-0 rounded-full object-cover" />
          ) : (
            <div className="h-10 w-10 shrink-0 rounded-full bg-neutral-800" />
          )}
          <div className="min-w-0">
            <a
              href={`https://www.facebook.com/${page.page_id}`}
              target="_blank"
              rel="noreferrer"
              className="block truncate text-text hover:text-accent-200"
            >
              {page.name || page.page_id}
            </a>
            <div className="text-xs text-neutral-500">
              {page.category ?? 'Facebook Page'} · token <span className="mono">{page.token_masked}</span> ·{' '}
              {page.source === 'oauth'
                ? 'kết nối Facebook'
                : page.source === 'pagesmanager'
                  ? 'nhập từ PagesManager'
                  : 'dán tay'}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`text-xs ${problem ? 'text-danger' : page.status === 'ok' ? 'text-accent-200' : 'text-neutral-500'}`}>
            {STATUS_LABEL[page.status]}
          </span>
          <button type="button" className={secondaryButtonClass} disabled={busy} onClick={() => run(() => api.checkFacebookPage(page.page_id))}>
            {busy ? 'Đang kiểm tra...' : 'Kiểm tra'}
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm text-danger"
            disabled={page.projects.length > 0 || busy}
            title={page.projects.length ? 'Bỏ gán khỏi dự án trước khi xoá' : undefined}
            onClick={() => {
              if (window.confirm(`Xoá Page "${page.name}" khỏi tool? (Không ảnh hưởng gì tới Page trên Facebook)`))
                run(() => api.deleteFacebookPage(page.page_id))
            }}
          >
            Xoá
          </button>
        </div>
      </div>
      <div className="text-sm">
        <span className="text-neutral-500">Dự án đăng lên Page này: </span>
        {page.projects.length ? (
          page.projects.map((p) => (
            <Link key={p.social_id} to={`/automated/${p.social_id}`} className="text-text hover:text-accent-200">
              {p.title}
            </Link>
          ))
        ) : (
          <span className="text-neutral-500">chưa gán — gán trong trang dự án tự động</span>
        )}
      </div>
      <p className={`text-xs ${expiringSoon ? 'text-danger' : 'text-neutral-500'}`}>{expiryText(page)}</p>
      {page.status_detail && page.status !== 'ok' && (
        <p className={`text-xs ${problem ? 'text-danger' : 'text-neutral-500'}`}>{page.status_detail}</p>
      )}
      {error && <p className="text-sm text-danger">{error}</p>}
    </li>
  )
}

export default function FacebookPagesSection() {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['facebook-pages'], queryFn: api.listFacebookPages, refetchInterval: 60_000 })
  const [path, setPath] = useState<string | null>(null)
  const [token, setToken] = useState('')
  const [showGuide, setShowGuide] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [oauthError, setOauthError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [params, setParams] = useSearchParams()

  // Đăng nhập Facebook xong, backend chuyển về đây kèm ?fb_connected=N hoặc
  // ?fb_error=... — đọc 1 lần rồi xoá khỏi URL (F5 không hiện lại).
  useEffect(() => {
    const connected = params.get('fb_connected')
    const error = params.get('fb_error')
    if (connected === null && error === null) return
    if (connected !== null) setNotice(`Đã kết nối Facebook — cập nhật ${connected} Page`)
    if (error) setOauthError(error)
    queryClient.invalidateQueries({ queryKey: ['facebook-pages'] })
    setParams({ tab: 'facebook' }, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['facebook-pages'] })
  const importPm = useMutation({
    mutationFn: () => api.importFacebookPagesManager(path ?? query.data?.pages_manager_path ?? ''),
    onSuccess: (r) => {
      setNotice(`Đã nhập ${r.imported} Page từ PagesManager`)
      refresh()
    },
  })
  const addToken = useMutation({
    mutationFn: () => api.addFacebookToken(token),
    onSuccess: (r) => {
      setToken('')
      setNotice(`Đã thêm ${r.imported} Page`)
      refresh()
    },
  })

  const pages = query.data?.pages ?? []
  const oauthReady = query.data?.oauth_configured ?? false
  const redirectUri = query.data?.redirect_uri ?? null

  return (
    <div className="space-y-6">
      <p className="text-sm text-neutral-500">
        Đăng Reels qua API chính thức của Facebook bằng token của Page — không mở trình duyệt. Token chỉ lưu trên máy
        này. App tự kiểm tra token của Page đang dùng mỗi 6 giờ.
      </p>

      <section className="card space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-3">
          {oauthReady && redirectUri ? (
            <a href="/api/facebook/login" className="btn btn-primary">
              Kết nối Facebook
            </a>
          ) : (
            <button type="button" className="btn btn-primary" disabled>
              Kết nối Facebook
            </button>
          )}
          <span className="text-sm text-neutral-400">
            Đăng nhập Facebook, tick chọn các Page cần dùng — Page mới tạo cũng thêm bằng nút này.
          </span>
        </div>
        {!oauthReady ? (
          <p className="text-xs text-danger">
            Chưa cấu hình đăng nhập Facebook — thêm <span className="mono">FB_APP_ID</span>,{' '}
            <span className="mono">FB_APP_SECRET</span> vào file <span className="mono">.env</span> rồi khởi động lại
            backend. Hoặc dùng cách khác bên dưới.
          </p>
        ) : redirectUri ? (
          <div className="space-y-1 text-xs text-neutral-500">
            <div>URI này phải có trong Meta App → Facebook Login → "URI chuyển hướng OAuth hợp lệ":</div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="mono break-all text-neutral-300">{redirectUri}</span>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  navigator.clipboard?.writeText(redirectUri).then(
                    () => setCopied(true),
                    () => setCopied(false),
                  )
                }}
              >
                {copied ? 'Đã copy' : 'Copy'}
              </button>
            </div>
            <div>
              Tên miền ngrok miễn phí đổi mỗi lần bật lại → phải thêm URI mới vào Meta. Dùng tên miền cố định (
              <span className="mono">ngrok http 5175 --url=...</span>) để chỉ khai báo 1 lần.
            </div>
          </div>
        ) : (
          <p className="text-xs text-danger">
            Chưa thấy ngrok đang chạy — mở terminal chạy <span className="mono">ngrok http 5175</span> rồi tải lại trang.
          </p>
        )}
        {oauthError && <p className="text-sm text-danger">{oauthError}</p>}
      </section>

      <details className="card p-4">
        <summary className="cursor-pointer text-sm text-neutral-300 select-none">
          Cách khác: nhập từ PagesManager / dán token
        </summary>
        <div className="mt-4 space-y-6">
          <div className="space-y-3">
            <div className="text-sm text-neutral-300">Nhập từ PagesManagerSupperTool</div>
            <div className="flex flex-wrap gap-2">
              <input
                className="input mono min-w-0 flex-1 text-xs"
                value={path ?? query.data?.pages_manager_path ?? ''}
                onChange={(e) => setPath(e.target.value)}
                placeholder="Đường dẫn tới backend/data/pages.json"
              />
              <button
                type="button"
                className={secondaryButtonClass}
                disabled={importPm.isPending}
                onClick={() => importPm.mutate()}
              >
                {importPm.isPending ? 'Đang nhập...' : 'Nhập Page'}
              </button>
            </div>
            {importPm.error && <p className="text-sm text-danger">{(importPm.error as Error).message}</p>}
          </div>

          <div className="space-y-3">
            <div className="flex items-center justify-between gap-2">
              <label htmlFor="fb-token" className="text-sm text-neutral-300">
                Dán token (máy không chạy được ngrok)
              </label>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowGuide((v) => !v)}>
                {showGuide ? 'Ẩn hướng dẫn' : 'Cách lấy token'}
              </button>
            </div>
            {showGuide && (
              <ol className="list-decimal space-y-1 pl-5 text-xs text-neutral-400">
                <li>
                  Mở{' '}
                  <a
                    className="text-accent-200"
                    href="https://developers.facebook.com/tools/explorer/"
                    target="_blank"
                    rel="noreferrer"
                  >
                    Graph API Explorer
                  </a>{' '}
                  bằng tài khoản Facebook quản trị Page.
                </li>
                <li>
                  Mục "User or Page" chọn <b>Get User Access Token</b>, tick các quyền:{' '}
                  <span className="mono">pages_show_list</span>, <span className="mono">pages_read_engagement</span>,{' '}
                  <span className="mono">pages_manage_posts</span>, <span className="mono">publish_video</span> →
                  Generate Access Token, chọn đúng các Page cần dùng.
                </li>
                <li>
                  Bấm biểu tượng (i) cạnh token → <b>Open in Access Token Tool</b> → <b>Extend Access Token</b> để đổi
                  sang token dài hạn. Token Page lấy từ token dài hạn sẽ <b>không hết hạn</b>.
                </li>
                <li>Copy token dài hạn, dán vào ô dưới — app tự lấy danh sách Page kèm token của từng Page.</li>
              </ol>
            )}
            <div className="flex flex-wrap gap-2">
              <input
                id="fb-token"
                type="password"
                autoComplete="off"
                className="input mono min-w-0 flex-1 text-xs"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="User token (dài hạn) hoặc Page token"
              />
              <button
                type="button"
                className={secondaryButtonClass}
                disabled={addToken.isPending || !token.trim()}
                onClick={() => addToken.mutate()}
              >
                {addToken.isPending ? 'Đang kiểm tra...' : 'Thêm Page'}
              </button>
            </div>
            {addToken.error && <p className="text-sm text-danger">{(addToken.error as Error).message}</p>}
          </div>
        </div>
      </details>

      {notice && <p className="text-sm text-accent-200">{notice}</p>}
      {query.isLoading && <div className="skeleton h-24" />}
      {!query.isLoading && !pages.length && <p className="text-sm text-neutral-500">Chưa có Facebook Page nào.</p>}
      <ul className="space-y-3">
        {pages.map((p) => (
          <PageRow key={p.page_id} page={p} onChanged={refresh} />
        ))}
      </ul>
    </div>
  )
}
