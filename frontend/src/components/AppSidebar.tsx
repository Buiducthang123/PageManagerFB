import { useState, type ReactNode } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { useLicense } from '../lib/license'

function navClass({ isActive }: { isActive: boolean }): string {
  return [
    'flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors',
    isActive ? 'bg-accent-900/70 text-accent-200' : 'text-neutral-300 hover:bg-accent-900/30 hover:text-text',
  ].join(' ')
}

function ProjectsIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4 shrink-0" aria-hidden>
      <rect x="2.5" y="4" width="15" height="12" rx="2" stroke="currentColor" strokeWidth="1.4" />
      <path d="M2.5 7.5h15" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  )
}

function MergeIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4 shrink-0" aria-hidden>
      <path d="M5 4v5a4 4 0 0 0 4 4h2a4 4 0 0 0 4-4V4" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="5" cy="4" r="1.6" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="15" cy="4" r="1.6" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="10" cy="16" r="1.6" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  )
}

function CleanVideoIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4 shrink-0" aria-hidden>
      <rect x="2.5" y="4" width="15" height="12" rx="2" stroke="currentColor" strokeWidth="1.4" />
      <path d="M6 12.5h5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeDasharray="1.5 2" />
      <path d="M13.5 7.2l.5 1.3 1.3.5-1.3.5-.5 1.3-.5-1.3-1.3-.5 1.3-.5z" fill="currentColor" />
    </svg>
  )
}

function DownloadIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4 shrink-0" aria-hidden>
      <path d="M10 3v9m0 0 3.5-3.5M10 12 6.5 8.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M3.5 14.5V16a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1v-1.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  )
}

function AutomatedIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4 shrink-0" aria-hidden>
      <circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="1.4" />
      <path d="M10 6v4l2.6 2.6" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function AccountsIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4 shrink-0" aria-hidden>
      <circle cx="10" cy="7" r="3" stroke="currentColor" strokeWidth="1.4" />
      <path d="M4 16.5c.8-3 3.2-4.5 6-4.5s5.2 1.5 6 4.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  )
}

function MonitorIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4 shrink-0" aria-hidden>
      <path d="M2.5 10h3l2-5 3 10 2-5h5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function CleanupIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4 shrink-0" aria-hidden>
      <path d="M4 6h12M8 6V4.5h4V6M5.5 6l.8 10h7.4l.8-10" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M8.5 9v4.5M11.5 9v4.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  )
}

function SettingsIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4 shrink-0" aria-hidden>
      <circle cx="10" cy="10" r="2.6" stroke="currentColor" strokeWidth="1.4" />
      <path
        d="M10 2.8v1.6M10 15.6v1.6M17.2 10h-1.6M4.4 10H2.8M15.1 4.9l-1.1 1.1M6 14l-1.1 1.1M15.1 15.1 14 14M6 6 4.9 4.9"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
  )
}

function AdminIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4 shrink-0" aria-hidden>
      <path d="M10 2.8 4 5.2v4.3c0 3.6 2.5 6.4 6 7.7 3.5-1.3 6-4.1 6-7.7V5.2z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
      <circle cx="10" cy="8.6" r="1.8" stroke="currentColor" strokeWidth="1.3" />
      <path d="M7 13.2c.6-1.4 1.7-2 3-2s2.4.6 3 2" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  )
}

function SystemCheckIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4 shrink-0" aria-hidden>
      <rect x="3" y="3" width="14" height="14" rx="2.5" stroke="currentColor" strokeWidth="1.4" />
      <path d="m6.5 10.2 2.2 2.2 4.8-4.8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

