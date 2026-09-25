import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, type MonitorDailyStatus, type MonitorPublishEntry } from '../lib/api'

// Màn giám sát hàng đợi CHUNG của mọi dự án tự động: toàn hệ thống chỉ xử lý
// 1 video, đăng 1 bài và crawl 1 kênh tại 1 thời điểm (xem bộ lập lịch trong
// app/main.py). Trang này cho biết đang chạy gì, dự án nào tới lượt tiếp theo
// và lịch đăng/crawl của từng dự án.

const fmtTime = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' }) : '—'

const fmtDuration = (sec: number | null) => {
  if (!sec) return ''
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return m ? `${m}p${s.toString().padStart(2, '0')}` : `${s}s`
}

const DAILY_STATUS: Record<MonitorDailyStatus, { label: string; tag: string }> = {
  processing: { label: 'Đang xử lý', tag: 'tag-accent' },
  waiting: { label: 'Chờ tới lượt', tag: 'tag-outline' },
  prepared: { label: 'Đã chuẩn bị đủ', tag: 'tag-outline' },
  done_today: { label: 'Hoàn thành hôm nay', tag: 'tag-accent' },
  stopped_failed: { label: 'Dừng — có video lỗi', tag: 'tag-danger' },
  paused: { label: 'Tạm dừng', tag: 'tag-neutral' },
  resting: { label: 'Nghỉ API Douyin', tag: 'tag-neutral' },
  no_pending: { label: 'Hết video', tag: 'tag-neutral' },
}

const PUBLISH_STATUS: Record<MonitorPublishEntry['status'], { label: string; tag: string }> = {
  due: { label: 'Tới lượt đăng', tag: 'tag-accent' },
  done_today: { label: 'Đã đăng đủ hôm nay', tag: 'tag-accent' },
  scheduled: { label: 'Đã hẹn giờ', tag: 'tag-outline' },
  waiting_window: { label: 'Chờ khung giờ đăng', tag: 'tag-neutral' },
  no_ready: { label: 'Chưa có video sẵn sàng', tag: 'tag-neutral' },
}

function ProjectLink({ id, title }: { id: string; title: string }) {
  return (
    <Link to={`/automated/${id}`} className="text-text hover:text-accent-200">
      {title}
    </Link>
  )
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="card px-4 py-3">
      <div className="text-xs text-neutral-500">{label}</div>
      <div className="mono mt-1 text-2xl">{value}</div>
    </div>
  )
}

