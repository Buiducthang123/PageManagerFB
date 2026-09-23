import { useState, type ReactNode } from 'react'
import type { StageStatus } from '../lib/api'
import StatusBadge from './StatusBadge'

export default function CollapsibleStageSection({
  id,
  label,
  status,
  meta,
  defaultExpanded,
  headerExtra,
  children,
}: {
  id: string
  label: string
  status: StageStatus
  meta?: string | null
  defaultExpanded: boolean
  headerExtra?: ReactNode
  children: ReactNode
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)

  return (
    <section
      id={id}
      className={expanded ? 'card p-5' : 'card items-center gap-3 p-3.5'}
      style={expanded ? undefined : { flexDirection: 'row' }}
    >
      {expanded ? (
        <>
          <div className="mb-1 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2.5">
              <h2 className="text-lg">{label}</h2>
              {meta && <span className="mono text-xs text-neutral-500">{meta}</span>}
            </div>
            <div className="flex items-center gap-2">
              {headerExtra}
              <StatusBadge status={status} />
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setExpanded(false)}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                  <path d="M18 15l-6-6-6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
            </div>
          </div>
          {children}
        </>
      ) : (
        <>
          {status === 'done' ? (
            <div className="flex h-6.5 w-6.5 shrink-0 items-center justify-center rounded-full bg-accent-900">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none">
                <path d="M4 12.5L9.5 18L20 6" stroke="#d2cefd" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
          ) : (
            <div className="h-6.5 w-6.5 shrink-0 rounded-full border-1.5 border-neutral-700" />
          )}
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium">{label}</div>
            {meta && <div className="mono truncate text-xs text-neutral-500">{meta}</div>}
          </div>
          <StatusBadge status={status} />
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setExpanded(true)}>
            {status === 'done' ? 'Chạy lại' : 'Mở'}
          </button>
        </>
      )}
    </section>
  )
}
