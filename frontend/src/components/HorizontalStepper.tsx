import type { StageStatus } from '../lib/api'

export interface StepperItem {
  id: string
  label: string
  status: StageStatus
}

export default function HorizontalStepper({ steps }: { steps: StepperItem[] }) {
  const doneCount = steps.filter((s) => s.status === 'done').length
  // Đường nối accent lấp đầy KHOẢNG GIỮA các bước (steps.length - 1 khoảng) —
  // nếu tất cả bước đều "done" thì doneCount = steps.length, chia cho số
  // khoảng (ít hơn 1) sẽ ra >100% và tràn ra ngoài khung. Giới hạn lại đúng
  // số khoảng tối đa có thể lấp.
  const filledGaps = Math.min(doneCount, steps.length - 1)
  const progressPct = steps.length > 1 ? (filledGaps / (steps.length - 1)) * 100 : 0

  return (
    <div className="card px-8 py-5.5">
      <div className="relative flex justify-between">
        <div className="absolute top-4 right-5.5 left-5.5 h-0.5 bg-neutral-800" />
        <div className="absolute top-4 left-5.5 h-0.5 bg-accent transition-all" style={{ width: `calc(${progressPct}% - ${progressPct > 0 ? '11px' : '0px'})` }} />
        {steps.map((s) => (
          <div key={s.id} className="relative z-1 flex flex-col items-center gap-1.5">
            {s.status === 'done' && (
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-accent-900">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                  <path d="M4 12.5L9.5 18L20 6" stroke="#d2cefd" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
            )}
            {s.status === 'running' && (
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-accent shadow-[0_0_0_5px_color-mix(in_srgb,var(--color-accent)_20%,transparent)] [animation:noct-pulse_1.6s_ease-in-out_infinite]">
                <div className="h-2 w-2 rounded-full bg-[#14121f]" />
              </div>
            )}
            {s.status === 'failed' && (
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-danger-800">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none">
                  <path d="M6 6l12 12M18 6L6 18" stroke="#fdf1f0" strokeWidth="2.5" strokeLinecap="round" />
                </svg>
              </div>
            )}
            {s.status === 'pending' && <div className="h-8 w-8 rounded-full border-1.5 border-neutral-700 bg-surface" />}
            <div
              className={[
                'text-xs font-medium',
                s.status === 'done' ? 'text-accent-300' : s.status === 'running' ? 'font-semibold text-text' : 'text-neutral-500',
              ].join(' ')}
            >
              {s.label}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
