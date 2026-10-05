import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, DEFAULT_ORIGINAL_AUDIO_VOLUME_DB, DEFAULT_SUBTITLE_FONT_SIZE, type AudioMode, type ProjectState } from '../lib/api'
import { inputClass, primaryButtonClass, secondaryButtonClass } from '../lib/ui'
import JobProgressBar from './JobProgressBar'
import OcrCropSelector, { type CropRegion } from './OcrCropSelector'
import BlurStrengthControl, { DEFAULT_BLUR_STRENGTH } from './BlurStrengthControl'

const DEFAULT_MUSIC_VOLUME_DB = -13
import { useLabels } from '../lib/labels'
import { useJobStatus } from '../hooks/useJobStatus'

// Thời gian "1 vòng" xử lý: từ lúc có video gốc (ingest xong) đến lúc xuất xong.
// Dùng cho dự án chạy tự động (ingest → ... → export liền mạch) để user biết mỗi
// video mất bao lâu. Trả null nếu thiếu mốc hoặc số vô lý (vd xuất lại sau nhiều
// ngày, khi đó hiệu số tính cả thời gian để không — bỏ qua cho khỏi gây hiểu nhầm).
export function runDurationLabel(ingestAt?: string | null, exportAt?: string | null): string | null {
  if (!ingestAt || !exportAt) return null
  const s = (new Date(exportAt).getTime() - new Date(ingestAt).getTime()) / 1000
  if (!(s > 0) || s > 24 * 3600) return null
  if (s < 60) return `${Math.round(s)} giây`
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  return sec === 0 ? `${m} phút` : `${m} phút ${sec} giây`
}

