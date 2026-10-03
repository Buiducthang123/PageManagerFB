import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  api,
  MIN_VIDEO_SPEED_OPTIONS,
  type QueueItem,
  type QueueItemStatus,
  type TranscribeEngine,
  type TTSEngine,
} from '../lib/api'
import { inputClass, primaryButtonClass, secondaryButtonClass } from '../lib/ui'
import JobProgressBar from '../components/JobProgressBar'
import OcrCropSelector, { type CropRegion } from '../components/OcrCropSelector'
import DouyinBrowserPanel from '../components/DouyinBrowserPanel'
import BlurStrengthControl, { DEFAULT_BLUR_STRENGTH } from '../components/BlurStrengthControl'
import CaptionDialog from '../components/CaptionDialog'
import TikTokAccountIdentity from '../components/TikTokAccountIdentity'
import { useLabels } from '../lib/labels'
import PostScheduleEditor from '../components/PostScheduleEditor'

const STATUS_LABEL: Record<QueueItemStatus, string> = {
  pending: 'Chờ xử lý',
  processing: 'Đang chạy pipeline',
  ready: 'Sẵn sàng đăng',
  posted: 'Đã đăng',
  failed: 'Lỗi',
  skipped: 'Đã bỏ qua',
}

const STATUS_TAG_CLASS: Record<QueueItemStatus, string> = {
  pending: 'tag-neutral',
  processing: 'tag-accent',
  ready: 'tag-accent',
  posted: 'tag-outline',
  failed: 'tag-danger',
  skipped: 'tag-neutral',
}

const QUEUE_PAGE_SIZE = 30

