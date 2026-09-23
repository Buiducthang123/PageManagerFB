import { useState, type ReactNode } from 'react'
import { Link, NavLink } from 'react-router-dom'

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

function DownloadIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4 shrink-0" aria-hidden>
      <path d="M10 3v9m0 0 3.5-3.5M10 12 6.5 8.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M3.5 14.5V16a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1v-1.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
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

const NAV_ITEMS = [
  { to: '/', end: true, label: 'Dự án', icon: <ProjectsIcon /> },
  { to: '/merge', end: false, label: 'Ghép video', icon: <MergeIcon /> },
  { to: '/download', end: false, label: 'Tải video', icon: <DownloadIcon /> },
  { to: '/settings', end: false, label: 'Cài đặt', icon: <SettingsIcon /> },
]

function SidebarContent({ onNavigate }: { onNavigate?: () => void }): ReactNode {
  return (
    <>
      <Link to="/" className="group flex items-center gap-3 px-2 py-1" onClick={onNavigate}>
        <span className="block h-[18px] w-[18px] rounded border border-accent shadow-[0_0_12px_color-mix(in_srgb,var(--color-accent)_45%,transparent)] transition-transform duration-200 group-hover:rotate-6" />
        <span className="font-heading text-base font-medium tracking-tight">OddlyLab Reup</span>
      </Link>
      <p className="px-2 text-xs text-neutral-500">Video zh → vi · Whisper + Gemini</p>
      <nav className="mt-4 flex flex-col gap-1">
        {NAV_ITEMS.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} className={navClass} onClick={onNavigate}>
            {item.icon}
            {item.label}
          </NavLink>
        ))}
      </nav>
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
