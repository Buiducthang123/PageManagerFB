import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

const PING_MS = 4000
const PING_TIMEOUT_MS = 3000
// 2 lần liền không trả lời mới báo — 1 request chậm lúc backend đang bận không tính.
const FAILS_BEFORE_SHOW = 2

async function ping(): Promise<boolean> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), PING_TIMEOUT_MS)
  try {
    const res = await fetch('/api/license/status', { signal: ctrl.signal, cache: 'no-store' })
    return res.ok
  } catch {
    return false
  } finally {
    clearTimeout(timer)
  }
}

/** Backend chết (crash, bị tắt) lúc tab đang mở: hiện thông báo + tự nối lại khi
 * launcher bật lại backend, thay vì để user bấm F5 ra trang lỗi của Chrome.
 * Màn "Đã tắt app" (nút Tắt app, z-[60]) nằm đè lên lớp này. */
export default function ConnectionLostOverlay() {
  const queryClient = useQueryClient()
  const [lostSince, setLostSince] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const fails = useRef(0)

  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    const loop = async () => {
      const ok = await ping()
      if (stopped) return
      if (ok) {
        if (fails.current >= FAILS_BEFORE_SHOW) {
          // Vừa nối lại: dữ liệu trên màn hình có thể đã cũ (job bị cắt ngang...) → tải lại hết.
          void queryClient.invalidateQueries()
        }
        fails.current = 0
        setLostSince(null)
      } else {
        fails.current += 1
        if (fails.current === FAILS_BEFORE_SHOW) setLostSince(Date.now())
      }
      timer = setTimeout(loop, fails.current ? 2000 : PING_MS)
    }
    timer = setTimeout(loop, PING_MS)
    return () => {
      stopped = true
      clearTimeout(timer)
    }
  }, [queryClient])

  useEffect(() => {
    if (lostSince === null) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [lostSince])

  if (lostSince === null) return null
  const seconds = Math.max(0, Math.round((now - lostSince) / 1000))
  const longOutage = seconds > 90

  return (
    <div className="fixed inset-0 z-[55] flex items-center justify-center bg-black/70 px-4 text-center text-text">
      <div className="max-w-md space-y-3 rounded-md border border-divider bg-surface p-6">
        <div className="inline-block animate-spin text-2xl text-accent-300">⟳</div>
        <h2 className="text-lg">Mất kết nối với app</h2>
        <p className="text-sm text-neutral-400">
          {longOutage
            ? 'App vẫn chưa chạy lại. Có thể app đã bị tắt hẳn — mở lại bằng shortcut OddlyLab Reup ngoài Desktop, trang này sẽ tự nối lại.'
            : 'App vừa bị tắt đột ngột và đang tự khởi động lại — chờ một chút, trang sẽ tự nối lại.'}
        </p>
        <p className="text-xs text-neutral-500">
          Đang thử kết nối lại... {seconds}s · Việc đang chạy dở (nếu có) sẽ báo lỗi, bấm chạy lại bước đó.
        </p>
      </div>
    </div>
  )
}
