import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type AdminConfig, type AdminUser, type AdminUserPatch } from '../lib/api'
import { useLicense } from '../lib/license'
import { inputClass, primaryButtonClass, secondaryButtonClass } from '../lib/ui'

export const FEATURES: { id: string; label: string }[] = [
  { id: 'projects', label: 'Dự án' },
  { id: 'capcut', label: 'Dựng CapCut' },
  { id: 'tiktok_publish', label: 'Đăng TikTok' },
  { id: 'facebook_publish', label: 'Đăng Facebook' },
  { id: 'automated', label: 'Dự án tự động' },
  { id: 'download', label: 'Tải video' },
  { id: 'merge', label: 'Ghép video' },
  { id: 'clean_video', label: 'Làm sạch video' },
  { id: 'monitor', label: 'Giám sát tiến trình' },
  { id: 'cleanup', label: 'Tự dọn ổ đĩa' },
]

const TTL_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: 'Vĩnh viễn' },
  { value: '24', label: '24 giờ' },
  { value: '168', label: '7 ngày' },
  { value: '720', label: '30 ngày' },
]

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString('vi-VN', { dateStyle: 'short', timeStyle: 'short' })
}

function userState(u: AdminUser): { label: string; cls: string } {
  if (u.approval === 'pending') return { label: 'Chờ duyệt', cls: 'text-accent-300' }
  if (u.approval === 'rejected') return { label: 'Đã từ chối', cls: 'text-neutral-500' }
  if (!u.enabled) return { label: 'Bị khoá', cls: 'text-danger' }
  if (u.account_expires_at && new Date(u.account_expires_at) < new Date()) return { label: 'Hết hạn', cls: 'text-danger' }
  if (!u.last_seen_at) return { label: 'Chưa dùng', cls: 'text-neutral-500' }
  const ago = Date.now() - new Date(u.last_seen_at).getTime()
  if (ago < 15 * 60_000) return { label: 'Đang hoạt động', cls: 'text-accent-300' }
  if (ago > 24 * 3600_000) return { label: 'Quá 1 ngày không thấy', cls: 'text-neutral-400' }
  return { label: 'Gần đây', cls: 'text-neutral-300' }
}

export function FeatureChecks({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3">
      {FEATURES.map((f) => (
        <label key={f.id} className="flex items-center gap-2 text-sm text-neutral-300">
          <input
            type="checkbox"
            checked={value.includes(f.id)}
            onChange={(e) => onChange(e.target.checked ? [...value, f.id] : value.filter((x) => x !== f.id))}
          />
          {f.label}
        </label>
      ))}
    </div>
  )
}

