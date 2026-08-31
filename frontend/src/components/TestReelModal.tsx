import { CheckCircle2, ExternalLink, Loader2, RefreshCw, Video, XCircle } from 'lucide-react';
import { useState } from 'react';
import { fetchReelStatus, submitTestReel } from '../api';
import type { ReelStatus } from '../types';
import { Modal } from './Modal';
import { toAbsoluteFacebookUrl } from '../utils';

type Result = { type: 'success' | 'error'; text: string } | null;

export function TestReelModal({
  pageId,
  onClose,
  onPublished,
}: {
  pageId: string;
  onClose: () => void;
  onPublished: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [description, setDescription] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<Result>(null);
  const [videoId, setVideoId] = useState<string | null>(null);
  const [status, setStatus] = useState<ReelStatus | null>(null);
  const [checking, setChecking] = useState(false);

  async function handleSubmit() {
    if (!file) return;
    setSubmitting(true);
    setResult(null);
    setVideoId(null);
    setStatus(null);
    try {
      const res = await submitTestReel(pageId, file, description.trim());
      setResult({ type: 'success', text: res.message });
      setVideoId(res.videoId);
    } catch (err) {
      setResult({ type: 'error', text: err instanceof Error ? err.message : 'Upload Reel thất bại' });
    } finally {
      setSubmitting(false);
    }
  }

  async function handleCheckStatus() {
    if (!videoId) return;
    setChecking(true);
    try {
      const res = await fetchReelStatus(pageId, videoId);
      setStatus(res);
      const s = res.status as { video_status?: string } | null;
      if (s?.video_status === 'ready') onPublished();
    } catch (err) {
      setResult({ type: 'error', text: err instanceof Error ? err.message : 'Không kiểm tra được trạng thái' });
    } finally {
      setChecking(false);
    }
  }

  const videoStatus = (status?.status as { video_status?: string } | undefined)?.video_status;

  return (
    <Modal title="Test đăng Reel (video)" onClose={onClose}>
      <input
        type="file"
        accept="video/mp4,video/quicktime,video/*"
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        className="block w-full text-xs text-slate-400 file:mr-3 file:rounded-lg file:border-0 file:bg-purple-500/10 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-purple-300 hover:file:bg-purple-500/20"
      />
      <textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="Mô tả cho Reel (tuỳ chọn)..."
        rows={2}
        className="neon-border mt-3 w-full resize-none rounded-lg bg-slate-950/60 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:outline-none"
      />
      <div className="mt-3 flex items-center gap-2">
        <button
          onClick={handleSubmit}
          disabled={submitting || !file}
          className="flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-purple-600 to-fuchsia-600 px-3.5 py-1.5 text-sm font-medium text-white shadow-[0_0_16px_rgba(192,38,211,0.3)] transition hover:from-purple-500 hover:to-fuchsia-500 disabled:opacity-40"
        >
          {submitting ? <Loader2 size={14} className="animate-spin" /> : <Video size={14} />}
          {submitting ? 'Đang upload...' : 'Đăng Reel'}
        </button>
        {videoId && (
          <button
            onClick={handleCheckStatus}
            disabled={checking}
            className="neon-border neon-border-hover flex items-center gap-1.5 rounded-lg bg-slate-900/50 px-3 py-1.5 text-xs text-slate-300"
          >
            {checking ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            Kiểm tra trạng thái
          </button>
        )}
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

      {status && (
        <div className="mt-3 space-y-2">
          <p className="text-xs text-slate-500">
            Trạng thái: <span className="text-slate-300">{videoStatus ?? 'không rõ'}</span>
          </p>
          {status.thumbnailUrl ? (
            <div className="neon-border overflow-hidden rounded-lg">
              <img src={status.thumbnailUrl} alt="Reel thumbnail" className="w-full object-cover" />
            </div>
          ) : (
            <p className="text-xs text-slate-600">Facebook đang xử lý, chưa có thumbnail. Bấm kiểm tra lại sau vài giây.</p>
          )}
          {status.permalinkUrl && (
            <a
              href={toAbsoluteFacebookUrl(status.permalinkUrl)}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1 text-xs text-purple-400 hover:text-purple-300"
            >
              Xem trên Facebook <ExternalLink size={11} />
            </a>
          )}
        </div>
      )}
    </Modal>
  );
}
