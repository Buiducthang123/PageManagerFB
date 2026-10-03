import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type MergeItem } from '../lib/api'
import { inputClass, primaryButtonClass, secondaryButtonClass } from '../lib/ui'
import ConfirmDialog from './ConfirmDialog'

function formatSize(bytes: number): string {
  const mb = bytes / (1024 * 1024)
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)}GB` : `${mb.toFixed(1)}MB`
}

const STATUS_LABEL: Record<MergeItem['status'], string> = {
  pending: 'chờ',
  running: 'đang ghép',
  done: 'xong',
  failed: 'lỗi',
}

function MergeRow({ item }: { item: MergeItem }) {
  const queryClient = useQueryClient()
  const [deleting, setDeleting] = useState(false)

  const jobQuery = useQuery({
    queryKey: ['merge-job', item.merge_id],
    queryFn: () => api.mergeJobStatus(item.merge_id),
    enabled: item.status === 'running',
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 1000 : false),
  })

  // Job vừa chuyển từ running sang xong/lỗi — refresh lại danh sách merges để
  // lấy status/output_filename mới nhất (job status không tự cập nhật meta.json
  // của item, chỉ phản ánh trạng thái job trong bộ nhớ).
  const prevJobStatus = useRef<string | null>(null)
  const currentJobStatus = jobQuery.data?.status ?? null
  if (prevJobStatus.current === 'running' && currentJobStatus && currentJobStatus !== 'running') {
    queryClient.invalidateQueries({ queryKey: ['merges'] })
  }
  prevJobStatus.current = currentJobStatus

  const cancelMutation = useMutation({
    mutationFn: () => api.cancelMergeJob(item.merge_id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['merge-job', item.merge_id] }),
  })

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteMerge(item.merge_id),
    onSuccess: () => {
      setDeleting(false)
      queryClient.invalidateQueries({ queryKey: ['merges'] })
    },
  })

  return (
    <div className="card flex flex-wrap items-center justify-between gap-3 p-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-medium">{item.title}</span>
          <span
            className={`rounded-md px-1.5 py-0.5 font-mono text-[10px] ${
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
          {item.input_filenames.length} video: {item.input_filenames.join(' + ')}
        </p>
        {item.status === 'running' && jobQuery.data && (
          <p className="mt-1 text-xs text-neutral-400">{jobQuery.data.current_label ?? 'đang xử lý...'}</p>
        )}
        {item.status === 'failed' && item.error && <p className="mt-1 text-xs text-danger">{item.error}</p>}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {item.status === 'running' && (
          <button
            type="button"
            className="btn btn-ghost btn-sm text-danger"
            disabled={cancelMutation.isPending}
            onClick={() => cancelMutation.mutate()}
          >
            Dừng
          </button>
        )}
        {item.status === 'done' && (
          <a className={secondaryButtonClass} href={api.downloadMergeUrl(item.merge_id)} download>
            Tải xuống
          </a>
        )}
        <button type="button" className="btn btn-ghost btn-sm text-danger" disabled={item.status === 'running'} onClick={() => setDeleting(true)}>
          Xoá
        </button>
      </div>

      <ConfirmDialog
        open={deleting}
        title="Xoá kết quả ghép?"
        message="File video đã ghép sẽ bị xoá khỏi đĩa."
        confirmLabel="Xoá"
        danger
        onCancel={() => setDeleting(false)}
        onConfirm={() => deleteMutation.mutate()}
      />
    </div>
  )
}

export default function VideoMergePanel() {
  const queryClient = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<File[]>([])
  const [title, setTitle] = useState('')

  const mergesQuery = useQuery({ queryKey: ['merges'], queryFn: api.listMerges })

  const createMutation = useMutation({
    mutationFn: () => api.createMerge(title, files),
    onSuccess: () => {
      setFiles([])
      setTitle('')
      if (fileRef.current) fileRef.current.value = ''
      queryClient.invalidateQueries({ queryKey: ['merges'] })
    },
  })

  const addFiles = (picked: FileList | null) => {
    if (!picked || picked.length === 0) return
    const added = Array.from(picked) // copy trước khi reset input (FileList "sống")
    setFiles((prev) => [...prev, ...added])
    if (fileRef.current) fileRef.current.value = ''
  }

  const moveFile = (index: number, dir: -1 | 1) => {
    setFiles((prev) => {
      const next = [...prev]
      const target = index + dir
      if (target < 0 || target >= next.length) return prev
      ;[next[index], next[target]] = [next[target], next[index]]
      return next
    })
  }

  const removeFile = (index: number) => setFiles((prev) => prev.filter((_, i) => i !== index))

  const merges = mergesQuery.data ?? []

  return (
    <section className="card space-y-4 p-5">
      <div>
        <h2 className="text-lg">Ghép video</h2>
        <p className="mt-1 text-sm text-neutral-400">
          Upload lần lượt nhiều video, sắp xếp thứ tự rồi ghép thành 1 file mp4 duy nhất — không tạo dự án reup, chỉ
          để nối video lại với nhau.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <input
          ref={fileRef}
          type="file"
          multiple
          accept="video/mp4,video/webm,video/quicktime,video/x-matroska,.mp4,.mkv,.webm,.mov,.avi,.m4v"
          onChange={(e) => addFiles(e.target.files)}
        />
        <span className="text-xs text-neutral-500">chọn xong bấm tiếp để thêm video khác</span>
      </div>

      {files.length > 0 && (
        <div className="space-y-1.5 rounded-lg border border-neutral-800 p-3">
          {files.map((f, i) => (
            <div key={`${f.name}-${i}`} className="flex items-center gap-2 text-sm">
              <span className="w-5 text-neutral-500">{i + 1}.</span>
              <span className="min-w-0 flex-1 truncate">{f.name}</span>
              <span className="shrink-0 text-xs text-neutral-500">{formatSize(f.size)}</span>
              <button type="button" className="btn btn-ghost btn-sm" disabled={i === 0} onClick={() => moveFile(i, -1)} title="Lên">
                ▲
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={i === files.length - 1}
                onClick={() => moveFile(i, 1)}
                title="Xuống"
              >
                ▼
              </button>
              <button type="button" className="btn btn-ghost btn-sm text-danger" onClick={() => removeFile(i)} title="Bỏ">
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <input
          className={`${inputClass} mt-0 max-w-xs flex-1`}
          placeholder="Tên file ghép (tuỳ chọn)..."
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <button
          type="button"
          className={primaryButtonClass}
          disabled={files.length < 2 || createMutation.isPending}
          onClick={() => createMutation.mutate()}
        >
          {createMutation.isPending ? 'Đang tải lên...' : `Ghép ${files.length} video thành 1`}
        </button>
      </div>
      {files.length === 1 && <p className="text-xs text-neutral-500">Cần ít nhất 2 video để ghép.</p>}
      {createMutation.error && <p className="text-sm text-danger">{(createMutation.error as Error).message}</p>}

      {merges.length > 0 && (
        <div className="space-y-2">
          {merges.map((m) => (
            <MergeRow key={m.merge_id} item={m} />
          ))}
        </div>
      )}
    </section>
  )
}
