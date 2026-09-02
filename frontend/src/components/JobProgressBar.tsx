import type { JobStatus } from '../lib/api'

function formatDuration(sec: number): string {
  if (sec < 60) return `${Math.round(sec)}s`
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return `${m}p${s}s`
}

export default function JobProgressBar({
  job,
  formatCount,
}: {
  job: JobStatus | undefined
  formatCount?: (n: number) => string
}) {
  if (!job) return null
  if (job.orphaned) {
    return (
      <div className="mb-3 rounded-xl border border-neutral-600 bg-surface p-3 text-sm text-neutral-300">
        Phiên trước bị gián đoạn (server restart) — bấm chạy lại.
      </div>
    )
  }
  if (!job.registered || job.status !== 'running') return null

  const pct = job.total > 0 ? Math.round((job.done_count / job.total) * 100) : 0
  const elapsed = job.started_at ? Date.now() / 1000 - job.started_at : 0
  const fmt = formatCount ?? ((n: number) => `${n}`)

  return (
    <div className="mb-3 rounded-xl bg-accent-900/40 p-4" style={{ boxShadow: 'var(--shadow-sm)' }}>
      <div className="mb-2 flex items-center justify-between gap-3 text-sm">
        <span className="min-w-0 truncate font-medium text-accent-300">
          {job.current_label ? `Đang xử lý «${job.current_label}»` : 'Đang chạy...'}
        </span>
        <span className="font-mono text-xs text-accent-400">
          {pct}% · {fmt(job.done_count)}/{fmt(job.total)}
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-neutral-900">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{
            width: `${Math.min(100, pct)}%`,
            background: 'linear-gradient(90deg, var(--color-accent-600), var(--color-accent-300))',
          }}
        />
      </div>
      <p className="mt-2 font-mono text-xs text-accent-400">Đã chạy {formatDuration(elapsed)}</p>
    </div>
  )
}