export function TtlSelect({ value, onChange }: { value: number | null; onChange: (v: number | null) => void }) {
  const preset = TTL_OPTIONS.some((o) => o.value === String(value ?? '')) ? String(value ?? '') : 'custom'
  return (
    <div className="flex gap-2">
      <select
        className={`${inputClass} mt-0 flex-1`}
        value={preset}
        onChange={(e) => {
          if (e.target.value === 'custom') onChange(value ?? 48)
          else onChange(e.target.value ? Number(e.target.value) : null)
        }}
      >
        {TTL_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
        <option value="custom">Tuỳ chỉnh (giờ)</option>
      </select>
      {preset === 'custom' && (
        <input
          className={`${inputClass} mt-0 w-24`}
          type="number"
          min={1}
          value={value ?? ''}
          onChange={(e) => onChange(Math.max(1, Number(e.target.value) || 1))}
        />
      )}
    </div>
  )
}

function toDateInput(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function UserEditor({ user, isSelf, onClose }: { user: AdminUser; isSelf: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<AdminUserPatch>({})
  const [newPassword, setNewPassword] = useState('')
  const [notice, setNotice] = useState('')
  useEffect(() => {
    setForm({
      display_name: user.display_name,
      enabled: user.enabled,
      features: user.features,
      role: user.role,
      session_ttl_hours: user.session_ttl_hours,
      account_expires_at: user.account_expires_at,
      note: user.note,
    })
    setNotice('')
    setNewPassword('')
  }, [user])
  const devices = useQuery({ queryKey: ['admin-devices', user.id], queryFn: () => api.adminDevices(user.id) })
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['admin-users'] })

  const save = useMutation({
    mutationFn: () => api.adminUpdateUser(user.id, form),
    onSuccess: () => {
      setNotice('Đã lưu — máy user nhận thay đổi ở lần heartbeat kế tiếp (≤ 5 phút)')
      refresh()
    },
  })
  const action = useMutation({
    mutationFn: async (kind: 'logout' | 'unbind' | 'reset' | 'delete') => {
      if (kind === 'logout') await api.adminForceLogout(user.id)
      if (kind === 'unbind') await api.adminUnbind(user.id)
      if (kind === 'reset') await api.adminResetPassword(user.id, newPassword)
      if (kind === 'delete') await api.adminDeleteUser(user.id)
      return kind
    },
    onSuccess: (kind) => {
      setNotice(
        {
          logout: 'Đã đăng xuất — máy user bị đá ra trong vòng 5 phút. User vẫn đăng nhập lại được nếu biết mật khẩu.',
          unbind: 'Đã gỡ máy — user phải đăng nhập lại.',
          reset: 'Đã đặt mật khẩu mới — gửi cho user qua Zalo, nhắc đổi ngay ở Cài đặt.',
          delete: 'Đã xoá tài khoản.',
        }[kind],
      )
      setNewPassword('')
      refresh()
      if (kind === 'delete') onClose()
    },
  })
  const set = (patch: AdminUserPatch) => setForm((f) => ({ ...f, ...patch }))
  const error = (save.error || action.error) as Error | null

  return (
    <div className="space-y-4 rounded-md border border-divider bg-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-base text-text">{user.email}</div>
          <div className="text-xs text-neutral-500">
            Tạo {fmtTime(user.created_at)} · {user.projects_created} dự án tạo · {user.projects_completed} hoàn thành
          </div>
        </div>
        <button type="button" className={secondaryButtonClass} onClick={onClose}>
          Đóng
        </button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block text-sm text-neutral-300">
          Tên hiển thị
          <input
            className={inputClass}
            value={form.display_name ?? ''}
            onChange={(e) => set({ display_name: e.target.value })}
          />
        </label>
        <label className="block text-sm text-neutral-300">
          Vai trò
          <select
            className={inputClass}
            value={form.role ?? 'user'}
            disabled={isSelf}
            onChange={(e) => set({ role: e.target.value as 'admin' | 'user' })}
          >
            <option value="user">User</option>
            <option value="admin">Admin (có mọi quyền)</option>
          </select>
        </label>
        <div className="text-sm text-neutral-300">
          Thời hạn phiên đăng nhập
          <div className="mt-1">
            <TtlSelect value={form.session_ttl_hours ?? null} onChange={(v) => set({ session_ttl_hours: v })} />
          </div>
        </div>
        <div className="text-sm text-neutral-300">
          Hạn dùng tài khoản
          <div className="mt-1 flex gap-2">
            <input
              className={`${inputClass} mt-0 flex-1`}
              type="date"
              value={toDateInput(form.account_expires_at ?? null)}
              onChange={(e) =>
                set({ account_expires_at: e.target.value ? new Date(`${e.target.value}T23:59:59`).toISOString() : null })
              }
            />
            <button
              type="button"
              className={secondaryButtonClass}
              onClick={() => {
                const base = form.account_expires_at && new Date(form.account_expires_at) > new Date()
                  ? new Date(form.account_expires_at)
                  : new Date()
                base.setDate(base.getDate() + 30)
                set({ account_expires_at: base.toISOString() })
              }}
            >
              +30 ngày
            </button>
          </div>
          <span className="mt-1 block text-xs text-neutral-500">Để trống = không hết hạn.</span>
        </div>
      </div>

      <div className="text-sm text-neutral-300">
        Quyền {form.role === 'admin' && <span className="text-xs text-neutral-500">(admin luôn có mọi quyền)</span>}
        <div className="mt-2">
          <FeatureChecks value={form.features ?? []} onChange={(v) => set({ features: v })} />
        </div>
      </div>

      <label className="flex items-center gap-2 text-sm text-neutral-300">
        <input
          type="checkbox"
          checked={form.enabled ?? false}
          disabled={isSelf}
          onChange={(e) => set({ enabled: e.target.checked })}
        />
        Cho phép đăng nhập (bỏ tick = khoá tài khoản)
      </label>

      <label className="block text-sm text-neutral-300">
        Ghi chú (user không thấy)
        <textarea
          className={`${inputClass} min-h-16`}
          value={form.note ?? ''}
          onChange={(e) => set({ note: e.target.value })}
        />
      </label>

      <button type="button" className={primaryButtonClass} disabled={save.isPending} onClick={() => save.mutate()}>
        {save.isPending ? 'Đang lưu...' : 'Lưu thay đổi'}
      </button>

      <div className="space-y-3 border-t border-divider pt-4">
        <div className="text-sm text-neutral-300">
          Đang dùng trên máy: <span className="text-text">{user.signed_in ? user.active_device_name || '—' : 'chưa đăng nhập'}</span>
          {user.signed_in && <span className="text-neutral-500"> · từ {fmtTime(user.active_login_at)}</span>}
        </div>
        {!isSelf && (
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className={secondaryButtonClass}
              disabled={action.isPending}
              onClick={() => action.mutate('logout')}
            >
              Đăng xuất ngay
            </button>
            <button
              type="button"
              className={secondaryButtonClass}
              disabled={action.isPending || !user.signed_in}
              onClick={() => action.mutate('unbind')}
            >
              Gỡ máy
            </button>
          </div>
        )}
        <p className="text-xs text-neutral-500">
          "Đăng xuất" chỉ bắt đăng nhập lại — muốn chặn hẳn thì bỏ tick "Cho phép đăng nhập" hoặc đặt lại mật khẩu.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <label className="block min-w-0 flex-1 text-sm text-neutral-300">
            Đặt lại mật khẩu
            <input
              className={inputClass}
              type="text"
              autoComplete="off"
              placeholder="Mật khẩu tạm, tối thiểu 8 ký tự"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
            />
          </label>
          <button
            type="button"
            className={secondaryButtonClass}
            disabled={action.isPending || newPassword.length < 8}
            onClick={() => action.mutate('reset')}
          >
            Đặt lại
          </button>
        </div>
        {devices.data && devices.data.length > 0 && (
          <details className="text-xs text-neutral-400">
            <summary className="cursor-pointer select-none">
              Lịch sử đăng nhập ({user.device_switches_7d} lần trong 7 ngày)
            </summary>
            <ul className="mt-1 space-y-0.5">
              {devices.data.map((d) => (
                <li key={d.at}>
                  {fmtTime(d.at)} — {d.device_name || '—'}
                </li>
              ))}
            </ul>
          </details>
        )}
        {!isSelf && (
          <button
            type="button"
            className="text-xs text-danger underline-offset-2 hover:underline"
            disabled={action.isPending}
            onClick={() => {
              if (window.confirm(`Xoá hẳn tài khoản ${user.email}? Thống kê của user này cũng bị xoá.`)) action.mutate('delete')
            }}
          >
            Xoá tài khoản
          </button>
        )}
      </div>

      {notice && <p className="text-sm text-accent-300">{notice}</p>}
      {error && <p className="text-sm text-danger">{error.message}</p>}
    </div>
  )
}

function CreateUserForm({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [features, setFeatures] = useState<string[]>(['projects', 'capcut'])
  const [ttl, setTtl] = useState<number | null>(null)
  const create = useMutation({
    mutationFn: () =>
      api.adminCreateUser({ email: email.trim(), password, display_name: name.trim(), features, session_ttl_hours: ttl }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      onDone()
    },
  })
  return (
    <div className="space-y-4 rounded-md border border-divider bg-surface p-4">
      <div className="text-base text-text">Tạo tài khoản mới</div>
      <div className="grid gap-4 sm:grid-cols-3">
        <label className="block text-sm text-neutral-300">
          Email
          <input className={inputClass} type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="block text-sm text-neutral-300">
          Mật khẩu ban đầu
          <input
            className={inputClass}
            type="text"
            autoComplete="off"
            placeholder="Tối thiểu 8 ký tự"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <label className="block text-sm text-neutral-300">
          Tên hiển thị
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
      </div>
      <div className="text-sm text-neutral-300">
        Thời hạn phiên
        <div className="mt-1 max-w-xs">
          <TtlSelect value={ttl} onChange={setTtl} />
        </div>
      </div>
      <div className="text-sm text-neutral-300">
        Quyền
        <div className="mt-2">
          <FeatureChecks value={features} onChange={setFeatures} />
        </div>
      </div>
      {features.includes('facebook_publish') && (
        <p className="text-xs text-neutral-500">
          Đăng Facebook: nhớ thêm tài khoản Facebook của user làm <b>Tester</b> trong Meta App Dashboard → App roles.
        </p>
      )}
      <div className="flex gap-2">
        <button
          type="button"
          className={primaryButtonClass}
          disabled={create.isPending || !email.trim() || password.length < 8}
          onClick={() => create.mutate()}
        >
          {create.isPending ? 'Đang tạo...' : 'Tạo tài khoản'}
        </button>
        <button type="button" className={secondaryButtonClass} onClick={onDone}>
          Huỷ
        </button>
      </div>
      {create.error && <p className="text-sm text-danger">{(create.error as Error).message}</p>}
    </div>
  )
}

function ConfigPanel() {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['admin-config'], queryFn: api.adminConfig })
  const [form, setForm] = useState<Partial<AdminConfig>>({})
  useEffect(() => {
    if (query.data) setForm(query.data)
  }, [query.data])
  const save = useMutation({
    mutationFn: () =>
      api.adminUpdateConfig({
        login_notice: form.login_notice ?? '',
        min_app_version: form.min_app_version ?? null,
        offline_grace_minutes: Number(form.offline_grace_minutes),
        heartbeat_seconds: Number(form.heartbeat_seconds),
      }),
    onSuccess: (data) => queryClient.setQueryData(['admin-config'], data),
  })
  return (
    <details className="rounded-md border border-divider px-4 py-3 text-sm text-neutral-300">
      <summary className="cursor-pointer select-none">Cấu hình chung</summary>
      <div className="mt-3 space-y-3">
        <label className="block">
          Thông báo cho user (hiện ở màn đăng nhập và đầu app)
          <input
            className={inputClass}
            value={form.login_notice ?? ''}
            placeholder="vd: Bảo trì 22h–23h tối nay"
            onChange={(e) => setForm({ ...form, login_notice: e.target.value })}
          />
        </label>
        <div className="grid gap-3 sm:grid-cols-3">
          <label className="block">
            Bản app tối thiểu
            <input
              className={inputClass}
              value={form.min_app_version ?? ''}
              placeholder="để trống = không chặn"
              onChange={(e) => setForm({ ...form, min_app_version: e.target.value })}
            />
          </label>
          <label className="block">
            Ân hạn offline (phút)
            <input
              className={inputClass}
              type="number"
              min={0}
              value={form.offline_grace_minutes ?? ''}
              onChange={(e) => setForm({ ...form, offline_grace_minutes: Number(e.target.value) })}
            />
          </label>
          <label className="block">
            Heartbeat (giây)
            <input
              className={inputClass}
              type="number"
              min={60}
              max={3600}
              value={form.heartbeat_seconds ?? ''}
              onChange={(e) => setForm({ ...form, heartbeat_seconds: Number(e.target.value) })}
            />
          </label>
        </div>
        <button type="button" className={secondaryButtonClass} disabled={save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? 'Đang lưu...' : 'Lưu cấu hình'}
        </button>
        {save.isSuccess && <p className="text-xs text-accent-300">Đã lưu.</p>}
        {save.error && <p className="text-xs text-danger">{(save.error as Error).message}</p>}
      </div>
    </details>
  )
}