// `features`: hiện mục khi có ít nhất 1 quyền (rỗng = luôn hiện). Chỉ để menu
// gọn — chặn thật ở backend (app/license/guard.py).
const NAV_ITEMS: { to: string; end: boolean; label: string; icon: ReactNode; features: string[] }[] = [
  { to: '/', end: true, label: 'Dự án', icon: <ProjectsIcon />, features: ['projects'] },
  { to: '/merge', end: false, label: 'Ghép video', icon: <MergeIcon />, features: ['merge'] },
  { to: '/clean-video', end: false, label: 'Làm sạch video', icon: <CleanVideoIcon />, features: ['clean_video'] },
  { to: '/download', end: false, label: 'Tải video', icon: <DownloadIcon />, features: ['download'] },
  { to: '/automated', end: false, label: 'Dự án tự động', icon: <AutomatedIcon />, features: ['automated'] },
  { to: '/accounts', end: false, label: 'Tài khoản', icon: <AccountsIcon />, features: ['tiktok_publish', 'facebook_publish'] },
  { to: '/monitor', end: false, label: 'Giám sát tiến trình', icon: <MonitorIcon />, features: ['monitor'] },
  { to: '/cleanup', end: false, label: 'Tự dọn ổ đĩa', icon: <CleanupIcon />, features: ['cleanup'] },
  { to: '/settings', end: false, label: 'Cài đặt', icon: <SettingsIcon />, features: [] },
  { to: '/system-check', end: false, label: 'Kiểm tra hệ thống', icon: <SystemCheckIcon />, features: [] },
]

function UserBox({ onNavigate }: { onNavigate?: () => void }) {
  const { license } = useLicense()
  const queryClient = useQueryClient()
  const logout = useMutation({
    mutationFn: api.logout,
    onSuccess: (status) => {
      queryClient.setQueryData(['license'], status)
      onNavigate?.()
    },
  })
  if (!license || license.mode === 'disabled' || !license.email) return null
  return (
    <div className="border-t border-divider px-2 pt-3 text-xs text-neutral-400">
      <div className="truncate text-neutral-200" title={license.email}>
        {license.display_name || license.email}
      </div>
      <div className="truncate">
        {license.role === 'admin' ? 'Admin · ' : ''}
        {license.device_name}
      </div>
      <button
        type="button"
        className="mt-2 text-neutral-400 underline-offset-2 hover:text-text hover:underline"
        disabled={logout.isPending}
        onClick={() => {
          if (window.confirm('Đăng xuất khỏi máy này?')) logout.mutate()
        }}
      >
        {logout.isPending ? 'Đang đăng xuất...' : 'Đăng xuất'}
      </button>
    </div>
  )
}

function PowerIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4 shrink-0" aria-hidden>
      <path d="M10 3v6.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M6.2 5.6a6 6 0 1 0 7.6 0" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}

/** Tắt hẳn app (backend chạy ngầm). Tắt trình duyệt KHÔNG tắt app — Dự án tự
 * động vẫn chạy nền; nút này để dừng hẳn. */
function QuitAppButton() {
  const [state, setState] = useState<'idle' | 'checking' | 'quitting' | 'done'>('idle')
  const [error, setError] = useState('')

  const quit = async () => {
    setError('')
    setState('checking')
    let running: string[] = []
    try {
      running = (await api.appRunningJobs()).jobs
    } catch {
      /* không hỏi được thì vẫn cho tắt */
    }
    const warn = running.length
      ? `Đang có ${running.length} việc chạy dở — tắt app sẽ HUỶ các việc này.

`
      : ''
    const ok = window.confirm(
      `${warn}Tắt hẳn OddlyLab Reup?

App sẽ dừng chạy ngầm: Dự án tự động ngừng crawl/xử lý/đăng bài cho tới khi mở lại bằng shortcut.`,
    )
    if (!ok) {
      setState('idle')
      return
    }
    setState('quitting')
    try {
      await api.quitApp()
      setState('done')
    } catch (err) {
      setError((err as Error).message)
      setState('idle')
    }
  }

  if (state === 'done') {
    return (
      <div className="fixed inset-0 z-[60] flex items-center justify-center bg-bg px-4 text-center text-text">
        <div className="max-w-sm space-y-2">
          <h2 className="text-lg">Đã tắt OddlyLab Reup</h2>
          <p className="text-sm text-neutral-400">
            App không còn chạy ngầm. Muốn dùng lại thì mở bằng shortcut <b>OddlyLab Reup</b> ngoài Desktop hoặc Start Menu.
            Có thể đóng tab này.
          </p>
        </div>
      </div>
    )
  }
  return (
    <div>
      <button
        type="button"
        className="flex w-full items-center justify-center gap-2 rounded-md border border-danger/60 bg-danger/15 px-3 py-2 text-sm font-medium text-danger transition-colors hover:bg-danger hover:text-white disabled:opacity-60"
        disabled={state !== 'idle'}
        onClick={() => void quit()}
        title="Dừng hẳn app đang chạy ngầm (tắt trình duyệt không tắt app)"
      >
        <PowerIcon />
        {state === 'quitting' ? 'Đang tắt...' : state === 'checking' ? 'Đang kiểm tra...' : 'Tắt app'}
      </button>
      {error && <p className="mt-1 text-xs text-danger">{error}</p>}
    </div>
  )
}

