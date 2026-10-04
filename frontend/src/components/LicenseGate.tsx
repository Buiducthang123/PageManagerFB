import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, LICENSE_EVENT, type LicenseStatus } from '../lib/api'
import { LicenseContext, makeHasFeature } from '../lib/license'
import { inputClass, primaryButtonClass, secondaryButtonClass } from '../lib/ui'
import UpdateBanner from './UpdateBanner'

function formatMinutes(total: number): string {
  const h = Math.floor(total / 60)
  const m = Math.round(total % 60)
  return h > 0 ? `${h} giờ ${m} phút` : `${m} phút`
}

/** Tự đăng ký — tài khoản tạo ra bị khoá, chờ admin duyệt + cấp quyền ở trang "Duyệt user". */
function RegisterScreen({ onDone, onBack }: { onDone: (email: string) => void; onBack: () => void }) {
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [contact, setContact] = useState('')
  const [password, setPassword] = useState('')
  const [password2, setPassword2] = useState('')
  const mismatch = password2.length > 0 && password !== password2
  const registerMutation = useMutation({
    mutationFn: () => api.register({ email: email.trim(), password, display_name: name.trim(), contact: contact.trim() }),
    onSuccess: () => onDone(email.trim()),
  })
  const canSubmit = !!email.trim() && !!name.trim() && password.length >= 8 && password === password2
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (canSubmit) registerMutation.mutate()
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-4 text-text">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-lg border border-divider bg-surface p-6">
        <div className="flex items-center gap-3">
          <span className="block h-[18px] w-[18px] rounded border border-accent" />
          <h1 className="font-heading text-lg font-medium">Đăng ký tài khoản</h1>
        </div>
        <p className="text-xs text-neutral-400">
          Sau khi đăng ký, admin sẽ duyệt và cấp quyền. Duyệt xong bạn đăng nhập bằng email và mật khẩu này.
        </p>
        <label className="block text-sm text-neutral-300">
          Email
          <input className={inputClass} type="email" autoComplete="username" autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="block text-sm text-neutral-300">
          Tên của bạn
          <input className={inputClass} autoComplete="name" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="block text-sm text-neutral-300">
          Số điện thoại / Zalo <span className="text-neutral-500">(để admin liên hệ — không bắt buộc)</span>
          <input className={inputClass} autoComplete="tel" maxLength={120} value={contact} onChange={(e) => setContact(e.target.value)} />
        </label>
        <label className="block text-sm text-neutral-300">
          Mật khẩu <span className="text-neutral-500">(tối thiểu 8 ký tự)</span>
          <input className={inputClass} type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        <label className="block text-sm text-neutral-300">
          Nhập lại mật khẩu
          <input className={inputClass} type="password" autoComplete="new-password" value={password2} onChange={(e) => setPassword2(e.target.value)} />
        </label>
        {mismatch && <p className="text-sm text-danger">Mật khẩu nhập lại không khớp</p>}
        {registerMutation.error && <p className="text-sm text-danger">{(registerMutation.error as Error).message}</p>}
        <button type="submit" className={`${primaryButtonClass} w-full`} disabled={registerMutation.isPending || !canSubmit}>
          {registerMutation.isPending ? 'Đang gửi...' : 'Gửi yêu cầu đăng ký'}
        </button>
        <button type="button" className={`${secondaryButtonClass} w-full`} onClick={onBack}>
          Đã có tài khoản? Đăng nhập
        </button>
      </form>
    </div>
  )
}

function LoginScreen({ license }: { license: LicenseStatus }) {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState(license.last_email || '')
  const [password, setPassword] = useState('')
  const [registering, setRegistering] = useState(false)
  const [registeredNotice, setRegisteredNotice] = useState(false)
  const loginMutation = useMutation({
    mutationFn: () => api.login(email.trim(), password),
    onSuccess: (status) => {
      setPassword('')
      queryClient.setQueryData(['license'], status)
      void queryClient.invalidateQueries()
    },
  })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (email.trim() && password) loginMutation.mutate()
  }

  if (registering) {
    return (
      <RegisterScreen
        onBack={() => setRegistering(false)}
        onDone={(registered) => {
          setEmail(registered)
          setPassword('')
          setRegisteredNotice(true)
          setRegistering(false)
        }}
      />
    )
  }

  return (
    // Cột: banner "Có bản mới" ở trên (chạy được KHI CHƯA đăng nhập — /api/update/*
    // nằm trong OPEN_PREFIXES, không cần license), form đăng nhập ở giữa. Sửa vụ
    // user cài bản cũ (bộ cài 1.0.x) bị kẹt: trước đây màn này không có đường tự
    // cập nhật, mà bản cũ lại chưa có nút Đăng ký → không vào được.
    <div className="flex min-h-screen flex-col bg-bg text-text">
      <UpdateBanner />
      <div className="flex flex-1 items-center justify-center px-4 py-8">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-lg border border-divider bg-surface p-6">
        <div className="flex items-center gap-3">
          <span className="block h-[18px] w-[18px] rounded border border-accent" />
          <h1 className="font-heading text-lg font-medium">OddlyLab Reup</h1>
        </div>
        {license.login_notice && (
          <p className="rounded-md border border-accent/40 bg-accent-900/30 px-3 py-2 text-sm text-accent-200">
            {license.login_notice}
          </p>
        )}
        {registeredNotice && (
          <p className="rounded-md border border-accent/40 bg-accent-900/30 px-3 py-2 text-sm text-accent-200">
            Đã gửi yêu cầu đăng ký. Admin duyệt xong bạn đăng nhập bằng email và mật khẩu vừa tạo.
          </p>
        )}
        {license.message && !loginMutation.error && <p className="text-sm text-danger">{license.message}</p>}
        <label className="block text-sm text-neutral-300">
          Email
          <input
            className={inputClass}
            type="email"
            autoComplete="username"
            autoFocus={!email}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label className="block text-sm text-neutral-300">
          Mật khẩu
          <input
            className={inputClass}
            type="password"
            autoComplete="current-password"
            autoFocus={!!email}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {loginMutation.error && <p className="text-sm text-danger">{(loginMutation.error as Error).message}</p>}
        <button
          type="submit"
          className={`${primaryButtonClass} w-full`}
          disabled={loginMutation.isPending || !email.trim() || !password}
        >
          {loginMutation.isPending ? 'Đang đăng nhập...' : 'Đăng nhập'}
        </button>
        <button
          type="button"
          className={`${secondaryButtonClass} w-full`}
          onClick={() => {
            loginMutation.reset()
            setRegistering(true)
          }}
        >
          Chưa có tài khoản? Đăng ký
        </button>
        <p className="text-xs text-neutral-500">
          Mỗi tài khoản dùng trên 1 máy — đăng nhập ở đây sẽ đăng xuất máy khác. Quên mật khẩu: liên hệ admin.
        </p>
        <p className="text-xs text-neutral-500">
          App gửi về máy chủ: số tài khoản đã liên kết, số dự án đã tạo/hoàn thành, tên máy, phiên bản app. Không gửi
          video, nội dung dự án, mật khẩu hay API key.
        </p>
      </form>
      </div>
    </div>
  )
}

