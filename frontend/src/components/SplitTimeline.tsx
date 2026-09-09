import { useRef } from 'react'

const MIN_GAP_S = 1 // khoảng cách tối thiểu giữa 2 điểm cắt (hoặc mép), tránh đoạn dài 0s

function formatTime(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
  return `${m}:${String(sec).padStart(2, '0')}`
}

export default function SplitTimeline({
  duration,
  points,
  onChange,
}: {
  duration: number
  points: number[] // N-1 mốc cắt, đã sort tăng dần
  onChange: (points: number[]) => void
}) {
  const trackRef = useRef<HTMLDivElement>(null)
  const draggingIndex = useRef<number | null>(null)

  const boundsFor = (i: number): [number, number] => {
    const lo = i === 0 ? 0 : points[i - 1]
    const hi = i === points.length - 1 ? duration : points[i + 1]
    return [lo + MIN_GAP_S, hi - MIN_GAP_S]
  }

  const commit = (i: number, raw: number) => {
    const [lo, hi] = boundsFor(i)
    const clamped = Math.min(Math.max(raw, lo), Math.max(lo, hi))
    const next = [...points]
    next[i] = clamped
    onChange(next)
  }

  const pointerToSeconds = (clientX: number): number => {
    const rect = trackRef.current?.getBoundingClientRect()
    if (!rect || rect.width === 0) return 0
    const frac = Math.min(Math.max((clientX - rect.left) / rect.width, 0), 1)
    return frac * duration
  }

  const onPointerDown = (i: number) => (e: React.PointerEvent) => {
    e.preventDefault()
    draggingIndex.current = i
    ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
  }

  const onPointerMove = (e: React.PointerEvent) => {
    const i = draggingIndex.current
    if (i === null) return
    commit(i, pointerToSeconds(e.clientX))
  }

  const onPointerUp = () => {
    draggingIndex.current = null
  }

  if (duration <= 0 || points.length === 0) return null

  return (
    <div className="select-none py-6">
      <div className="relative mb-2 h-4">
        {points.map((p, i) => (
          <span
            key={i}
            className="absolute -translate-x-1/2 whitespace-nowrap font-mono text-xs text-accent-300"
            style={{ left: `${(p / duration) * 100}%` }}
          >
            {formatTime(p)}
          </span>
        ))}
      </div>
      <div
        ref={trackRef}
        className="relative h-2 w-full rounded-full bg-neutral-800"
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      >
        {points.map((p, i) => (
          <div
            key={i}
            onPointerDown={onPointerDown(i)}
            className="absolute top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 cursor-ew-resize rounded-full border-2 border-accent-300 bg-accent-600 shadow"
            style={{ left: `${(p / duration) * 100}%` }}
            title={formatTime(p)}
          />
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-neutral-500">
        <span>0:00</span>
        <span>{formatTime(duration)}</span>
      </div>
    </div>
  )
}
