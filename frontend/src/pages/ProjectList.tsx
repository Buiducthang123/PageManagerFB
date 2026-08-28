import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, STAGE_LABELS, type ProjectSummary } from '../lib/api'
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
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const projectsQuery = useQuery({ queryKey: ['projects'], queryFn: api.listProjects })

  const createMutation = useMutation({
    mutationFn: (t: string) => api.createProject(t),
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

  const list = useMemo(() => projectsQuery.data ?? [], [projectsQuery.data])

  return (
    <div className="mx-auto max-w-4xl">
      <h1 className="mb-1 text-2xl">Dự án reup</h1>
      <p className="mb-6 text-sm text-neutral-400">Upload video tiếng Trung → Whisper → Gemini dịch tiếng Việt.</p>

      <form
        className="mb-8 flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (title.trim()) createMutation.mutate(title.trim())
        }}
      >
        <input
          className={`${inputClass} mt-0 max-w-md flex-1`}
          placeholder="Tên dự án mới..."
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <button type="submit" disabled={!title.trim() || createMutation.isPending} className={primaryButtonClass}>
          {createMutation.isPending ? 'Đang tạo...' : 'Tạo dự án'}
        </button>
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
        <table className="table">
          <thead>
            <tr>
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
                  <Link to={`/projects/${p.project_id}`} className="font-medium">
                    {p.title}
                  </Link>
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
    </div>
  )
}
