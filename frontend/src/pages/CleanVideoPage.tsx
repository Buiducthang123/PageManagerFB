import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type HardsubEngine, type HardsubItem } from '../lib/api'
import { primaryButtonClass, secondaryButtonClass } from '../lib/ui'
import ConfirmDialog from '../components/ConfirmDialog'
import JobProgressBar from '../components/JobProgressBar'

// Trang "Làm sạch video": xoá chữ Hán in sẵn (phụ đề cứng, tiêu đề, sticker)
// khỏi video bằng OCR + LaMa inpaint — worker chạy ở venv GPU riêng
// (app/stages/hardsub_worker.py). Đứng riêng, chưa nối vào pipeline reup.

function formatSize(bytes: number): string {
  const mb = bytes / (1024 * 1024)
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)}GB` : `${mb.toFixed(1)}MB`
}

function formatElapsed(sec: number): string {
  if (sec < 60) return `${Math.round(sec)}s`
  return `${Math.floor(sec / 60)}p${Math.round(sec % 60)}s`
}

const STATUS_LABEL: Record<HardsubItem['status'], string> = {
  pending: 'chờ',
  running: 'đang xử lý',
  done: 'xong',
  failed: 'lỗi',
}

// Gốc + đã làm sạch cạnh nhau; khi có cả 2 thì phát/dừng/tua video gốc kéo
// video sạch chạy theo cùng thời điểm để so từng khung.
function ComparePreview({ itemId, done }: { itemId: string; done: boolean }) {
  const srcRef = useRef<HTMLVideoElement>(null)
  const outRef = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    const a = srcRef.current
    const b = outRef.current
    if (!a || !b) return
    const sync = () => {
      if (Math.abs(b.currentTime - a.currentTime) > 0.15) b.currentTime = a.currentTime
    }
    const play = () => {
      sync()
      b.play().catch(() => {})
    }
    const pause = () => {
      b.pause()
      sync()
    }
    a.addEventListener('play', play)
    a.addEventListener('pause', pause)
    a.addEventListener('seeked', sync)
    return () => {
      a.removeEventListener('play', play)
      a.removeEventListener('pause', pause)
      a.removeEventListener('seeked', sync)
    }
  }, [done])

  const videoClass = 'max-h-[60vh] w-full rounded-lg bg-black object-contain'
  return (
    <div className={`grid grid-cols-1 gap-3 ${done ? 'sm:grid-cols-2' : ''}`}>
      <div className="space-y-1">
        <p className="text-xs text-neutral-400">Gốc{done && ' (phát/tua video này, bên phải chạy theo)'}</p>
        <video ref={srcRef} src={api.hardsubVideoUrl(itemId, 'input')} controls preload="metadata" className={videoClass} />
      </div>
      {done && (
        <div className="space-y-1">
          <p className="text-xs text-neutral-400">Đã làm sạch</p>
          <video ref={outRef} src={api.hardsubVideoUrl(itemId, 'output')} controls muted preload="metadata" className={videoClass} />
        </div>
      )}
    </div>
  )
}

// Xem trước file vừa chọn (chưa upload) — object URL từ máy, thu hồi khi bỏ.
function LocalVideoPreview({ file }: { file: File }) {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    const u = URL.createObjectURL(file)
    setUrl(u)
    return () => URL.revokeObjectURL(u)
  }, [file])
  if (!url) return null
  return <video src={url} controls muted preload="metadata" className="max-h-72 w-full rounded-lg bg-black object-contain" />
}

function HardsubRow({ item }: { item: HardsubItem }) {
  const queryClient = useQueryClient()
  const [deleting, setDeleting] = useState(false)
  const [preview, setPreview] = useState(false)
  const active = item.status === 'running' || item.status === 'pending'

  const jobQuery = useQuery({
    queryKey: ['hardsub-job', item.item_id],
    queryFn: () => api.hardsubJobStatus(item.item_id),
    enabled: active,
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 1000 : false),
  })

  // Job vừa xong/lỗi — tải lại danh sách để lấy status/output mới trong meta.json.
  const prevJobStatus = useRef<string | null>(null)
  const currentJobStatus = jobQuery.data?.status ?? null
  if (prevJobStatus.current === 'running' && currentJobStatus && currentJobStatus !== 'running') {
    queryClient.invalidateQueries({ queryKey: ['hardsub'] })
  }
  prevJobStatus.current = currentJobStatus
  // Server restart giữa chừng — route status đã tự đổi meta sang "failed", tải lại 1 lần.
  const orphaned = jobQuery.data?.orphaned ?? false
  useEffect(() => {
    if (orphaned) queryClient.invalidateQueries({ queryKey: ['hardsub'] })
  }, [orphaned, queryClient])

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['hardsub'] })
    queryClient.invalidateQueries({ queryKey: ['hardsub-job', item.item_id] })
  }
  const cancelMutation = useMutation({ mutationFn: () => api.cancelHardsubJob(item.item_id), onSuccess: refresh })
  const retryMutation = useMutation({ mutationFn: () => api.retryHardsub(item.item_id), onSuccess: refresh })
  const deleteMutation = useMutation({
    mutationFn: () => api.deleteHardsub(item.item_id),
    onSuccess: () => {
      setDeleting(false)
      queryClient.invalidateQueries({ queryKey: ['hardsub'] })
    },
  })

  const opts = item.options ?? { icon_pad: 0, all_text: false, nvenc: false }
  const optLabels = [
    // lượt tạo trước khi có chế độ STTN không lưu engine -> đã chạy bằng cách vá nhanh
    (opts.engine ?? 'fast') === 'sttn' ? 'vá mượt' : 'vá nhanh',
    opts.all_text ? 'mọi chữ' : 'chữ Hán',
    opts.icon_pad > 0 ? `xoá icon ×${opts.icon_pad}` : null,
    opts.nvenc ? 'NVENC' : null,
  ].filter(Boolean)

  return (
    <div className="card space-y-3 p-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate font-medium">{item.title}</span>
            <span
              className={`shrink-0 rounded-md px-1.5 py-0.5 font-mono text-[10px] ${
                item.status === 'done'
                  ? 'bg-accent-800 text-accent-100'
                  : item.status === 'failed'
                    ? 'bg-danger-900 text-danger-200'
                    : 'bg-neutral-800 text-neutral-300'
              }`}
            >
              {STATUS_LABEL[item.status]}
            </span>
          </div>
          <p className="mt-0.5 truncate text-xs text-neutral-500">
            {optLabels.join(' · ')}
            {item.elapsed_s != null && ` · mất ${formatElapsed(item.elapsed_s)}`}
          </p>
          {item.warning && <p className="mt-1 break-words text-xs text-danger-300">{item.warning}</p>}
          {item.status === 'failed' && item.error && (
            <p className="mt-1 whitespace-pre-wrap break-words text-xs text-danger">{item.error}</p>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {active && (
            <button
              type="button"
              className="btn btn-ghost btn-sm text-danger"
              disabled={cancelMutation.isPending}
              onClick={() => cancelMutation.mutate()}
            >
              Dừng
            </button>
          )}
          {item.status === 'failed' && (
            <button type="button" className={secondaryButtonClass} disabled={retryMutation.isPending} onClick={() => retryMutation.mutate()}>
              Chạy lại
            </button>
          )}
          {item.input_path && (
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setPreview((v) => !v)}>
              {preview ? 'Ẩn xem trước' : item.status === 'done' ? 'So sánh' : 'Xem video gốc'}
            </button>
          )}
          {item.status === 'done' && (
            <>
              <a className={secondaryButtonClass} href={api.hardsubVideoUrl(item.item_id, 'output', true)} download>
                Tải xuống
              </a>
            </>
          )}
          <button type="button" className="btn btn-ghost btn-sm text-danger" disabled={active} onClick={() => setDeleting(true)}>
            Xoá
          </button>
        </div>
      </div>

      {active && <JobProgressBar job={jobQuery.data} formatCount={(n) => `${n}%`} />}
      {retryMutation.error && <p className="text-xs text-danger">{(retryMutation.error as Error).message}</p>}

      {preview && item.input_path && <ComparePreview itemId={item.item_id} done={item.status === 'done'} />}

      <ConfirmDialog
        open={deleting}
        title="Xoá lượt làm sạch?"
        message="Video gốc đã tải lên và video đã làm sạch sẽ bị xoá khỏi đĩa."
        confirmLabel="Xoá"
        danger
        onCancel={() => setDeleting(false)}
        onConfirm={() => deleteMutation.mutate()}
      />
    </div>
  )
}

export default function CleanVideoPage() {
  const queryClient = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<File[]>([])
  const [removeIcons, setRemoveIcons] = useState(true)
  const [allText, setAllText] = useState(false)
  const [nvenc, setNvenc] = useState(false)
  const [engine, setEngine] = useState<HardsubEngine>('sttn')

  const listQuery = useQuery({ queryKey: ['hardsub'], queryFn: api.listHardsub })
  // Máy không có môi trường GPU riêng (vd bản cài cho user) → báo rõ, khoá chọn video.
  const envQuery = useQuery({ queryKey: ['hardsub-env'], queryFn: () => api.hardsubEnvironment(), staleTime: 10 * 60_000 })
  const unsupported = envQuery.data ? !envQuery.data.ok : false

  const createMutation = useMutation({
    mutationFn: () => api.createHardsub(files, { iconPad: removeIcons ? 0.8 : 0, allText, nvenc, engine }),
    onSuccess: () => {
      setFiles([])
      if (fileRef.current) fileRef.current.value = ''
      queryClient.invalidateQueries({ queryKey: ['hardsub'] })
    },
  })

  const addFiles = (picked: FileList | null) => {
    if (!picked || picked.length === 0) return
    // Copy ra mảng TRƯỚC khi reset input: FileList là "sống", reset value làm
    // nó rỗng — mà updater của setFiles chạy muộn hơn dòng reset bên dưới.
    const added = Array.from(picked)
    setFiles((prev) => [...prev, ...added])
    if (fileRef.current) fileRef.current.value = ''
  }

  const items = listQuery.data ?? []

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <section className="card space-y-4 p-5">
        <div>
          <h2 className="text-lg">Làm sạch video</h2>
          <p className="mt-1 text-sm text-neutral-400">
            Xoá chữ tiếng Trung in sẵn trên video (phụ đề cứng, tiêu đề, sticker chữ) và vá lại nền. Chạy bằng GPU, nên hợp
            với video ngắn. Mỗi lần chỉ xử lý 1 video, các video khác xếp hàng chờ.
          </p>
        </div>

        {envQuery.data && envQuery.data.message && (
          <p className={`rounded-md border px-3 py-2 text-sm ${unsupported ? 'border-danger/50 text-danger' : 'border-divider text-neutral-300'}`}>
            {envQuery.data.message}
            {unsupported && ' — chức năng này chưa dùng được trên máy này.'}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <input
            ref={fileRef}
            type="file"
            disabled={unsupported}
            multiple
            accept="video/mp4,video/webm,video/quicktime,video/x-matroska,.mp4,.mkv,.webm,.mov,.avi,.m4v"
            onChange={(e) => addFiles(e.target.files)}
          />
        </div>

        {files.length > 0 && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {files.map((f, i) => (
              <div key={`${f.name}-${f.size}-${f.lastModified}-${i}`} className="space-y-1.5 rounded-lg border border-neutral-800 p-2">
                <LocalVideoPreview file={f} />
                <div className="flex items-center gap-2 text-sm">
                  <span className="min-w-0 flex-1 truncate" title={f.name}>
                    {f.name}
                  </span>
                  <span className="shrink-0 text-xs text-neutral-500">{formatSize(f.size)}</span>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm text-danger"
                    onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}
                    title="Bỏ"
                  >
                    ✕
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        <fieldset className="space-y-2 text-sm">
          <legend className="mb-1 text-xs uppercase tracking-wide text-neutral-500">Cách vá nền</legend>
          <label className="flex items-start gap-2">
            <input type="radio" name="engine" className="mt-0.5" checked={engine === 'sttn'} onChange={() => setEngine('sttn')} />
            <span>
              Mượt (khuyên dùng)
              <span className="block text-xs text-neutral-500">
                Vá cả chuỗi khung hình cùng lúc nên vùng vá không nhảy/giật. Chậm hơn: khoảng 20–25 lần thời lượng video; nền
                nhiều chi tiết (thảm lông, hoa văn) có thể hơi mờ
              </span>
            </span>
          </label>
          <label className="flex items-start gap-2">
            <input type="radio" name="engine" className="mt-0.5" checked={engine === 'fast'} onChange={() => setEngine('fast')} />
            <span>
              Nhanh
              <span className="block text-xs text-neutral-500">
                Vá từng khung hình, khoảng 8–17 lần thời lượng video. Nét hơn nhưng vùng vá dễ nhảy khi chữ đổi, camera lia hoặc có
                vật đi qua chữ
              </span>
            </span>
          </label>
        </fieldset>

        <div className="space-y-2 text-sm">
          <label className="flex items-start gap-2">
            <input type="checkbox" className="mt-0.5" checked={removeIcons} onChange={(e) => setRemoveIcons(e.target.checked)} />
            <span>
              Xoá cả icon trang trí sát chữ
              <span className="block text-xs text-neutral-500">Nới vùng xoá ra 2 đầu dòng chữ (✦, ✓, !!)</span>
            </span>
          </label>
          <label className="flex items-start gap-2">
            <input type="checkbox" className="mt-0.5" checked={allText} onChange={(e) => setAllText(e.target.checked)} />
            <span>
              Xoá mọi chữ, cả chữ Latin và số
              <span className="block text-xs text-neutral-500">Mặc định chỉ xoá chữ Hán để không xoá nhầm logo, bảng hiệu</span>
            </span>
          </label>
          <label className="flex items-start gap-2">
            <input type="checkbox" className="mt-0.5" checked={nvenc} onChange={(e) => setNvenc(e.target.checked)} />
            <span>
              Encode bằng GPU (NVENC)
              <span className="block text-xs text-neutral-500">
                Nhanh hơn, file hơi to hơn. Cần driver NVIDIA 570 trở lên — thiếu thì tự chuyển sang encode CPU
              </span>
            </span>
          </label>
        </div>

        <button
          type="button"
          className={primaryButtonClass}
          disabled={files.length === 0 || createMutation.isPending}
          onClick={() => createMutation.mutate()}
        >
          {createMutation.isPending ? 'Đang tải lên...' : files.length > 1 ? `Làm sạch ${files.length} video` : 'Làm sạch video'}
        </button>
        {createMutation.error && <p className="text-sm text-danger">{(createMutation.error as Error).message}</p>}
      </section>

      {items.length > 0 && (
        <div className="space-y-2">
          {items.map((it) => (
            <HardsubRow key={it.item_id} item={it} />
          ))}
        </div>
      )}
    </div>
  )
}
