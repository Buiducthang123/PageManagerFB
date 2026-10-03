import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { secondaryButtonClass } from '../lib/ui'

const PROJECT_LOG = 'du-an/pipeline.jsonl'

const LOG_TITLE: Record<string, string> = { [PROJECT_LOG]: 'Dự án này' }

const LOG_HINT: Record<string, string> = {
  [PROJECT_LOG]: 'Từng bước của dự án này — bước nào lỗi sẽ hiện ở đây',
  'app.log': 'Log chính của app',
  'launcher.log': 'Lúc nào app bật / tắt / tự bật lại',
  'crash.log': 'Chỉ có nội dung khi app crash',
  'heartbeat.log': 'RAM + việc đang chạy, 30 giây 1 dòng',
}

// "Lỗi:" có dấu hai chấm — log dự án ghi lỗi dạng "transcribe · Lỗi: ..."; không bắt chữ "lỗi" trần (vd "Video lỗi").
const ERROR_RE = /\b(ERROR|CRITICAL|Traceback|Exception|Fatal Python error|Windows fatal exception)\b|Lỗi:|thất bại/
const WARN_RE = /\bWARNING\b/

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** Khung xem log ngay trong app — đỡ phải mở file txt trong thư mục cài.
 * projectId: mở từ trang dự án → có thêm tab log của dự án đó, mở sẵn tab này. */
export default function LogViewer({ onClose, projectId }: { onClose: () => void; projectId?: string }) {
  const listQuery = useQuery({
    queryKey: ['system-logs', projectId ?? ''],
    queryFn: () => api.systemLogs(projectId),
    refetchInterval: 5000,
  })
  const logs = listQuery.data?.logs ?? []
  const [name, setName] = useState(projectId ? PROJECT_LOG : 'app.log')
  const [onlyErrors, setOnlyErrors] = useState(false)
  const [follow, setFollow] = useState(true)
  const [copied, setCopied] = useState(false)
  const boxRef = useRef<HTMLPreElement>(null)

  useEffect(() => {
    if (logs.length && !logs.some((l) => l.name === name)) setName(logs[0].name)
  }, [logs, name])

  const logQuery = useQuery({
    queryKey: ['system-log', name, projectId ?? ''],
    queryFn: () => api.systemLog(name, 1000, projectId),
    enabled: logs.some((l) => l.name === name),
    refetchInterval: follow ? 3000 : false,
  })
  const text = logQuery.data?.text ?? ''
  const lines = text ? text.split('\n') : []
  const shown = onlyErrors ? lines.filter((l) => ERROR_RE.test(l) || /^\s+(File |\^)/.test(l)) : lines

  useEffect(() => {
    if (follow && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight
  }, [text, follow, onlyErrors])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={onClose}>
      <div
        className="flex h-[85vh] w-full max-w-5xl flex-col rounded-md border border-divider bg-surface"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex flex-wrap items-center gap-2 border-b border-divider px-4 py-3">
          <h2 className="mr-2 text-lg">Log</h2>
          {logs.map((l) => (
            <button
              key={l.name}
              type="button"
              title={LOG_HINT[l.name] ?? ''}
              onClick={() => setName(l.name)}
              className={`rounded px-2 py-1 text-xs ${
                l.name === name ? 'bg-accent-300/20 text-accent-300' : 'text-neutral-400 hover:text-text'
              }`}
            >
              {LOG_TITLE[l.name] ?? l.name}
              {l.size > 0 && <span className="text-neutral-500"> · {formatSize(l.size)}</span>}
            </button>
          ))}
          <div className="ml-auto flex flex-wrap items-center gap-3 text-xs text-neutral-400">
            <label className="flex items-center gap-1">
              <input type="checkbox" checked={onlyErrors} onChange={(e) => setOnlyErrors(e.target.checked)} />
              Chỉ dòng lỗi
            </label>
            <label className="flex items-center gap-1">
              <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
              Tự cập nhật
            </label>
            <button
              type="button"
              className={secondaryButtonClass}
              onClick={() => {
                void navigator.clipboard.writeText(shown.join('\n')).then(() => {
                  setCopied(true)
                  setTimeout(() => setCopied(false), 1500)
                })
              }}
            >
              {copied ? 'Đã copy' : 'Copy'}
            </button>
            <button type="button" className={secondaryButtonClass} onClick={() => void logQuery.refetch()}>
              Làm mới
            </button>
            <button type="button" className={secondaryButtonClass} onClick={onClose}>
              Đóng
            </button>
          </div>
        </div>
        {LOG_HINT[name] && <div className="px-4 pt-2 text-xs text-neutral-500">{LOG_HINT[name]}</div>}
        <pre
          ref={boxRef}
          className="m-0 min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-all px-4 py-2 font-mono text-[11px] leading-relaxed"
        >
          {!logs.length && !listQuery.isLoading && (
            <span className="text-neutral-500">Chưa có file log nào.</span>
          )}
          {logQuery.error && <span className="text-danger">{(logQuery.error as Error).message}</span>}
          {shown.map((line, i) => (
            <div
              key={i}
              className={ERROR_RE.test(line) ? 'text-danger' : WARN_RE.test(line) ? 'text-amber-300' : 'text-neutral-300'}
            >
              {line || ' '}
            </div>
          ))}
          {onlyErrors && lines.length > 0 && shown.length === 0 && (
            <span className="text-accent-300">Không có dòng lỗi nào.</span>
          )}
        </pre>
      </div>
    </div>
  )
}
