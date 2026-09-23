import type { RefObject } from 'react'
import type { SrtCue } from '../lib/api'
import { srtTimeToSeconds } from '../lib/srt'

function formatTime(sec: number): string {
  const s = Math.max(0, Math.floor(sec))
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m}:${r.toString().padStart(2, '0')}`
}

export default function PinnedVideoPanel({
  videoUrl,
  videoRef,
  cues,
  currentTime,
  duration,
  onTimeUpdate,
  onLoadedMetadata,
}: {
  videoUrl: string
  videoRef: RefObject<HTMLVideoElement | null>
  cues: SrtCue[]
  currentTime: number
  duration: number
  onTimeUpdate: () => void
  onLoadedMetadata: () => void
}) {
  const activeCue = cues.find((c) => currentTime >= srtTimeToSeconds(c.start) && currentTime < srtTimeToSeconds(c.end))
  const progressPct = duration > 0 ? Math.min(100, (currentTime / duration) * 100) : 0

  const seekFromBar = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!duration || !videoRef.current) return
    const rect = e.currentTarget.getBoundingClientRect()
    const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width))
    videoRef.current.currentTime = frac * duration
  }

  return (
    <div className="w-100 shrink-0 sticky top-6 self-start space-y-2.5">
      <div className="card overflow-hidden p-0! shadow-md">
        <video
          ref={videoRef}
          className="block max-h-72 w-full bg-black"
          src={videoUrl}
          controls
          preload="metadata"
          onTimeUpdate={onTimeUpdate}
          onLoadedMetadata={onLoadedMetadata}
        />
        <div className="space-y-2 p-3.5">
          <div className="cursor-pointer" onClick={seekFromBar}>
            <div className="relative h-1.5 rounded-full bg-neutral-800">
              {duration > 0 &&
                cues.map((c) => (
                  <div
                    key={c.id}
                    className="absolute -top-0.75 h-3 w-px bg-neutral-600"
                    style={{ left: `${(srtTimeToSeconds(c.start) / duration) * 100}%` }}
                  />
                ))}
              <div className="absolute inset-y-0 left-0 rounded-full bg-accent" style={{ width: `${progressPct}%` }} />
              <div
                className="absolute top-1/2 h-3 w-3 -translate-y-1/2 -translate-x-1/2 rounded-full bg-accent-200"
                style={{ left: `${progressPct}%` }}
              />
            </div>
          </div>
          <div className="mono text-xs text-neutral-500">
            {formatTime(currentTime)} / {formatTime(duration)}
          </div>
          {activeCue && (
            <div className="text-sm leading-snug text-neutral-300">
              <span className="mono text-xs text-accent-300">Đang phát &middot; câu {activeCue.id}</span>
              <br />
              {activeCue.text_vi || activeCue.text}
            </div>
          )}
        </div>
      </div>
      <p className="text-xs text-neutral-500">
        Video luôn hiện ở đây để đối chiếu — bấm bất kỳ dòng phụ đề bên phải sẽ tua tới đúng mốc.
      </p>
    </div>
  )
}
