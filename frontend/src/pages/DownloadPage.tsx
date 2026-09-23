import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type DownloadItem, type DownloadMode, type DownloadVideoInfo } from '../lib/api'
import { inputClass, primaryButtonClass, secondaryButtonClass } from '../lib/ui'
import ConfirmDialog from '../components/ConfirmDialog'
import JobProgressBar from '../components/JobProgressBar'

const STATUS_LABEL: Record<DownloadItem['status'], string> = {
  pending: 'chờ',
  running: 'đang tải',
  done: 'xong',
  failed: 'lỗi',
}

const MODE_LABEL: Record<DownloadMode, string> = {
  single: '1 video',
  profile: 'Trang cá nhân',
  search: 'Từ khoá',
  info: 'Lấy thông tin (không tải)',
}

const PROFILE_MODES = [
  { id: 'post', label: 'Đã đăng' },
  { id: 'like', label: 'Đã thích' },
  { id: 'mix', label: 'Playlist/bộ sưu tập' },
  { id: 'music', label: 'Nhạc đã dùng' },
]

const IMAGE_EXTS = new Set(['.jpg', '.jpeg', '.png', '.webp'])

function CopyLinkButton({ text, label = 'Chép link' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      className="btn btn-ghost btn-sm shrink-0"
      disabled={!text}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text)
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        } catch {
          /* clipboard không khả dụng (vd http không secure) — im lặng bỏ qua */
        }
      }}
    >
      {copied ? 'Đã chép' : label}
    </button>
  )
}

function VideoInfoPanel({ info }: { info: DownloadVideoInfo }) {
  return (
    <div className="min-w-0 flex-1 space-y-1.5 text-xs">
      {info.caption && <p className="text-neutral-300">{info.caption}</p>}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-neutral-500">
        {info.author && <span>{info.author}</span>}
        {info.duration_sec > 0 && <span>{info.duration_sec.toFixed(1)}s</span>}
        {info.likes != null && <span>❤ {info.likes.toLocaleString()}</span>}
        {info.comments != null && <span>💬 {info.comments.toLocaleString()}</span>}
        {info.shares != null && <span>↗ {info.shares.toLocaleString()}</span>}
      </div>
      {info.share_url && (
        <div className="flex items-center gap-2">
          <span className="mono min-w-0 flex-1 truncate text-neutral-600">{info.share_url}</span>
          <CopyLinkButton text={info.share_url} />
        </div>
      )}
      {info.play_url && (
        <div className="flex items-center gap-2">
          <span className="mono min-w-0 flex-1 truncate text-accent-400" title="Link tải trực tiếp (CDN) — có thể hết hạn sau vài giờ, nên tải sớm">
            {info.play_url}
          </span>
          <CopyLinkButton text={info.play_url} />
        </div>
      )}
    </div>
  )
}

function InfoOnlyRow({ info }: { info: DownloadVideoInfo }) {
  return (
    <div className="flex gap-3 rounded-lg border border-neutral-800 p-2">
      <div className="aspect-video w-40 shrink-0 overflow-hidden rounded-md bg-black">
        {info.thumb_url && <img className="h-full w-full object-cover" src={info.thumb_url} loading="lazy" />}
      </div>
      <VideoInfoPanel info={info} />
    </div>
  )
}

function FilePreview({ downloadId, file, info }: { downloadId: string; file: string; info?: DownloadVideoInfo | null }) {
  const name = file.split(/[/\\]/).pop() ?? file
  const ext = name.slice(name.lastIndexOf('.')).toLowerCase()
  const url = api.downloadFileUrl(downloadId, file)
  return (
    <div className="flex gap-3 rounded-lg border border-neutral-800 p-2">
      <div className="w-40 shrink-0 space-y-1">
        {IMAGE_EXTS.has(ext) ? (
          <img className="aspect-video w-full rounded-md bg-black object-cover" src={url} loading="lazy" />
        ) : (
          <video className="aspect-video w-full rounded-md bg-black" src={url} controls preload="metadata" />
        )}
        <a className="block truncate text-xs text-accent-300" href={url} download title={name}>
          {name}
        </a>
      </div>
      {info && <VideoInfoPanel info={info} />}
    </div>
  )
}

