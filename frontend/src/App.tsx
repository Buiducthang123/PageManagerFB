import { Link, NavLink, Route, Routes } from 'react-router-dom'
import ProjectList from './pages/ProjectList'
import ProjectDetail from './pages/ProjectDetail'
import SettingsPage from './pages/SettingsPage'

function navClass({ isActive }: { isActive: boolean }): string {
  return isActive ? 'btn btn-sm bg-accent-900/70 text-accent-200' : 'btn btn-ghost btn-sm'
}

export default function App() {
  return (
    <div className="min-h-screen bg-bg text-text antialiased">
      <header className="sticky top-0 z-10 border-b border-divider bg-bg/90 backdrop-blur">
        <div className="flex flex-wrap items-center gap-3 px-8 py-4">
          <Link to="/" className="group flex items-center gap-3">
            <span className="block h-[18px] w-[18px] rounded border border-accent shadow-[0_0_12px_color-mix(in_srgb,var(--color-accent)_45%,transparent)] transition-transform duration-200 group-hover:rotate-6" />
            <span className="font-heading text-base font-medium tracking-tight">OddlyLab Reup</span>
          </Link>
          <span className="text-neutral-600">/</span>
          <span className="hidden text-sm text-neutral-400 sm:inline">Video zh → vi · Whisper + Gemini</span>
          <nav className="ml-auto flex gap-2">
            <NavLink to="/" end className={navClass}>
              Dự án
            </NavLink>
            <NavLink to="/settings" className={navClass}>
              Cài đặt
            </NavLink>
          </nav>
        </div>
      </header>
      <main className="px-8 py-8">
        <Routes>
          <Route path="/" element={<ProjectList />} />
          <Route path="/projects/:projectId" element={<ProjectDetail />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  )
}
