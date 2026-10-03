import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type ModelSetupItem, type ModelSetupState } from '../lib/api'
import { primaryButtonClass, secondaryButtonClass } from '../lib/ui'
import { useIsAdmin } from '../lib/license'

function formatMb(mb: number): string {
  return mb >= 1024 ? `${(mb / 1024).toFixed(1).replace('.', ',')} GB` : `${Math.round(mb)} MB`
}

function formatEta(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return ''
  if (seconds < 60) return `còn ~${Math.ceil(seconds)} giây`
  if (seconds < 3600) return `còn ~${Math.ceil(seconds / 60)} phút`
  return `còn ~${(seconds / 3600).toFixed(1)} giờ`
}

function ProgressBar({ percent }: { percent: number }) {
  return (
    <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded bg-neutral-700">
      <div className="h-full bg-accent-300 transition-[width] duration-700" style={{ width: `${percent}%` }} />
    </div>
  )
}

function ItemStatus({ item }: { item: ModelSetupItem }) {
  if (item.status === 'downloading') {
    const percent = item.total_mb ? Math.min(99, (item.downloaded_mb / item.total_mb) * 100) : 0
    const remainingMb = Math.max(0, item.total_mb - item.downloaded_mb)
    const eta = item.speed_mbps > 0.01 ? formatEta(remainingMb / item.speed_mbps) : ''
    return (
      <div className="text-xs text-accent-300">
        Đang tải {formatMb(item.downloaded_mb)} / ~{formatMb(item.total_mb)} · {Math.floor(percent)}%
        {item.speed_mbps > 0.01 ? ` · ${item.speed_mbps.toFixed(1)} MB/s` : ' · đang chờ dữ liệu...'}
        {eta && ` · ${eta}`}
        <ProgressBar percent={percent} />
      </div>
    )
  }
  if (item.status === 'done') return <div className="text-xs text-accent-300">Đã có · {formatMb(item.total_mb)}</div>
  if (item.status === 'queued') return <div className="text-xs text-neutral-400">Chờ tới lượt tải</div>
  if (item.external_download) {
    return (
      <div className="text-xs text-accent-300">
        Đang được tải bởi 1 dự án đang chạy · đã có {formatMb(item.external_mb)} / ~{formatMb(item.total_mb)}
      </div>
    )
  }
  if (item.status === 'error') return <div className="break-words text-xs text-danger">{item.error}</div>
  if (item.status === 'cancelled') return <div className="text-xs text-neutral-400">Đã huỷ · chưa có</div>
  return <div className="text-xs text-neutral-400">Chưa có · cần tải ~{formatMb(item.total_mb)}</div>
}

/** Khung "Model AI" ở trang Kiểm tra hệ thống: tải trước model + hiện tiến độ từng cái. */
export default function ModelSetupPanel() {
  const queryClient = useQueryClient()
  const isAdmin = useIsAdmin()
  const query = useQuery({
    queryKey: ['system-models'],
    queryFn: api.systemModels,
    // Đang tải (bởi nút này hoặc bởi dự án) thì cập nhật nhanh, còn lại thưa cho nhẹ.
    refetchInterval: (q) => {
      const data = q.state.data as ModelSetupState | undefined
      return data?.busy || data?.items.some((i) => i.external_download) ? 1500 : 10000
    },
  })
  const items = query.data?.items ?? []
  const missing = items.filter((i) => !i.installed)
  const [selected, setSelected] = useState<Set<string> | null>(null)

  // Mặc định chọn hết những model chưa có (lần đầu có dữ liệu).
  useEffect(() => {
    if (selected === null && query.data) setSelected(new Set(query.data.items.filter((i) => !i.installed).map((i) => i.id)))
  }, [query.data, selected])

  const setData = (data: ModelSetupState) => queryClient.setQueryData(['system-models'], data)
  const install = useMutation({ mutationFn: api.installModels, onSuccess: setData })
  const cancel = useMutation({ mutationFn: api.cancelModels, onSuccess: setData })

  const chosen = missing.filter((i) => selected?.has(i.id) && i.status !== 'downloading' && i.status !== 'queued')
  const chosenMb = chosen.reduce((sum, i) => sum + i.total_mb, 0)
  const busy = query.data?.busy ?? false
  const freeGb = query.data?.free_gb ?? 0
  // Chưa có dữ liệu thì freeGb = 0 — không được báo "không đủ chỗ" lúc đang tải trang.
  const notEnoughDisk = !!query.data && chosenMb > 0 && chosenMb / 1024 > freeGb - 2

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev ?? [])
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg">Dữ liệu AI</h2>
        <div className="flex flex-wrap items-center gap-2">
          {busy && (
            <button type="button" className={secondaryButtonClass} disabled={cancel.isPending} onClick={() => cancel.mutate()}>
              Huỷ tải
            </button>
          )}
          {missing.length > 0 && (!busy || chosen.length > 0) && (
            <button
              type="button"
              className={primaryButtonClass}
              disabled={!chosen.length || install.isPending || notEnoughDisk}
              onClick={() => install.mutate(chosen.map((i) => i.id))}
            >
              {busy ? 'Thêm vào hàng đợi' : 'Cài đặt môi trường'}
              {chosen.length > 0 && ` (${chosen.length} mục · ~${formatMb(chosenMb)})`}
            </button>
          )}
        </div>
      </div>
      {query.data && (
      <p className="text-xs text-neutral-500">
        {missing.length
          ? 'App tự tải ở lần dùng đầu, nhưng tải trước ở đây thì chạy dự án không phải chờ và thấy rõ tiến độ. Bỏ chọn mục không dùng tới.'
          : 'Đã có đủ.'}{' '}
        {isAdmin && <>Lưu ở {query.data?.models_dir} · </>}Ổ còn trống {freeGb.toLocaleString('vi-VN')} GB
        {notEnoughDisk && <span className="text-danger"> — không đủ chỗ cho các mục đã chọn</span>}
      </p>
      )}
      {install.error && <p className="text-xs text-danger">{(install.error as Error).message}</p>}
      <ul className="divide-y divide-divider rounded-md border border-divider">
        {items.map((item) => {
          const canPick = !item.installed && item.status !== 'downloading' && item.status !== 'queued'
          return (
            <li key={item.id} className="flex gap-3 px-4 py-3 text-sm">
              <span className="w-4 shrink-0 pt-0.5">
                {item.installed ? (
                  <span className="font-medium text-accent-300">✓</span>
                ) : item.status === 'downloading' || item.status === 'queued' || item.external_download ? (
                  <span className="inline-block animate-spin text-accent-300">⟳</span>
                ) : (
                  <input
                    type="checkbox"
                    aria-label={`Chọn tải ${item.label}`}
                    checked={selected?.has(item.id) ?? false}
                    disabled={!canPick}
                    onChange={() => toggle(item.id)}
                  />
                )}
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-text">{item.label}</div>
                <div className="text-xs text-neutral-500">{item.purpose}</div>
                <ItemStatus item={item} />
                {isAdmin && (
                  <div className="mono mt-0.5 break-all text-[11px] text-neutral-600">
                    {item.tech}
                    {item.error_tech && ` · lỗi: ${item.error_tech}`}
                  </div>
                )}
              </div>
              {item.status === 'error' && (
                <button
                  type="button"
                  className={`${secondaryButtonClass} self-start`}
                  onClick={() => install.mutate([item.id])}
                >
                  Thử lại
                </button>
              )}
            </li>
          )
        })}
        {query.isLoading && <li className="px-4 py-3 text-sm text-neutral-500">Đang kiểm tra...</li>}
      </ul>
    </section>
  )
}
