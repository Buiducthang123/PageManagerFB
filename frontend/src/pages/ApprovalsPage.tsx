import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, type AdminUser } from '../lib/api'
import { primaryButtonClass, secondaryButtonClass } from '../lib/ui'
import { FeatureChecks, fmtTime, TtlSelect } from './AdminPage'

// Quyền tick sẵn khi duyệt — đủ để làm 1 video reup từ đầu tới cuối; admin sửa tuỳ người.
const DEFAULT_FEATURES = ['projects', 'capcut', 'download']

export const usePendingUsers = (enabled = true) =>
  useQuery({ queryKey: ['admin-users'], queryFn: api.adminUsers, refetchInterval: 60_000, enabled })

function PendingCard({ user }: { user: AdminUser }) {
  const queryClient = useQueryClient()
  const [features, setFeatures] = useState<string[]>(DEFAULT_FEATURES)
  const [ttl, setTtl] = useState<number | null>(null)
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['admin-users'] })

  const approve = useMutation({
    mutationFn: () =>
      api.adminUpdateUser(user.id, { approval: 'approved', enabled: true, features, session_ttl_hours: ttl }),
    onSuccess: refresh,
  })
  const reject = useMutation({
    mutationFn: () => api.adminUpdateUser(user.id, { approval: 'rejected' }),
    onSuccess: refresh,
  })
  const busy = approve.isPending || reject.isPending
  const error = (approve.error ?? reject.error) as Error | null

  return (
    <li className="space-y-3 rounded-md border border-divider p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className="text-text">{user.display_name || '(chưa nhập tên)'}</div>
          <div className="text-sm text-neutral-400">{user.email}</div>
          {user.signup_contact && <div className="text-sm text-neutral-300">Liên hệ: {user.signup_contact}</div>}
        </div>
        <div className="text-xs text-neutral-500">Đăng ký lúc {fmtTime(user.signup_at ?? user.created_at)}</div>
      </div>
      <div>
        <div className="mb-1.5 text-xs text-neutral-400">Cấp quyền</div>
        <FeatureChecks value={features} onChange={setFeatures} />
      </div>
      <label className="block max-w-xs text-xs text-neutral-400">
        Thời hạn mỗi lần đăng nhập
        <TtlSelect value={ttl} onChange={setTtl} />
      </label>
      {error && <p className="text-sm text-danger">{error.message}</p>}
      <div className="flex flex-wrap gap-2">
        <button type="button" className={primaryButtonClass} disabled={busy || !features.length} onClick={() => approve.mutate()}>
          {approve.isPending ? 'Đang duyệt...' : 'Duyệt'}
        </button>
        <button
          type="button"
          className={secondaryButtonClass}
          disabled={busy}
          onClick={() => {
            if (window.confirm(`Từ chối yêu cầu đăng ký của ${user.email}?`)) reject.mutate()
          }}
        >
          Từ chối
        </button>
        {!features.length && <span className="self-center text-xs text-neutral-500">Chọn ít nhất 1 quyền để duyệt</span>}
      </div>
    </li>
  )
}

function RejectedRow({ user }: { user: AdminUser }) {
  const queryClient = useQueryClient()
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
  const reopen = useMutation({ mutationFn: () => api.adminUpdateUser(user.id, { approval: 'pending' }), onSuccess: refresh })
  const remove = useMutation({ mutationFn: () => api.adminDeleteUser(user.id), onSuccess: refresh })
  return (
    <li className="flex flex-wrap items-center gap-3 px-4 py-2 text-sm">
      <div className="min-w-0 flex-1">
        <span className="text-neutral-300">{user.display_name || user.email}</span>
        <span className="ml-2 text-xs text-neutral-500">{user.email}</span>
      </div>
      <button type="button" className={secondaryButtonClass} disabled={reopen.isPending} onClick={() => reopen.mutate()}>
        Xét lại
      </button>
      <button
        type="button"
        className="btn btn-ghost btn-sm text-danger"
        disabled={remove.isPending}
        onClick={() => {
          if (window.confirm(`Xoá hẳn tài khoản ${user.email}? Người này có thể đăng ký lại.`)) remove.mutate()
        }}
      >
        Xoá
      </button>
    </li>
  )
}

/** Trang "Duyệt user" (chỉ admin): tài khoản tự đăng ký ở màn đăng nhập → duyệt + cấp quyền / từ chối. */
export default function ApprovalsPage() {
  const query = usePendingUsers()
  const users = query.data ?? []
  const pending = users
    .filter((u) => u.approval === 'pending')
    .sort((a, b) => (a.signup_at ?? a.created_at).localeCompare(b.signup_at ?? b.created_at))
  const rejected = users.filter((u) => u.approval === 'rejected')

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl">Duyệt user</h1>
        <Link to="/admin" className="text-sm">
          Quản lý user →
        </Link>
      </div>
      <p className="text-sm text-neutral-400">
        Người dùng tự đăng ký ở màn đăng nhập. Tài khoản bị khoá cho tới khi bạn duyệt; duyệt xong họ đăng nhập bằng
        email + mật khẩu đã tạo. Sửa quyền về sau ở trang Quản lý user.
      </p>
      {query.error && <p className="text-sm text-danger">{(query.error as Error).message}</p>}
      {query.isLoading && <p className="text-sm text-neutral-500">Đang tải...</p>}

      <section className="space-y-2">
        <h2 className="text-lg">Chờ duyệt ({pending.length})</h2>
        {pending.length === 0 && !query.isLoading ? (
          <p className="text-sm text-neutral-500">Không có yêu cầu nào đang chờ.</p>
        ) : (
          <ul className="space-y-3">
            {pending.map((u) => (
              <PendingCard key={u.id} user={u} />
            ))}
          </ul>
        )}
      </section>

      {rejected.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-lg">Đã từ chối ({rejected.length})</h2>
          <ul className="divide-y divide-divider rounded-md border border-divider">
            {rejected.map((u) => (
              <RejectedRow key={u.id} user={u} />
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
