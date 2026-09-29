import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type ProjectState } from '../lib/api'
import { primaryButtonClass } from '../lib/ui'

// Đăng tay video đã xuất (export/final.mp4) của dự án đơn lên 1 Facebook Page
// dạng Reels — qua Graph API, không mở trình duyệt. Cùng vai trò TikTokPublishPanel.

const fmtTime = (iso: string) =>
  new Date(iso).toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })

export default function FacebookPublishPanel({ projectId, project }: { projectId: string; project: ProjectState }) {
  const queryClient = useQueryClient()
  const pagesQuery = useQuery({ queryKey: ['facebook-pages'], queryFn: api.listFacebookPages })
  const jobQuery = useQuery({
    queryKey: ['project-facebook-publish-job', projectId],
    queryFn: () => api.projectFacebookPublishJob(projectId),
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 2000 : false),
  })

  const posts = project.facebook_posts ?? []
  const [caption, setCaption] = useState(project.facebook_caption || project.tiktok_caption || '')
  const [pageId, setPageId] = useState(posts.length ? posts[posts.length - 1].page_id : '')

  const pages = pagesQuery.data?.pages ?? []
  const page = pages.find((p) => p.page_id === pageId) ?? null
  const running = jobQuery.data?.status === 'running'

  const publish = useMutation({
    mutationFn: () => api.publishProjectFacebook(projectId, pageId, caption),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['project-facebook-publish-job', projectId] }),
  })

  const jobStatus = jobQuery.data?.status
  useEffect(() => {
    if (jobStatus && jobStatus !== 'running') {
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
      queryClient.invalidateQueries({ queryKey: ['facebook-pages'] })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobStatus])

  const submit = () => {
    if (!caption.trim() && !window.confirm('Caption đang trống — vẫn đăng?')) return
    if (posts.some((p) => p.page_id === pageId) && !window.confirm(`Video này đã từng đăng lên ${page?.name} — đăng thêm lần nữa?`))
      return
    publish.mutate()
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <label htmlFor="fb-page" className="text-sm text-neutral-300">
            Facebook Page
          </label>
          <Link to="/accounts?tab=facebook" className="text-xs text-neutral-400 hover:text-accent-200">
            Quản lý Facebook Page →
          </Link>
        </div>
        <select
          id="fb-page"
          className="input max-w-sm"
          value={pageId}
          disabled={running}
          onChange={(e) => setPageId(e.target.value)}
        >
          <option value="">— Chọn Page —</option>
          {pages.map((p) => (
            <option key={p.page_id} value={p.page_id} disabled={p.status === 'expired'}>
              {p.name}
              {p.status === 'expired' ? ' (token hết hạn)' : ''}
              {p.projects.length ? ` · dự án tự động: ${p.projects.map((x) => x.title).join(', ')}` : ''}
            </option>
          ))}
        </select>
        {!pagesQuery.isLoading && !pages.length && (
          <p className="text-xs text-neutral-500">
            Chưa có Page nào — thêm ở <Link to="/accounts?tab=facebook" className="text-accent-200">trang Tài khoản</Link>.
          </p>
        )}
      </div>

      <div className="space-y-1">
        <label htmlFor="fb-caption" className="text-sm text-neutral-300">
          Mô tả Reel
        </label>
        <textarea
          id="fb-caption"
          className="input min-h-32"
          value={caption}
          disabled={running}
          placeholder="Mô tả + hashtag"
          onChange={(e) => setCaption(e.target.value)}
        />
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          className={primaryButtonClass}
          disabled={!page || running || publish.isPending}
          onClick={submit}
        >
          {running ? 'Đang đăng...' : 'Đăng Reel lên Facebook'}
        </button>
        {running && <span className="text-sm text-neutral-400">{jobQuery.data?.current_label}</span>}
        {jobStatus === 'done' && !running && <span className="text-sm text-accent-200">{jobQuery.data?.current_label}</span>}
      </div>
      <p className="text-xs text-neutral-500">
        Đăng qua API chính thức của Facebook, không mở trình duyệt. Tải video lên xong Facebook còn xử lý thêm vài phút
        trước khi Reel hiện công khai.
      </p>
      {publish.error && <p className="text-sm text-danger">{(publish.error as Error).message}</p>}
      {jobStatus === 'failed' && jobQuery.data?.error && <p className="text-sm text-danger">Đăng lỗi: {jobQuery.data.error}</p>}

      {posts.length > 0 && (
        <div className="border-t border-divider pt-3">
          <h3 className="mb-2 text-sm text-neutral-300">Đã đăng</h3>
          <ul className="space-y-1 text-sm">
            {[...posts].reverse().map((p, i) => (
              <li key={i} className="flex flex-wrap gap-2">
                <span className="mono text-xs text-neutral-400">{fmtTime(p.posted_at)}</span>
                {p.permalink_url ? (
                  <a href={p.permalink_url} target="_blank" rel="noreferrer" className="text-accent-200 hover:underline">
                    {p.page_name || p.page_id} ↗
                  </a>
                ) : (
                  <span>{p.page_name || p.page_id}</span>
                )}
                <span className="truncate text-neutral-500">{p.caption.slice(0, 80)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