export default function DirectExportPanel({
  projectId,
  project,
  videoUrl,
  ttsDone,
  busyAny,
}: {
  projectId: string
  project: ProjectState
  videoUrl: string | null
  ttsDone: boolean
  busyAny: boolean
}) {
  const queryClient = useQueryClient()
  const labels = useLabels()
  const musicRef = useRef<HTMLInputElement>(null)
  const logoRef = useRef<HTMLInputElement>(null)
  // Project chạy tự động (auto pipeline / dự án tự động) xuất bằng cấu hình
  // `auto_*` đã lưu — form phải hiện ĐÚNG cấu hình đó, không phải mặc định cố
  // định. Trước đây form luôn hiện "Giữ nguyên âm thanh gốc" trong khi bản
  // xuất tự động đang chạy "separated" (tách nhạc nền bằng demucs), gây hiểu
  // nhầm là app chạy sai cấu hình.
  const useAuto = project.auto_pipeline
  const [audioMode, setAudioMode] = useState<AudioMode>(useAuto ? project.auto_audio_mode ?? 'original' : 'original')
  const [originalAudioVolumeDb, setOriginalAudioVolumeDb] = useState(
    useAuto ? project.auto_original_audio_volume_db ?? DEFAULT_ORIGINAL_AUDIO_VOLUME_DB : DEFAULT_ORIGINAL_AUDIO_VOLUME_DB,
  )
  const [subtitleFontSize, setSubtitleFontSize] = useState(
    useAuto ? project.auto_subtitle_font_size ?? DEFAULT_SUBTITLE_FONT_SIZE : DEFAULT_SUBTITLE_FONT_SIZE,
  )
  const [minVideoSpeed, setMinVideoSpeed] = useState(useAuto ? project.auto_min_video_speed ?? 0.85 : 0.85)
  // Độ mờ nền: lấy giá trị đã lưu của project (dự án tự động copy sang lúc
  // kích hoạt, hoặc lần xuất tay trước đó), không có thì mặc định hệ thống.
  const [blurStrength, setBlurStrength] = useState(project.auto_blur_strength ?? DEFAULT_BLUR_STRENGTH)
  // Âm lượng nhạc nền tự thêm — mặc định -13dB, lưu lại theo project sau mỗi lần xuất.
  const [musicVolumeDb, setMusicVolumeDb] = useState(project.auto_music_volume_db ?? DEFAULT_MUSIC_VOLUME_DB)

  const exportRecord = project.export
  const busyExport = exportRecord.status === 'running'
  const exportJob = useJobStatus(projectId, 'export')

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['project', projectId] })

  const blurMutation = useMutation({
    mutationFn: (region: CropRegion | null) => api.updateExportBlurRegion(projectId, region),
    onSuccess: () => refresh(),
  })

  // Dò vùng che chạy NỀN (vài tới hơn 10 phút) — theo dõi tiến độ qua job,
  // làm mới project ngay khi dò xong để khung khoanh vùng cập nhật mà không
  // cần tải lại trang.
  const detectJobQuery = useQuery({
    queryKey: ['blur-detect-job', projectId],
    queryFn: () => api.detectExportBlurRegionStatus(projectId),
    // Vẫn hỏi thưa (5s) khi chưa chạy: lượt dò có thể do nơi khác khởi động
    // (tab khác, bộ lập lịch) — không hỏi thì trang đang mở không hề biết.
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 1500 : 5000),
  })
  const detectBlurMutation = useMutation({
    mutationFn: () => api.detectExportBlurRegion(projectId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['blur-detect-job', projectId] }),
  })
  const detectStatus = detectJobQuery.data?.status
  const detecting = detectBlurMutation.isPending || detectStatus === 'running'
  const prevDetectStatus = useRef<string | null | undefined>(undefined)
  useEffect(() => {
    if (prevDetectStatus.current === 'running' && detectStatus && detectStatus !== 'running') refresh()
    prevDetectStatus.current = detectStatus
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detectStatus])

  // Ép <audio>/<img> tải lại sau khi upload/xoá — URL cố định (không đoán
  // đuôi file nữa, xem exportMusicUrl/exportLogoUrl) nên trình duyệt có thể
  // cache response cũ (404 hoặc file cũ), phải đổi query string mỗi lần đổi.
  const [musicVersion, setMusicVersion] = useState(0)
  const [logoVersion, setLogoVersion] = useState(0)

  const musicMutation = useMutation({
    mutationFn: (file: File) => api.uploadExportMusic(projectId, file),
    onSuccess: () => {
      refresh()
      setMusicVersion((v) => v + 1)
    },
  })
  const deleteMusicMutation = useMutation({
    mutationFn: () => api.deleteExportMusic(projectId),
    onSuccess: () => {
      refresh()
      setMusicVersion((v) => v + 1)
    },
  })
  const logoMutation = useMutation({
    mutationFn: (file: File) => api.uploadExportLogo(projectId, file),
    onSuccess: () => {
      refresh()
      setLogoVersion((v) => v + 1)
    },
  })
  const deleteLogoMutation = useMutation({
    mutationFn: () => api.deleteExportLogo(projectId),
    onSuccess: () => {
      refresh()
      setLogoVersion((v) => v + 1)
    },
  })

  const startMutation = useMutation({
    mutationFn: () => api.startExport(projectId, audioMode, minVideoSpeed, originalAudioVolumeDb, subtitleFontSize, blurStrength, musicVolumeDb),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['job', projectId, 'export'] }),
  })

  const cancelMutation = useMutation({
    mutationFn: () => api.cancelJob(projectId, 'export'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['job', projectId, 'export'] }),
  })

  const revealMutation = useMutation({
    mutationFn: () => api.revealExportVideo(projectId),
  })

  const blurRegion = (project.export_blur_region as CropRegion | null) ?? null

  return (
    <div className="space-y-4">
      <p className="text-sm text-neutral-400">
        Dựng video hoàn chỉnh (che phụ đề cũ, chèn phụ đề mới, trộn nhạc nền, chèn logo) rồi xuất thẳng ra
        1 file mp4 — <b>không cần mở CapCut</b>. Tuỳ chọn thêm, chạy song song với "Dựng CapCut", không ảnh hưởng.
      </p>

      {videoUrl && (
        <div>
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <p className="text-sm text-neutral-300">Khoanh vùng che phụ đề cũ (để trống = không che)</p>
            <button
              type="button"
              className="btn btn-ghost btn-sm shrink-0"
              disabled={detecting || busyAny}
              onClick={() => detectBlurMutation.mutate()}
            >
              {detecting ? 'Đang dò...' : 'Tự động phát hiện'}
            </button>
          </div>
          {detectStatus === 'running' && (
            <p className="mb-1.5 text-xs text-neutral-400">
              {detectJobQuery.data?.current_label ?? 'Đang dò vùng phụ đề cũ...'} — có thể mất vài phút, cứ để trang mở
              hoặc quay lại sau.
            </p>
          )}
          {detectStatus === 'done' && detectJobQuery.data?.current_label && (
            <p className="mb-1.5 text-xs text-accent-300">{detectJobQuery.data.current_label}</p>
          )}
          {detectStatus === 'failed' && detectJobQuery.data?.error && (
            <p className="mb-1.5 text-xs text-danger">{detectJobQuery.data.error}</p>
          )}
          {detectBlurMutation.error && (
            <p className="mb-1.5 text-xs text-danger">{(detectBlurMutation.error as Error).message}</p>
          )}
          <OcrCropSelector
            videoUrl={videoUrl}
            crop={blurRegion}
            onChange={(region) => blurMutation.mutate(region)}
          />
          {blurRegion && (
            <div className="mt-3">
              <BlurStrengthControl value={blurStrength} onChange={setBlurStrength} disabled={busyAny} />
            </div>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="field">
          <label>Nhạc nền (tuỳ chọn)</label>
          <ExportAssetSlot
            kind="audio"
            currentUrl={`${api.exportMusicUrl(projectId)}?v=${musicVersion}`}
            onUpload={(f) => musicMutation.mutate(f)}
            onDelete={() => deleteMusicMutation.mutate()}
            inputRef={musicRef}
            accept="audio/*"
            busy={musicMutation.isPending || deleteMusicMutation.isPending}
          />
          <label className="mt-2 block text-xs text-neutral-400">
            Âm lượng nhạc nền (dB) — nhạc tự lặp lại hết video, nhỏ dần ở cuối
            <input
              type="number"
              step={1}
              min={-60}
              max={12}
              className={`${inputClass} mt-1 max-w-[8rem]`}
              value={musicVolumeDb}
              disabled={busyAny}
              onChange={(e) => setMusicVolumeDb(Number(e.target.value))}
            />
          </label>
        </div>
        <div className="field">
          <label>Logo (tuỳ chọn)</label>
          <ExportAssetSlot
            kind="image"
            currentUrl={`${api.exportLogoUrl(projectId)}?v=${logoVersion}`}
            onUpload={(f) => logoMutation.mutate(f)}
            onDelete={() => deleteLogoMutation.mutate()}
            inputRef={logoRef}
            accept="image/*"
            busy={logoMutation.isPending || deleteLogoMutation.isPending}
          />
        </div>
      </div>

      <details className="rounded-lg border border-neutral-800 p-3">
        <summary className="cursor-pointer text-sm text-neutral-300 select-none">Tuỳ chọn nâng cao</summary>
        <label className="mt-3 mb-3 block text-sm text-neutral-300">
          Âm thanh gốc
          <select
            className={`${inputClass} max-w-sm`}
            value={audioMode}
            disabled={busyAny}
            onChange={(e) => setAudioMode(e.target.value as AudioMode)}
          >
            <option value="original">Giữ nguyên âm thanh gốc (mặc định)</option>
            <option value="separated">{labels.separatedAudio}</option>
            <option value="mute">Tắt hoàn toàn âm thanh gốc</option>
          </select>
        </label>
        {audioMode === 'original' && (
          <label className="mt-3 mb-3 block text-sm text-neutral-300">
            Âm lượng âm thanh gốc (dB)
            <input
              type="number"
              step={1}
              className={`${inputClass} max-w-sm`}
              value={originalAudioVolumeDb}
              disabled={busyAny}
              onChange={(e) => setOriginalAudioVolumeDb(Number(e.target.value))}
            />
          </label>
        )}
        <label className="mt-3 mb-3 block text-sm text-neutral-300">
          Cỡ chữ phụ đề mới
          <input
            type="number"
            step={1}
            min={1}
            className={`${inputClass} max-w-sm`}
            value={subtitleFontSize}
            disabled={busyAny}
            onChange={(e) => setSubtitleFontSize(Number(e.target.value))}
          />
        </label>
        <label className="block text-sm text-neutral-300">
          Video được chậm tối đa
          <select
            className={`${inputClass} max-w-sm`}
            value={minVideoSpeed}
            disabled={busyAny}
            onChange={(e) => setMinVideoSpeed(Number(e.target.value))}
          >
            {[0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0].map((v) => (
              <option key={v} value={v}>
                Chậm tối đa {Math.round((1 - v) * 100)}% ({v.toFixed(2)}x)
              </option>
            ))}
          </select>
        </label>
      </details>

      {exportRecord.progress && exportRecord.status !== 'done' && (
        <p className="text-sm text-neutral-300">{exportRecord.progress}</p>
      )}
      {exportRecord.error && <p className="text-sm text-danger">{exportRecord.error}</p>}
      <JobProgressBar job={exportJob.data} />

      <div className="flex items-center gap-2">
        <button
          type="button"
          className={primaryButtonClass}
          disabled={!ttsDone || busyAny || startMutation.isPending}
          onClick={() => startMutation.mutate()}
        >
          {busyExport ? 'Đang xuất...' : exportRecord.status === 'done' ? 'Xuất lại video' : 'Xuất video'}
        </button>
        {busyExport && (
          <button
            type="button"
            className="btn btn-ghost btn-sm text-danger"
            disabled={cancelMutation.isPending}
            onClick={() => cancelMutation.mutate()}
          >
            {cancelMutation.isPending ? 'Đang dừng...' : 'Dừng'}
          </button>
        )}
      </div>
      {startMutation.error && <p className="mt-2 text-sm text-danger">{(startMutation.error as Error).message}</p>}
      {cancelMutation.error && <p className="mt-2 text-sm text-danger">{(cancelMutation.error as Error).message}</p>}

      {exportRecord.status === 'done' && (
        <div className="space-y-2">
          {runDurationLabel(project.stages.ingest?.at, exportRecord.at) && (
            <p className="text-xs text-neutral-400">
              ⏱ Hoàn thành trong{' '}
              <b className="text-accent-300">{runDurationLabel(project.stages.ingest?.at, exportRecord.at)}</b>{' '}
              <span className="text-neutral-500" title="Tính từ khi có video gốc (tải/upload xong) đến khi xuất xong file final.mp4">
                (từ lúc có video gốc đến khi xuất xong)
              </span>
            </p>
          )}
          {/* Thêm mốc xuất xong vào URL: file luôn tên final.mp4, nếu URL không
              đổi thì trình duyệt giữ bản cũ trong cache — xuất lại xong vẫn
              xem video cũ tới khi tải lại trang. `key` ép thẻ video nạp lại. */}
          <video
            key={exportRecord.at ?? 'final'}
            className="max-h-96 w-full rounded-lg bg-black"
            src={`${api.exportVideoUrl(projectId)}?v=${encodeURIComponent(exportRecord.at ?? '')}`}
            controls
            preload="metadata"
          />
          <div className="flex flex-wrap items-center gap-2">
            <a
              className={secondaryButtonClass}
              href={`${api.exportVideoUrl(projectId)}?v=${encodeURIComponent(exportRecord.at ?? '')}`}
              download
            >
              Tải video xuống
            </a>
            <button
              type="button"
              className={secondaryButtonClass}
              disabled={revealMutation.isPending}
              onClick={() => revealMutation.mutate()}
            >
              Mở thư mục
            </button>
          </div>
          {revealMutation.isError && (
            <p className="text-sm text-danger">Không mở được thư mục: {(revealMutation.error as Error).message}</p>
          )}
        </div>
      )}
    </div>
  )
}

function ExportAssetSlot({
  kind,
  currentUrl,
  onUpload,
  onDelete,
  inputRef,
  accept,
  busy,
}: {
  kind: 'audio' | 'image'
  currentUrl: string
  onUpload: (file: File) => void
  onDelete: () => void
  inputRef: React.RefObject<HTMLInputElement | null>
  accept: string
  busy: boolean
}) {
  const [hasFile, setHasFile] = useState<boolean | null>(null)

  return (
    <div className="mt-1 space-y-2">
      {hasFile !== false && (
        <div className="rounded-lg border border-neutral-800 p-2">
          {kind === 'audio' ? (
            <audio
              className="h-8 w-full"
              controls
              preload="metadata"
              src={currentUrl}
              onError={() => setHasFile(false)}
              onLoadedMetadata={() => setHasFile(true)}
            />
          ) : (
            <img
              className="h-16 w-16 rounded object-contain"
              src={currentUrl}
              onError={() => setHasFile(false)}
              onLoad={() => setHasFile(true)}
            />
          )}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) onUpload(f)
          }}
        />
        {hasFile !== false && (
          <button
            type="button"
            className="btn btn-ghost btn-sm text-danger"
            disabled={busy}
            onClick={() => {
              onDelete()
              setHasFile(false)
              if (inputRef.current) inputRef.current.value = ''
            }}
          >
            Bỏ
          </button>
        )}
      </div>
    </div>
  )
}
