import { useRef, useState } from 'react'

function fmt(s: number): string {
  if (!Number.isFinite(s) || s < 0) return '0:00'
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${sec.toString().padStart(2, '0')}`
}

/** Trình phát audio gọn tự vẽ (nút ▶/⏸ bằng SVG) — thay cho <audio controls>
 * native vì ở bề rộng hẹp Chrome thu gọn control và GIẤU luôn nút phát. */
export default function MiniAudioPlayer({ src }: { src: string }) {
  const ref = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)
  const [cur, setCur] = useState(0)
  const [dur, setDur] = useState(0)
  const pct = dur > 0 ? (cur / dur) * 100 : 0

  const toggle = () => {
    const a = ref.current
    if (!a) return
    if (a.paused) a.play().catch(() => {})
    else a.pause()
  }

  const seek = (e: React.MouseEvent<HTMLDivElement>) => {
    const a = ref.current
    if (!a || !dur) return
    const rect = e.currentTarget.getBoundingClientRect()
    a.currentTime = ((e.clientX - rect.left) / rect.width) * dur
  }

  return (
    <div className="inline-flex items-center gap-2 rounded-full border border-divider bg-bg py-0.5 pr-2.5 pl-0.5">
      <audio
        ref={ref}
        src={src}
        preload="none"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onTimeUpdate={() => setCur(ref.current?.currentTime ?? 0)}
        onLoadedMetadata={() => setDur(ref.current?.duration ?? 0)}
      />
      <button
        type="button"
        onClick={toggle}
        aria-label={playing ? 'Tạm dừng' : 'Phát'}
        className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-accent text-[#14121f] transition-colors hover:bg-accent-400"
      >
        {playing ? (
          <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
            <rect x="6" y="5" width="4" height="14" rx="1" />
            <rect x="14" y="5" width="4" height="14" rx="1" />
          </svg>
        ) : (
          <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
            <path d="M8 5v14l11-7z" />
          </svg>
        )}
      </button>
      <div className="h-1 w-16 cursor-pointer overflow-hidden rounded-full bg-neutral-700" onClick={seek}>
        <div className="h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
      </div>
      <span className="font-mono text-[11px] text-neutral-400 tabular-nums">{fmt(cur > 0 ? cur : dur)}</span>
    </div>
  )
}
