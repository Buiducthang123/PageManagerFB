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
  const queryClient = useQueryClient()
  const [publishingId, setPublishingId] = useState<string | null>(null)
  const logoRef = useRef<HTMLInputElement>(null)
  const musicRef = useRef<HTMLInputElement>(null)
  const [errorDialogItem, setErrorDialogItem] = useState<QueueItem | null>(null)
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

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['social', socialId] })

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
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tiktokLoginStatus])

  const state = detailQuery.data

  if (detailQuery.isLoading || !state) {
    return (
      <div className="mx-auto max-w-5xl">
        <div className="skeleton h-24" />
      </div>
    )
  }

  const oldestPending = [...state.queue].filter((i) => i.status === 'pending').sort(compareVideoAge)[0]
  const oldestReady = [...state.queue].filter((i) => i.status === 'ready').sort(compareVideoAge)[0]

  // Giờ hẹn do backend tính sẵn (`next_post_at`: giãn cách ngẫu nhiên, chỉ
  // trong khung giờ cao điểm 7-9h, 12-13h, 19-22h). Chưa đăng bài nào thì
  // bộ lập lịch đăng ngay khi đang trong khung giờ — tự tính lại ở FE để
  // hiển thị đúng. Còn lệch vài phút theo tick ~60s của bộ lập lịch.
  const POSTING_WINDOWS: [number, number][] = [[7, 9], [12, 13], [19, 22]]
  const inPostingWindow = (d: Date) => POSTING_WINDOWS.some(([s, e]) => d.getHours() >= s && d.getHours() < e)
  const nextPostAt = state.next_post_at ? new Date(state.next_post_at) : null
  const nextPostDue = nextPostAt ? nextPostAt.getTime() <= Date.now() : inPostingWindow(new Date())
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
          <label className="ml-auto flex items-center gap-2 text-sm text-neutral-300">
            Số video/ngày (tối đa 3)
            <input
              type="number"
              min={1}
              max={3}
              className={`${inputClass} mt-0 w-20`}
              defaultValue={state.posts_per_day}
              onBlur={(e) => {
                const n = Math.min(3, Math.max(1, Number(e.target.value) || 1))
                e.target.value = String(n)
                if (n !== state.posts_per_day) updateMutation.mutate({ posts_per_day: n })
              }}
            />
          </label>
        </div>
        <JobProgressBar job={jobQuery.data} />
        {crawlMutation.error && <p className="text-sm text-danger">{(crawlMutation.error as Error).message}</p>}
        {activateMutation.error && <p className="text-sm text-danger">{(activateMutation.error as Error).message}</p>}
        {state.last_crawl_at && (
          <p className="text-xs text-neutral-500">Crawl lần cuối: {new Date(state.last_crawl_at).toLocaleString('vi-VN')}</p>
        )}
        <p className="text-xs text-neutral-500">
          {state.status !== 'active'
            ? 'Dự án đang tạm dừng — bộ lập lịch nền sẽ không tự crawl/kích hoạt/đăng cho tới khi bật lại.'
            : !oldestReady
              ? 'Chưa có video "Sẵn sàng đăng" nào trong hàng đợi — bộ lập lịch nền tự kích hoạt video cũ nhất rồi đăng khi có.'
              : nextPostDue
                ? 'Đã tới lượt đăng — bộ lập lịch nền (tick mỗi ~60s) sẽ tự đăng video cũ nhất sẵn sàng trong ít phút tới.'
                : nextPostAt
                  ? `Video tiếp theo dự kiến tự đăng lúc: ${nextPostAt.toLocaleString('vi-VN')} (${state.posts_per_day} video/ngày, giờ ngẫu nhiên trong khung 7-9h, 12-13h, 19-22h).`
                  : 'Chưa tới khung giờ đăng — bộ lập lịch chỉ đăng trong khung 7-9h, 12-13h, 19-22h.'}
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
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm text-neutral-300">Đăng bài TikTok (Playwright — Chrome thật)</span>
          <span
            className={`tag ${tiktokStatusQuery.data?.logged_in ? 'tag-accent' : 'tag-neutral'}`}
            title="Chỉ là gợi ý — không đảm bảo đăng nhập còn hợp lệ, phép thử thật là bấm 'Đăng lên TikTok'"
          >
            {tiktokStatusQuery.data?.logged_in ? 'Đã setup' : 'Chưa setup'}
          </span>
          <button
            type="button"
            className={secondaryButtonClass}
            disabled={busyTiktokLogin}
            onClick={() => tiktokLoginMutation.mutate()}
          >
            {busyTiktokLogin ? 'Đang chờ đăng nhập...' : 'Đăng nhập TikTok'}
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
          Bấm "Đăng nhập TikTok" — 1 cửa sổ Chrome thật sẽ mở lên, tự đăng nhập tay (kể cả 2FA/captcha), xong thì đóng
          cửa sổ đó lại. Chỉ cần làm 1 lần/tài khoản.
        </p>
        {tiktokLoginMutation.error && <p className="text-sm text-danger">{(tiktokLoginMutation.error as Error).message}</p>}
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
      </section>

      <details className="card mb-6 space-y-3 p-4">
        <summary className="cursor-pointer text-sm text-neutral-300 select-none">
          Cài đặt xử lý video (riêng cho dự án này — mặc định lúc tạo dự án mới lấy từ đây)
        </summary>
        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
          <label className="text-sm text-neutral-300">
            Engine phiên dịch
            <select className={`${inputClass} mt-1`} value={engine} onChange={(e) => setEngine(e.target.value as TranscribeEngine)}>
              <option value="ocr">OCR — đọc phụ đề cứng (mặc định)</option>
              <option value="whisper">Whisper (nhận diện giọng nói)</option>
              <option value="sensevoice">SenseVoice (nhận diện giọng nói)</option>
            </select>
          </label>
          <label className="text-sm text-neutral-300">
            Engine TTS
            <select
              className={`${inputClass} mt-1`}
              value={ttsEngine}
              onChange={(e) => {
                setTtsEngine(e.target.value as TTSEngine)
                setVoice('')
              }}
            >
              <option value="capcut">CapCut TTS</option>
              <option value="vieneu">VieNeu-TTS</option>
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
              <option value="separated">Tách nhạc nền/SFX bằng demucs</option>
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
            Dùng dịch vụ bên thứ 3 (viesnap) làm tầng dự phòng dò link tải — giảm bị Douyin giới hạn tốc độ API
          </label>
        </div>

        <div>
          <p className="mb-1.5 text-sm text-neutral-300">
            Khoanh vùng OCR quét (tuỳ chọn, dùng chung mọi video của dự án — để trống = quét mặc định)
          </p>
          <p className="mb-1.5 text-xs text-neutral-500">
            Thu hẹp vùng đọc chữ giúp giảm hẳn OCR đọc nhầm hoạ tiết/nhân vật phức tạp ngoài dải phụ đề thành "chữ
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

      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg">Hàng đợi video ({state.queue.length})</h2>
        <div className="flex flex-wrap items-center gap-2 text-sm text-neutral-300">
          <label className="flex items-center gap-1.5">
            Lọc
            <select
              className={`${inputClass} mt-0`}
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as QueueItemStatus | 'all')}
            >
              <option value="all">Tất cả</option>
              {(Object.keys(STATUS_LABEL) as QueueItemStatus[]).map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABEL[s]}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1.5">
            Sắp xếp
            <select className={`${inputClass} mt-0`} value={sortMode} onChange={(e) => setSortMode(e.target.value as typeof sortMode)}>
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
            const filtered = statusFilter === 'all' ? state.queue : state.queue.filter((i) => i.status === statusFilter)
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
            const LIMIT = 60
            const visible = sorted.slice(0, LIMIT)
            const hiddenCount = sorted.length - visible.length
            return (
              <>
                {visible.map((item: QueueItem) => (
                  <div key={item.aweme_id} className="flex gap-3 rounded-lg border border-neutral-800 p-2">
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
                      </div>
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
                      {(item.status === 'ready' || (item.status === 'failed' && item.failed_stage === 'publish')) && (
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          disabled={busyPublish && publishingId === item.aweme_id}
                          onClick={() => publishMutation.mutate(item.aweme_id)}
                        >
                          {busyPublish && publishingId === item.aweme_id
                            ? 'Đang đăng...'
                            : item.status === 'failed'
                              ? 'Đăng lại'
                              : 'Đăng lên TikTok'}
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
                {hiddenCount > 0 && (
                  <p className="py-2 text-center text-xs text-neutral-500">
                    ...còn {hiddenCount} video khác (đa số đang "Chờ xử lý", không hiện hết để tránh trang bị nặng).
                  </p>
                )}
              </>
            )
          })()}
        </div>
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
