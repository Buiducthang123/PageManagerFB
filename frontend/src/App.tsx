import { Route, Routes } from 'react-router-dom'
import AppSidebar from './components/AppSidebar'
import ConnectionLostOverlay from './components/ConnectionLostOverlay'
import LicenseGate from './components/LicenseGate'
import ProjectList from './pages/ProjectList'
import ProjectDetail from './pages/ProjectDetail'
import MergePage from './pages/MergePage'
import CleanVideoPage from './pages/CleanVideoPage'
import DownloadPage from './pages/DownloadPage'
import SettingsPage from './pages/SettingsPage'
import AutomatedPage from './pages/AutomatedPage'
import AutomatedDetailPage from './pages/AutomatedDetailPage'
import MonitorPage from './pages/MonitorPage'
import CleanupPage from './pages/CleanupPage'
import AccountsPage from './pages/AccountsPage'
import SystemCheckPage from './pages/SystemCheckPage'
import AdminPage from './pages/AdminPage'
import ApprovalsPage from './pages/ApprovalsPage'

export default function App() {
  return (
    <>
    <ConnectionLostOverlay />
    <LicenseGate>
    <div className="flex min-h-screen flex-col bg-bg text-text antialiased md:flex-row">
      <AppSidebar />
      <main className="min-w-0 flex-1 px-5 py-6 md:px-10 md:py-8">
        <Routes>
          <Route path="/" element={<ProjectList />} />
          <Route path="/projects/:projectId" element={<ProjectDetail />} />
          <Route path="/merge" element={<MergePage />} />
          <Route path="/clean-video" element={<CleanVideoPage />} />
          <Route path="/download" element={<DownloadPage />} />
          <Route path="/automated" element={<AutomatedPage />} />
          <Route path="/automated/:socialId" element={<AutomatedDetailPage />} />
          <Route path="/monitor" element={<MonitorPage />} />
          <Route path="/cleanup" element={<CleanupPage />} />
          <Route path="/accounts" element={<AccountsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/system-check" element={<SystemCheckPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="/approvals" element={<ApprovalsPage />} />
        </Routes>
      </main>
    </div>
    </LicenseGate>
    </>
  )
}
