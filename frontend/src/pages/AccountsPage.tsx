import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type TikTokAccount } from '../lib/api'
import { secondaryButtonClass } from '../lib/ui'
import TikTokAccountIdentity from '../components/TikTokAccountIdentity'

// Trang "Tài khoản": mọi tài khoản TikTok dùng để đăng bài — đang đăng nhập
// @ai, gán cho dự án tự động nào, còn phiên đăng nhập hay không. App tự kiểm
// tra lại tài khoản của dự án đang chạy mỗi 6 giờ (xem ACCOUNT_CHECK_INTERVAL_H
// trong app/main.py); nút "Kiểm tra" để kiểm tra ngay.

function AccountRow({ account, onChanged }: { account: TikTokAccount; onChanged: () => void }) {
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

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['accounts'] })

  const create = useMutation({
    mutationFn: async () => {
      const acc = await api.createAccount(newLabel)
      await api.loginAccount(acc.id)
    },
    onSuccess: () => {
      setNewLabel('')
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
      <div>
        <h1 className="text-2xl">Tài khoản TikTok</h1>
        <p className="mt-1 text-sm text-neutral-500">
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

      <section className="card space-y-2 p-4">
        <label htmlFor="new-account-label" className="block text-sm text-neutral-300">
          Thêm tài khoản
        </label>
        <div className="flex flex-wrap gap-2">
          <input
            id="new-account-label"
            className="input min-w-0 flex-1"
            value={newLabel}
            placeholder="Ghi chú (không bắt buộc), vd: Kênh Pokemon phụ"
            onChange={(e) => setNewLabel(e.target.value)}
          />
          <button type="button" className="btn btn-primary" disabled={create.isPending} onClick={() => create.mutate()}>
            Thêm và đăng nhập
          </button>
        </div>
        <p className="text-xs text-neutral-500">
          Một cửa sổ Chrome sẽ mở ra — đăng nhập tay (kể cả 2FA/captcha) rồi đóng cửa sổ. App tự đọc tên tài khoản sau
          khi bạn đóng.
        </p>
        {create.error && <p className="text-sm text-danger">{(create.error as Error).message}</p>}
      </section>

      {query.isLoading && <div className="skeleton h-24" />}
      {!query.isLoading && !accounts.length && <p className="text-sm text-neutral-500">Chưa có tài khoản nào.</p>}
      <ul className="space-y-3">
        {accounts.map((a) => (
          <AccountRow key={a.id} account={a} onChanged={refresh} />
        ))}
      </ul>
    </div>
  )
}
