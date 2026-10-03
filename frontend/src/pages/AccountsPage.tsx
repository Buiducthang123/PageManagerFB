import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type AccountCredentials, type TikTokAccount } from '../lib/api'
import { secondaryButtonClass } from '../lib/ui'
import TikTokAccountIdentity from '../components/TikTokAccountIdentity'
import FacebookPagesSection from '../components/FacebookPagesSection'

// Trang "Tài khoản": mọi tài khoản TikTok dùng để đăng bài — đang đăng nhập
// @ai, gán cho dự án tự động nào, còn phiên đăng nhập hay không. App tự kiểm
// tra lại tài khoản của dự án đang chạy mỗi 6 giờ (xem ACCOUNT_CHECK_INTERVAL_H
// trong app/main.py); nút "Kiểm tra" để kiểm tra ngay.

const CREDENTIAL_ROWS: { key: keyof AccountCredentials; label: string; secret: boolean }[] = [
  { key: 'username', label: 'Tài khoản', secret: false },
  { key: 'password', label: 'Mật khẩu', secret: true },
  { key: 'email', label: 'Email', secret: false },
  { key: 'email_password', label: 'Mật khẩu email', secret: true },
]

const EMPTY_CREDENTIALS: AccountCredentials = { username: '', password: '', email: '', email_password: '' }

// "tài khoản|mật khẩu|email|mật khẩu email|cookie" → 4 ô; bỏ phần cookie (có
// "=" và ";", hoặc có sessionid) vì giờ chỉ đăng nhập tay.
function parseCredentialLine(line: string): AccountCredentials {
  const fields = line
    .trim()
    .split('|')
    .map((p) => p.trim())
    .filter((p) => !(p.includes('=') && (p.includes(';') || p.includes('sessionid'))))
  const [username = '', password = '', email = '', email_password = ''] = fields
  return { username: username.replace(/^@/, ''), password, email, email_password }
}

// Thông tin đăng nhập lưu mã hoá trên máy (app/secret_store.py) — chỉ tải về
// khi bấm mở, đóng lại là xoá khỏi bộ nhớ trang; mật khẩu che mặc định.
function CredentialsPanel({ account, onChanged }: { account: TikTokAccount; onChanged: () => void }) {
  const [creds, setCreds] = useState<AccountCredentials | null>(account.has_credentials ? null : EMPTY_CREDENTIALS)
  const [draft, setDraft] = useState<AccountCredentials>(EMPTY_CREDENTIALS)
  const [editing, setEditing] = useState(!account.has_credentials)
  const [revealed, setRevealed] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!account.has_credentials) return
    api
      .getAccountCredentials(account.id)
      .then((c) => {
        setCreds(c)
        setDraft(c)
      })
      .catch((err: Error) => setError(err.message))
  }, [account.id, account.has_credentials])

  const save = useMutation({
    mutationFn: () => api.updateAccountCredentials(account.id, draft),
    onSuccess: () => {
      setCreds(draft)
      setEditing(false)
      onChanged()
    },
    onError: (err: Error) => setError(err.message),
  })

  const copy = (key: string, value: string) => {
    navigator.clipboard
      .writeText(value)
      .then(() => {
        setCopied(key)
        window.setTimeout(() => setCopied((k) => (k === key ? null : k)), 1500)
      })
      .catch(() => setError('Không copy được — trình duyệt chặn quyền clipboard'))
  }

  if (error) return <p className="text-sm text-danger">{error}</p>
  if (!creds) return <div className="skeleton h-16" />

  if (editing) {
    return (
      <div className="space-y-2 rounded-lg border border-neutral-800 p-3">
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {CREDENTIAL_ROWS.map((row) => (
            <label key={row.key} className="text-xs text-neutral-400">
              {row.label}
              <input
                className="input mono mt-1 w-full"
                value={draft[row.key]}
                autoComplete="off"
                spellCheck={false}
                onChange={(e) => setDraft({ ...draft, [row.key]: e.target.value })}
              />
            </label>
          ))}
        </div>
        <div className="flex gap-2">
          <button type="button" className={secondaryButtonClass} disabled={save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? 'Đang lưu...' : 'Lưu'}
          </button>
          {account.has_credentials && (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => {
                setDraft(creds)
                setEditing(false)
              }}
            >
              Huỷ
            </button>
          )}
        </div>
        <p className="text-xs text-neutral-500">Lưu mã hoá trên máy này. Để trống hết rồi Lưu = xoá thông tin đã lưu.</p>
      </div>
    )
  }

  return (
    <div className="space-y-2 rounded-lg border border-neutral-800 p-3">
      <dl className="grid grid-cols-[auto_1fr_auto] items-center gap-x-3 gap-y-1.5 text-sm">
        {CREDENTIAL_ROWS.map((row) => {
          const value = creds[row.key]
          return (
            <div key={row.key} className="contents">
              <dt className="text-xs text-neutral-500">{row.label}</dt>
              <dd className="mono min-w-0 truncate">
                {value ? (row.secret && !revealed ? '••••••••' : value) : <span className="text-neutral-600">—</span>}
              </dd>
              <dd>
                {value && (
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => copy(row.key, value)}>
                    {copied === row.key ? 'Đã copy' : 'Copy'}
                  </button>
                )}
              </dd>
            </div>
          )
        })}
      </dl>
      <div className="flex gap-2">
        <button type="button" className={secondaryButtonClass} onClick={() => setRevealed((v) => !v)}>
          {revealed ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing(true)}>
          Sửa
        </button>
      </div>
    </div>
  )
}

