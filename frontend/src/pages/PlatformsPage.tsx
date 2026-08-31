import { CheckCircle2, ExternalLink, Loader2, LogIn, RefreshCw, XCircle } from 'lucide-react';
import { useEffect, useState } from 'react';
import { cancelPlatformLogin, fetchPlatforms, finishPlatformLogin, startPlatformLogin } from '../api';
import type { PlatformStatus } from '../types';

const PLATFORM_LABELS: Record<string, string> = {
  tiktok: 'TikTok',
};

export default function PlatformsPage() {
  const [platforms, setPlatforms] = useState<PlatformStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setPlatforms(await fetchPlatforms());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Lỗi không xác định');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleStart(platform: string) {
    setBusy(platform);
    setError(null);
    try {
      await startPlatformLogin(platform);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Không mở được cửa sổ đăng nhập');
    } finally {
      setBusy(null);
    }
  }

  async function handleFinish(platform: string) {
    setBusy(platform);
    setError(null);
    try {
      await finishPlatformLogin(platform);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Không lưu được session');
    } finally {
      setBusy(null);
    }
  }

  async function handleCancel(platform: string) {
    setBusy(platform);
    try {
      await cancelPlatformLogin(platform);
      await load();
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="px-8 py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight text-white">Nền tảng</h1>
        <p className="mt-1 text-sm text-slate-400">
          Đăng nhập &amp; lưu session cho các nguồn cần trình duyệt thật (TikTok) &middot; chỉ chạy được trên máy có
          màn hình
        </p>
      </header>

      {error && (
        <div className="mb-6 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <Loader2 size={16} className="animate-spin" /> Đang tải...
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {platforms.map((p) => (
            <div key={p.platform} className="neon-border rounded-2xl bg-slate-900/50 p-5">
              <div className="mb-4 flex items-center justify-between">
                <p className="font-medium text-white">{PLATFORM_LABELS[p.platform] ?? p.platform}</p>
                <span
                  className={[
                    'flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium',
                    p.loggedIn ? 'bg-emerald-500/10 text-emerald-300' : 'bg-red-500/10 text-red-300',
                  ].join(' ')}
                >
                  {p.loggedIn ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
                  {p.loggedIn ? 'Đã đăng nhập' : 'Chưa đăng nhập'}
                </span>
              </div>

              {p.loggedInAt && (
                <p className="mb-4 text-xs text-slate-500">
                  Lần đăng nhập gần nhất: {new Date(p.loggedInAt).toLocaleString('vi-VN')}
                </p>
              )}

              {p.loginWindowOpen ? (
                <div className="space-y-2">
                  <p className="flex items-center gap-1.5 text-xs text-amber-300">
                    <ExternalLink size={12} /> Cửa sổ trình duyệt đang mở — đăng nhập xong thì bấm &ldquo;Hoàn
                    tất&rdquo; bên dưới.
                  </p>
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleFinish(p.platform)}
                      disabled={busy === p.platform}
                      className="flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-purple-600 to-fuchsia-600 px-3.5 py-1.5 text-sm font-medium text-white shadow-[0_0_16px_rgba(192,38,211,0.3)] disabled:opacity-40"
                    >
                      {busy === p.platform ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
                      Hoàn tất
                    </button>
                    <button
                      onClick={() => handleCancel(p.platform)}
                      disabled={busy === p.platform}
                      className="rounded-lg border border-slate-700 px-3.5 py-1.5 text-sm text-slate-300 hover:bg-white/5"
                    >
                      Huỷ
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  onClick={() => handleStart(p.platform)}
                  disabled={busy === p.platform}
                  className="flex items-center gap-1.5 rounded-lg border border-purple-500/20 bg-purple-500/5 px-3.5 py-1.5 text-sm font-medium text-purple-300 transition hover:border-purple-400/40 hover:bg-purple-500/10 disabled:opacity-40"
                >
                  {busy === p.platform ? <Loader2 size={14} className="animate-spin" /> : <LogIn size={14} />}
                  {p.loggedIn ? 'Đăng nhập lại' : 'Đăng nhập'}
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      <button
        onClick={load}
        className="mt-6 flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-300"
      >
        <RefreshCw size={12} /> Làm mới trạng thái
      </button>
    </div>
  );
}