function QueuePager({
  page,
  pageCount,
  total,
  onChange,
}: {
  page: number
  pageCount: number
  total: number
  onChange: (page: number) => void
}) {
  // Hiện trang đầu, trang cuối và 2 trang quanh trang hiện tại; chỗ bị lược
  // bỏ thay bằng "…".
  const pages: (number | '…')[] = []
  for (let n = 1; n <= pageCount; n++) {
    if (n === 1 || n === pageCount || Math.abs(n - page) <= 2) pages.push(n)
    else if (pages[pages.length - 1] !== '…') pages.push('…')
  }
  const from = (page - 1) * QUEUE_PAGE_SIZE + 1
  const to = Math.min(page * QUEUE_PAGE_SIZE, total)
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 pt-2 text-sm">
      <span className="text-xs text-neutral-500">
        Video {from}–{to} / {total}
      </span>
      <div className="flex flex-wrap items-center gap-1">
        <button type="button" className="btn btn-ghost btn-sm" disabled={page <= 1} onClick={() => onChange(page - 1)}>
          ← Trước
        </button>
        {pages.map((n, idx) =>
          n === '…' ? (
            <span key={`gap-${idx}`} className="px-1 text-neutral-500">
              …
            </span>
          ) : (
            <button
              key={n}
              type="button"
              className={`btn btn-sm min-w-8 ${n === page ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => onChange(n)}
            >
              {n}
            </button>
          ),
        )}
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          disabled={page >= pageCount}
          onClick={() => onChange(page + 1)}
        >
          Sau →
        </button>
      </div>
    </div>
  )
}

// So sánh theo NGÀY ĐĂNG TRÊN DOUYIN (cũ trước): aweme_id tăng dần theo thời
// gian đăng. Không dùng discovered_at — mỗi lần crawl Douyin trả video mới
// nhất trước, nên discovered_at xếp ngược tuổi thật trong cùng 1 lần crawl.
// aweme_id dài 19 chữ số (vượt Number an toàn) → so theo độ dài rồi chuỗi.
function compareVideoAge(a: QueueItem, b: QueueItem): number {
  const x = a.aweme_id
  const y = b.aweme_id
  if (/^\d+$/.test(x) && /^\d+$/.test(y)) return x.length - y.length || (x < y ? -1 : x > y ? 1 : 0)
  return a.discovered_at.localeCompare(b.discovered_at)
}

export default function AutomatedDetailPage() {
  const { socialId } = useParams<{ socialId: string }>()
  const labels = useLabels()
  const queryClient = useQueryClient()
  const [publishingId, setPublishingId] = useState<string | null>(null)
  const [fbPublishingId, setFbPublishingId] = useState<string | null>(null)
  const logoRef = useRef<HTMLInputElement>(null)
  const musicRef = useRef<HTMLInputElement>(null)
  const [errorDialogItem, setErrorDialogItem] = useState<QueueItem | null>(null)
  const [captionItem, setCaptionItem] = useState<QueueItem | null>(null)
  const [queuePage, setQueuePage] = useState(1)
  // Video đang tích chọn để bỏ qua hàng loạt — chỉ trong trang đang xem.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [bulkMessage, setBulkMessage] = useState<string | null>(null)
  const [crawlAll, setCrawlAll] = useState(false)
  const [crawlLimit, setCrawlLimit] = useState(100)
  const [statusFilter, setStatusFilter] = useState<QueueItemStatus | 'all'>('all')
  // Mặc định cũ → mới theo ngày đăng trên Douyin — đúng thứ tự hàng đợi thật
  // sự xử lý và đăng, để nhìn danh sách là biết video nào tới lượt.
  const [sortMode, setSortMode] = useState<'smart' | 'newest' | 'oldest'>('oldest')

  const [settingsInitialized, setSettingsInitialized] = useState(false)
  const [engine, setEngine] = useState<TranscribeEngine>('ocr')
  const [ttsEngine, setTtsEngine] = useState<TTSEngine>('capcut')
  const [voice, setVoice] = useState('')
  const [audioMode, setAudioMode] = useState<'original' | 'separated' | 'mute'>('original')
  const [originalVolumeDb, setOriginalVolumeDb] = useState(-13)
  const [musicVolumeDb, setMusicVolumeDb] = useState(-13)
  const [blurStrength, setBlurStrength] = useState(DEFAULT_BLUR_STRENGTH)
  const [captionHashtags, setCaptionHashtags] = useState('')
  const [coverEnabled, setCoverEnabled] = useState(true)
  const [coverBg, setCoverBg] = useState('#F2555A')
  const [coverFg, setCoverFg] = useState('#FFFFFF')
  const [subtitleFontSize, setSubtitleFontSize] = useState(6)
  const [minVideoSpeed, setMinVideoSpeed] = useState(0.85)
  const [useViesnapFallback, setUseViesnapFallback] = useState(true)
  const [ocrCrop, setOcrCrop] = useState<CropRegion | null>(null)

  const detailQuery = useQuery({
    queryKey: ['social', socialId],
    queryFn: () => api.getSocial(socialId!),
    enabled: !!socialId,
  })

  const settingsQuery = useQuery({ queryKey: ['settings'], queryFn: api.getSettings })

  // Dự án tự động không có 1 video cố định để xem trước — mượn video của
  // video GẦN NHẤT đã có pipeline chạy (bất kể trạng thái) làm mẫu để kéo
  // khoanh vùng OCR, áp dụng chung cho mọi video của dự án.
  const previewProjectId = [...(detailQuery.data?.queue ?? [])]
    .filter((i) => i.project_id)
    .sort((a, b) => b.discovered_at.localeCompare(a.discovered_at))[0]?.project_id
  const previewProjectQuery = useQuery({
    queryKey: ['social-ocr-preview-project', previewProjectId],
    queryFn: () => api.getProject(previewProjectId!),
    enabled: !!previewProjectId,
  })

  const jobQuery = useQuery({
    queryKey: ['social-crawl-job', socialId],
    queryFn: () => api.socialCrawlJobStatus(socialId!),
    enabled: !!socialId,
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 1500 : false),
  })

  // Lịch tự dọn file theo từng video (xem app/social_cleanup.py) — hiện ngay
  // trên từng video để biết cái nào sẽ bị xoá file và lúc nào.
  const cleanupPlanQuery = useQuery({
    queryKey: ['social-cleanup-plan', socialId],
    queryFn: () => api.socialCleanupPlan(socialId!),
    enabled: !!socialId,
    refetchInterval: 60_000,
  })

  const keepMutation = useMutation({
    mutationFn: ({ projectId, keep }: { projectId: string; keep: boolean }) => api.setCleanupKeep(projectId, keep),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['social-cleanup-plan', socialId] })
      queryClient.invalidateQueries({ queryKey: ['cleanup-overview'] })
    },
  })

  const tiktokStatusQuery = useQuery({
    queryKey: ['social-tiktok-status', socialId],
    queryFn: () => api.tiktokLoginStatus(socialId!),
    enabled: !!socialId,
    // Đang kiểm tra tài khoản thì cập nhật nhanh để thấy kết quả.
    refetchInterval: (q) => (q.state.data?.account?.busy ? 2000 : 60_000),
  })

  const accountsQuery = useQuery({ queryKey: ['accounts'], queryFn: api.listAccounts })

  const assignAccountMutation = useMutation({
    mutationFn: ({ id, move }: { id: string; move: boolean }) => api.assignTiktokAccount(socialId!, id, move),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['social-tiktok-status', socialId] })
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      queryClient.invalidateQueries({ queryKey: ['social'] })
    },
  })

  const checkAccountMutation = useMutation({
    mutationFn: (accountId: string) => api.checkAccount(accountId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['social-tiktok-status', socialId] }),
  })

  const tiktokLoginJobQuery = useQuery({
    queryKey: ['social-tiktok-login-job', socialId],
    queryFn: () => api.tiktokLoginJobStatus(socialId!),
    enabled: !!socialId,
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 2000 : false),
  })

  const publishJobQuery = useQuery({
    queryKey: ['social-publish-job', socialId, publishingId],
    queryFn: () => api.publishQueueItemJobStatus(socialId!, publishingId!),
    enabled: !!socialId && !!publishingId,
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 1500 : false),
  })

  const fbPublishJobQuery = useQuery({
    queryKey: ['social-fb-publish-job', socialId, fbPublishingId],
    queryFn: () => api.fbPublishQueueItemJobStatus(socialId!, fbPublishingId!),
    enabled: !!socialId && !!fbPublishingId,
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 2000 : false),
  })

  const fbPagesQuery = useQuery({ queryKey: ['facebook-pages'], queryFn: api.listFacebookPages })

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['social', socialId] })

  const assignFbPageMutation = useMutation({
    mutationFn: ({ id, move }: { id: string; move: boolean }) => api.assignFacebookPage(socialId!, id, move),
    onSuccess: () => {
      refresh()
      queryClient.invalidateQueries({ queryKey: ['facebook-pages'] })
    },
  })

  const checkFbPageMutation = useMutation({
    mutationFn: (pageId: string) => api.checkFacebookPage(pageId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['facebook-pages'] }),
  })

  const fbPublishMutation = useMutation({
    mutationFn: (awemeId: string) => {
      setFbPublishingId(awemeId)
      return api.fbPublishQueueItem(socialId!, awemeId)
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['social-fb-publish-job', socialId, fbPublishingId] }),
  })

  const crawlMutation = useMutation({
    mutationFn: (limit: number | null) => api.crawlSocial(socialId!, limit),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['social-crawl-job', socialId] })
    },
  })

  const cancelCrawlMutation = useMutation({
    mutationFn: () => api.cancelSocialCrawl(socialId!),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['social-crawl-job', socialId] }),
  })

  const activateMutation = useMutation({
    mutationFn: (awemeId: string) => api.activateQueueItem(socialId!, awemeId),
    onSuccess: () => refresh(),
  })

  const skipMutation = useMutation({
    mutationFn: (awemeId: string) => api.skipQueueItem(socialId!, awemeId),
    onSuccess: () => refresh(),
  })

  const skipBulkMutation = useMutation({
    mutationFn: (awemeIds: string[]) => api.skipQueueItems(socialId!, awemeIds),
    onSuccess: (res) => {
      setSelectedIds(new Set())
      setBulkMessage(
        `Đã bỏ qua ${res.skipped} video.` + (res.kept_posted ? ` ${res.kept_posted} video đã đăng nên giữ nguyên.` : ''),
      )
      refresh()
    },
  })

  // Đổi trang/bộ lọc/sắp xếp → bỏ chọn, để không bỏ qua nhầm video ở trang
  // khác mà người dùng không còn nhìn thấy.
  useEffect(() => {
    setSelectedIds(new Set())
  }, [queuePage, statusFilter, sortMode])

  const unskipMutation = useMutation({
    mutationFn: (awemeId: string) => api.unskipQueueItem(socialId!, awemeId),
    onSuccess: () => refresh(),
  })

  const updateMutation = useMutation({
    mutationFn: (body: Parameters<typeof api.updateSocial>[1]) => api.updateSocial(socialId!, body),
    onSuccess: () => refresh(),
  })

  // Nạp cấu hình đã lưu của dự án (đóng vai "riêng" — mặc định lúc TẠO dự án
  // đến từ default của backend, xem CreateSocialProjectRequest) vào form CHỈ
  // 1 LẦN — không ghi đè lại mỗi lần refetch, để không xoá mất bạn đang gõ dở.
  useEffect(() => {
    const s = detailQuery.data
    if (!s || settingsInitialized) return
    setEngine((s.engine as TranscribeEngine) || 'ocr')
    setTtsEngine((s.tts_engine as TTSEngine) || 'capcut')
    setVoice(s.voice || '')
    setAudioMode((s.audio_mode as typeof audioMode) || 'original')
    setOriginalVolumeDb(s.original_audio_volume_db ?? -13)
    setMusicVolumeDb(s.music_volume_db ?? -13)
    setBlurStrength(s.blur_strength ?? DEFAULT_BLUR_STRENGTH)
    setCaptionHashtags(s.caption_hashtags ?? '')
    setCoverEnabled(s.cover_enabled ?? true)
    setCoverBg(s.cover_bg || '#F2555A')
    setCoverFg(s.cover_fg || '#FFFFFF')
    setSubtitleFontSize(s.subtitle_font_size ?? 6)
    setMinVideoSpeed(s.min_video_speed ?? 0.85)
    setUseViesnapFallback(s.use_viesnap_fallback ?? true)
    setOcrCrop((s.ocr_crop_region as CropRegion | null) ?? null)
    setSettingsInitialized(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detailQuery.data, settingsInitialized])

  const saveSettingsMutation = useMutation({
    mutationFn: () =>
      api.updateSocial(socialId!, {
        engine,
        tts_engine: ttsEngine,
        voice,
        audio_mode: audioMode,
        original_audio_volume_db: originalVolumeDb,
        music_volume_db: musicVolumeDb,
        blur_strength: blurStrength,
        caption_hashtags: captionHashtags,
        cover_enabled: coverEnabled,
        cover_bg: coverBg,
        cover_fg: coverFg,
        subtitle_font_size: subtitleFontSize,
        min_video_speed: minVideoSpeed,
        use_viesnap_fallback: useViesnapFallback,
      }),
    onSuccess: () => refresh(),
  })

  const ocrCropMutation = useMutation({
    mutationFn: (region: CropRegion | null) => api.updateSocialOcrCropRegion(socialId!, region),
    onSuccess: () => refresh(),
  })

  const [logoVersion, setLogoVersion] = useState(0)
  const [musicVersion, setMusicVersion] = useState(0)

  const uploadLogoMutation = useMutation({
    mutationFn: (file: File) => api.uploadSocialLogo(socialId!, file),
    onSuccess: () => setLogoVersion((v) => v + 1),
  })
  const deleteLogoMutation = useMutation({
    mutationFn: () => api.deleteSocialLogo(socialId!),
    onSuccess: () => setLogoVersion((v) => v + 1),
  })
  const uploadMusicMutation = useMutation({
    mutationFn: (file: File) => api.uploadSocialMusic(socialId!, file),
    onSuccess: () => setMusicVersion((v) => v + 1),
  })
  const deleteMusicMutation = useMutation({
    mutationFn: () => api.deleteSocialMusic(socialId!),
    onSuccess: () => setMusicVersion((v) => v + 1),
  })

  const tiktokLoginMutation = useMutation({
    mutationFn: () => api.tiktokLogin(socialId!),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['social-tiktok-login-job', socialId] }),
  })

  const publishMutation = useMutation({
    mutationFn: (awemeId: string) => {
      setPublishingId(awemeId)
      return api.publishQueueItem(socialId!, awemeId)
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['social-publish-job', socialId, publishingId] }),
  })

  const voiceOptions = (ttsEngine === 'vieneu' ? settingsQuery.data?.tts_voices_vieneu : settingsQuery.data?.tts_voices) ?? []

  const busyCrawl = jobQuery.data?.status === 'running'
  const busyTiktokLogin = tiktokLoginJobQuery.data?.status === 'running'
  const busyPublish = publishJobQuery.data?.status === 'running'
  const busyFbPublish = fbPublishJobQuery.data?.status === 'running'

  const fbPublishStatus = fbPublishJobQuery.data?.status
  useEffect(() => {
    if (fbPublishingId && fbPublishStatus && fbPublishStatus !== 'running') refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fbPublishStatus, fbPublishingId])

  const publishStatus = publishJobQuery.data?.status
  useEffect(() => {
    if (publishingId && publishStatus && publishStatus !== 'running') {
      refresh()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [publishStatus, publishingId])

  // Badge "Đã setup"/"Chưa setup" chỉ fetch 1 LẦN lúc vào trang (React Query
  // mặc định không tự refetch) — nếu không invalidate tay ở đây, đăng nhập
  // xong đóng cửa sổ Chrome thật (job login chuyển 'done') nhưng badge vẫn
  // hiện "Chưa setup" mãi tới khi tự F5 lại trang. Đã xác nhận thật đây là
  // nguyên nhân "đăng nhập rồi mà không thấy cập nhật trạng thái".
  const tiktokLoginStatus = tiktokLoginJobQuery.data?.status
  useEffect(() => {
    if (tiktokLoginStatus && tiktokLoginStatus !== 'running') {
      queryClient.invalidateQueries({ queryKey: ['social-tiktok-status', socialId] })
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tiktokLoginStatus])

  const state = detailQuery.data
  const tiktokAccount = tiktokStatusQuery.data?.account ?? null
  const fbPages = fbPagesQuery.data?.pages ?? []
  const fbPage = state ? fbPages.find((p) => p.page_id === state.facebook_page_id) ?? null : null

  if (detailQuery.isLoading || !state) {
    return (
      <div className="mx-auto max-w-5xl">
        <div className="skeleton h-24" />
      </div>
    )
  }

  const oldestPending = [...state.queue].filter((i) => i.status === 'pending').sort(compareVideoAge)[0]
  const oldestReady = [...state.queue].filter((i) => i.status === 'ready').sort(compareVideoAge)[0]

  const douyinBackoffUntil = state.douyin_backoff_until ? new Date(state.douyin_backoff_until) : null
  const douyinResting = douyinBackoffUntil !== null && douyinBackoffUntil.getTime() > Date.now()

  return (
    <div className="mx-auto max-w-5xl">
      <Link to="/automated" className="mb-3 inline-block text-sm text-neutral-400 hover:text-text">
        ← Dự án tự động
      </Link>
      <div className="mb-1 flex items-center gap-3">
        <h1 className="text-2xl">{state.title}</h1>
        <span className={`tag ${state.status === 'active' ? 'tag-accent' : 'tag-neutral'}`}>
          {state.status === 'active' ? 'Đang chạy' : 'Tạm dừng'}
        </span>
      </div>
      <p className="mono mb-6 text-xs text-neutral-500">{state.douyin_profile_url}</p>

      <section className="card mb-6 space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className={primaryButtonClass}
            disabled={busyCrawl}
            onClick={() => crawlMutation.mutate(crawlAll ? null : crawlLimit)}
          >
            {busyCrawl ? 'Đang crawl...' : 'Crawl ngay'}
          </button>
          {busyCrawl && (
            <button
              type="button"
              className="btn btn-ghost btn-sm text-danger"
              disabled={cancelCrawlMutation.isPending}
              onClick={() => cancelCrawlMutation.mutate()}
            >
              {cancelCrawlMutation.isPending ? 'Đang dừng...' : 'Dừng crawl'}
            </button>
          )}
          <label className="flex items-center gap-1.5 text-sm text-neutral-300">
            <input type="checkbox" checked={crawlAll} onChange={(e) => setCrawlAll(e.target.checked)} />
            Crawl tất cả
          </label>
          {!crawlAll && (
            <label className="flex items-center gap-1.5 text-sm text-neutral-300">
              số video mới nhất
              <input
                type="number"
                min={1}
                className={`${inputClass} mt-0 w-20`}
                value={crawlLimit}
                onChange={(e) => setCrawlLimit(Math.max(1, Number(e.target.value) || 1))}
              />
            </label>
          )}
          <button
            type="button"
            className={secondaryButtonClass}
            onClick={() => updateMutation.mutate({ status: state.status === 'active' ? 'paused' : 'active' })}
          >
            {state.status === 'active' ? 'Tạm dừng dự án' : 'Bật lại dự án'}
          </button>
          {oldestPending && (
            <button
              type="button"
              className={secondaryButtonClass}
              disabled={activateMutation.isPending}
              onClick={() => activateMutation.mutate(oldestPending.aweme_id)}
            >
              Kích hoạt video cũ nhất ({oldestPending.title.slice(0, 30) || oldestPending.aweme_id})
            </button>
          )}
          <label
            className="ml-auto flex items-center gap-2 text-sm text-neutral-300"
            title="Áp dụng cho giờ đăng cố định của cả TikTok và Facebook — đăng đúng từng phút mỗi ngày dễ bị coi là bot"
          >
            Giờ cố định lệch ngẫu nhiên ±
            <input
              type="number"
              min={0}
              max={60}
              className={`${inputClass} mt-0 w-16`}
              defaultValue={state.post_time_jitter_min}
              key={state.post_time_jitter_min}
              onBlur={(e) => {
                const n = Math.min(60, Math.max(0, Number(e.target.value) || 0))
                e.target.value = String(n)
                if (n !== state.post_time_jitter_min) updateMutation.mutate({ post_time_jitter_min: n })
              }}
            />
            phút
          </label>
        </div>
        <JobProgressBar job={jobQuery.data} />
        {crawlMutation.error && <p className="text-sm text-danger">{(crawlMutation.error as Error).message}</p>}
        {activateMutation.error && <p className="text-sm text-danger">{(activateMutation.error as Error).message}</p>}
        {state.last_crawl_at && (
          <p className="text-xs text-neutral-500">Crawl lần cuối: {new Date(state.last_crawl_at).toLocaleString('vi-VN')}</p>
        )}
        {!!state.crawl_fail_count && state.last_crawl_failed_at && (
          <p className="text-xs text-danger">
            Crawl lỗi {state.crawl_fail_count} lần liên tiếp (lần gần nhất{' '}
            {new Date(state.last_crawl_failed_at).toLocaleString('vi-VN')}): {state.last_crawl_error} — app tạm nghỉ{' '}
            {[1, 3, 6, 12][Math.min(state.crawl_fail_count, 4) - 1]} giờ rồi mới tự crawl lại. Bấm "Crawl ngay" để thử
            luôn.
          </p>
        )}
        <p className="text-xs text-neutral-500">
          {state.status !== 'active'
            ? 'Dự án đang tạm dừng — bộ lập lịch nền sẽ không tự crawl/kích hoạt/đăng cho tới khi bật lại.'
            : !state.tiktok_enabled && !state.facebook_enabled
              ? 'Chưa bật nền tảng đăng nào — bật TikTok và/hoặc Facebook ở bên dưới để dự án chạy.'
              : !oldestReady
                ? 'Chưa có video "Sẵn sàng đăng" nào trong hàng đợi — bộ lập lịch nền tự kích hoạt video cũ nhất rồi đăng khi có.'
                : 'Lịch đăng riêng của từng nền tảng xem ở mục TikTok / Facebook bên dưới.'}
        </p>
        {douyinResting && (
          <p className="text-xs text-danger">
            Đang tạm nghỉ gọi API Douyin tới {douyinBackoffUntil!.toLocaleString('vi-VN')} (nghi bị risk-control, lần liên
            tiếp thứ {state.douyin_backoff_level || 1}) — không tự crawl/kích hoạt trong lúc này, đăng bài vẫn chạy.
          </p>
        )}
      </section>

      <DouyinBrowserPanel
        enabled={state.crawl_via_browser}
        toggling={updateMutation.isPending}
        onToggle={(v) => updateMutation.mutate({ crawl_via_browser: v })}
      />

      <section className="card mb-6 space-y-2 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-sm text-neutral-300">Đăng bài TikTok (Playwright — Chrome thật)</span>
          <Link to="/accounts" className="text-xs text-neutral-400 hover:text-accent-200">
            Quản lý tài khoản →
          </Link>
        </div>
        {tiktokAccount ? (
          <TikTokAccountIdentity account={tiktokAccount} />
        ) : (
          <p className="text-sm text-danger">Chưa gán tài khoản TikTok — dự án sẽ không tự đăng bài.</p>
        )}
        {tiktokAccount?.status_detail && tiktokAccount.status !== 'ok' && (
          <p className="text-xs text-danger">{tiktokAccount.status_detail}</p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="input max-w-xs"
            value={state.tiktok_account_id || ''}
            disabled={assignAccountMutation.isPending || busyTiktokLogin}
            onChange={(e) => {
              const id = e.target.value
              const acc = (accountsQuery.data ?? []).find((a) => a.id === id)
              const others = (acc?.projects ?? []).filter((p) => p.social_id !== socialId)
              if (others.length) {
                const name = acc!.username ? `@${acc!.username}` : acc!.label || acc!.id
                const ok = window.confirm(
                  `Tài khoản ${name} đang dùng cho dự án "${others.map((p) => p.title).join(', ')}".\n\n` +
                    'Chuyển sang dự án này? Dự án kia sẽ không còn tài khoản TikTok và ngừng đăng TikTok.',
                )
                if (!ok) {
                  e.target.value = state.tiktok_account_id || ''
                  return
                }
              }
              assignAccountMutation.mutate({ id, move: others.length > 0 })
            }}
          >
            <option value="">— Chưa gán tài khoản —</option>
            {(accountsQuery.data ?? []).map((a) => {
              const others = a.projects.filter((p) => p.social_id !== socialId)
              return (
                <option key={a.id} value={a.id}>
                  {a.username ? `@${a.username}` : a.label || a.id}
                  {a.status !== 'ok' ? ` (${a.status === 'unknown' ? 'chưa kiểm tra' : 'cần đăng nhập lại'})` : ''}
                  {others.length ? ` · đang dùng: ${others.map((p) => p.title).join(', ')}` : ''}
                </option>
              )
            })}
          </select>
          {tiktokAccount && (
            <button
              type="button"
              className={secondaryButtonClass}
              disabled={tiktokAccount.busy || checkAccountMutation.isPending}
              onClick={() => checkAccountMutation.mutate(tiktokAccount.id)}
            >
              {tiktokAccount.busy && !busyTiktokLogin ? 'Đang kiểm tra...' : 'Kiểm tra'}
            </button>
          )}
          <button
            type="button"
            className={secondaryButtonClass}
            disabled={busyTiktokLogin}
            onClick={() => tiktokLoginMutation.mutate()}
          >
            {busyTiktokLogin
              ? 'Đang chờ đăng nhập...'
              : tiktokAccount
                ? 'Đăng nhập lại'
                : 'Tạo tài khoản mới và đăng nhập'}
          </button>
          {busyTiktokLogin && (
            <button
              type="button"
              className="btn btn-ghost btn-sm text-danger"
              onClick={() => api.cancelTiktokLogin(socialId!)}
            >
              Huỷ
            </button>
          )}
        </div>
        <p className="text-xs text-neutral-500">
          Chọn tài khoản có sẵn, hoặc bấm đăng nhập — 1 cửa sổ Chrome thật sẽ mở lên, tự đăng nhập tay (kể cả
          2FA/captcha), xong thì đóng cửa sổ đó lại. App tự đọc tên tài khoản sau khi đóng, và kiểm tra đúng tài khoản
          trước mỗi lần đăng.
        </p>
        {tiktokLoginMutation.error && <p className="text-sm text-danger">{(tiktokLoginMutation.error as Error).message}</p>}
        {assignAccountMutation.error && (
          <p className="text-sm text-danger">{(assignAccountMutation.error as Error).message}</p>
        )}
        {checkAccountMutation.error && (
          <p className="text-sm text-danger">{(checkAccountMutation.error as Error).message}</p>
        )}
        {/* Trước đây bấm "Đăng lên TikTok" mà server từ chối (vd mất file video
            đã xuất) thì không hiện gì — trông như nút không bấm được. */}
        {publishMutation.error && (
          <p className="text-sm text-danger">Không đăng được: {(publishMutation.error as Error).message}</p>
        )}
        {publishJobQuery.data?.status === 'failed' && publishJobQuery.data.error && (
          <p className="text-sm text-danger">Đăng TikTok lỗi: {publishJobQuery.data.error}</p>
        )}
        {busyPublish && publishJobQuery.data?.current_label && (
          <p className="text-xs text-neutral-400">{publishJobQuery.data.current_label}</p>
        )}
        <PostScheduleEditor
          platformLabel="TikTok"
          enabled={state.tiktok_enabled}
          canEnable
          postsPerDay={state.posts_per_day}
          maxPostsPerDay={3}
          postTimes={state.tiktok_post_times ?? []}
          jitterMin={state.post_time_jitter_min}
          nextPostAt={state.next_post_at}
          saving={updateMutation.isPending}
          onToggle={(v) => updateMutation.mutate({ tiktok_enabled: v })}
          onSavePostsPerDay={(n) => updateMutation.mutate({ posts_per_day: n })}
          onSaveTimes={(times) => updateMutation.mutate({ tiktok_post_times: times })}
        />
      </section>

      <section className="card mb-6 space-y-2 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-sm text-neutral-300">Đăng Reels lên Facebook Page (Graph API)</span>
          <Link to="/accounts?tab=facebook" className="text-xs text-neutral-400 hover:text-accent-200">
            Quản lý Facebook Page →
          </Link>
        </div>
        {fbPage ? (
          <div className="flex items-center gap-3">
            {fbPage.picture_url && <img src={fbPage.picture_url} alt="" className="h-8 w-8 rounded-full object-cover" />}
            <div className="text-sm">
              <a
                href={`https://www.facebook.com/${fbPage.page_id}`}
                target="_blank"
                rel="noreferrer"
                className="text-text hover:text-accent-200"
              >
                {fbPage.name}
              </a>
              <div className={`text-xs ${fbPage.status === 'expired' ? 'text-danger' : 'text-neutral-500'}`}>
                {fbPage.status === 'ok'
                  ? 'Token còn dùng được'
                  : fbPage.status === 'expired'
                    ? 'Token hết hạn — vào trang Tài khoản nhập lại token'
                    : fbPage.status === 'error'
                      ? 'Không kiểm tra được token'
                      : 'Chưa kiểm tra token'}
              </div>
            </div>
          </div>
        ) : (
          <p className="text-sm text-neutral-500">Chưa chọn Facebook Page.</p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="input max-w-xs"
            value={state.facebook_page_id || ''}
            disabled={assignFbPageMutation.isPending || busyFbPublish}
            onChange={(e) => {
              const id = e.target.value
              const page = fbPages.find((p) => p.page_id === id)
              const others = (page?.projects ?? []).filter((p) => p.social_id !== socialId)
              if (others.length) {
                const ok = window.confirm(
                  `Page "${page!.name}" đang dùng cho dự án "${others.map((p) => p.title).join(', ')}".\n\n` +
                    'Chuyển sang dự án này? Dự án kia sẽ không còn Page và tắt đăng Facebook.',
                )
                if (!ok) {
                  e.target.value = state.facebook_page_id || ''
                  return
                }
              }
              assignFbPageMutation.mutate({ id, move: others.length > 0 })
            }}
          >
            <option value="">— Chưa chọn Page —</option>
            {fbPages.map((p) => {
              const others = p.projects.filter((x) => x.social_id !== socialId)
              return (
                <option key={p.page_id} value={p.page_id}>
                  {p.name}
                  {p.status === 'expired' ? ' (token hết hạn)' : ''}
                  {others.length ? ` · đang dùng: ${others.map((x) => x.title).join(', ')}` : ''}
                </option>
              )
            })}
          </select>
          {fbPage && (
            <button
              type="button"
              className={secondaryButtonClass}
              disabled={checkFbPageMutation.isPending}
              onClick={() => checkFbPageMutation.mutate(fbPage.page_id)}
            >
              {checkFbPageMutation.isPending ? 'Đang kiểm tra...' : 'Kiểm tra'}
            </button>
          )}
        </div>
        {!fbPages.length && (
          <p className="text-xs text-neutral-500">
            Chưa có Facebook Page nào — vào trang Tài khoản để nhập từ PagesManager hoặc dán token.
          </p>
        )}
        {assignFbPageMutation.error && (
          <p className="text-sm text-danger">{(assignFbPageMutation.error as Error).message}</p>
        )}
        {checkFbPageMutation.error && (
          <p className="text-sm text-danger">{(checkFbPageMutation.error as Error).message}</p>
        )}
        {fbPublishMutation.error && (
          <p className="text-sm text-danger">Không đăng được: {(fbPublishMutation.error as Error).message}</p>
        )}
        {fbPublishJobQuery.data?.status === 'failed' && fbPublishJobQuery.data.error && (
          <p className="text-sm text-danger">Đăng Facebook lỗi: {fbPublishJobQuery.data.error}</p>
        )}
        {busyFbPublish && fbPublishJobQuery.data?.current_label && (
          <p className="text-xs text-neutral-400">{fbPublishJobQuery.data.current_label}</p>
        )}
        <PostScheduleEditor
          platformLabel="Facebook"
          enabled={state.facebook_enabled}
          canEnable={!!state.facebook_page_id}
          disabledReason="Chọn Page trước"
          postsPerDay={state.facebook_posts_per_day}
          maxPostsPerDay={3}
          postTimes={state.facebook_post_times ?? []}
          jitterMin={state.post_time_jitter_min}
          nextPostAt={state.facebook_next_post_at}
          saving={updateMutation.isPending}
          onToggle={(v) => updateMutation.mutate({ facebook_enabled: v })}
          onSavePostsPerDay={(n) => updateMutation.mutate({ facebook_posts_per_day: n })}
          onSaveTimes={(times) => updateMutation.mutate({ facebook_post_times: times })}
        />
        {updateMutation.error && <p className="text-sm text-danger">{(updateMutation.error as Error).message}</p>}
      </section>

      <details className="card mb-6 space-y-3 p-4">
        <summary className="cursor-pointer text-sm text-neutral-300 select-none">
          Cài đặt xử lý video (riêng cho dự án này — mặc định lúc tạo dự án mới lấy từ đây)
        </summary>
        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
          <label className="text-sm text-neutral-300">
            Cách nhận diện lời thoại
            <select className={`${inputClass} mt-1`} value={engine} onChange={(e) => setEngine(e.target.value as TranscribeEngine)}>
              <option value="ocr">{labels.transcribe.ocr} — đọc phụ đề có sẵn (mặc định)</option>
              <option value="whisper">{labels.transcribe.whisper}</option>
              {(labels.isAdmin || engine === 'sensevoice') && <option value="sensevoice">{labels.transcribe.sensevoice}</option>}
            </select>
          </label>
          <label className="text-sm text-neutral-300">
            Loại giọng đọc
            <select
              className={`${inputClass} mt-1`}
              value={ttsEngine}
              onChange={(e) => {
                setTtsEngine(e.target.value as TTSEngine)
                setVoice('')
              }}
            >
              <option value="capcut">{labels.tts.capcut}</option>
              <option value="vieneu">{labels.tts.vieneu}</option>
            </select>
          </label>
          <label className="text-sm text-neutral-300">
            Giọng đọc
            <select className={`${inputClass} mt-1`} value={voice} onChange={(e) => setVoice(e.target.value)}>
              <option value="">(mặc định server)</option>
              {voiceOptions.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.label}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm text-neutral-300">
            Âm thanh gốc
            <select
              className={`${inputClass} mt-1`}
              value={audioMode}
              onChange={(e) => setAudioMode(e.target.value as typeof audioMode)}
            >
              <option value="original">Giữ nguyên âm thanh gốc (mặc định)</option>
              <option value="separated">{labels.separatedAudio}</option>
              <option value="mute">Tắt hoàn toàn âm thanh gốc</option>
            </select>
          </label>
          {audioMode === 'original' && (
            <label className="text-sm text-neutral-300">
              Âm lượng âm thanh gốc (dB)
              <input
                type="number"
                step={1}
                className={`${inputClass} mt-1`}
                value={originalVolumeDb}
                onChange={(e) => setOriginalVolumeDb(Number(e.target.value))}
              />
            </label>
          )}
          <label className="text-sm text-neutral-300">
            Âm lượng nhạc nền tự thêm (dB)
            <input
              type="number"
              step={1}
              className={`${inputClass} mt-1`}
              value={musicVolumeDb}
              onChange={(e) => setMusicVolumeDb(Number(e.target.value))}
            />
          </label>
          <BlurStrengthControl value={blurStrength} onChange={setBlurStrength} />
          <div className="text-sm text-neutral-300">
            Ảnh bìa tiếng Việt
            <label className="mt-1 flex items-center gap-2">
              <input type="checkbox" checked={coverEnabled} onChange={(e) => setCoverEnabled(e.target.checked)} />
              Thay ảnh bìa chữ Trung (khung đầu video) bằng tiêu đề Việt
            </label>
            {coverEnabled && (
              <div className="mt-2 flex flex-wrap items-center gap-4">
                <label className="flex items-center gap-2">
                  Màu nền
                  <input type="color" value={coverBg} onChange={(e) => setCoverBg(e.target.value)} className="h-8 w-12 cursor-pointer" />
                </label>
                <label className="flex items-center gap-2">
                  Màu chữ
                  <input type="color" value={coverFg} onChange={(e) => setCoverFg(e.target.value)} className="h-8 w-12 cursor-pointer" />
                </label>
                <span
                  className="rounded-full px-3 py-1 text-sm font-bold"
                  style={{ background: coverBg, color: coverFg }}
                >
                  Xem trước màu
                </span>
              </div>
            )}
            <span className="mt-1 block text-xs text-neutral-500">
              Áp dụng cho video xử lý sau khi lưu. Sửa tiêu đề từng video trong trang dự án pipeline của video đó.
            </span>
          </div>
          <label className="text-sm text-neutral-300">
            Hashtag cố định (luôn thêm vào caption)
            <input
              type="text"
              className={`${inputClass} mt-1`}
              placeholder="#Pokemon #AIContent"
              value={captionHashtags}
              onChange={(e) => setCaptionHashtags(e.target.value)}
            />
            <span className="text-xs text-neutral-500">Viết liền không dấu, cách nhau bằng dấu cách.</span>
          </label>
          <label className="text-sm text-neutral-300">
            Cỡ chữ phụ đề mới
            <input
              type="number"
              step={1}
              min={1}
              className={`${inputClass} mt-1`}
              value={subtitleFontSize}
              onChange={(e) => setSubtitleFontSize(Number(e.target.value))}
            />
          </label>
          <label className="text-sm text-neutral-300">
            Video chậm tối đa
            <select
              className={`${inputClass} mt-1`}
              value={minVideoSpeed}
              onChange={(e) => setMinVideoSpeed(Number(e.target.value))}
            >
              {MIN_VIDEO_SPEED_OPTIONS.map((v) => (
                <option key={v} value={v}>
                  Chậm tối đa {Math.round((1 - v) * 100)}% ({v.toFixed(2)}x)
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1.5 text-sm text-neutral-300">
            <input
              type="checkbox"
              checked={useViesnapFallback}
              onChange={(e) => setUseViesnapFallback(e.target.checked)}
            />
            {labels.isAdmin
              ? 'Dùng dịch vụ bên thứ 3 (viesnap) làm tầng dự phòng dò link tải — giảm bị Douyin giới hạn tốc độ API'
              : 'Dùng dịch vụ tải bên ngoài làm dự phòng khi lấy link video — giảm bị Douyin giới hạn'}
          </label>
        </div>

        <div>
          <p className="mb-1.5 text-sm text-neutral-300">
            Khoanh vùng đọc phụ đề (tuỳ chọn, dùng chung mọi video của dự án — để trống = quét mặc định)
          </p>
          <p className="mb-1.5 text-xs text-neutral-500">
            Thu hẹp vùng đọc chữ giúp giảm hẳn việc đọc nhầm hoạ tiết/nhân vật phức tạp ngoài dải phụ đề thành "chữ
            giả" — áp dụng cho cả bước đọc lời thoại lẫn bước tự dò vùng che phụ đề cũ.
          </p>
          {previewProjectQuery.data?.video_url ? (
            <OcrCropSelector
              videoUrl={previewProjectQuery.data.video_url}
              crop={ocrCrop}
              onChange={(region) => {
                setOcrCrop(region)
                ocrCropMutation.mutate(region)
              }}
            />
          ) : (
            <p className="text-xs text-neutral-500">
              Chưa có video nào từ dự án này để xem trước — kích hoạt ít nhất 1 video rồi quay lại đây khoanh vùng.
            </p>
          )}
          {ocrCropMutation.error && <p className="text-sm text-danger">{(ocrCropMutation.error as Error).message}</p>}
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="field">
            <label>Logo (tuỳ chọn, dùng chung mọi video của dự án)</label>
            <div className="mt-1 space-y-2">
              <img
                key={logoVersion}
                className="h-16 w-16 rounded object-contain bg-black"
                src={`${api.socialLogoUrl(socialId!)}?v=${logoVersion}`}
                onError={(e) => (e.currentTarget.style.display = 'none')}
                onLoad={(e) => (e.currentTarget.style.display = '')}
              />
              <div className="flex items-center gap-2">
                <input
                  ref={logoRef}
                  type="file"
                  accept="image/*"
                  onChange={(e) => {
                    const f = e.target.files?.[0]
                    if (f) uploadLogoMutation.mutate(f)
                  }}
                />
                <button
                  type="button"
                  className="btn btn-ghost btn-sm text-danger"
                  onClick={() => {
                    deleteLogoMutation.mutate()
                    if (logoRef.current) logoRef.current.value = ''
                  }}
                >
                  Bỏ
                </button>
              </div>
            </div>
          </div>
          <div className="field">
            <label>Nhạc nền (tuỳ chọn, dùng chung mọi video của dự án)</label>
            <div className="mt-1 space-y-2">
              <audio
                key={musicVersion}
                className="h-8 w-full"
                controls
                preload="metadata"
                src={`${api.socialMusicUrl(socialId!)}?v=${musicVersion}`}
              />
              <div className="flex items-center gap-2">
                <input
                  ref={musicRef}
                  type="file"
                  accept="audio/*"
                  onChange={(e) => {
                    const f = e.target.files?.[0]
                    if (f) uploadMusicMutation.mutate(f)
                  }}
                />
                <button
                  type="button"
                  className="btn btn-ghost btn-sm text-danger"
                  onClick={() => {
                    deleteMusicMutation.mutate()
                    if (musicRef.current) musicRef.current.value = ''
                  }}
                >
                  Bỏ
                </button>
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            className={primaryButtonClass}
            disabled={saveSettingsMutation.isPending}
            onClick={() => saveSettingsMutation.mutate()}
          >
            {saveSettingsMutation.isPending ? 'Đang lưu...' : 'Lưu cài đặt'}
          </button>
          {saveSettingsMutation.isSuccess && <span className="text-xs text-neutral-500">Đã lưu — áp dụng cho lần kích hoạt tiếp theo.</span>}
        </div>
        {saveSettingsMutation.error && <p className="text-sm text-danger">{(saveSettingsMutation.error as Error).message}</p>}
      </details>

      <div id="queue-top" className="mb-2 flex flex-wrap items-center justify-between gap-2 scroll-mt-4">
        <h2 className="text-lg">Hàng đợi video ({state.queue.length})</h2>
        <div className="flex flex-wrap items-center gap-2 text-sm text-neutral-300">
          <label className="flex items-center gap-1.5">
            Lọc
            <select
              className={`${inputClass} mt-0`}
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value as QueueItemStatus | 'all')
                setQueuePage(1)
              }}
            >
              <option value="all">
                Tất cả (trừ đã bỏ qua) — {state.queue.filter((i) => i.status !== 'skipped').length}
              </option>
              {(Object.keys(STATUS_LABEL) as QueueItemStatus[]).map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABEL[s]} — {state.queue.filter((i) => i.status === s).length}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1.5">
            Sắp xếp
            <select className={`${inputClass} mt-0`} value={sortMode} onChange={(e) => {
                setSortMode(e.target.value as typeof sortMode)
                setQueuePage(1)
              }}>
              <option value="oldest">Cũ → mới, theo ngày đăng Douyin (mặc định)</option>
              <option value="newest">Mới → cũ</option>
              <option value="smart">Ưu tiên đang hoạt động</option>
            </select>
          </label>
        </div>
      </div>
      {state.queue.length === 0 ? (
        <div className="empty-state">
          <div className="empty-icon">▸</div>
          Chưa có video nào — bấm "Crawl ngay" để quét từ trang Douyin nguồn.
        </div>
      ) : (
        <div className="space-y-2">
          {(() => {
            // "Tất cả" ẩn video đã bỏ qua (người dùng không muốn thấy nữa) — xem
            // lại chúng qua lọc "Đã bỏ qua".
            const filtered =
              statusFilter === 'all'
                ? state.queue.filter((i) => i.status !== 'skipped')
                : state.queue.filter((i) => i.status === statusFilter)
            // Ưu tiên hiện video ĐANG CÓ HOẠT ĐỘNG (processing/failed/ready)
            // lên đầu — trước đây sort thẳng theo discovered_at giảm dần,
            // hàng đợi lớn (hàng trăm/nghìn video) khiến video đang chạy (luôn
            // là video CŨ NHẤT, kích hoạt theo thứ tự cũ→mới) bị chôn tận đáy
            // danh sách, nhìn như "không có gì cập nhật" dù bot đang chạy
            // thật (đã xác nhận thật qua dữ liệu: hàng đợi 944 video, 8 video
            // mới quét nhất đều pending, 5 video có hoạt động thật nằm ở top
            // các video CŨ NHẤT — hoàn toàn khuất khỏi màn hình đầu). Người
            // dùng chọn được sort khác (cũ/mới nhất trước) qua ô "Sắp xếp".
            const PRIORITY: Record<QueueItemStatus, number> = {
              processing: 0,
              failed: 1,
              ready: 2,
              pending: 3,
              posted: 4,
              skipped: 5,
            }
            const sorted = [...filtered].sort((a, b) => {
              if (sortMode === 'oldest') return compareVideoAge(a, b)
              if (sortMode === 'newest') return compareVideoAge(b, a)
              const pd = PRIORITY[a.status] - PRIORITY[b.status]
              if (pd !== 0) return pd
              // Trong cùng nhóm pending: cũ nhất trước (đúng thứ tự kích hoạt
              // tiếp theo). Nhóm khác: mới nhất trước.
              return a.status === 'pending' ? compareVideoAge(a, b) : compareVideoAge(b, a)
            })
            // Phân trang thay cho giới hạn cứng 60 video cũ (phần còn lại bị
            // ẩn, không xem được) — vẫn nhẹ trang vì chỉ vẽ 1 trang mỗi lần.
            const pageCount = Math.max(1, Math.ceil(sorted.length / QUEUE_PAGE_SIZE))
            const page = Math.min(queuePage, pageCount)
            const visible = sorted.slice((page - 1) * QUEUE_PAGE_SIZE, page * QUEUE_PAGE_SIZE)
            const canSkip = (i: QueueItem) => i.status !== 'posted' && i.status !== 'skipped'
            const selectable = visible.filter(canSkip)
            const chosen = selectable.filter((i) => selectedIds.has(i.aweme_id))
            const allChosen = selectable.length > 0 && chosen.length === selectable.length
            const toggleOne = (id: string) =>
              setSelectedIds((prev) => {
                const next = new Set(prev)
                if (next.has(id)) next.delete(id)
                else next.add(id)
                return next
              })
            const runBulkSkip = () => {
              if (!chosen.length) return
              const busy = chosen.filter((i) => i.status === 'processing' || i.status === 'ready').length
              const msg =
                `Bỏ qua ${chosen.length} video đã chọn? App sẽ không tự xử lý/đăng các video này nữa (bấm "Huỷ bỏ qua" từng video để lấy lại).` +
                (busy ? `

Trong đó có ${busy} video đang xử lý hoặc sẵn sàng đăng.` : '')
              if (!window.confirm(msg)) return
              setBulkMessage(null)
              skipBulkMutation.mutate(chosen.map((i) => i.aweme_id))
            }
            return (
              <>
                <div className="flex flex-wrap items-center gap-3 rounded-lg border border-neutral-800 px-3 py-2 text-sm">
                  <label className="flex items-center gap-2 text-neutral-300">
                    <input
                      type="checkbox"
                      checked={allChosen}
                      disabled={!selectable.length}
                      ref={(el) => {
                        if (el) el.indeterminate = chosen.length > 0 && !allChosen
                      }}
                      onChange={() =>
                        setSelectedIds(allChosen ? new Set() : new Set(selectable.map((i) => i.aweme_id)))
                      }
                    />
                    Chọn tất cả trong trang ({selectable.length})
                  </label>
                  {chosen.length > 0 && (
                    <>
                      <span className="text-neutral-400">Đã chọn {chosen.length}</span>
                      <button
                        type="button"
                        className="btn btn-sm btn-ghost text-danger"
                        disabled={skipBulkMutation.isPending}
                        onClick={runBulkSkip}
                      >
                        {skipBulkMutation.isPending ? 'Đang bỏ qua...' : `Bỏ qua ${chosen.length} video`}
                      </button>
                      <button type="button" className="btn btn-sm btn-ghost" onClick={() => setSelectedIds(new Set())}>
                        Bỏ chọn
                      </button>
                    </>
                  )}
                  {bulkMessage && !chosen.length && <span className="text-xs text-neutral-400">{bulkMessage}</span>}
                  {skipBulkMutation.error && (
                    <span className="text-xs text-danger">{(skipBulkMutation.error as Error).message}</span>
                  )}
                </div>
                {visible.map((item: QueueItem) => (
                  <div
                    key={item.aweme_id}
                    className={`flex gap-3 rounded-lg border p-2 ${selectedIds.has(item.aweme_id) ? 'border-accent-700 bg-accent-900/20' : 'border-neutral-800'}`}
                  >
                    <div className="flex w-5 shrink-0 items-center justify-center">
                      {canSkip(item) && (
                        <input
                          type="checkbox"
                          aria-label="Chọn video để bỏ qua"
                          checked={selectedIds.has(item.aweme_id)}
                          onChange={() => toggleOne(item.aweme_id)}
                        />
                      )}
                    </div>
                    <div className="aspect-video w-32 shrink-0 overflow-hidden rounded-md bg-black">
                      {item.thumb_url && <img className="h-full w-full object-cover" src={item.thumb_url} loading="lazy" />}
                    </div>
                    <div className="min-w-0 flex-1 space-y-1 text-sm">
                      <p className="line-clamp-2 text-neutral-200">{item.title_vi || item.title || item.aweme_id}</p>
                      {item.title_vi && item.title && (
                        <p className="line-clamp-1 text-xs text-neutral-500">{item.title}</p>
                      )}
                      <div className="flex flex-wrap items-center gap-2 text-xs text-neutral-500">
                        <span className={`tag ${STATUS_TAG_CLASS[item.status]}`}>{STATUS_LABEL[item.status]}</span>
                        {item.duration_sec > 0 && <span>{item.duration_sec.toFixed(1)}s</span>}
                        {item.files_cleaned_at && (
                          <span title="Đã tự xoá video gốc/thành phẩm/audio để giải phóng ổ đĩa — còn giữ phụ đề và cấu hình">
                            Đã dọn file
                          </span>
                        )}
                        {(() => {
                          const c = cleanupPlanQuery.data?.[item.aweme_id]
                          if (!c || item.files_cleaned_at) return null
                          const toggle = (c.kept_by_user || c.due_at) && (
                            <button
                              type="button"
                              className="text-neutral-400 underline hover:text-text"
                              disabled={keepMutation.isPending}
                              onClick={() => keepMutation.mutate({ projectId: c.project_id, keep: !c.kept_by_user })}
                            >
                              {c.kept_by_user ? 'Cho phép tự xoá' : 'Giữ lại'}
                            </button>
                          )
                          if (!c.due_at) {
                            return (
                              <>
                                <span title={c.protected_reason ?? ''}>
                                  {c.kept_by_user ? 'Đã chọn giữ file' : 'Giữ file'} ({c.size_mb}MB)
                                </span>
                                {toggle}
                              </>
                            )
                          }
                          const due = new Date(c.due_at)
                          return (
                            <>
                              <span
                                className="text-accent-300"
                                title={`${c.rule_label} — tự xoá video gốc/thành phẩm/audio, giữ phụ đề và cấu hình`}
                              >
                                {due.getTime() <= Date.now()
                                  ? `Tự dọn ở lượt tới (${c.size_mb}MB)`
                                  : `Tự dọn file lúc ${due.toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })} (${c.size_mb}MB)`}
                              </span>
                              {toggle}
                            </>
                          )
                        })()}
                        {item.project_id && (
                          <Link to={`/projects/${item.project_id}`} className="text-accent-300 hover:underline">
                            Xem dự án pipeline
                          </Link>
                        )}
                        {item.error && (
                          <button
                            type="button"
                            className="text-danger hover:underline"
                            onClick={() => setErrorDialogItem(item)}
                          >
                            Xem lỗi
                          </button>
                        )}
                        {item.project_id && item.status !== 'skipped' && (
                          <button type="button" className="text-accent-300 hover:underline" onClick={() => setCaptionItem(item)}>
                            {item.caption_vi ? 'Xem / sửa caption' : 'Caption'}
                          </button>
                        )}
                      </div>
                      {(item.tiktok_posted_at || item.fb_posted_at || (item.status === 'ready' && state.facebook_enabled)) && (
                        <div className="flex flex-wrap items-center gap-2 text-xs">
                          {(state.tiktok_enabled || item.tiktok_posted_at) && (
                            <span className={item.tiktok_posted_at ? 'text-accent-300' : 'text-neutral-500'}>
                              TikTok:{' '}
                              {item.tiktok_posted_at
                                ? `đã đăng ${new Date(item.tiktok_posted_at).toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })}`
                                : 'chờ đăng'}
                            </span>
                          )}
                          {(state.facebook_enabled || item.fb_posted_at) && (
                            <span className={item.fb_posted_at ? 'text-accent-300' : 'text-neutral-500'}>
                              Facebook:{' '}
                              {item.fb_posted_at ? (
                                item.fb_permalink ? (
                                  <a href={item.fb_permalink} target="_blank" rel="noreferrer" className="hover:underline">
                                    đã đăng{' '}
                                    {new Date(item.fb_posted_at).toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })}{' '}
                                    ↗
                                  </a>
                                ) : (
                                  `đã đăng ${new Date(item.fb_posted_at).toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })}`
                                )
                              ) : (
                                'chờ đăng'
                              )}
                            </span>
                          )}
                        </div>
                      )}
                      {/* Đăng lỗi vẫn là "Sẵn sàng đăng" — chỉ báo lỗi lần đăng gần nhất. */}
                      {item.status === 'ready' && item.publish_error && !item.tiktok_posted_at && (
                        <p className="mt-1 text-xs text-danger" title={item.publish_error}>
                          Đăng TikTok lỗi lần {item.publish_fail_count ?? 1}: {item.publish_error.slice(0, 160)}
                          {(item.publish_fail_count ?? 0) >= 3
                            ? ' — đã ngừng tự thử, bấm "Đăng lại".'
                            : ' — app sẽ tự thử lại sau 30 phút.'}
                        </p>
                      )}
                      {item.status === 'ready' && item.fb_publish_error && !item.fb_posted_at && (
                        <p className="mt-1 text-xs text-danger" title={item.fb_publish_error}>
                          Đăng Facebook lỗi lần {item.fb_publish_fail_count ?? 1}: {item.fb_publish_error.slice(0, 200)}
                          {(item.fb_publish_fail_count ?? 0) >= 3
                            ? ' — đã ngừng tự thử, bấm "Đăng lại Facebook".'
                            : ' — app sẽ tự thử lại sau 30 phút.'}
                        </p>
                      )}
                    </div>
                    <div className="flex shrink-0 flex-col gap-1 self-center">
                      {item.share_url && (
                        <a
                          className="btn btn-ghost btn-sm text-center"
                          href={item.share_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Xem video
                        </a>
                      )}
                      {(item.status === 'pending' || (item.status === 'failed' && item.failed_stage === 'activate')) && (
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          disabled={activateMutation.isPending}
                          onClick={() => activateMutation.mutate(item.aweme_id)}
                        >
                          {item.status === 'failed' ? 'Thử lại (đưa về hàng chờ)' : 'Kích hoạt'}
                        </button>
                      )}
                      {(item.status === 'ready' || (item.status === 'failed' && item.failed_stage === 'publish')) &&
                        !item.tiktok_posted_at && (
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            disabled={busyPublish && publishingId === item.aweme_id}
                            onClick={() => publishMutation.mutate(item.aweme_id)}
                          >
                            {busyPublish && publishingId === item.aweme_id
                              ? 'Đang đăng...'
                              : item.status === 'failed' || item.publish_error
                                ? 'Đăng lại'
                                : 'Đăng lên TikTok'}
                          </button>
                        )}
                      {/* Video đã "posted" (vd đăng TikTok trước khi bật Facebook) vẫn đăng tay
                          lên Facebook được, miễn còn file đã xuất. */}
                      {(item.status === 'ready' || (item.status === 'posted' && !item.files_cleaned_at)) &&
                        !!state.facebook_page_id &&
                        !item.fb_posted_at &&
                        !!item.project_id && (
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            disabled={busyFbPublish}
                            onClick={() => fbPublishMutation.mutate(item.aweme_id)}
                          >
                            {busyFbPublish && fbPublishingId === item.aweme_id
                              ? 'Đang đăng FB...'
                              : item.fb_publish_error
                                ? 'Đăng lại Facebook'
                                : 'Đăng lên Facebook'}
                          </button>
                        )}
                      {item.status === 'skipped' ? (
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          disabled={unskipMutation.isPending}
                          onClick={() => unskipMutation.mutate(item.aweme_id)}
                        >
                          Huỷ bỏ qua
                        </button>
                      ) : (
                        item.status !== 'posted' && (
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm text-danger"
                            disabled={skipMutation.isPending}
                            onClick={() => skipMutation.mutate(item.aweme_id)}
                          >
                            Bỏ qua
                          </button>
                        )
                      )}
                    </div>
                  </div>
                ))}
                {pageCount > 1 && (
                  <QueuePager
                    page={page}
                    pageCount={pageCount}
                    total={sorted.length}
                    onChange={(n) => {
                      setQueuePage(n)
                      document.getElementById('queue-top')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                    }}
                  />
                )}
              </>
            )
          })()}
        </div>
      )}

      {captionItem && (
        <CaptionDialog socialId={socialId!} item={captionItem} onClose={() => setCaptionItem(null)} onSaved={refresh} />
      )}
      {errorDialogItem && (
        <div className="dialog-backdrop" onClick={() => setErrorDialogItem(null)}>
          <div className="dialog max-w-2xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="dialog-title">Chi tiết lỗi — {errorDialogItem.title_vi || errorDialogItem.title || errorDialogItem.aweme_id}</h3>
            <p className="dialog-body mono max-h-96 overflow-auto whitespace-pre-wrap text-xs">{errorDialogItem.error}</p>
            <div className="dialog-actions">
              <button type="button" className="btn btn-primary btn-sm" onClick={() => setErrorDialogItem(null)}>
                Đóng
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
