import { useEffect, useRef, useState } from 'react'

export type CropRegion = [number, number, number, number] // x, y, w, h — phân số 0-1 CỦA VIDEO THẬT

// Luôn lấy gần trọn chiều ngang — đã xác nhận thực tế: khoanh vừa khít 1 câu
// phụ đề NGẮN sẽ cắt cụt 2 đầu mọi câu DÀI hơn xuất hiện sau đó trong cùng
// video (ffmpeg xoá hẳn phần ngoài vùng khoanh trước khi OCR kịp đọc, không
// phải lỗi tính toạ độ) — nên đổi hẳn cách chọn: chỉ kéo để chọn ĐỘ CAO dải
// phụ đề, bề ngang cố định rộng rãi, tránh hẳn khả năng chọn hẹp nhầm.
const BAND_X = 0.02
const BAND_W = 0.96

function formatTime(sec: number): string {
  const s = Math.max(0, sec)
  const m = Math.floor(s / 60)
  const r = (s % 60).toFixed(1)
  return `${m}:${r.padStart(4, '0')}`
}

export default function OcrCropSelector({
  videoUrl,
  crop,
  onChange,
}: {
  videoUrl: string
  crop: CropRegion | null
  onChange: (crop: CropRegion | null) => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const dragStartY = useRef<number | null>(null)
  const [duration, setDuration] = useState(0)
  const [time, setTime] = useState(0)
  const [videoBox, setVideoBox] = useState({ left: 0, top: 0, width: 0, height: 0 })
  // Khi đang kéo: chỉ cập nhật state LOCAL để vẽ lại khung ngay lập tức (rẻ,
  // không round-trip mạng) — `onChange` (gọi API lưu) chỉ bắn 1 LẦN DUY NHẤT
  // lúc thả chuột. Trước đây gọi `onChange` (→ mutation → POST) ở MỖI SỰ KIỆN
  // pointermove — hàng chục request/giây trong lúc kéo, đã xác nhận thật đó
  // là nguyên nhân kéo bị giật/lag, không phải do render khung overlay.
  const [dragCrop, setDragCrop] = useState<CropRegion | null>(null)

  const updateVideoBox = () => {
    const v = videoRef.current
    const c = containerRef.current
    if (!v || !c) return
    const vr = v.getBoundingClientRect()
    const cr = c.getBoundingClientRect()
    setVideoBox({ left: vr.left - cr.left, top: vr.top - cr.top, width: vr.width, height: vr.height })
  }

  useEffect(() => {
    window.addEventListener('resize', updateVideoBox)
    return () => window.removeEventListener('resize', updateVideoBox)
  }, [])

  const yFrac = (clientY: number) => {
    const vr = videoRef.current?.getBoundingClientRect()
    if (!vr || vr.height === 0) return 0
    return Math.min(Math.max((clientY - vr.top) / vr.height, 0), 1)
  }

  const onPointerDown = (e: React.PointerEvent) => {
    e.preventDefault()
    const y = yFrac(e.clientY)
    dragStartY.current = y
    ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
    setDragCrop([BAND_X, y, BAND_W, 0])
  }

  const onPointerMove = (e: React.PointerEvent) => {
    const start = dragStartY.current
    if (start === null) return
    const y = yFrac(e.clientY)
    setDragCrop([BAND_X, Math.min(start, y), BAND_W, Math.abs(y - start)])
  }

  const onPointerUp = () => {
    if (dragStartY.current === null) return
    dragStartY.current = null
    if (dragCrop) onChange(dragCrop)
    setDragCrop(null)
  }

  const seek = (t: number) => {
    setTime(t)
    if (videoRef.current) videoRef.current.currentTime = t
  }

  const displayCrop = dragCrop ?? crop
  const hasBand = displayCrop && displayCrop[3] > 0.005

  return (
    <div className="space-y-2">
      <p className="text-xs text-neutral-500">
        Tua tới đoạn có <b>phụ đề lời thoại thật</b> rồi kéo chọn ĐỘ CAO dải phụ đề (bề ngang luôn lấy gần trọn khung
        hình để không cắt cụt câu dài) — khung hình đầu video thường chỉ có tên chương trình/logo, không phải vị trí
        phụ đề chạy suốt video.
      </p>
      <div
        ref={containerRef}
        className="relative w-full cursor-row-resize touch-none select-none overflow-hidden rounded-lg bg-black"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      >
        <video
          ref={videoRef}
          className="block max-h-80 w-full"
          src={videoUrl}
          muted
          preload="metadata"
          onLoadedMetadata={() => {
            setDuration(videoRef.current?.duration || 0)
            updateVideoBox()
          }}
        />
        {hasBand && videoBox.width > 0 && (
          <div
            className="pointer-events-none absolute border-y-2 border-accent-300 bg-accent-500/25"
            style={{
              left: `${videoBox.left}px`,
              top: `${videoBox.top + displayCrop![1] * videoBox.height}px`,
              width: `${videoBox.width}px`,
              height: `${displayCrop![3] * videoBox.height}px`,
            }}
          />
        )}
        {!hasBand && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/40 text-sm text-neutral-200">
            Kéo lên/xuống để chọn dải phụ đề
          </div>
        )}
      </div>
      {duration > 0 && (
        <div className="flex items-center gap-2">
          <input
            type="range"
            min={0}
            max={duration}
            step={0.1}
            value={time}
            onChange={(e) => seek(Number(e.target.value))}
            className="flex-1"
          />
          <span className="w-14 shrink-0 text-right font-mono text-xs text-neutral-400">{formatTime(time)}</span>
        </div>
      )}
      <div className="flex items-center gap-2">
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => onChange(null)}>
          Đặt lại mặc định (25% đáy khung hình)
        </button>
        {hasBand && <span className="text-xs text-neutral-500">Cao {Math.round(displayCrop![3] * 100)}% khung hình</span>}
      </div>
    </div>
  )
}
