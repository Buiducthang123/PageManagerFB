import { useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, type AudioMode, type ProjectState } from '../lib/api'
import { inputClass, primaryButtonClass, secondaryButtonClass } from '../lib/ui'
import JobProgressBar from './JobProgressBar'
import OcrCropSelector, { type CropRegion } from './OcrCropSelector'
import { useJobStatus } from '../hooks/useJobStatus'

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
  const musicRef = useRef<HTMLInputElement>(null)
  const logoRef = useRef<HTMLInputElement>(null)
  const [audioMode, setAudioMode] = useState<AudioMode>('separated')
  const [minVideoSpeed, setMinVideoSpeed] = useState(0.85)

  const exportRecord = project.export
  const busyExport = exportRecord.status === 'running'
  const exportJob = useJobStatus(projectId, 'export')

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['project', projectId] })

  const blurMutation = useMutation({
    mutationFn: (region: CropRegion | null) => api.updateExportBlurRegion(projectId, region),
    onSuccess: () => refresh(),
  })

  const detectBlurMutation = useMutation({
    mutationFn: () => api.detectExportBlurRegion(projectId),
    onSuccess: () => refresh(),
  })

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
    mutationFn: () => api.startExport(projectId, audioMode, minVideoSpeed),
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
        Dựng video hoàn chỉnh bằng ffmpeg (che phụ đề cũ, chèn phụ đề mới, trộn nhạc nền, chèn logo) rồi xuất thẳng ra
        1 file mp4 — <b>không cần mở CapCut</b>. Tuỳ chọn thêm, chạy song song với "Dựng CapCut", không ảnh hưởng.
      </p>

      {videoUrl && (
        <div>
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <p className="text-sm text-neutral-300">Khoanh vùng che phụ đề cũ (để trống = không che)</p>
            <button
              type="button"
              className="btn btn-ghost btn-sm shrink-0"
              disabled={detectBlurMutation.isPending || busyAny}
              onClick={() => detectBlurMutation.mutate()}
            >
              {detectBlurMutation.isPending ? 'Đang dò...' : 'Tự động phát hiện'}
            </button>
          </div>
          {detectBlurMutation.error && (
            <p className="mb-1.5 text-xs text-danger">{(detectBlurMutation.error as Error).message}</p>
          )}
          <OcrCropSelector
            videoUrl={videoUrl}
            crop={blurRegion}
            onChange={(region) => blurMutation.mutate(region)}
          />
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
            <option value="separated">Tách nhạc nền/SFX bằng demucs (mặc định)</option>
            <option value="original">Giữ nguyên âm thanh gốc</option>
            <option value="mute">Tắt hoàn toàn âm thanh gốc</option>
          </select>
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
          <video className="max-h-96 w-full rounded-lg bg-black" src={api.exportVideoUrl(projectId)} controls preload="metadata" />
          <div className="flex flex-wrap items-center gap-2">
            <a className={secondaryButtonClass} href={api.exportVideoUrl(projectId)} download>
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