export default function MonitorPage() {
  const query = useQuery({ queryKey: ['social-monitor'], queryFn: api.socialMonitor, refetchInterval: 5000 })
  const d = query.data

  if (query.isLoading || !d) {
    return (
      <div className="mx-auto max-w-6xl">
        <div className="skeleton h-24" />
      </div>
    )
  }

  const idle = !d.processing.length && !d.publishing.length && !d.crawling.length

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 className="text-2xl">Giám sát tiến trình</h1>
        <p className="mt-1 text-sm text-neutral-500">
          Toàn hệ thống xử lý lần lượt 1 video, đăng 1 bài và crawl 1 kênh tại 1 thời điểm. Khung giờ đăng:{' '}
          {d.posting_windows.join(', ')} — {d.in_posting_window ? 'đang trong khung giờ đăng' : 'hiện ngoài khung giờ đăng'}.
          Tự làm mới mỗi 5 giây.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <Stat label="Dự án đang chạy" value={d.stats.projects_active} />
        <Stat label="Video chờ xử lý" value={d.stats.pending} />
        <Stat label="Đang xử lý" value={d.stats.processing} />
        <Stat label="Sẵn sàng đăng" value={d.stats.ready} />
        <Stat label="Đã đăng hôm nay" value={d.stats.posted_today} />
        <Stat label="Lỗi" value={d.stats.failed} />
      </div>

      <section className={`card space-y-1 p-4 ${d.storage.low_disk ? 'border-danger-300' : ''}`}>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-neutral-300">Ổ đĩa còn trống</span>
          <span className="mono">{d.storage.free_gb} GB</span>
          {d.storage.low_disk ? (
            <span className="tag tag-danger">Dưới {d.storage.min_free_gb} GB — đã tạm ngừng xử lý video mới</span>
          ) : (
            <span className="tag tag-outline">An toàn (ngưỡng {d.storage.min_free_gb} GB)</span>
          )}
        </div>
        <p className="text-xs text-neutral-500">
          Tự dọn: xoá video gốc, video thành phẩm và audio {d.storage.cleanup_after_hours} giờ sau khi đăng (và với video
          lỗi, bỏ qua, bản xử lý cũ không hoạt động {d.storage.cleanup_after_hours} giờ); dọn bộ nhớ đệm Chrome mỗi ngày.
          {d.storage.last_cleanup &&
            ` Lần dọn gần nhất ${fmtTime(d.storage.last_cleanup.at)}: ${d.storage.last_cleanup.projects_cleaned} project, giải phóng ${d.storage.last_cleanup.freed_mb} MB.`}
        </p>
        <p className="text-xs">
          <Link to="/cleanup" className="text-accent-300 hover:underline">
            Sắp tự xoá {d.storage.plan_summary.due_count} project ({d.storage.plan_summary.due_mb} MB), được giữ lại{' '}
            {d.storage.plan_summary.kept_count} — xem chi tiết và chọn giữ lại ở trang Tự dọn ổ đĩa →
          </Link>
        </p>
      </section>

      <section className="card space-y-3 p-4">
        <h2 className="text-lg">Đang chạy</h2>
        {idle && <p className="text-sm text-neutral-500">Không có tiến trình nào đang chạy.</p>}
        {d.processing.map((p) => {
          const pct = p.progress && p.progress.total > 0 ? Math.min(100, (p.progress.done / p.progress.total) * 100) : null
          return (
            <div key={`${p.social_id}:${p.aweme_id}`} className="space-y-1.5">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="tag tag-accent">Xử lý video</span>
                <ProjectLink id={p.social_id} title={p.social_title} />
                <span className="truncate text-neutral-400">{p.video_title.slice(0, 70)}</span>
                {p.duration_sec ? <span className="mono text-xs text-neutral-500">{fmtDuration(p.duration_sec)}</span> : null}
                {p.project_id && (
                  <Link to={`/projects/${p.project_id}`} className="ml-auto text-xs text-neutral-400 hover:text-text">
                    Mở pipeline →
                  </Link>
                )}
              </div>
              <div className="text-xs text-neutral-400">
                Bước: {p.stage_label}
                {p.progress?.label ? ` — ${p.progress.label}` : ''}
              </div>
              {pct !== null && (
                <div className="h-1.5 overflow-hidden rounded bg-neutral-800">
                  <div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} />
                </div>
              )}
            </div>
          )
        })}
        {d.publishing.map((p) => (
          <div key={`pub:${p.social_id}`} className="flex flex-wrap items-center gap-2 text-sm">
            <span className="tag tag-accent">Đăng TikTok</span>
            <ProjectLink id={p.social_id} title={p.social_title} />
            <span className="text-xs text-neutral-400">{p.label}</span>
          </div>
        ))}
        {d.crawling.map((p) => (
          <div key={`crawl:${p.social_id}`} className="flex flex-wrap items-center gap-2 text-sm">
            <span className="tag tag-outline">Crawl Douyin</span>
            <ProjectLink id={p.social_id} title={p.social_title} />
            <span className="text-xs text-neutral-400">{p.label}</span>
          </div>
        ))}
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="card space-y-3 p-4">
          <div>
            <h2 className="text-lg">Lượt chạy hôm nay</h2>
            <p className="text-xs text-neutral-500">
              Chạy lần lượt theo thứ tự dự án: làm đủ số video hôm nay cho dự án trên rồi mới sang dự án dưới. Mọi dự án đủ
              rồi thì chuẩn bị trước cho ngày mai. Dự án có video lỗi sẽ dừng cho tới khi bạn thử lại hoặc bỏ qua video đó.
            </p>
          </div>
          {!d.daily_plan.length && <p className="text-sm text-neutral-500">Chưa có dự án tự động nào.</p>}
          <ol className="space-y-2.5">
            {d.daily_plan.map((e) => {
              const next = d.pipeline_queue[0]?.social_id === e.social_id
              return (
                <li key={e.social_id} className="flex items-start gap-3 text-sm">
                  <span className="mono mt-0.5 w-6 shrink-0 text-right text-neutral-500">{e.order}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <ProjectLink id={e.social_id} title={e.social_title} />
                      <span className={`tag ${DAILY_STATUS[e.status].tag}`}>{DAILY_STATUS[e.status].label}</span>
                      {next && e.status !== 'processing' && (
                        <span className="tag tag-accent">
                          {d.pipeline_queue[0]?.ahead ? 'Tiếp theo (chuẩn bị cho ngày mai)' : 'Tiếp theo'}
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-neutral-500">
                      Đã đăng {e.posted_today}/{e.posts_per_day} hôm nay · {e.ready_count} sẵn sàng · {e.pending_count} chờ
                      {e.failed_count ? ` · ${e.failed_count} lỗi` : ''}
                    </div>
                    {e.reason && <div className="text-xs text-neutral-400">{e.reason}</div>}
                  </div>
                </li>
              )
            })}
          </ol>
        </section>

        <section className="card space-y-3 p-4">
          <div>
            <h2 className="text-lg">Lịch đăng</h2>
            <p className="text-xs text-neutral-500">
              Video chưa kịp xử lý thì chờ tới khi sẵn sàng rồi đăng ở khung giờ gần nhất. Giờ hẹn tiếp theo tính từ lần
              đăng thật, không đăng bù dồn dập.
            </p>
          </div>
          {!d.publish_plan.length && <p className="text-sm text-neutral-500">Chưa có dự án nào đang chạy.</p>}
          <ul className="space-y-2">
            {d.publish_plan.map((e) => (
              <li key={e.social_id} className="flex flex-wrap items-center gap-2 text-sm">
                <span className="mono w-24 shrink-0 text-xs text-neutral-400">{fmtTime(e.next_post_at)}</span>
                <ProjectLink id={e.social_id} title={e.social_title} />
                <span className={`tag ${PUBLISH_STATUS[e.status].tag}`}>{PUBLISH_STATUS[e.status].label}</span>
                <span className="text-xs text-neutral-500">
                  {e.ready_count} sẵn sàng · {e.posts_per_day} bài/ngày
                </span>
              </li>
            ))}
          </ul>

          <div className="border-t border-neutral-800 pt-3">
            <h3 className="mb-2 text-sm text-neutral-300">Lịch crawl</h3>
            <ul className="space-y-1.5">
              {d.crawl_plan.map((e) => (
                <li key={e.social_id} className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="mono w-24 shrink-0 text-xs text-neutral-400">
                    {e.next_crawl_at ? fmtTime(e.next_crawl_at) : 'Ngay'}
                  </span>
                  <ProjectLink id={e.social_id} title={e.social_title} />
                  {!e.active && <span className="tag tag-neutral">Tạm dừng</span>}
                  <span className="text-xs text-neutral-500">lần cuối {fmtTime(e.last_crawl_at)}</span>
                </li>
              ))}
            </ul>
          </div>
        </section>
      </div>
    </div>
  )
}