export default function AdminPage() {
  const { license } = useLicense()
  const usersQuery = useQuery({ queryKey: ['admin-users'], queryFn: api.adminUsers, refetchInterval: 60_000 })
  const [selected, setSelected] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  if (license?.role !== 'admin' || license.mode !== 'enabled') {
    return <p className="text-sm text-neutral-400">Trang này chỉ dành cho admin.</p>
  }
  const users = usersQuery.data ?? []
  const current = users.find((u) => u.id === selected) ?? null

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl">Quản lý user</h1>
        <button
          type="button"
          className={primaryButtonClass}
          onClick={() => {
            setCreating(true)
            setSelected(null)
          }}
        >
          Tạo tài khoản
        </button>
      </div>

      {creating && <CreateUserForm onDone={() => setCreating(false)} />}
      {current && (
        <UserEditor
          user={current}
          isSelf={current.email === license.email}
          onClose={() => setSelected(null)}
        />
      )}

      {usersQuery.error && <p className="text-sm text-danger">{(usersQuery.error as Error).message}</p>}
      <div className="overflow-x-auto rounded-md border border-divider">
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead className="border-b border-divider text-xs text-neutral-500">
            <tr>
              <th className="px-3 py-2 font-normal">User</th>
              <th className="px-3 py-2 font-normal">Trạng thái</th>
              <th className="px-3 py-2 font-normal">Thấy lần cuối</th>
              <th className="px-3 py-2 font-normal">Máy</th>
              <th className="px-3 py-2 font-normal">TikTok / FB</th>
              <th className="px-3 py-2 font-normal">Dự án tạo / xong</th>
              <th className="px-3 py-2 font-normal">Bản</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-divider">
            {users.map((u) => {
              const st = userState(u)
              return (
                <tr
                  key={u.id}
                  className={`cursor-pointer hover:bg-accent-900/20 ${selected === u.id ? 'bg-accent-900/30' : ''}`}
                  onClick={() => {
                    setSelected(u.id)
                    setCreating(false)
                  }}
                >
                  <td className="px-3 py-2">
                    <div className="text-text">{u.display_name || u.email}</div>
                    {u.display_name && <div className="text-xs text-neutral-500">{u.email}</div>}
                    {u.role === 'admin' && <div className="text-xs text-accent-300">admin</div>}
                  </td>
                  <td className={`px-3 py-2 ${st.cls}`}>{st.label}</td>
                  <td className="px-3 py-2 text-neutral-300">{fmtTime(u.last_seen_at)}</td>
                  <td className="px-3 py-2 text-neutral-300">
                    {u.signed_in ? u.active_device_name || '—' : '—'}
                    {u.device_switches_7d >= 4 && (
                      <div className="text-xs text-danger" title="Số lần đăng nhập trong 7 ngày">
                        {u.device_switches_7d} lần đổi máy / 7 ngày
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2 text-neutral-300">
                    {u.tiktok_accounts ?? 0} / {u.facebook_pages ?? 0}
                  </td>
                  <td className="px-3 py-2 text-neutral-300">
                    {u.projects_created} / {u.projects_completed}
                  </td>
                  <td className="px-3 py-2 text-neutral-400">{u.app_version || '—'}</td>
                </tr>
              )
            })}
            {usersQuery.isLoading && (
              <tr>
                <td colSpan={7} className="px-3 py-3 text-neutral-500">
                  Đang tải...
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <ConfigPanel />
    </div>
  )
}
