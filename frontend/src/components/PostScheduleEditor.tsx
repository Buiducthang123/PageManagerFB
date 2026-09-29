import { useEffect, useState } from 'react'
import { inputClass } from '../lib/ui'

// Lịch đăng của 1 nền tảng trong dự án tự động: bật/tắt, số bài/ngày (chế độ
// tự động — giờ ngẫu nhiên trong khung cao điểm) hoặc danh sách giờ cố định.
// Giờ cố định có giá trị thì số bài/ngày = số mốc giờ (backend tự tính).

export default function PostScheduleEditor({
  platformLabel,
  enabled,
  canEnable,
  disabledReason,
  postsPerDay,
  maxPostsPerDay,
  postTimes,
  jitterMin,
  nextPostAt,
  saving,
  onToggle,
  onSavePostsPerDay,
  onSaveTimes,
}: {
  platformLabel: string
  enabled: boolean
  canEnable: boolean
  disabledReason?: string
  postsPerDay: number
  maxPostsPerDay: number
  postTimes: string[]
  jitterMin: number
  nextPostAt: string | null
  saving: boolean
  onToggle: (v: boolean) => void
  onSavePostsPerDay: (n: number) => void
  onSaveTimes: (times: string[]) => void
}) {
  const fixed = postTimes.length > 0
  const [mode, setMode] = useState<'auto' | 'fixed'>(fixed ? 'fixed' : 'auto')
  const [timesText, setTimesText] = useState(postTimes.join(', '))
  useEffect(() => {
    setTimesText(postTimes.join(', '))
    if (postTimes.length) setMode('fixed')
  }, [postTimes.join(',')]) // eslint-disable-line react-hooks/exhaustive-deps

  const parseTimes = (text: string) =>
    text
      .split(/[,\s;]+/)
      .map((t) => t.trim())
      .filter(Boolean)

  const next = nextPostAt ? new Date(nextPostAt) : null

  return (
    <div className="space-y-2 rounded-lg border border-neutral-800 p-3">
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-neutral-200">
          <input
            type="checkbox"
            checked={enabled}
            disabled={saving || (!enabled && !canEnable)}
            onChange={(e) => onToggle(e.target.checked)}
          />
          Tự đăng lên {platformLabel}
        </label>
        {!enabled && !canEnable && disabledReason && <span className="text-xs text-neutral-500">{disabledReason}</span>}
      </div>
      {enabled && (
        <>
          <div className="flex flex-wrap items-center gap-3 text-sm text-neutral-300">
            <label className="flex items-center gap-1.5">
              <input
                type="radio"
                checked={mode === 'auto'}
                disabled={saving}
                onChange={() => {
                  setMode('auto')
                  if (fixed) onSaveTimes([])
                }}
              />
              Giờ tự động
            </label>
            <label className="flex items-center gap-1.5">
              <input type="radio" checked={mode === 'fixed'} disabled={saving} onChange={() => setMode('fixed')} />
              Giờ cố định
            </label>
          </div>
          {mode === 'auto' ? (
            <label className="flex flex-wrap items-center gap-2 text-sm text-neutral-300">
              Số video/ngày (tối đa {maxPostsPerDay})
              <input
                type="number"
                min={1}
                max={maxPostsPerDay}
                className={`${inputClass} mt-0 w-20`}
                defaultValue={postsPerDay}
                key={postsPerDay}
                onBlur={(e) => {
                  const n = Math.min(maxPostsPerDay, Math.max(1, Number(e.target.value) || 1))
                  e.target.value = String(n)
                  if (n !== postsPerDay) onSavePostsPerDay(n)
                }}
              />
              <span className="text-xs text-neutral-500">giờ ngẫu nhiên trong khung 7-9h, 12-13h, 19-22h</span>
            </label>
          ) : (
            <label className="block text-sm text-neutral-300">
              Các giờ đăng mỗi ngày (cách nhau bằng dấu phẩy)
              <input
                className={`${inputClass} mt-1`}
                value={timesText}
                placeholder="vd: 10:00, 19:30"
                onChange={(e) => setTimesText(e.target.value)}
                onBlur={() => {
                  const times = parseTimes(timesText)
                  if (times.join(',') !== postTimes.join(',')) onSaveTimes(times)
                }}
              />
              <span className="mt-1 block text-xs text-neutral-500">
                {postTimes.length
                  ? `${postTimes.length} video/ngày, lệch ngẫu nhiên ±${jitterMin} phút quanh mỗi mốc. Lỡ mốc quá 90 phút (chưa có video sẵn sàng, máy tắt...) thì chờ mốc sau.`
                  : 'Nhập giờ rồi bấm ra ngoài ô để lưu.'}
              </span>
            </label>
          )}
          <p className="text-xs text-neutral-500">
            {next
              ? next.getTime() <= Date.now()
                ? 'Đã tới giờ đăng — bộ lập lịch sẽ đăng video cũ nhất sẵn sàng trong ít phút tới.'
                : `Lần đăng kế tiếp: ${next.toLocaleString('vi-VN')}`
              : fixed
                ? 'Đang tính giờ đăng kế tiếp...'
                : 'Chưa đăng bài nào — sẽ đăng ngay khi có video sẵn sàng trong khung giờ.'}
          </p>
        </>
      )}
    </div>
  )
}
