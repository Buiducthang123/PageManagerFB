import { CheckCircle2, ExternalLink, Loader2, Save, Search, Sparkles } from 'lucide-react';
import { useState } from 'react';
import { searchAndSaveContent, searchContent } from '../api';
import type { DiscoveredContent } from '../types';

const SOURCES = [
  { value: 'youtube', label: 'YouTube Shorts' },
  { value: 'tiktok', label: 'TikTok' },
];

export default function ContentPage() {
  const [source, setSource] = useState('youtube');
  const [keyword, setKeyword] = useState('');
  const [results, setResults] = useState<DiscoveredContent[]>([]);
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  async function handleSearch() {
    if (!keyword.trim()) return;
    setSearching(true);
    setError(null);
    setSaveMsg(null);
    try {
      setResults(await searchContent(source, keyword.trim(), 12));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Tìm kiếm thất bại');
    } finally {
      setSearching(false);
    }
  }

  async function handleSaveAll() {
    if (!keyword.trim()) return;
    setSaving(true);
    setSaveMsg(null);
    try {
      const res = await searchAndSaveContent(source, keyword.trim(), 12);
      setSaveMsg(`Tìm thấy ${res.found}, thêm mới ${res.added}, đã có từ trước ${res.duplicates}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Lưu thất bại');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="px-8 py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight text-white">Nội dung</h1>
        <p className="mt-1 text-sm text-slate-400">
          Test tìm kiếm video theo từ khoá &middot; Content Engine (Phase 4)
        </p>
      </header>

      <div className="neon-border mb-6 rounded-2xl bg-slate-900/50 p-5">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex rounded-lg border border-slate-800 p-1">
            {SOURCES.map((s) => (
              <button
                key={s.value}
                onClick={() => setSource(s.value)}
                className={[
                  'rounded-md px-3 py-1.5 text-sm font-medium transition',
                  source === s.value ? 'bg-purple-500/20 text-purple-300' : 'text-slate-400 hover:text-slate-200',
                ].join(' ')}
              >
                {s.label}
              </button>
            ))}
          </div>

          <input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            placeholder="Nhập từ khoá..."
            className="min-w-[220px] flex-1 rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-purple-500/40 focus:outline-none"
          />

          <button
            onClick={handleSearch}
            disabled={searching || !keyword.trim()}
            className="flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-purple-600 to-fuchsia-600 px-4 py-2 text-sm font-medium text-white shadow-[0_0_16px_rgba(192,38,211,0.3)] disabled:opacity-40"
          >
            {searching ? <Loader2 size={15} className="animate-spin" /> : <Search size={15} />}
            Tìm kiếm
          </button>

          <button
            onClick={handleSaveAll}
            disabled={saving || !keyword.trim()}
            className="neon-border neon-border-hover flex items-center gap-1.5 rounded-lg bg-slate-900/60 px-4 py-2 text-sm font-medium text-slate-200 disabled:opacity-40"
          >
            {saving ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}
            Lưu vào Content Pool
          </button>
        </div>

        {source === 'tiktok' && (
          <p className="mt-3 flex items-center gap-1.5 text-xs text-amber-300">
            <Sparkles size={12} /> Cần đăng nhập TikTok ở trang &ldquo;Nền tảng&rdquo; trước khi search.
          </p>
        )}
      </div>

      {error && (
        <div className="mb-6 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}
      {saveMsg && (
        <div className="mb-6 flex items-center gap-2 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-300">
          <CheckCircle2 size={16} /> {saveMsg}
        </div>
      )}

      {searching ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="aspect-[9/16] animate-pulse rounded-xl border border-slate-800 bg-slate-900/40" />
          ))}
        </div>
      ) : results.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-700 bg-slate-900/30 py-16 text-center text-sm text-slate-500">
          Nhập từ khoá và bấm &ldquo;Tìm kiếm&rdquo; để xem kết quả.
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {results.map((r) => (
            <a
              key={`${r.source}:${r.sourceId}`}
              href={r.sourceUrl}
              target="_blank"
              rel="noreferrer"
              className="neon-border neon-border-hover group relative flex aspect-[9/16] flex-col justify-end overflow-hidden rounded-xl bg-slate-900/50"
            >
              {r.thumbnail && (
                <img src={r.thumbnail} alt={r.title} className="absolute inset-0 h-full w-full object-cover" />
              )}
              <div className="relative bg-gradient-to-t from-black/90 via-black/40 to-transparent p-2.5 pt-8">
                <p className="line-clamp-2 text-[11px] font-medium text-white">{r.title}</p>
                {typeof r.metadata?.viewsText === 'string' && (
                  <p className="mt-0.5 text-[10px] text-slate-300">{r.metadata.viewsText}</p>
                )}
              </div>
              <span
                className={[
                  'absolute right-1.5 top-1.5 rounded-full px-1.5 py-0.5 text-[10px] font-medium',
                  r.isNew ? 'bg-emerald-500/80 text-white' : 'bg-slate-700/80 text-slate-300',
                ].join(' ')}
              >
                {r.isNew ? 'Mới' : 'Đã có'}
              </span>
              <ExternalLink size={12} className="absolute left-1.5 top-1.5 text-white/70" />
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
