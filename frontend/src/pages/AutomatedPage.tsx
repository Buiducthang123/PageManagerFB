import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type SocialProjectSummary } from '../lib/api'
import { inputClass, primaryButtonClass } from '../lib/ui'
import { NumberInput } from '../components/NumberInput'
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

export default function AutomatedPage() {
  const queryClient = useQueryClient()
  const [title, setTitle] = useState('')
  const [douyinUrl, setDouyinUrl] = useState('')
  const [postsPerDay, setPostsPerDay] = useState(1)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const listQuery = useQuery({ queryKey: ['social'], queryFn: api.listSocial })

  const createMutation = useMutation({
    mutationFn: () => api.createSocial(title.trim(), douyinUrl.trim(), postsPerDay),
    onSuccess: () => {
      setTitle('')
      setDouyinUrl('')
      queryClient.invalidateQueries({ queryKey: ['social'] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteSocial(id),
    onSuccess: () => {
      setDeletingId(null)
      queryClient.invalidateQueries({ queryKey: ['social'] })
    },
  })

  const list = useMemo(() => listQuery.data ?? [], [listQuery.data])

  return (
    <div className="mx-auto max-w-4xl">
      <h1 className="mb-1 text-2xl">Dự án tự động</h1>
      <p className="mb-6 text-sm text-neutral-400">
        Mỗi cặp: 1 trang cá nhân Douyin nguồn → crawl video → dịch/lồng giọng/xuất tự động → đăng lần lượt.
      </p>

      <form
        className="mb-8 flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          if (title.trim() && douyinUrl.trim()) createMutation.mutate()
        }}
      >
        <div className="flex flex-wrap gap-2">
          <input
            className={`${inputClass} mt-0 max-w-xs`}
            placeholder="Tên dự án..."
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <input
            className={`${inputClass} mt-0 max-w-md flex-1`}
            placeholder="https://www.douyin.com/user/..."
            value={douyinUrl}
            onChange={(e) => setDouyinUrl(e.target.value)}
          />
          <NumberInput
            min={1}
            max={3}
            fallback={1}
            className={`${inputClass} mt-0 w-24`}
            value={postsPerDay}
            onChange={setPostsPerDay}
            title="Số video đăng mỗi ngày (tối đa 3)"
          />
          <button
            type="submit"
            disabled={!title.trim() || !douyinUrl.trim() || createMutation.isPending}
            className={primaryButtonClass}
          >
            {createMutation.isPending ? 'Đang tạo...' : 'Tạo dự án'}
          </button>
        </div>
      </form>
      {createMutation.error && <p className="mb-4 text-sm text-danger">{(createMutation.error as Error).message}</p>}

      {listQuery.isLoading ? (
        <div className="skeleton h-24" />
      ) : list.length === 0 ? (
        <div className="empty-state">
          <div className="empty-icon">▸</div>
          Chưa có dự án tự động nào — dán link trang cá nhân Douyin rồi bấm Tạo.
        </div>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Tên</th>
              <th>Nguồn Douyin</th>
              <th>Trạng thái</th>
              <th>Tạo lúc</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {list.map((s: SocialProjectSummary) => (
              <tr key={s.id}>
                <td>
                  <Link to={`/automated/${s.id}`} className="font-medium">
                    {s.title}
                  </Link>
                  <div className="font-mono text-[11px] text-neutral-500">{s.id}</div>
                </td>
                <td className="mono max-w-xs truncate text-xs text-neutral-400">{s.douyin_profile_url}</td>
                <td>
                  <span className={`tag ${s.status === 'active' ? 'tag-accent' : 'tag-neutral'}`}>
                    {s.status === 'active' ? 'Đang chạy' : 'Tạm dừng'}
                  </span>
                </td>
                <td className="text-sm text-neutral-400" title={s.created_at}>
                  {relativeTime(s.created_at)}
                </td>
                <td>
                  <div className="row-actions">
                    <button type="button" className="btn btn-danger btn-sm" onClick={() => setDeletingId(s.id)}>
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
        title="Xoá dự án tự động?"
        message="Hàng đợi video của dự án này sẽ bị xoá khỏi đĩa (các project pipeline đã tạo ra từ đó KHÔNG bị xoá theo)."
        confirmLabel="Xoá"
        danger
        onCancel={() => setDeletingId(null)}
        onConfirm={() => deletingId && deleteMutation.mutate(deletingId)}
      />
    </div>
  )
}
