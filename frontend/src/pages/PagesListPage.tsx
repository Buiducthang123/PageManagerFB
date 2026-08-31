import { CheckCircle2, Inbox, Link2, RefreshCw, Store, Tags, XCircle } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { facebookLoginUrl, fetchConnectedPages } from '../api';
import { PageCard } from '../components/PageCard';
import { StatCard } from '../components/StatCard';
import type { ConnectedPage } from '../types';

type Notice = { type: 'success' | 'error'; message: string };

export default function PagesListPage() {
  const [pages, setPages] = useState<ConnectedPage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);

  async function loadPages() {
    setLoading(true);
    setError(null);
    try {
      setPages(await fetchConnectedPages());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Lỗi không xác định');
    } finally {
      setLoading(false);
    }
  }

  // Doc ket qua OAuth callback (?connected=N hoac ?fb_error=...) roi don URL.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const connected = params.get('connected');
    const fbError = params.get('fb_error');

    if (connected !== null) {
      setNotice({ type: 'success', message: `Đã kết nối thành công ${connected} Page.` });
    } else if (fbError) {
      setNotice({ type: 'error', message: fbError });
    }

    if (connected !== null || fbError) {
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  useEffect(() => {
    loadPages();
  }, []);

  const categoryCount = useMemo(() => new Set(pages.map((p) => p.category).filter(Boolean)).size, [pages]);
  const lastConnected = useMemo(() => {
    if (pages.length === 0) return '-';
    const latest = pages.reduce((max, p) => (p.connectedAt > max ? p.connectedAt : max), pages[0].connectedAt);
    return new Date(latest).toLocaleDateString('vi-VN');
  }, [pages]);

  return (
    <div className="mx-auto max-w-5xl px-8 py-10">
      <header className="mb-8 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-white">Trang Facebook</h1>
          <p className="mt-1 text-sm text-slate-400">
            Quản lý kết nối Page &middot; Giai đoạn 0 &mdash; Kiểm chứng kỹ thuật
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <button
            onClick={loadPages}
            disabled={loading}
            className="neon-border neon-border-hover flex items-center gap-1.5 rounded-lg bg-slate-900/60 px-3.5 py-2 text-sm font-medium text-slate-200 transition hover:bg-slate-900 disabled:opacity-50"
          >
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
            Làm mới
          </button>
          <a
            href={facebookLoginUrl}
            className="flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-purple-600 to-fuchsia-600 px-3.5 py-2 text-sm font-medium text-white shadow-[0_0_20px_rgba(192,38,211,0.35)] transition hover:from-purple-500 hover:to-fuchsia-500 hover:shadow-[0_0_28px_rgba(192,38,211,0.5)]"
          >
            <Link2 size={15} />
            Kết nối Facebook
          </a>
        </div>
      </header>

      <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="Tổng số Page" value={String(pages.length)} icon={Store} tone="indigo" />
        <StatCard label="Danh mục khác nhau" value={String(categoryCount)} icon={Tags} tone="emerald" />
        <StatCard label="Kết nối gần nhất" value={lastConnected} icon={Inbox} tone="amber" />
      </div>

      {notice && (
        <div
          className={[
            'mb-6 flex items-center gap-2 rounded-xl border px-4 py-3 text-sm',
            notice.type === 'success'
              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
              : 'border-red-500/30 bg-red-500/10 text-red-300',
          ].join(' ')}
        >
          {notice.type === 'success' ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
          {notice.message}
        </div>
      )}

      {error && (
        <div className="mb-6 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {loading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-48 animate-pulse rounded-2xl border border-slate-800 bg-slate-900/40" />
          ))}
        </div>
      ) : pages.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-purple-500/25 bg-slate-900/30 py-16 text-center">
          <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-slate-800">
            <Store size={20} className="text-slate-500" />
          </div>
          <p className="font-medium text-slate-200">Chưa có Page nào được kết nối</p>
          <p className="mt-1 max-w-sm text-sm text-slate-500">
            Bấm &ldquo;Kết nối Facebook&rdquo; để bắt đầu OAuth và lấy danh sách Page bạn quản lý.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {pages.map((page) => (
            <PageCard key={page.pageId} page={page} />
          ))}
        </div>
      )}
    </div>
  );
}
