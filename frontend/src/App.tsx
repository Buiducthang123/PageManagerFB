import { Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import ContentPage from './pages/ContentPage';
import PageDetailPage from './pages/PageDetailPage';
import PagesListPage from './pages/PagesListPage';
import PlatformsPage from './pages/PlatformsPage';

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<PagesListPage />} />
        <Route path="/pages/:pageId" element={<PageDetailPage />} />
        <Route path="/platforms" element={<PlatformsPage />} />
        <Route path="/content" element={<ContentPage />} />
      </Route>
    </Routes>
  );
}
