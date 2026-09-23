import { Route, Routes } from 'react-router-dom'
import AppSidebar from './components/AppSidebar'
import ProjectList from './pages/ProjectList'
import ProjectDetail from './pages/ProjectDetail'
import MergePage from './pages/MergePage'
import DownloadPage from './pages/DownloadPage'
import SettingsPage from './pages/SettingsPage'

export default function App() {
  return (
    <div className="flex min-h-screen flex-col bg-bg text-text antialiased md:flex-row">
      <AppSidebar />
      <main className="min-w-0 flex-1 px-5 py-6 md:px-10 md:py-8">
        <Routes>
          <Route path="/" element={<ProjectList />} />
          <Route path="/projects/:projectId" element={<ProjectDetail />} />
          <Route path="/merge" element={<MergePage />} />
          <Route path="/download" element={<DownloadPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  )
}
