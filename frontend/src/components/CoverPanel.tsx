import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { primaryButtonClass, secondaryButtonClass } from '../lib/ui'

// Ảnh bìa tiếng Việt (app/stages/cover.py): nhiều video Douyin mở đầu bằng 2-4
// khung ảnh bìa có tiêu đề chữ Trung — TikTok lấy khung đầu làm ảnh bìa. Lúc
// xuất video, tool xoá tiêu đề cũ bằng AI (LaMa) rồi đặt khung tiêu đề Việt
// lên đúng các khung đó; không nhận ra chữ tiêu đề thì giữ ảnh bìa gốc. Ở
// đây: xem trước, sửa tiêu đề/màu, tạo lại/quét lại.
// Muốn áp dụng vào video đã xuất thì bấm Xuất lại.

type ZoomImage = { src: string; label: string }

function ImageLightbox({ images, index, onIndex, onClose }: {
  images: ZoomImage[]
  index: number
  onIndex: (i: number) => void
  onClose: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowLeft') onIndex((index - 1 + images.length) % images.length)
      else if (e.key === 'ArrowRight') onIndex((index + 1) % images.length)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [index, images.length, onIndex, onClose])

  const img = images[index]
  return (
    <div
      className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-3 bg-black/85 p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={img.label}
    >
      <img
        src={img.src}
        alt={img.label}
        className="max-h-[85vh] max-w-full rounded object-contain shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      />
      <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
        {images.length > 1 &&
          images.map((im, i) => (
            <button
              key={im.src}
              type="button"
              className={i === index ? primaryButtonClass : secondaryButtonClass}
              onClick={() => onIndex(i)}
            >
              {im.label}
            </button>
          ))}
        <button type="button" className={secondaryButtonClass} onClick={onClose}>
          Đóng (Esc)
        </button>
      </div>
    </div>
  )
}

