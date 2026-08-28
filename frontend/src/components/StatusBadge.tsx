import type { StageStatus } from '../lib/api'

const LABELS: Record<StageStatus, string> = {
  pending: 'chờ',
  running: 'đang chạy',
  done: '✓ xong',
  failed: '✕ lỗi',
}

function badgeStyle(status: StageStatus): string {
  if (status === 'done') return 'text-accent-300 bg-accent-900'
  if (status === 'running') return 'text-text bg-accent-800 [animation:noct-pulse_1.6s_ease-in-out_infinite]'
  if (status === 'failed') return 'text-danger-100 bg-danger-800'
  return 'text-neutral-600'
}

export default function StatusBadge({ status }: { status: StageStatus }) {
  return (
    <span className={`inline-block rounded-md px-2 py-0.5 font-mono text-[10px] tracking-wide whitespace-nowrap ${badgeStyle(status)}`}>
      {LABELS[status]}
    </span>
  )
}
