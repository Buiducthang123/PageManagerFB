import type { EpisodeStageName, JobStatus, StageRecord, StageStatus } from '../lib/api'
import StatusBadge from './StatusBadge'

export interface SegmentItem {
  id: string
  index: number
  title: string | null
  stages: Record<EpisodeStageName, StageRecord>
  exportStatus: StageStatus
  durationSec: number | null
}

// 5 bước hiện trên mini-pipeline của mỗi đoạn (bỏ "assemble"/CapCut — dự án
// split xuất trực tiếp, không qua CapCut).
const MINI_STEPS: { key: EpisodeStageName | 'export'; label: string }[] = [
  { key: 'ingest', label: 'Tải' },
  { key: 'transcribe', label: 'Nhận diện' },
  { key: 'translate', label: 'Dịch' },
  { key: 'tts', label: 'Giọng' },
  { key: 'export', label: 'Xuất' },
]

function barClass(status: StageStatus): string {
  if (status === 'done') return 'bg-accent'
  if (status === 'running') return 'bg-accent-400 [animation:noct-pulse_1.6s_ease-in-out_infinite]'
  if (status === 'failed') return 'bg-danger'
  return 'bg-neutral-800'
}

function fmtDuration(sec: number | null): string {
  if (!sec || sec <= 0) return ''
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

export default function SegmentRail({
  items,
  selectedId,
  onSelect,
  selectedForMerge,
  onToggleMerge,
  onToggleAll,
  mergeBusy,
  mergeStatus,
  mergeResult,
  onMerge,
  onCancelMerge,
  onRevealMerge,
  onRevealSegment,
}: {
  items: SegmentItem[]
  selectedId: string | null
  onSelect: (id: string) => void
  selectedForMerge: string[]
  onToggleMerge: (id: string) => void
  onToggleAll: () => void
  mergeBusy: boolean
  mergeStatus: JobStatus | null | undefined
  mergeResult: StageRecord | undefined
  onMerge: () => void
  onCancelMerge: () => void
  onRevealMerge: () => void
  onRevealSegment: (id: string) => void
}) {
  const exportedIds = items.filter((it) => it.exportStatus === 'done').map((it) => it.id)
  const selectedSet = new Set(selectedForMerge)
  const selectedExported = exportedIds.filter((id) => selectedSet.has(id))
  const allExportedSelected = exportedIds.length > 0 && selectedExported.length === exportedIds.length
  const canMerge = selectedExported.length >= 2 && !mergeBusy

  return (
    <aside className="sticky top-6 hidden w-80 shrink-0 self-start lg:block">
      <div className="mb-2 flex items-center gap-2 px-1">
        <p className="text-xs font-medium tracking-wide text-neutral-500 uppercase">Các đoạn</p>
        {exportedIds.length >= 2 && (
          <label className="ml-auto flex items-center gap-1.5 text-xs text-neutral-400">
            <input type="checkbox" checked={allExportedSelected} onChange={onToggleAll} /> Chọn tất cả
          </label>
        )}
      </div>

      {/* Thanh gộp đoạn — chỉ hiện khi đã chọn ≥2 đoạn đã xuất, hoặc đang/đã gộp */}
      {(selectedExported.length >= 2 || mergeBusy || mergeResult?.status === 'done') && (
        <div className="mb-3 rounded-md border border-accent-800 bg-accent-900 p-3">
          {mergeBusy ? (
            <>
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium text-accent-200">Đang gộp đoạn...</span>
                <button type="button" className="btn btn-ghost btn-sm text-danger" onClick={onCancelMerge}>
                  Dừng
                </button>
              </div>
              {mergeStatus?.current_label && <p className="mt-1 text-xs text-neutral-400">{mergeStatus.current_label}</p>}
            </>
          ) : (
            <>
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium text-accent-200">Đã chọn {selectedExported.length} đoạn</span>
                <button type="button" className="btn btn-primary btn-sm" disabled={!canMerge} onClick={onMerge}>
                  Gộp thành 1 video →
                </button>
              </div>
              <p className="mt-1 text-xs text-neutral-400">Gộp nối theo thứ tự đoạn ở dưới.</p>
            </>
          )}
          {mergeStatus?.status === 'failed' && mergeStatus.error && (
            <p className="mt-2 text-xs text-danger">{mergeStatus.error}</p>
          )}
          {!mergeBusy && mergeResult?.status === 'done' && (
            <div className="mt-2 flex items-center gap-2 border-t border-accent-800 pt-2 text-xs">
              <span className="text-accent-300">✓ Đã gộp: merged.mp4</span>
              <button type="button" className="btn btn-ghost btn-sm" onClick={onRevealMerge}>
                Mở thư mục
              </button>
            </div>
          )}
          {!mergeBusy && mergeResult?.status === 'failed' && mergeResult.error && (
            <p className="mt-2 text-xs text-danger">{mergeResult.error}</p>
          )}
        </div>
      )}

      <nav className="flex flex-col gap-2.5">
        {items.map((it) => {
          const exported = it.exportStatus === 'done'
          const dur = fmtDuration(it.durationSec)
          return (
            <div
              key={it.id}
              role="button"
              tabIndex={0}
              onClick={() => onSelect(it.id)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onSelect(it.id)
                }
              }}
              className={[
                'cursor-pointer rounded-md border bg-bg p-3 transition-colors',
                it.id === selectedId
                  ? 'border-accent shadow-[inset_3px_0_0_var(--color-accent)]'
                  : 'border-divider hover:border-neutral-600',
              ].join(' ')}
            >
              <div className="flex items-center gap-2.5">
                <input
                  type="checkbox"
                  checked={selectedSet.has(it.id)}
                  disabled={!exported}
                  title={exported ? 'Chọn để gộp' : 'Xuất video xong mới gộp được'}
                  onClick={(e) => e.stopPropagation()}
                  onChange={() => onToggleMerge(it.id)}
                />
                <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-accent-900 text-xs font-semibold text-accent-200">
                  {it.index + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">Đoạn {it.index + 1}</div>
                  {it.title && <div className="truncate text-xs text-neutral-500">{it.title}</div>}
                </div>
                <StatusBadge status={exported ? 'done' : it.exportStatus === 'failed' ? 'failed' : it.exportStatus === 'running' ? 'running' : overallOf(it)} />
              </div>

              <div className="mt-2.5 flex gap-1">
                {MINI_STEPS.map((step) => {
                  const st = step.key === 'export' ? it.exportStatus : it.stages[step.key]?.status ?? 'pending'
                  return (
                    <div key={step.key} className="flex flex-1 flex-col items-center gap-1">
                      <span className={`h-1 w-full rounded-full ${barClass(st)}`} />
                      <span className={`text-[9.5px] ${st === 'done' || st === 'running' ? 'text-neutral-400' : 'text-neutral-600'}`}>
                        {step.label}
                      </span>
                    </div>
                  )
                })}
              </div>

              {exported && (
                <div className="mt-2.5 flex items-center gap-2 text-xs text-neutral-400">
                  <span className="text-accent-300">● final.mp4{dur ? ` · ${dur}` : ''}</span>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm ml-auto"
                    onClick={(e) => {
                      e.stopPropagation()
                      onRevealSegment(it.id)
                    }}
                  >
                    Mở thư mục
                  </button>
                </div>
              )}
            </div>
          )
        })}
      </nav>
    </aside>
  )
}

// Trạng thái tổng hợp các bước pipeline của đoạn (chưa tính xuất) — dùng cho
// badge khi đoạn chưa xuất xong.
function overallOf(it: SegmentItem): StageStatus {
  const statuses = Object.values(it.stages).map((s) => s.status)
  if (statuses.some((s) => s === 'failed')) return 'failed'
  if (statuses.some((s) => s === 'running')) return 'running'
  if (statuses.every((s) => s === 'done')) return 'done'
  return 'pending'
}