export default function CoverPanel({ projectId, canBuild }: { projectId: string; canBuild: boolean }) {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['cover', projectId], queryFn: () => api.getCover(projectId) })
  const cover = query.data
  const [title, setTitle] = useState('')
  const [bg, setBg] = useState('#F2555A')
  const [fg, setFg] = useState('#FFFFFF')
  const [zoomIndex, setZoomIndex] = useState<number | null>(null)

  useEffect(() => {
    if (!cover) return
    setTitle(cover.title ?? '')
    setBg(cover.bg)
    setFg(cover.fg)
  }, [cover?.title, cover?.bg, cover?.fg]) // eslint-disable-line react-hooks/exhaustive-deps

  const update = useMutation({
    mutationFn: (body: Parameters<typeof api.updateCover>[1]) => api.updateCover(projectId, body),
    onSuccess: (data) => queryClient.setQueryData(['cover', projectId], data),
  })

  if (!cover) return <div className="skeleton h-24" />

  const noCover = cover.frames === 0
  const skipped = !noCover && !!cover.skip_reason
  const changed = title.trim() !== (cover.title ?? '') || bg.toLowerCase() !== cover.bg.toLowerCase() || fg.toLowerCase() !== cover.fg.toLowerCase()
  const zoomImages: ZoomImage[] = [
    ...(cover.original_url ? [{ src: cover.original_url, label: 'Ảnh bìa gốc' }] : []),
    ...(cover.cover_url && !skipped ? [{ src: cover.cover_url, label: 'Ảnh bìa mới' }] : []),
  ]
  const openZoom = (src: string) => {
    const i = zoomImages.findIndex((im) => im.src === src)
    if (i >= 0) setZoomIndex(i)
  }
  const thumbClass =
    'h-64 cursor-zoom-in rounded border border-neutral-800 object-contain transition hover:border-accent-400'

  return (
    <div className="space-y-4">
      <label className="flex items-center gap-2 text-sm text-neutral-200">
        <input
          type="checkbox"
          checked={cover.enabled}
          disabled={update.isPending}
          onChange={(e) => update.mutate({ enabled: e.target.checked })}
        />
        Thay ảnh bìa chữ Trung bằng ảnh bìa tiếng Việt khi xuất video
      </label>

      {cover.frames === null && (
        <p className="text-sm text-neutral-500">
          Chưa dò ảnh bìa — tool tự tạo lúc xuất video, hoặc bấm "Tạo ảnh bìa" để xem trước ngay.
        </p>
      )}
      {noCover && (
        <p className="text-sm text-neutral-500">
          Video này không có khung ảnh bìa ở đầu — không cần thay, TikTok sẽ lấy khung đầu của video làm ảnh bìa.
        </p>
      )}

      {skipped && (
        <div className="space-y-2">
          <p className="text-sm text-neutral-300">
            Giữ nguyên ảnh bìa gốc — {cover.skip_reason}.
          </p>
          {cover.original_url && (
            <figure className="space-y-1">
              <img
              src={cover.original_url}
              alt="Ảnh bìa gốc"
              title="Bấm để phóng to"
              className={thumbClass}
              onClick={() => openZoom(cover.original_url!)}
            />
              <figcaption className="text-xs text-neutral-500">
                Gốc{cover.zh.length ? `: ${cover.zh.join(' / ')}` : ''}
              </figcaption>
            </figure>
          )}
          <button
            type="button"
            className={secondaryButtonClass}
            disabled={!canBuild || update.isPending}
            onClick={() => update.mutate({ rescan: true, bg, fg })}
          >
            {update.isPending ? 'Đang quét...' : 'Quét lại video'}
          </button>
        </div>
      )}

      {cover.cover_url && cover.original_url && !noCover && (
        <div className="flex flex-wrap items-start gap-3">
          <figure className="space-y-1">
            <img
              src={cover.original_url}
              alt="Ảnh bìa gốc"
              title="Bấm để phóng to"
              className={thumbClass}
              onClick={() => openZoom(cover.original_url!)}
            />
            <figcaption className="text-xs text-neutral-500">
              Gốc{cover.zh.length ? `: ${cover.zh.join(' / ')}` : ' (không nhận ra tiêu đề chữ)'}
            </figcaption>
          </figure>
          <figure className="space-y-1">
            <img
              src={cover.cover_url}
              alt="Ảnh bìa tiếng Việt"
              title="Bấm để phóng to"
              className={thumbClass}
              onClick={() => openZoom(cover.cover_url!)}
            />
            <figcaption className="text-xs text-neutral-500">Ảnh bìa mới</figcaption>
          </figure>
        </div>
      )}

      {!noCover && !skipped && (
        <div className="space-y-3">
          <label className="block text-sm text-neutral-300">
            Tiêu đề ảnh bìa
            <input
              className="input mt-1"
              value={title}
              placeholder="Để trống = AI tự viết từ tiêu đề gốc"
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          <div className="flex flex-wrap items-center gap-4 text-sm text-neutral-300">
            <label className="flex items-center gap-2">
              Màu nền
              <input type="color" value={bg} onChange={(e) => setBg(e.target.value)} className="h-8 w-12 cursor-pointer" />
            </label>
            <label className="flex items-center gap-2">
              Màu chữ
              <input type="color" value={fg} onChange={(e) => setFg(e.target.value)} className="h-8 w-12 cursor-pointer" />
            </label>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className={primaryButtonClass}
              disabled={!canBuild || update.isPending}
              onClick={() =>
                update.mutate({ rebuild: true, ...(title.trim() ? { title: title.trim() } : {}), bg, fg })
              }
            >
              {update.isPending ? 'Đang tạo...' : cover.cover_url ? (changed ? 'Lưu & tạo lại' : 'Tạo lại') : 'Tạo ảnh bìa'}
            </button>
            {cover.cover_url && (
              <button
                type="button"
                className={secondaryButtonClass}
                disabled={!canBuild || update.isPending}
                onClick={() => update.mutate({ rewrite: true, bg, fg })}
              >
                AI viết tiêu đề khác
              </button>
            )}
          </div>
          <p className="text-xs text-neutral-500">
            Đổi xong bấm <b>Xuất lại video</b> ở mục bên dưới để áp dụng vào video đã xuất. Tiêu đề AI viết dựa trên chữ
            ảnh bìa gốc và nội dung video.
          </p>
        </div>
      )}
      {cover.error && <p className="text-sm text-danger">Lỗi lần tạo gần nhất: {cover.error}</p>}
      {update.error && <p className="text-sm text-danger">{(update.error as Error).message}</p>}
      {zoomIndex !== null && zoomImages[zoomIndex] && (
        <ImageLightbox images={zoomImages} index={zoomIndex} onIndex={setZoomIndex} onClose={() => setZoomIndex(null)} />
      )}
    </div>
  )
}
