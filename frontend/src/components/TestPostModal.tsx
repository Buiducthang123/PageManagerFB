import { CheckCircle2, Loader2, Send, XCircle } from 'lucide-react';
import { useState } from 'react';
import { createTestPost } from '../api';
import { Modal } from './Modal';

type Result = { type: 'success' | 'error'; text: string } | null;

export function TestPostModal({
  pageId,
  onClose,
  onPosted,
}: {
  pageId: string;
  onClose: () => void;
  onPosted: () => void;
}) {
  const [message, setMessage] = useState('');
  const [posting, setPosting] = useState(false);
  const [result, setResult] = useState<Result>(null);

  async function handleSubmit() {
    if (!message.trim()) return;
    setPosting(true);
    setResult(null);
    try {
      const res = await createTestPost(pageId, message.trim());
      setResult({ type: 'success', text: `Đã đăng thành công. Post ID: ${res.postId}` });
      setMessage('');
      onPosted();
    } catch (err) {
      setResult({ type: 'error', text: err instanceof Error ? err.message : 'Đăng bài thất bại' });
    } finally {
      setPosting(false);
    }
  }

  return (
    <Modal title="Test đăng bài (Post)" onClose={onClose}>
      <textarea
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        placeholder="Nhập nội dung bài test..."
        rows={4}
        autoFocus
        className="neon-border w-full resize-none rounded-lg bg-slate-950/60 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:outline-none"
      />
      <div className="mt-3 flex items-center justify-between">
        <button
          onClick={handleSubmit}
          disabled={posting || !message.trim()}
          className="flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-purple-600 to-fuchsia-600 px-3.5 py-1.5 text-sm font-medium text-white shadow-[0_0_16px_rgba(192,38,211,0.3)] transition hover:from-purple-500 hover:to-fuchsia-500 disabled:opacity-40"
        >
          {posting ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
          Đăng thử
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