function AccountRow({
  account,
  onChanged,
  openCredentials = false,
}: {
  account: TikTokAccount
  onChanged: () => void
  openCredentials?: boolean
}) {
  const [showCredentials, setShowCredentials] = useState(openCredentials && account.has_credentials)
  useEffect(() => {
    if (openCredentials && account.has_credentials) setShowCredentials(true)
  }, [openCredentials, account.has_credentials])
  const [editing, setEditing] = useState(false)
  const [label, setLabel] = useState(account.label)
  const [error, setError] = useState<string | null>(null)

  const run = (fn: () => Promise<unknown>) => {
    setError(null)
    fn()
      .then(onChanged)
      .catch((err: Error) => setError(err.message))
  }

  const saveLabel = useMutation({
    mutationFn: () => api.updateAccount(account.id, label),
    onSuccess: () => {
      setEditing(false)
      onChanged()
    },
    onError: (err: Error) => setError(err.message),
  })

  const remove = () => {
    if (!window.confirm(`Xoá tài khoản ${account.username ? '@' + account.username : account.label}?\nPhiên đăng nhập (profile Chrome) của tài khoản này sẽ bị xoá, muốn dùng lại phải đăng nhập lại.`))
      return
    run(() => api.deleteAccount(account.id))
  }

  const problem = account.status === 'expired' || account.status === 'mismatch' || account.duplicate_uid

  return (
    <li className={`card space-y-3 p-4 ${problem ? 'border-danger-300' : ''}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <TikTokAccountIdentity account={account} />
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className={secondaryButtonClass}
            disabled={account.busy}
            title="Mở Chrome bằng tài khoản này, vào trang kênh để tự xem — đóng cửa sổ khi xem xong"
            onClick={() => run(() => api.viewAccount(account.id))}
          >
            Xem
          </button>
          <button
            type="button"
            className={secondaryButtonClass}
            disabled={account.busy}
            onClick={() => run(() => api.checkAccount(account.id))}
          >
            {account.busy ? 'Đang chạy...' : 'Kiểm tra'}
          </button>
          <button
            type="button"
            className={secondaryButtonClass}
            disabled={account.busy}
            onClick={() => run(() => api.loginAccount(account.id))}
          >
            {account.uid ? 'Đăng nhập lại' : 'Đăng nhập'}
          </button>
          {account.window_open && (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => run(() => api.cancelAccountLogin(account.id))}
            >
              Đóng cửa sổ Chrome
            </button>
          )}
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            aria-expanded={showCredentials}
            onClick={() => setShowCredentials((v) => !v)}
          >
            {showCredentials ? 'Đóng thông tin đăng nhập' : account.has_credentials ? 'Thông tin đăng nhập' : 'Lưu thông tin đăng nhập'}
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing((v) => !v)}>
            Đổi ghi chú
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm text-danger"
            disabled={account.projects.length > 0}
            title={account.projects.length ? 'Bỏ gán khỏi dự án trước khi xoá' : undefined}
            onClick={remove}
          >
            Xoá
          </button>
        </div>
      </div>

      {editing && (
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="input max-w-xs"
            value={label}
            placeholder="Ghi chú, vd: Kênh Pokemon phụ"
            onChange={(e) => setLabel(e.target.value)}
          />
          <button type="button" className={secondaryButtonClass} onClick={() => saveLabel.mutate()}>
            Lưu
          </button>
        </div>
      )}

      {showCredentials && <CredentialsPanel account={account} onChanged={onChanged} />}

      <div className="text-sm">
        <span className="text-neutral-500">Dự án đăng lên tài khoản này: </span>
        {account.projects.length ? (
          account.projects.map((p) => (
            <Link key={p.social_id} to={`/automated/${p.social_id}`} className="text-text hover:text-accent-200">
              {p.title}
            </Link>
          ))
        ) : (
          <span className="text-neutral-500">chưa gán — gán trong trang dự án tự động</span>
        )}
      </div>

      {account.status_detail && account.status !== 'ok' && (
        <p className={`text-xs ${problem ? 'text-danger' : 'text-neutral-500'}`}>{account.status_detail}</p>
      )}
      {account.duplicate_uid && (
        <p className="text-xs text-danger">
          Tài khoản TikTok này đang đăng nhập ở một profile khác — 2 dự án sẽ đăng chung 1 kênh, gấp đôi số bài/ngày.
        </p>
      )}
      {error && <p className="text-sm text-danger">{error}</p>}
    </li>
  )
}

export default function AccountsPage() {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['accounts'],
    queryFn: api.listAccounts,
    // Đang kiểm tra/mở cửa sổ đăng nhập thì cập nhật nhanh để thấy kết quả.
    refetchInterval: (q) => (q.state.data?.some((a) => a.busy) ? 2000 : 30_000),
  })
  const [newLabel, setNewLabel] = useState('')
  // Tab đang mở nằm trên URL (?tab=facebook) — F5 hay mở link vẫn đúng tab.
  const [params, setParams] = useSearchParams()
  const tab: 'tiktok' | 'facebook' = params.get('tab') === 'facebook' ? 'facebook' : 'tiktok'
  const fbQuery = useQuery({ queryKey: ['facebook-pages'], queryFn: api.listFacebookPages, refetchInterval: 60_000 })
  const fbPages = fbQuery.data?.pages ?? []
  const fbProblem = fbPages.filter((p) => p.status === 'expired').length

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['accounts'] })

  const [newCreds, setNewCreds] = useState<AccountCredentials>(EMPTY_CREDENTIALS)
  // Tài khoản vừa thêm — tự mở sẵn khung thông tin đăng nhập để copy vào
  // cửa sổ Chrome đang chờ đăng nhập tay.
  const [justAddedId, setJustAddedId] = useState<string | null>(null)
  const [revealNew, setRevealNew] = useState(false)
  const newHasCreds = Object.values(newCreds).some((v) => v.trim())

  // login=false: chỉ lưu tài khoản + thông tin đăng nhập, đăng nhập sau bằng
  // nút "Đăng nhập" ở dòng tài khoản.
  const create = useMutation({
    mutationFn: async (login: boolean) => {
      const acc = await api.createAccount(newLabel || newCreds.username, newHasCreds ? newCreds : undefined)
      if (login) await api.loginAccount(acc.id)
      return acc.id
    },
    onSuccess: (id) => {
      setNewLabel('')
      setNewCreds(EMPTY_CREDENTIALS)
      setRevealNew(false)
      setJustAddedId(id)
      refresh()
    },
  })

  const accounts = query.data ?? []
  const counts = {
    ok: accounts.filter((a) => a.status === 'ok').length,
    problem: accounts.filter((a) => a.status === 'expired' || a.status === 'mismatch' || a.duplicate_uid).length,
    unassigned: accounts.filter((a) => !a.projects.length).length,
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <h1 className="text-2xl">Tài khoản đăng bài</h1>

      <div role="tablist" className="flex gap-1 border-b border-neutral-800">
        {(
          [
            ['tiktok', 'TikTok', accounts.length, counts.problem],
            ['facebook', 'Facebook Page', fbPages.length, fbProblem],
          ] as const
        ).map(([key, label, total, problem]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setParams(key === 'tiktok' ? {} : { tab: key }, { replace: true })}
            className={`-mb-px flex items-center gap-2 border-b-2 px-4 py-2 text-sm transition-colors ${
              tab === key
                ? 'border-accent text-text'
                : 'border-transparent text-neutral-500 hover:text-neutral-300'
            }`}
          >
            {label}
            <span className="mono text-xs text-neutral-500">{total}</span>
            {problem > 0 && (
              <span className="tag tag-danger" title="Cần xử lý">
                {problem}
              </span>
            )}
          </button>
        ))}
      </div>

      {tab === 'facebook' ? (
        <FacebookPagesSection />
      ) : (
        <>
          <div>
            <p className="text-sm text-neutral-500">
              Mỗi tài khoản là một cửa sổ Chrome riêng đã đăng nhập tay. App đọc tên tài khoản thật để biết mỗi dự án đang
              đăng lên kênh nào, tự kiểm tra lại mỗi 6 giờ, và không đăng khi tài khoản hết đăng nhập hoặc không khớp tài
              khoản đã gán.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <div className="card px-4 py-3">
              <div className="text-xs text-neutral-500">Tổng tài khoản</div>
              <div className="mono mt-1 text-2xl">{accounts.length}</div>
            </div>
            <div className="card px-4 py-3">
              <div className="text-xs text-neutral-500">Đang đăng nhập</div>
              <div className="mono mt-1 text-2xl">{counts.ok}</div>
            </div>
            <div className={`card px-4 py-3 ${counts.problem ? 'border-danger-300' : ''}`}>
              <div className="text-xs text-neutral-500">Cần xử lý</div>
              <div className="mono mt-1 text-2xl">{counts.problem}</div>
            </div>
            <div className="card px-4 py-3">
              <div className="text-xs text-neutral-500">Chưa gán dự án</div>
              <div className="mono mt-1 text-2xl">{counts.unassigned}</div>
            </div>
          </div>

          <section className="card space-y-3 p-4">
            <label htmlFor="new-account-paste" className="block text-sm text-neutral-300">
              Thêm tài khoản
            </label>
            <input
              id="new-account-paste"
              className="input mono w-full text-xs"
              placeholder="Dán nhanh 1 dòng: tài khoản|mật khẩu|email|mật khẩu email (phần cookie phía sau nếu có sẽ bị bỏ qua)"
              autoComplete="off"
              spellCheck={false}
              value=""
              onChange={() => {}}
              onPaste={(e) => {
                e.preventDefault()
                setNewCreds(parseCredentialLine(e.clipboardData.getData('text')))
              }}
            />
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {CREDENTIAL_ROWS.map((row) => (
                <label key={row.key} className="text-xs text-neutral-400">
                  {row.label}
                  <input
                    className="input mono mt-1 w-full"
                    type={row.secret && !revealNew ? 'password' : 'text'}
                    autoComplete="off"
                    spellCheck={false}
                    value={newCreds[row.key]}
                    onChange={(e) => setNewCreds({ ...newCreds, [row.key]: e.target.value })}
                  />
                </label>
              ))}
            </div>
            <button
              type="button"
              className="btn btn-ghost btn-sm w-fit"
              aria-pressed={revealNew}
              onClick={() => setRevealNew((v) => !v)}
            >
              {revealNew ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}
            </button>
            <div className="flex flex-wrap gap-2">
              <input
                id="new-account-label"
                className="input min-w-0 flex-1"
                value={newLabel}
                placeholder="Ghi chú (không bắt buộc), vd: Kênh Pokemon phụ"
                onChange={(e) => setNewLabel(e.target.value)}
              />
              <button
                type="button"
                className="btn btn-secondary"
                disabled={create.isPending || !newHasCreds}
                title={newHasCreds ? 'Lưu tài khoản, chưa mở Chrome — đăng nhập sau bằng nút Đăng nhập' : 'Điền thông tin đăng nhập trước'}
                onClick={() => create.mutate(false)}
              >
                Chỉ lưu
              </button>
              <button type="button" className="btn btn-primary" disabled={create.isPending} onClick={() => create.mutate(true)}>
                {create.isPending ? 'Đang thêm...' : 'Thêm và đăng nhập'}
              </button>
            </div>
            <p className="text-xs text-neutral-500">
              <b>Thêm và đăng nhập</b>: mở cửa sổ Chrome để bạn đăng nhập tay (kể cả 2FA/captcha/mã gửi về email), thông tin
              đăng nhập hiện sẵn kèm nút Copy ở dòng tài khoản bên dưới; đóng cửa sổ khi xong, app tự đọc tên tài khoản.{' '}
              <b>Chỉ lưu</b>: lưu để đăng nhập sau. Thông tin được lưu mã hoá trên máy; không bắt buộc điền.
            </p>
            {create.error && <p className="text-sm text-danger">{(create.error as Error).message}</p>}
          </section>

          {query.isLoading && <div className="skeleton h-24" />}
          {!query.isLoading && !accounts.length && <p className="text-sm text-neutral-500">Chưa có tài khoản nào.</p>}
          <ul className="space-y-3">
            {accounts.map((a) => (
              <AccountRow key={a.id} account={a} onChanged={refresh} openCredentials={a.id === justAddedId} />
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
