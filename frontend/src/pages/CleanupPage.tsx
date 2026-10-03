import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type CleanupPlanEntry } from '../lib/api'
import { inputClass } from '../lib/ui'

// Trang "Tự dọn ổ đĩa": lịch tự xoá file nặng (video gốc, video thành phẩm,
// audio) của các video trong dự án tự động — cùng nguồn với việc xoá thật
// (app/social_cleanup.py::cleanup_plan), kèm nút "Giữ lại" để loại 1 video
// khỏi tự xoá.

type Filter = 'all' | 'due' | 'kept'

const fmtTime = (iso: string) =>
  new Date(iso).toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })

export default function CleanupPage() {
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState<Filter>('all')
  const query = useQuery({ queryKey: ['cleanup-overview'], queryFn: api.cleanupOverview, refetchInterval: 30_000 })

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [resultMsg, setResultMsg] = useState<string | null>(null)

  const refreshAll = () => {
    queryClient.invalidateQueries({ queryKey: ['cleanup-overview'] })
    queryClient.invalidateQueries({ queryKey: ['social-cleanup-plan'] })
  }

  const keepMutation = useMutation({
    mutationFn: ({ projectId, keep }: { projectId: string; keep: boolean }) => api.setCleanupKeep(projectId, keep),
    onSuccess: refreshAll,
  })

  const allowMutation = useMutation({
    mutationFn: ({ projectId, allow }: { projectId: string; allow: boolean }) =>
      api.setCleanupAllowDelete(projectId, allow),
    onSuccess: refreshAll,
  })
  const askAllowDelete = (e: CleanupPlanEntry) => {
    const ok = window.confirm(
      `Video "${e.video_title || e.aweme_id}" đang chờ đăng.\n\n` +
        'Cho phép xoá thì video bị rút khỏi lịch đăng (chuyển sang Đã bỏ qua) và bị xoá ở lượt tự dọn tới nếu đã cũ hơn ' +
        `${query.data?.cleanup_after_hours ?? 24} giờ — hoặc chọn rồi bấm Xoá ngay. ` +
        'Chưa xoá thì bấm "Không xoá nữa" để đưa về lại Sẵn sàng đăng.',
    )
    if (ok) allowMutation.mutate({ projectId: e.project_id, allow: true })
  }

  const deleteMutation = useMutation({
    mutationFn: ({ ids, mode }: { ids: string[]; mode: 'files' | 'project' }) => api.cleanupDelete(ids, mode),
    onSuccess: (res) => {
      const failed = res.results.filter((r) => !r.ok)
      setResultMsg(
        `Đã xoá ${res.deleted} project, giải phóng ${res.freed_mb} MB.` +
          (failed.length ? ` Không xoá được ${failed.length}: ${failed.map((f) => f.error).join('; ')}` : ''),
      )
      setSelected(new Set())
      refreshAll()
    },
  })

  const d = query.data
  if (query.isLoading || !d) {
    return (
      <div className="mx-auto max-w-6xl">
        <div className="skeleton h-24" />
      </div>
    )
  }

  const due = d.plan.filter((e) => e.due_at)
  const kept = d.plan.filter((e) => !e.due_at)
  const rows: CleanupPlanEntry[] = filter === 'due' ? due : filter === 'kept' ? kept : d.plan
  // Không chọn được video hệ thống đang tự bảo vệ (đang xử lý, sẵn sàng
  // đăng, đang chạy 1 bước) — backend cũng từ chối các project này.
  const selectable = (e: CleanupPlanEntry) => !e.protected_reason || e.kept_by_user
  const selectableRows = rows.filter(selectable)
  const selectedEntries = d.plan.filter((e) => selected.has(e.project_id))
  const selectedMb = selectedEntries.reduce((s, e) => s + e.size_mb, 0)
  const allChecked = selectableRows.length > 0 && selectableRows.every((e) => selected.has(e.project_id))

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const runDelete = (mode: 'files' | 'project') => {
    const ids = selectedEntries.map((e) => e.project_id)
    if (!ids.length) return
    const msg =
      mode === 'files'
        ? `Xoá ngay video gốc, video thành phẩm và audio của ${ids.length} project (${selectedMb} MB)?\nPhụ đề và cấu hình vẫn được giữ.`
        : `Xoá HẲN ${ids.length} project pipeline (${selectedMb} MB)?\nToàn bộ thư mục project sẽ bị xoá và không khôi phục được. Video trong hàng đợi dự án tự động vẫn giữ nguyên trạng thái.`
    if (!window.confirm(msg)) return
    setResultMsg(null)
    deleteMutation.mutate({ ids, mode })
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 className="text-2xl">Tự dọn ổ đĩa</h1>
        <p className="mt-1 text-sm text-neutral-500">
          App tự xoá video gốc, video thành phẩm và audio của video trong dự án tự động {d.cleanup_after_hours} giờ sau khi
          đăng, và với video lỗi, bị bỏ qua hoặc bản xử lý cũ không hoạt động {d.cleanup_after_hours} giờ. Phụ đề, giọng
          đọc và cấu hình luôn được giữ. Video đang xử lý, sẵn sàng đăng và project tạo tay không bao giờ bị xoá. Bấm "Giữ
          lại" để loại một video khỏi tự xoá.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <div className={`card px-4 py-3 ${d.low_disk ? 'border-danger-300' : ''}`}>
          <div className="text-xs text-neutral-500">Ổ đĩa còn trống</div>
          <div className="mono mt-1 text-2xl">{d.free_gb} GB</div>
          <div className="text-xs text-neutral-500">
            {d.low_disk ? `Dưới ${d.min_free_gb} GB — đã ngừng xử lý video mới` : `Ngưỡng an toàn ${d.min_free_gb} GB`}
          </div>
        </div>
        <div className="card px-4 py-3">
          <div className="text-xs text-neutral-500">Sắp tự xoá</div>
          <div className="mono mt-1 text-2xl">{due.length}</div>
          <div className="text-xs text-neutral-500">{due.reduce((s, e) => s + e.size_mb, 0)} MB</div>
        </div>
        <div className="card px-4 py-3">
          <div className="text-xs text-neutral-500">Được giữ lại</div>
          <div className="mono mt-1 text-2xl">{kept.length}</div>
          <div className="text-xs text-neutral-500">{kept.reduce((s, e) => s + e.size_mb, 0)} MB</div>
        </div>
        <div className="card px-4 py-3">
          <div className="text-xs text-neutral-500">Lần dọn gần nhất</div>
          <div className="mono mt-1 text-2xl">{d.last_cleanup ? `${d.last_cleanup.freed_mb} MB` : '—'}</div>
          <div className="text-xs text-neutral-500">
            {d.last_cleanup ? `${fmtTime(d.last_cleanup.at)} · ${d.last_cleanup.projects_cleaned} project` : 'Chưa ghi nhận lần nào'}
          </div>
        </div>
      </div>

      <section className="card space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-lg">Lịch tự dọn</h2>
          <label className="ml-auto flex items-center gap-2 text-sm text-neutral-300">
            Hiện
            <select className={`${inputClass} mt-0`} value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
              <option value="all">Tất cả ({d.plan.length})</option>
              <option value="due">Sắp tự xoá ({due.length})</option>
              <option value="kept">Được giữ lại ({kept.length})</option>
            </select>
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-neutral-800 px-3 py-2 text-sm">
          <span className="text-neutral-300">
            Đã chọn {selectedEntries.length} project{selectedEntries.length ? ` · ${selectedMb} MB` : ''}
          </span>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={!selectedEntries.length || deleteMutation.isPending}
            onClick={() => runDelete('files')}
          >
            Xoá file ngay
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm text-danger"
            disabled={!selectedEntries.length || deleteMutation.isPending}
            onClick={() => runDelete('project')}
          >
            Xoá hẳn project
          </button>
          {selectedEntries.length > 0 && (
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setSelected(new Set())}>
              Bỏ chọn
            </button>
          )}
          {deleteMutation.isPending && <span className="text-xs text-neutral-400">Đang xoá...</span>}
        </div>
        {resultMsg && <p className="text-sm text-neutral-300">{resultMsg}</p>}
        {deleteMutation.error && <p className="text-sm text-danger">{(deleteMutation.error as Error).message}</p>}
        {keepMutation.error && <p className="text-sm text-danger">{(keepMutation.error as Error).message}</p>}
        {allowMutation.error && <p className="text-sm text-danger">{(allowMutation.error as Error).message}</p>}
        {!rows.length ? (
          <p className="text-sm text-neutral-500">Không có video nào.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-neutral-500">
                <tr>
                  <th className="w-8 py-1.5 pr-2 font-normal">
                    <input
                      type="checkbox"
                      aria-label="Chọn tất cả"
                      checked={allChecked}
                      disabled={!selectableRows.length}
                      onChange={() =>
                        setSelected((prev) => {
                          const next = new Set(prev)
                          for (const e of selectableRows) {
                            if (allChecked) next.delete(e.project_id)
                            else next.add(e.project_id)
                          }
                          return next
                        })
                      }
                    />
                  </th>
                  <th className="py-1.5 pr-3 font-normal">Tự xoá lúc</th>
                  <th className="py-1.5 pr-3 font-normal">Dự án</th>
                  <th className="py-1.5 pr-3 font-normal">Video</th>
                  <th className="py-1.5 pr-3 font-normal">Lý do</th>
                  <th className="py-1.5 pr-3 text-right font-normal">Dung lượng</th>
                  <th className="py-1.5 font-normal" />
                </tr>
              </thead>
              <tbody>
                {rows.map((e) => {
                  const busy =
                    (keepMutation.isPending && keepMutation.variables?.projectId === e.project_id) ||
                    (allowMutation.isPending && allowMutation.variables?.projectId === e.project_id)
                  // Chỉ bật/tắt được với video không bị hệ thống tự bảo vệ
                  // (đang xử lý / sẵn sàng đăng / đang chạy 1 bước).
                  const canToggle = e.kept_by_user || !!e.due_at
                  return (
                    <tr key={e.project_id} className="border-t border-neutral-800 align-top">
                      <td className="py-2 pr-2">
                        <input
                          type="checkbox"
                          aria-label="Chọn"
                          checked={selected.has(e.project_id)}
                          disabled={!selectable(e)}
                          title={selectable(e) ? '' : `${e.protected_reason} — không chọn xoá được`}
                          onChange={() => toggle(e.project_id)}
                        />
                      </td>
                      <td className="mono py-2 pr-3 text-xs whitespace-nowrap">
                        {e.due_at ? (
                          new Date(e.due_at).getTime() <= Date.now() ? (
                            'Lượt dọn tới'
                          ) : (
                            fmtTime(e.due_at)
                          )
                        ) : (
                          <span className="text-neutral-500">Không xoá</span>
                        )}
                      </td>
                      <td className="py-2 pr-3 whitespace-nowrap">
                        <Link to={`/automated/${e.social_id}`} className="hover:text-accent-200">
                          {e.social_title}
                        </Link>
                      </td>
                      <td className="max-w-sm py-2 pr-3">
                        <Link to={`/projects/${e.project_id}`} className="line-clamp-2 text-neutral-300 hover:text-text">
                          {e.video_title || e.aweme_id}
                        </Link>
                      </td>
                      <td className="py-2 pr-3 text-xs text-neutral-400">{e.protected_reason ?? e.rule_label}</td>
                      <td className="mono py-2 pr-3 text-right text-xs">{e.size_mb} MB</td>
                      <td className="py-2 text-right whitespace-nowrap">
                        {e.can_release && (
                          <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => askAllowDelete(e)}>
                            Cho phép xoá
                          </button>
                        )}
                        {e.released && (
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            disabled={busy}
                            onClick={() => allowMutation.mutate({ projectId: e.project_id, allow: false })}
                          >
                            Không xoá nữa
                          </button>
                        )}
                        {canToggle && !e.released && (
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            disabled={busy}
                            onClick={() => keepMutation.mutate({ projectId: e.project_id, keep: !e.kept_by_user })}
                          >
                            {e.kept_by_user ? 'Cho phép tự xoá' : 'Giữ lại'}
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
