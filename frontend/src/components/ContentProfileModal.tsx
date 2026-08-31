import { CheckCircle2, Loader2, Plus, Save, Trash2, X, XCircle } from 'lucide-react';
import { useEffect, useState } from 'react';
import { fetchContentProfile, saveContentProfile } from '../api';
import { Modal } from './Modal';

type Result = { type: 'success' | 'error'; text: string } | null;

const PRIORITY_LABELS = ['Ưu tiên 1', 'Ưu tiên 2', 'Ưu tiên 3', 'Ưu tiên 4', 'Ưu tiên 5'];

export function ContentProfileModal({ pageId, onClose }: { pageId: string; onClose: () => void }) {
  const [groups, setGroups] = useState<string[][]>([]);
  const [drafts, setDrafts] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<Result>(null);

  useEffect(() => {
    fetchContentProfile(pageId)
      .then((profile) => {
        const g = profile.keywordGroups.length > 0 ? profile.keywordGroups : [[]];
        setGroups(g);
        setDrafts(g.map(() => ''));
      })
      .catch((err) => setResult({ type: 'error', text: err instanceof Error ? err.message : 'Lỗi tải dữ liệu' }))
      .finally(() => setLoading(false));
  }, [pageId]);

  function addGroup() {
    setGroups((g) => [...g, []]);
    setDrafts((d) => [...d, '']);
  }

  function removeGroup(index: number) {
    setGroups((g) => g.filter((_, i) => i !== index));
    setDrafts((d) => d.filter((_, i) => i !== index));
  }

  function addKeyword(groupIndex: number) {
    const value = drafts[groupIndex]?.trim();
    if (!value) return;
    setGroups((g) => g.map((group, i) => (i === groupIndex ? [...group, value] : group)));
    setDrafts((d) => d.map((v, i) => (i === groupIndex ? '' : v)));
  }

  function removeKeyword(groupIndex: number, keywordIndex: number) {
    setGroups((g) => g.map((group, i) => (i === groupIndex ? group.filter((_, k) => k !== keywordIndex) : group)));
  }

  async function handleSave() {
    setSaving(true);
    setResult(null);
    try {
      const cleaned = groups.filter((g) => g.length > 0);
      const saved = await saveContentProfile(pageId, { keywordGroups: cleaned });
      setGroups(saved.keywordGroups.length > 0 ? saved.keywordGroups : [[]]);
      setResult({ type: 'success', text: 'Đã lưu từ khoá.' });
    } catch (err) {
      setResult({ type: 'error', text: err instanceof Error ? err.message : 'Lưu thất bại' });
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal title="Từ khoá tìm nội dung" onClose={onClose}>
      <p className="mb-4 text-xs text-slate-500">
        Content Engine sẽ tìm theo nhóm từ khoá Ưu tiên 1 trước — nếu đủ nội dung thì dừng, thiếu mới chuyển sang Ưu
        tiên 2, v.v. (đúng logic fallback đã thống nhất).
      </p>

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <Loader2 size={14} className="animate-spin" /> Đang tải...
        </div>
      ) : (
        <div className="max-h-[50vh] space-y-4 overflow-y-auto pr-1">
          {groups.map((group, gi) => (
            <div key={gi} className="neon-border rounded-lg bg-slate-900/40 p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-xs font-medium text-purple-300">
                  {PRIORITY_LABELS[gi] ?? `Ưu tiên ${gi + 1}`}
                </span>
                {groups.length > 1 && (
                  <button onClick={() => removeGroup(gi)} className="text-slate-500 hover:text-red-400">
                    <Trash2 size={13} />
                  </button>
                )}
              </div>

              <div className="mb-2 flex flex-wrap gap-1.5">
                {group.map((kw, ki) => (
                  <span
                    key={ki}
                    className="flex items-center gap-1 rounded-full bg-purple-500/10 px-2 py-0.5 text-xs text-purple-300"
                  >
                    {kw}
                    <button onClick={() => removeKeyword(gi, ki)} className="hover:text-red-300">
                      <X size={11} />
                    </button>
                  </span>
                ))}
                {group.length === 0 && <span className="text-xs text-slate-600">Chưa có từ khoá nào.</span>}
              </div>

              <div className="flex gap-1.5">
                <input
                  value={drafts[gi] ?? ''}
                  onChange={(e) => setDrafts((d) => d.map((v, i) => (i === gi ? e.target.value : v)))}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      addKeyword(gi);
                    }
                  }}
                  placeholder="Nhập từ khoá rồi Enter..."
                  className="flex-1 rounded-lg border border-slate-800 bg-slate-950/60 px-2.5 py-1.5 text-xs text-slate-200 placeholder:text-slate-600 focus:border-purple-500/40 focus:outline-none"
                />
                <button
                  onClick={() => addKeyword(gi)}
                  className="rounded-lg border border-purple-500/20 bg-purple-500/5 px-2.5 text-purple-300 hover:bg-purple-500/10"
                >
                  <Plus size={13} />
                </button>
              </div>
            </div>
          ))}

          <button
            onClick={addGroup}
            className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-slate-700 py-2 text-xs text-slate-400 hover:border-purple-500/30 hover:text-purple-300"
          >
            <Plus size={13} /> Thêm nhóm ưu tiên
          </button>
        </div>
      )}

      <div className="mt-4 flex items-center justify-between">
        <button
          onClick={handleSave}
          disabled={saving || loading}
          className="flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-purple-600 to-fuchsia-600 px-3.5 py-1.5 text-sm font-medium text-white shadow-[0_0_16px_rgba(192,38,211,0.3)] transition hover:from-purple-500 hover:to-fuchsia-500 disabled:opacity-40"
        >
          {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
          Lưu
        </button>
      </div>

      {result && (
        <div
          className={[
            'mt-3 flex items-start gap-1.5 rounded-lg px-3 py-2 text-xs',
            result.type === 'success' ? 'bg-emerald-500/10 text-emerald-300' : 'bg-red-500/10 text-red-300',
          ].join(' ')}
        >
          {result.type === 'success' ? <CheckCircle2 size={14} className="mt-0.5 shrink-0" /> : <XCircle size={14} className="mt-0.5 shrink-0" />}
          <span className="break-words">{result.text}</span>
        </div>
      )}
    </Modal>
  );
}