function SidebarContent({ onNavigate }: { onNavigate?: () => void }): ReactNode {
  const { hasFeature, license } = useLicense()
  const items = NAV_ITEMS.filter((item) => item.features.length === 0 || item.features.some(hasFeature))
  if (license?.mode === 'enabled' && license.role === 'admin') {
    items.push({ to: '/admin', end: false, label: 'Quản lý user', icon: <AdminIcon />, features: [] })
  }
  return (
    <>
      <Link to="/" className="group flex items-center gap-3 px-2 py-1" onClick={onNavigate}>
        <span className="block h-[18px] w-[18px] rounded border border-accent shadow-[0_0_12px_color-mix(in_srgb,var(--color-accent)_45%,transparent)] transition-transform duration-200 group-hover:rotate-6" />
        <span className="font-heading text-base font-medium tracking-tight">OddlyLab Reup</span>
      </Link>
      <p className="px-2 text-xs text-neutral-500">Video tiếng Trung → tiếng Việt</p>
      <div className="mt-3">
        <QuitAppButton />
      </div>
      <nav className="mt-4 flex flex-col gap-1">
        {items.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} className={navClass} onClick={onNavigate}>
            {item.icon}
            {item.label}
          </NavLink>
        ))}
      </nav>
      <div className="mt-auto space-y-3 pt-4">
        <UserBox onNavigate={onNavigate} />
      </div>
    </>
  )
}

export default function AppSidebar() {
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <>
      {/* Desktop: sidebar cố định bên trái */}
      <aside className="hidden w-60 shrink-0 flex-col gap-1 border-r border-divider bg-surface px-3 py-5 md:flex">
        <SidebarContent />
      </aside>

      {/* Mobile: thanh trên cùng + nút hamburger mở overlay */}
      <div className="sticky top-0 z-20 flex items-center gap-3 border-b border-divider bg-bg/90 px-4 py-3 backdrop-blur md:hidden">
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          aria-label="Mở menu"
          onClick={() => setMobileOpen(true)}
        >
          <svg viewBox="0 0 20 20" fill="none" className="h-5 w-5" aria-hidden>
            <path d="M3 5h14M3 10h14M3 15h14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
        </button>
        <Link to="/" className="flex items-center gap-2">
          <span className="block h-[14px] w-[14px] rounded border border-accent" />
          <span className="font-heading text-sm font-medium tracking-tight">OddlyLab Reup</span>
        </Link>
      </div>

      {mobileOpen && (
        <div className="fixed inset-0 z-30 md:hidden">
          <div className="absolute inset-0 bg-black/55 backdrop-blur-[2px]" onClick={() => setMobileOpen(false)} />
          <aside className="relative flex h-full w-64 flex-col gap-1 bg-surface px-3 py-5 shadow-lg">
            <SidebarContent onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}
    </>
  )
}