function BlockedScreen({ license, onLogout }: { license: LicenseStatus; onLogout: () => void }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-4 text-text">
      <div className="w-full max-w-sm space-y-4 rounded-lg border border-divider bg-surface p-6 text-center">
        <h1 className="text-lg">Cần cập nhật</h1>
        <p className="text-sm text-neutral-300">{license.message}</p>
        <p className="text-xs text-neutral-500">Bản đang dùng: {license.app_version}</p>
        <UpdateBanner blocking />
        <button type="button" className={secondaryButtonClass} onClick={onLogout}>
          Đăng xuất
        </button>
      </div>
    </div>
  )
}

function LockedOverlay({ license }: { license: LicenseStatus }) {
  const queryClient = useQueryClient()
  const retry = useMutation({
    mutationFn: api.licenseRetry,
    onSuccess: (status) => queryClient.setQueryData(['license'], status),
  })
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4 backdrop-blur-sm">
      <div className="w-full max-w-md space-y-4 rounded-lg border border-divider bg-surface p-6 text-center">
        <h2 className="text-lg">Tạm khoá</h2>
        <p className="text-sm text-neutral-300">{license.message || 'Mất kết nối tới máy chủ — đang thử lại…'}</p>
        <p className="text-xs text-neutral-500">
          Việc đang chạy dở vẫn chạy nốt. Kết nối lại được thì màn này tự đóng, mọi thứ đang mở giữ nguyên.
        </p>
        <button type="button" className={primaryButtonClass} disabled={retry.isPending} onClick={() => retry.mutate()}>
          {retry.isPending ? 'Đang thử...' : 'Thử lại ngay'}
        </button>
        <a href="/api/system/diagnostics" download className="block text-xs">
          Xuất log chẩn đoán (gửi admin nếu lỗi kéo dài)
        </a>
      </div>
    </div>
  )
}

export default function LicenseGate({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['license'],
    queryFn: api.licenseStatus,
    refetchInterval: (q) => (q.state.data?.status === 'locked' || q.state.data?.status === 'offline' ? 10_000 : 30_000),
    retry: false,
  })
  useEffect(() => {
    const onChange = () => void queryClient.invalidateQueries({ queryKey: ['license'] })
    window.addEventListener(LICENSE_EVENT, onChange)
    return () => window.removeEventListener(LICENSE_EVENT, onChange)
  }, [queryClient])

  const license = query.data ?? null
  const value = useMemo(
    () => ({
      license,
      hasFeature: makeHasFeature(license),
      refresh: () => void queryClient.invalidateQueries({ queryKey: ['license'] }),
    }),
    [license, queryClient],
  )
  const logout = useMutation({
    mutationFn: api.logout,
    onSuccess: (status) => queryClient.setQueryData(['license'], status),
  })

  if (!license) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg text-sm text-neutral-500">
        {query.error ? `Không kết nối được backend: ${(query.error as Error).message}` : 'Đang tải...'}
      </div>
    )
  }
  if (license.mode !== 'disabled') {
    if (license.status === 'signed_out') return <LoginScreen license={license} />
    if (license.status === 'blocked') return <BlockedScreen license={license} onLogout={() => logout.mutate()} />
    if (license.status === 'locked' && license.mode === 'misconfigured') return <LockedOverlay license={license} />
  }

  return (
    <LicenseContext.Provider value={value}>
      {license.mode !== 'disabled' && license.status === 'offline' && (
        <div className="fixed bottom-3 right-3 z-40 rounded-md border border-divider bg-surface px-3 py-1.5 text-xs text-neutral-300 shadow">
          {license.code === 'offline' && license.message !== 'Đang offline'
            ? license.message
            : `Đang offline — còn ${formatMinutes(license.offline_remaining_minutes)}`}
        </div>
      )}
      <UpdateBanner />
      {license.mode !== 'disabled' && license.login_notice && license.status === 'active' && (
        <div className="border-b border-accent/40 bg-accent-900/30 px-4 py-1.5 text-center text-xs text-accent-200">
          {license.login_notice}
        </div>
      )}
      {children}
      {license.mode !== 'disabled' && license.status === 'locked' && <LockedOverlay license={license} />}
    </LicenseContext.Provider>
  )
}
