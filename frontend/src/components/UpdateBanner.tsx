import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { primaryButtonClass, secondaryButtonClass } from '../lib/ui'

/** Dải báo có bản mới (kế hoạch mục 12). `blocking` = đang ở màn "Cần cập
 * nhật" (bản quá cũ bị chặn) — luôn hiện nút, kể cả khi chưa kiểm tra xong. */
export default function UpdateBanner({ blocking = false }: { blocking?: boolean }) {
  const query = useQuery({
    queryKey: ['update-status'],
    queryFn: api.updateStatus,
    refetchInterval: (q) =>
      q.state.data?.status === 'downloading' || q.state.data?.status === 'installing' ? 1500 : 30 * 60_000,
  })
  const download = useMutation({ mutationFn: api.updateDownload, onSuccess: () => void query.refetch() })
  const restart = useMutation({
    mutationFn: api.updateRestart,
    onSuccess: () => {
      // Backend thoát, launcher chạy bản mới ở cùng cổng — chờ rồi tải lại trang.
      setTimeout(() => window.location.reload(), 8000)
    },
  })
  const s = query.data
  if (!s) return null
  const busy = s.status === 'downloading' || s.status === 'installing'
  if (!s.available && !busy && s.status !== 'ready' && s.status !== 'error') {
    return blocking && s.check_error ? (
      <p className="text-xs text-danger">Không kiểm tra được bản mới: {s.check_error}</p>
    ) : null
  }

  return (
    <div
      className={
        blocking
          ? 'space-y-2 text-sm'
          : 'flex flex-wrap items-center justify-center gap-3 border-b border-accent/40 bg-accent-900/30 px-4 py-1.5 text-xs text-accent-200'
      }
    >
      {s.status === 'ready' ? (
        <>
          <span>{s.message}</span>
          <button
            type="button"
            className={blocking ? primaryButtonClass : secondaryButtonClass}
            disabled={restart.isPending || restart.isSuccess}
            onClick={() => restart.mutate()}
          >
            {restart.isSuccess ? 'Đang khởi động lại…' : 'Khởi động lại'}
          </button>
        </>
      ) : busy ? (
        <span>
          {s.message} {s.status === 'downloading' && `${s.progress}%`}
        </span>
      ) : (
        <>
          <span>
            Có bản mới {s.latest}
            {s.notes ? ` — ${s.notes}` : ''}
          </span>
          {!s.packaged ? (
            <span className="text-neutral-400">(chạy từ source — cập nhật bằng git)</span>
          ) : s.runtime_ok === false ? (
            <span className="text-danger">Cần cài lại bộ cài mới (runtime {s.min_runtime})</span>
          ) : (
            <button
              type="button"
              className={blocking ? primaryButtonClass : secondaryButtonClass}
              disabled={download.isPending}
              onClick={() => download.mutate()}
            >
              Cập nhật
            </button>
          )}
        </>
      )}
      {s.status === 'error' && <span className="text-danger">{s.message}</span>}
      {(download.error || restart.error) && (
        <span className="text-danger">{((download.error || restart.error) as Error).message}</span>
      )}
    </div>
  )
}
