import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type ProjectState } from '../lib/api'
import { primaryButtonClass } from '../lib/ui'
import TikTokAccountIdentity from './TikTokAccountIdentity'

// Đăng tay video đã xuất (export/final.mp4) của dự án đơn lên 1 tài khoản
// TikTok bất kỳ — không qua hàng đợi dự án tự động. Backend kiểm tra đúng
// tài khoản trước khi đưa file lên (publish_tiktok expected_uid).

const CAPTION_MAX = 4000

const fmtTime = (iso: string) =>
  new Date(iso).toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })

export default function TikTokPublishPanel({ projectId, project }: { projectId: string; project: ProjectState }) {
  const queryClient = useQueryClient()
  const accountsQuery = useQuery({ queryKey: ['accounts'], queryFn: api.listAccounts })
  const jobQuery = useQuery({
    queryKey: ['project-tiktok-publish-job', projectId],
    queryFn: () => api.projectTiktokPublishJob(projectId),
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 2000 : false),
  })

  const posts = project.tiktok_posts ?? []
  const [caption, setCaption] = useState(project.tiktok_caption ?? '')
  // Mặc định chọn lại tài khoản lần đăng trước.
  const [accountId, setAccountId] = useState(posts.length ? posts[posts.length - 1].account_id : '')

  const accounts = accountsQuery.data ?? []
  const account = accounts.find((a) => a.id === accountId) ?? null
  const running = jobQuery.data?.status === 'running'

  const publish = useMutation({
    mutationFn: () => api.publishProjectTiktok(projectId, accountId, caption),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['project-tiktok-publish-job', projectId] }),
  })

  const jobStatus = jobQuery.data?.status
  useEffect(() => {
    if (jobStatus && jobStatus !== 'running') {
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobStatus])

  const alreadyOnAccount = posts.some((p) => p.account_id === accountId)

  const submit = () => {
    if (!caption.trim() && !window.confirm('Caption đang trống — vẫn đăng?')) return
    if (alreadyOnAccount && !window.confirm(`Video này đã từng đăng lên @${account?.username} — đăng thêm lần nữa?`)) return
    publish.mutate()
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <label htmlFor="tiktok-account" className="text-sm text-neutral-300">
            Tài khoản
          </label>
          <Link to="/accounts" className="text-xs text-neutral-400 hover:text-accent-200">
            Quản lý tài khoản →
          </Link>
        </div>
        <select
          id="tiktok-account"
          className="input max-w-sm"
          value={accountId}
          disabled={running}
          onChange={(e) => setAccountId(e.target.value)}
        >
          <option value="">— Chọn tài khoản —</option>
          {accounts.map((a) => (
            <option key={a.id} value={a.id} disabled={a.status === 'expired' || a.status === 'mismatch'}>
              {a.username ? `@${a.username}` : a.label || a.id}
              {a.status === 'expired' || a.status === 'mismatch' ? ' (cần đăng nhập lại)' : ''}
              {a.projects.length ? ` · dự án tự động: ${a.projects.map((p) => p.title).join(', ')}` : ''}
            </option>
          ))}
        </select>
        {!accountsQuery.isLoading && !accounts.length && (
          <p className="text-xs text-neutral-500">
            Chưa có tài khoản nào — thêm ở <Link to="/accounts" className="text-accent-200">trang Tài khoản</Link>.
          </p>
        )}
        {account && <TikTokAccountIdentity account={account} />}
        {account && account.projects.length > 0 && (
          <p className="text-xs text-neutral-500">
            Tài khoản này đang dùng cho dự án tự động — bài đăng tay không tính vào số bài/ngày của dự án đó.
          </p>
        )}
      </div>

      <div className="space-y-1">
        <label htmlFor="tiktok-caption" className="text-sm text-neutral-300">
          Caption
        </label>
        <textarea
          id="tiktok-caption"
          className="input min-h-32"
          value={caption}
          disabled={running}
          placeholder="Dán caption + hashtag vào đây"
          onChange={(e) => setCaption(e.target.value)}
        />
        <div className={`text-right text-xs ${caption.length > CAPTION_MAX ? 'text-danger' : 'text-neutral-500'}`}>
          {caption.length}/{CAPTION_MAX}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          className={primaryButtonClass}
          disabled={!account || running || publish.isPending || caption.length > CAPTION_MAX}
          onClick={submit}
        >
          {running ? 'Đang đăng...' : 'Đăng lên TikTok'}
        </button>
        {running && <span className="text-sm text-neutral-400">{jobQuery.data?.current_label}</span>}
        {jobStatus === 'done' && !running && (
          <span className="text-sm text-accent-200">{jobQuery.data?.current_label}</span>
        )}
      </div>
      <p className="text-xs text-neutral-500">
        Một cửa sổ Chrome sẽ mở ra và tự đăng — đừng đóng cửa sổ trong lúc đăng. App kiểm tra đúng tài khoản trước khi
        đưa video lên.
      </p>
      {publish.error && <p className="text-sm text-danger">{(publish.error as Error).message}</p>}
      {jobStatus === 'failed' && jobQuery.data?.error && (
        <p className="text-sm text-danger">Đăng lỗi: {jobQuery.data.error}</p>
      )}

      {posts.length > 0 && (
        <div className="border-t border-divider pt-3">
          <h3 className="mb-2 text-sm text-neutral-300">Đã đăng</h3>
          <ul className="space-y-1 text-sm">
            {[...posts].reverse().map((p, i) => (
              <li key={i} className="flex flex-wrap gap-2">
                <span className="mono text-xs text-neutral-400">{fmtTime(p.posted_at)}</span>
                <span className="mono">@{p.username || p.account_id}</span>
                <span className="truncate text-neutral-500">{p.caption.slice(0, 80)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
