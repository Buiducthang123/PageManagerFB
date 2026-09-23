import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, STAGE_LABELS, type ProjectSummary, type ProjectType } from '../lib/api'
import { inputClass, primaryButtonClass } from '../lib/ui'
import ConfirmDialog from '../components/ConfirmDialog'

function relativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diffMs / 60_000)
  if (mins < 1) return 'vừa xong'
  if (mins < 60) return `${mins} phút trước`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} giờ trước`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days} ngày trước`
  return new Date(iso).toLocaleDateString('vi-VN')
}

export default function ProjectList() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [title, setTitle] = useState('')
  const [projectType, setProjectType] = useState<ProjectType>('single')
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false)

  const projectsQuery = useQuery({ queryKey: ['projects'], queryFn: api.listProjects })

  const createMutation = useMutation({
    mutationFn: ({ t, type }: { t: string; type: ProjectType }) => api.createProject(t, type),
    onSuccess: (p) => {
      setTitle('')
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      navigate(`/projects/${p.project_id}`)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteProject(id),
    onSuccess: () => {
      setDeletingId(null)
      queryClient.invalidateQueries({ queryKey: ['projects'] })
    },
  })

  const bulkDeleteMutation = useMutation({
    mutationFn: (ids: string[]) => Promise.all(ids.map((id) => api.deleteProject(id))),
    onSuccess: () => {
      setBulkDeleteOpen(false)
      setSelectedIds(new Set())
      queryClient.invalidateQueries({ queryKey: ['projects'] })
    },
  })

  const list = useMemo(() => projectsQuery.data ?? [], [projectsQuery.data])

  const toggleSelected = (id: string) =>
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const allSelected = list.length > 0 && list.every((p) => selectedIds.has(p.project_id))
  const toggleSelectAll = () =>
    setSelectedIds(allSelected ? new Set() : new Set(list.map((p) => p.project_id)))

  return (
    <div className="mx-auto max-w-4xl">
      <h1 className="mb-1 text-2xl">Dự án reup</h1>
      <p className="mb-6 text-sm text-neutral-400">Upload video tiếng Trung → Whisper → Gemini dịch tiếng Việt.</p>

      <form
        className="mb-8 flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          if (title.trim()) createMutation.mutate({ t: title.trim(), type: projectType })
        }}
      >
        <div className="flex gap-4 text-sm text-neutral-300">
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="project-type"
              checked={projectType === 'single'}
              onChange={() => setProjectType('single')}
            />
            Dự án đơn (1 video)
          </label>
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="project-type"
              checked={projectType === 'multi'}
              onChange={() => setProjectType('multi')}
            />
            Dự án dài tập (nhiều video/link)
          </label>
        </div>
        <div className="flex flex-wrap gap-2">
          <input
            className={`${inputClass} mt-0 max-w-md flex-1`}
            placeholder="Tên dự án mới..."
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <button type="submit" disabled={!title.trim() || createMutation.isPending} className={primaryButtonClass}>
            {createMutation.isPending ? 'Đang tạo...' : 'Tạo dự án'}
          </button>
        </div>
      </form>
      {createMutation.error && <p className="mb-4 text-sm text-danger">{(createMutation.error as Error).message}</p>}

      {projectsQuery.isLoading ? (
        <div className="skeleton h-24" />
      ) : list.length === 0 ? (
        <div className="empty-state">
          <div className="empty-icon">▸</div>
          Chưa có dự án — đặt tên rồi bấm Tạo.
        </div>
      ) : (
        <>
        {selectedIds.size > 0 && (
          <div className="mb-3 flex items-center gap-3 rounded-lg border border-danger-800 bg-danger-950/30 px-3 py-2 text-sm">
            <span>Đã chọn {selectedIds.size} dự án</span>
            <button type="button" className="btn btn-danger btn-sm" onClick={() => setBulkDeleteOpen(true)}>
              Xoá đã chọn
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setSelectedIds(new Set())}>
              Bỏ chọn
            </button>
          </div>
        )}
        <table className="table">
          <thead>
            <tr>
              <th className="w-8">
                <input type="checkbox" checked={allSelected} onChange={toggleSelectAll} />
              </th>
              <th>Tên</th>
              <th>Bước hiện tại</th>
              <th>Tạo lúc</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {list.map((p: ProjectSummary) => (
              <tr key={p.project_id}>
                <td>
                  <input
                    type="checkbox"
                    checked={selectedIds.has(p.project_id)}
                    onChange={() => toggleSelected(p.project_id)}
                  />
                </td>
                <td>
                  <Link to={`/projects/${p.project_id}`} className="font-medium">
                    {p.title}
                  </Link>
                  {p.project_type === 'multi' && (
                    <span className="ml-2 rounded-md bg-accent-800 px-1.5 py-0.5 font-mono text-[10px] text-accent-100">
                      dài tập
                    </span>
                  )}
                  <div className="font-mono text-[11px] text-neutral-500">{p.project_id}</div>
                </td>
                <td className="text-sm text-neutral-300">
                  {p.current_stage ? STAGE_LABELS[p.current_stage] : 'Xong Gemini'}
                </td>
                <td className="text-sm text-neutral-400" title={p.created_at}>
                  {relativeTime(p.created_at)}
                </td>
                <td>
                  <div className="row-actions">
                    <button type="button" className="btn btn-danger btn-sm" onClick={() => setDeletingId(p.project_id)}>
                      Xoá
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </>
      )}

      <ConfirmDialog
        open={deletingId !== null}
        title="Xoá dự án?"
        message="Video, phụ đề và entity dict sẽ bị xoá khỏi đĩa."
        confirmLabel="Xoá"
        danger
        onCancel={() => setDeletingId(null)}
        onConfirm={() => deletingId && deleteMutation.mutate(deletingId)}
      />

      <ConfirmDialog
        open={bulkDeleteOpen}
        title={`Xoá ${selectedIds.size} dự án đã chọn?`}
        message="Video, phụ đề và entity dict của TẤT CẢ dự án đã chọn sẽ bị xoá khỏi đĩa. Không thể hoàn tác."
        confirmLabel={bulkDeleteMutation.isPending ? 'Đang xoá...' : 'Xoá tất cả'}
        danger
        onCancel={() => setBulkDeleteOpen(false)}
        onConfirm={() => bulkDeleteMutation.mutate([...selectedIds])}
      />
      {bulkDeleteMutation.error && (
        <p className="mt-2 text-sm text-danger">{(bulkDeleteMutation.error as Error).message}</p>
      )}
    </div>
  )
}
