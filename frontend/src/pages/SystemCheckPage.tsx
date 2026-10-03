import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, type CapcutDirStatus } from '../lib/api'
import { secondaryButtonClass } from '../lib/ui'
import LogViewer from '../components/LogViewer'
import ModelSetupPanel from '../components/ModelSetupPanel'
import { useIsAdmin } from '../lib/license'

const STATUS_CLASS: Record<CapcutDirStatus, string> = {
  ok: 'text-accent-300',
  warning: 'text-neutral-300',
  error: 'text-danger',
}

const STATUS_ICON: Record<CapcutDirStatus, string> = { ok: '✓', warning: '!', error: '✗' }

export default function SystemCheckPage() {
  const checkQuery = useQuery({ queryKey: ['system-check'], queryFn: api.systemCheck })
  const [geminiResult, setGeminiResult] = useState<{ status: CapcutDirStatus; message: string } | null>(null)
  const geminiMutation = useMutation({
    mutationFn: () => api.testGeminiKey(''),
    onSuccess: setGeminiResult,
    onError: (err) => setGeminiResult({ status: 'error', message: (err as Error).message }),
  })

  const [showLogs, setShowLogs] = useState(false)
  const isAdmin = useIsAdmin()

  const items = checkQuery.data?.items ?? []
  const errors = items.filter((i) => i.status === 'error').length
  const warnings = items.filter((i) => i.status === 'warning').length

  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl">Kiểm tra hệ thống</h1>
        <div className="flex flex-wrap gap-2">
        <button type="button" className={secondaryButtonClass} onClick={() => setShowLogs(true)}>
          Xem log
        </button>
        <a
          href="/api/system/diagnostics"
          download
          className={secondaryButtonClass}
          title="Tải file zip gồm log + cấu hình (đã che key/token) để gửi admin khi gặp lỗi"
        >
          Xuất log chẩn đoán
        </a>
        <button
          type="button"
          className={secondaryButtonClass}
          disabled={checkQuery.isFetching}
          onClick={() => {
            setGeminiResult(null)
            void checkQuery.refetch()
          }}
        >
          {checkQuery.isFetching ? 'Đang kiểm tra...' : 'Kiểm tra lại'}
        </button>
        </div>
      </div>
      <p className="text-sm text-neutral-400">
        Những thứ cần có trên máy này để tool chạy đủ chức năng. Mục ✗ cần sửa trước; mục ! vẫn dùng được nhưng nên xem
        lại. Phần lớn chỉnh ở <Link to="/settings">Cài đặt</Link>. Gặp lỗi thì bấm <b>Xuất log chẩn đoán</b> và gửi
        file cho admin — file đã che key, token, mật khẩu.
      </p>

      {checkQuery.error && <p className="text-sm text-danger">{(checkQuery.error as Error).message}</p>}

      {items.length > 0 && (
        <p className={`text-sm ${errors ? 'text-danger' : warnings ? 'text-neutral-300' : 'text-accent-300'}`}>
          {errors
            ? `${errors} mục cần sửa, ${warnings} mục nên xem lại`
            : warnings
              ? `Dùng được — ${warnings} mục nên xem lại`
              : 'Mọi thứ đã sẵn sàng'}
        </p>
      )}

      <ul className="divide-y divide-divider rounded-md border border-divider">
        {items.map((item) => (
          <li key={item.id} className="flex gap-3 px-4 py-3 text-sm">
            <span className={`w-4 shrink-0 font-medium ${STATUS_CLASS[item.status]}`} aria-label={item.status}>
              {STATUS_ICON[item.status]}
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-text">{item.label}</div>
              <div className={`break-words text-xs ${STATUS_CLASS[item.status]}`}>{item.message}</div>
              {item.hint && <div className="mt-0.5 text-xs text-neutral-500">{item.hint}</div>}
              {isAdmin && item.tech && <div className="mono mt-0.5 break-all text-[11px] text-neutral-600">{item.tech}</div>}
              {item.link && (
                <Link to={item.link} className="mt-1 inline-block text-xs">
                  Mở Cài đặt →
                </Link>
              )}
              {item.id === 'gemini' && item.status !== 'error' && (
                <div className="mt-1.5 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    className={secondaryButtonClass}
                    disabled={geminiMutation.isPending}
                    onClick={() => geminiMutation.mutate()}
                  >
                    {geminiMutation.isPending ? 'Đang thử...' : 'Thử key'}
                  </button>
                  {geminiResult && (
                    <span className={`text-xs ${STATUS_CLASS[geminiResult.status]}`}>{geminiResult.message}</span>
                  )}
                </div>
              )}
            </div>
          </li>
        ))}
        {checkQuery.isLoading && <li className="px-4 py-3 text-sm text-neutral-500">Đang kiểm tra...</li>}
      </ul>
      <ModelSetupPanel />
      {showLogs && <LogViewer onClose={() => setShowLogs(false)} />}
    </div>
  )
}
