import { Clock, FlaskConical } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { ConnectedPage } from '../types';

const TASK_STYLES: Record<string, string> = {
  MANAGE: 'bg-purple-500/10 text-purple-300',
  CREATE_CONTENT: 'bg-emerald-500/10 text-emerald-300',
  MODERATE: 'bg-amber-500/10 text-amber-300',
  ADVERTISE: 'bg-fuchsia-500/10 text-fuchsia-300',
  ANALYZE: 'bg-sky-500/10 text-sky-300',
  MESSAGING: 'bg-slate-500/10 text-slate-300',
};

const TASK_LABELS: Record<string, string> = {
  MANAGE: 'Quản lý',
  CREATE_CONTENT: 'Tạo nội dung',
  MODERATE: 'Kiểm duyệt',
  ADVERTISE: 'Quảng cáo',
  ANALYZE: 'Phân tích',
  MESSAGING: 'Nhắn tin',
};

function initialOf(name: string) {
  return name.trim().charAt(0).toUpperCase() || '?';
}

export function PageCard({ page }: { page: ConnectedPage }) {
  const [imgError, setImgError] = useState(false);
  const showImage = page.pictureUrl && !imgError;

  return (
    <div className="neon-border neon-border-hover flex flex-col gap-4 rounded-2xl bg-slate-900/50 p-5 transition">
      <div className="flex items-start gap-3">
        {showImage ? (
          <img
            src={page.pictureUrl!}
            alt={page.pageName}
            onError={() => setImgError(true)}
            className="h-11 w-11 shrink-0 rounded-full border border-purple-500/20 object-cover"
          />
        ) : (
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-purple-500 to-fuchsia-600 text-base font-semibold text-white">
            {initialOf(page.pageName)}
          </div>
        )}
        <div className="min-w-0">
          <p className="truncate font-medium text-white">{page.pageName}</p>
          <p className="truncate text-xs text-slate-500">{page.category ?? 'Chưa rõ danh mục'}</p>
        </div>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {page.tasks.map((task) => (
          <span
            key={task}
            className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${TASK_STYLES[task] ?? 'bg-slate-500/10 text-slate-300'}`}
          >
            {TASK_LABELS[task] ?? task}
          </span>
        ))}
      </div>

      <div className="flex items-center gap-1.5 text-xs text-slate-500">
        <Clock size={13} />
        Kết nối lúc {new Date(page.connectedAt).toLocaleString('vi-VN')}
      </div>

      <Link
        to={`/pages/${page.pageId}`}
        className="flex items-center justify-center gap-1.5 rounded-lg border border-purple-500/20 bg-purple-500/5 py-2 text-xs font-medium text-purple-300 transition hover:border-purple-400/40 hover:bg-purple-500/10"
      >
        <FlaskConical size={13} />
        Xem thống kê &amp; Test đăng bài
      </Link>
    </div>
  );
}