function DownloadRow({ item }: { item: DownloadItem }) {
  const queryClient = useQueryClient()
  const [deleting, setDeleting] = useState(false)

  // Bug đã gặp thật: chỉ bật polling khi item.status === 'running' thì không
  // bao giờ BẮT ĐẦU polling được, vì `item` đến từ query ['downloads'] chỉ
  // fetch 1 lần lúc tạo (lúc đó status còn "pending") — không có gì kích hoạt
  // theo dõi job để phát hiện lúc nó chuyển sang "running", nên UI kẹt ở
  // "chờ" mãi dù job thật đã chạy/xong từ lâu. Phải bật polling ngay từ khi
  // còn "pending", không chỉ đợi "running".
  const jobQuery = useQuery({
    queryKey: ['download-job', item.download_id],
    queryFn: () => api.downloadJobStatus(item.download_id),
    enabled: item.status === 'pending' || item.status === 'running',
    refetchInterval: (q) => (q.state.data?.status === 'running' || !q.state.data ? 1000 : false),
  })

  const prevJobStatus = useRef<string | null>(null)
  const currentJobStatus = jobQuery.data?.status ?? null
  if (
    currentJobStatus &&
    currentJobStatus !== prevJobStatus.current &&
    (currentJobStatus === 'running' || currentJobStatus === 'done' || currentJobStatus === 'failed' || currentJobStatus === 'cancelled')
  ) {
    queryClient.invalidateQueries({ queryKey: ['downloads'] })
  }
  prevJobStatus.current = currentJobStatus

  const cancelMutation = useMutation({
    mutationFn: () => api.cancelDownloadJob(item.download_id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['download-job', item.download_id] }),
  })

  const revealMutation = useMutation({
    mutationFn: () => api.revealDownload(item.download_id),
  })

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteDownload(item.download_id),
    onSuccess: () => {
      setDeleting(false)
      queryClient.invalidateQueries({ queryKey: ['downloads'] })
    },
  })

  // "Quét lại" — Douyin đôi lúc trả thiếu ngay ở bước liệt kê danh sách
  // video của trang cá nhân (đã xác nhận thật: không phải lỗi cố định, gọi
  // lại y hệt tham số thường ra đủ) — cho bấm lại 1 click thay vì phải nhập
  // lại từ đầu form.
  const rescanMutation = useMutation({
    mutationFn: () => {
      const input = item.input as {
        url?: string
        modes?: string[]
        number?: Record<string, number>
        keyword?: string
        search_max?: number
      }
      if (item.mode === 'info') return api.createInfoScanDownload(input.url ?? '', input.modes ?? [], input.number ?? {}, item.title)
      if (item.mode === 'single') return api.createSingleDownload(input.url ?? '', '', item.title)
      if (item.mode === 'profile') return api.createProfileDownload(input.url ?? '', input.modes ?? [], input.number ?? {}, '', item.title)
      return api.createSearchDownload(input.keyword ?? '', input.search_max ?? 20, '', item.title)
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['downloads'] }),
  })

  return (
    <div className="card flex flex-wrap items-start justify-between gap-3 p-3" style={{ flexDirection: 'row' }}>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{item.title}</span>
          <span className="tag tag-neutral">{MODE_LABEL[item.mode]}</span>
          <span
            className={`rounded-md px-1.5 py-0.5 font-mono text-[10px] ${
              item.status === 'done'
                ? 'bg-accent-800 text-accent-100'
                : item.status === 'failed'
                  ? 'bg-danger-900 text-danger-200'
                  : 'bg-neutral-800 text-neutral-300'
            }`}
          >
            {STATUS_LABEL[item.status]}
          </span>
        </div>
        {(item.status === 'pending' || item.status === 'running') && jobQuery.data && (
          <div className="mt-2">
            {!jobQuery.data.registered && <p className="text-xs text-neutral-400">Đang chuẩn bị...</p>}
            <JobProgressBar job={jobQuery.data} />
            {jobQuery.data.registered && jobQuery.data.items.length === 0 && (
              <p className="text-xs text-neutral-500">
                {item.mode === 'single'
                  ? 'Đang lấy thông tin video...'
                  : 'Đang tìm video (có thể mất một lúc trước khi tải video đầu tiên)...'}
              </p>
            )}
            {jobQuery.data.items.length > 0 && (
              <ul className="mt-1.5 max-h-40 space-y-0.5 overflow-auto text-xs text-neutral-500">
                {jobQuery.data.items.map((it) => (
                  <li key={it.id} className="truncate">
                    ✓ {it.label}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        {item.status === 'failed' && item.error && <p className="mt-1 text-xs text-danger">{item.error}</p>}
        {item.status === 'done' && item.mode === 'info' && (item.video_info?.length ?? 0) > 0 && (
          <div className="mt-2 space-y-2">
            <div className="flex items-center justify-end">
              <CopyLinkButton
                label={`Chép tất cả link tải (${(item.video_info ?? []).filter((info) => info?.play_url).length})`}
                text={(item.video_info ?? [])
                  .filter((info): info is DownloadVideoInfo => !!info?.play_url)
                  .map((info) => info.play_url)
                  .join('\n')}
              />
            </div>
            {(item.video_info ?? []).map((info, i) => info && <InfoOnlyRow key={i} info={info} />)}
          </div>
        )}
        {item.status === 'done' && item.mode !== 'info' && item.output_files.length > 0 && (
          <div className="mt-2 space-y-2">
            {item.output_files.map((f, i) => (
              <FilePreview key={f} downloadId={item.download_id} file={f} info={item.video_info?.[i]} />
            ))}
          </div>
        )}
        {item.status === 'done' && item.mode !== 'info' && item.output_files.length === 0 && (
          <p className="mt-1 text-xs text-neutral-500">Xong nhưng không tạo được file nào — kiểm tra log server.</p>
        )}
        {item.status === 'done' && item.mode === 'info' && (item.video_info?.length ?? 0) === 0 && (
          <p className="mt-1 text-xs text-neutral-500">Xong nhưng không lấy được thông tin video nào.</p>
        )}
        {revealMutation.isError && (
          <p className="mt-1 text-xs text-danger">
            Không mở được thư mục: {(revealMutation.error as Error).message}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {item.status === 'done' && item.mode !== 'info' && (
          <button
            type="button"
            className={secondaryButtonClass}
            disabled={revealMutation.isPending}
            onClick={() => revealMutation.mutate()}
          >
            Mở thư mục
          </button>
        )}
        {item.status === 'running' && (
          <button
            type="button"
            className="btn btn-ghost btn-sm text-danger"
            disabled={cancelMutation.isPending}
            onClick={() => cancelMutation.mutate()}
          >
            Dừng
          </button>
        )}
        {(item.status === 'done' || item.status === 'failed') && (
          <button
            type="button"
            className={secondaryButtonClass}
            disabled={rescanMutation.isPending}
            onClick={() => rescanMutation.mutate()}
            title="Douyin đôi lúc trả thiếu kết quả — bấm để quét lại đúng tham số này"
          >
            {rescanMutation.isPending ? 'Đang quét lại...' : 'Quét lại'}
          </button>
        )}
        <button type="button" className="btn btn-ghost btn-sm text-danger" disabled={item.status === 'running'} onClick={() => setDeleting(true)}>
          Xoá
        </button>
      </div>
      {rescanMutation.error && (
        <p className="w-full text-xs text-danger">{(rescanMutation.error as Error).message}</p>
      )}

      <ConfirmDialog
        open={deleting}
        title="Xoá kết quả tải?"
        message="File đã tải sẽ bị xoá khỏi đĩa."
        confirmLabel="Xoá"
        danger
        onCancel={() => setDeleting(false)}
        onConfirm={() => deleteMutation.mutate()}
      />
    </div>
  )
}

export default function DownloadPage() {
  const queryClient = useQueryClient()
  const [mode, setMode] = useState<DownloadMode>('single')
  const [url, setUrl] = useState('')
  const [profileModes, setProfileModes] = useState<string[]>(['post'])
  const [profileLimit, setProfileLimit] = useState(20)
  const [keyword, setKeyword] = useState('')
  const [searchMax, setSearchMax] = useState(20)
  const [destDir, setDestDir] = useState('')

  const downloadsQuery = useQuery({ queryKey: ['downloads'], queryFn: api.listDownloads })

  const pickFolderMutation = useMutation({
    mutationFn: () => api.pickDownloadFolder(),
    onSuccess: (data) => {
      if (data.path) setDestDir(data.path)
    },
  })

  const createMutation = useMutation({
    mutationFn: () => {
      if (mode === 'single') return api.createSingleDownload(url.trim(), destDir.trim())
      if (mode === 'profile') {
        const number = Object.fromEntries(profileModes.map((m) => [m, profileLimit]))
        return api.createProfileDownload(url.trim(), profileModes, number, destDir.trim())
      }
      if (mode === 'info') {
        const number = Object.fromEntries(profileModes.map((m) => [m, profileLimit]))
        return api.createInfoScanDownload(url.trim(), profileModes, number)
      }
      return api.createSearchDownload(keyword.trim(), searchMax, destDir.trim())
    },
    onSuccess: () => {
      setUrl('')
      setKeyword('')
      queryClient.invalidateQueries({ queryKey: ['downloads'] })
    },
  })

  const toggleProfileMode = (id: string) =>
    setProfileModes((prev) => (prev.includes(id) ? prev.filter((m) => m !== id) : [...prev, id]))

  const canSubmit =
    (mode === 'single' && url.trim().length > 0) ||
    ((mode === 'profile' || mode === 'info') && url.trim().length > 0 && profileModes.length > 0) ||
    (mode === 'search' && keyword.trim().length > 0)

  const downloads = downloadsQuery.data ?? []

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl">Tải video</h1>
        <p className="mt-1 text-sm text-neutral-400">
          Tải video Douyin trực tiếp, không cần gắn vào dự án nào — chỉ hỗ trợ Douyin (không phải TikTok). Tải theo
          trang cá nhân/từ khoá không đăng nhập có thể chỉ lấy được khoảng 20 video đầu.
        </p>
      </div>

      <section className="card space-y-4 p-5">
        <div className="flex flex-wrap gap-4 text-sm text-neutral-300">
          {(['single', 'profile', 'search', 'info'] as DownloadMode[]).map((m) => (
            <label key={m} className="flex items-center gap-1.5">
              <input type="radio" checked={mode === m} onChange={() => setMode(m)} />
              {MODE_LABEL[m]}
            </label>
          ))}
        </div>

        {mode === 'single' && (
          <label className="block text-sm text-neutral-300">
            Link video
            <input
              className={inputClass}
              placeholder="https://www.douyin.com/video/..."
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          </label>
        )}

        {mode === 'profile' && (
          <>
            <label className="block text-sm text-neutral-300">
              Link trang cá nhân
              <input
                className={inputClass}
                placeholder="https://www.douyin.com/user/..."
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </label>
            <div>
              <span className="text-sm text-neutral-300">Nội dung cần tải</span>
              <div className="mt-1 flex flex-wrap gap-4">
                {PROFILE_MODES.map((m) => (
                  <label key={m.id} className="flex items-center gap-1.5 text-sm text-neutral-300">
                    <input type="checkbox" checked={profileModes.includes(m.id)} onChange={() => toggleProfileMode(m.id)} />
                    {m.label}
                  </label>
                ))}
              </div>
            </div>
            <label className="block max-w-48 text-sm text-neutral-300">
              Số lượng giới hạn (0 = không giới hạn)
              <input
                type="number"
                min={0}
                className={inputClass}
                value={profileLimit}
                onChange={(e) => setProfileLimit(Math.max(0, Number(e.target.value) || 0))}
              />
            </label>
          </>
        )}

        {mode === 'info' && (
          <>
            <p className="text-sm text-neutral-400">
              Chỉ lấy thông tin từng video (thumbnail, mô tả, link chia sẻ, tác giả, lượt thích/bình luận/chia sẻ) —
              <b> không tải video</b>, nhanh hơn nhiều so với tải đầy đủ. Muốn tải video nào thì dán link chia sẻ của
              nó vào chế độ "1 video" sau.
            </p>
            <label className="block text-sm text-neutral-300">
              Link trang cá nhân
              <input
                className={inputClass}
                placeholder="https://www.douyin.com/user/..."
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </label>
            <div>
              <span className="text-sm text-neutral-300">Nội dung cần lấy</span>
              <div className="mt-1 flex flex-wrap gap-4">
                {PROFILE_MODES.map((m) => (
                  <label key={m.id} className="flex items-center gap-1.5 text-sm text-neutral-300">
                    <input type="checkbox" checked={profileModes.includes(m.id)} onChange={() => toggleProfileMode(m.id)} />
                    {m.label}
                  </label>
                ))}
              </div>
            </div>
            <label className="block max-w-48 text-sm text-neutral-300">
              Số lượng giới hạn (0 = toàn bộ, khuyến nghị ≤100)
              <input
                type="number"
                min={0}
                className={inputClass}
                value={profileLimit}
                onChange={(e) => setProfileLimit(Math.max(0, Number(e.target.value) || 0))}
              />
            </label>
          </>
        )}

        {mode === 'search' && (
          <>
            <label className="block text-sm text-neutral-300">
              Từ khoá
              <input className={inputClass} value={keyword} onChange={(e) => setKeyword(e.target.value)} />
            </label>
            <label className="block max-w-48 text-sm text-neutral-300">
              Số lượng tối đa
              <input
                type="number"
                min={1}
                max={100}
                className={inputClass}
                value={searchMax}
                onChange={(e) => setSearchMax(Math.min(100, Math.max(1, Number(e.target.value) || 1)))}
              />
            </label>
          </>
        )}

        {mode !== 'info' && (
          <label className="block text-sm text-neutral-300">
            Thư mục lưu (để trống = mặc định trong workspace)
            <div className="mt-1 flex gap-2">
              <input
                className={`${inputClass} mt-0 flex-1`}
                placeholder="vd: D:\Videos\Douyin"
                value={destDir}
                onChange={(e) => setDestDir(e.target.value)}
              />
              <button
                type="button"
                className={secondaryButtonClass}
                disabled={pickFolderMutation.isPending}
                onClick={() => pickFolderMutation.mutate()}
              >
                {pickFolderMutation.isPending ? 'Đang chọn...' : 'Duyệt...'}
              </button>
            </div>
          </label>
        )}

        <button
          type="button"
          className={primaryButtonClass}
          disabled={!canSubmit || createMutation.isPending}
          onClick={() => createMutation.mutate()}
        >
          {createMutation.isPending ? 'Đang bắt đầu...' : 'Tải'}
        </button>
        {createMutation.error && <p className="text-sm text-danger">{(createMutation.error as Error).message}</p>}
      </section>

      {downloads.length > 0 && (
        <div className="space-y-2">
          {downloads.map((d) => (
            <DownloadRow key={d.download_id} item={d} />
          ))}
        </div>
      )}
    </div>
  )
}
