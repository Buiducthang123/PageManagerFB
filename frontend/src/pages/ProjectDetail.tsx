import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  api,
  DEFAULT_MIN_VIDEO_SPEED,
  MIN_VIDEO_SPEED_OPTIONS,
  STAGE_LABELS,
  type AudioMode,
  type StageName,
  type StageRecord,
  type TranscribeEngine,
  type TTSEngine,
} from '../lib/api'
import { inputClass, primaryButtonClass, secondaryButtonClass } from '../lib/ui'
import StatusBadge from '../components/StatusBadge'
import JobProgressBar from '../components/JobProgressBar'
import EpisodeCard from '../components/EpisodeCard'
import ConfirmDialog from '../components/ConfirmDialog'
import SplitTimeline from '../components/SplitTimeline'
import { useJobStatus } from '../hooks/useJobStatus'

function StageHeader({
  name,
  record,
  busy,
  cancelling,
  onCancel,
}: {
  name: StageName
  record: StageRecord
  busy?: boolean
  cancelling?: boolean
  onCancel?: () => void
}) {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <h2 className="text-lg">{STAGE_LABELS[name]}</h2>
      <div className="flex items-center gap-2">
        {busy && onCancel && (
          <button type="button" className="btn btn-ghost btn-sm text-danger" disabled={cancelling} onClick={onCancel}>
            {cancelling ? 'Đang dừng...' : 'Dừng'}
          </button>
        )}
        <StatusBadge status={record.status} />
      </div>
    </div>
  )
}

export default function ProjectDetail() {
  const { projectId = '' } = useParams()
  const queryClient = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [ingestTab, setIngestTab] = useState<'file' | 'url'>('file')
  const [shareUrl, setShareUrl] = useState('')
  const [tab, setTab] = useState<'table' | 'zh' | 'vi' | 'entity'>('table')
  const [engine, setEngine] = useState<TranscribeEngine>('whisper')
  const [ttsEngine, setTtsEngine] = useState<TTSEngine>('capcut')
  const [voice, setVoice] = useState('')
  const [previewVoice, setPreviewVoice] = useState<string | null>(null)
  const [previewNonce, setPreviewNonce] = useState(0)
  const [editingCueId, setEditingCueId] = useState<number | null>(null)
  const [draftText, setDraftText] = useState('')
  const [audioMode, setAudioMode] = useState<AudioMode>('separated')
  const [minVideoSpeed, setMinVideoSpeed] = useState<number>(DEFAULT_MIN_VIDEO_SPEED)

  const [voiceInitialized, setVoiceInitialized] = useState(false)
  const [newEpisodeUrl, setNewEpisodeUrl] = useState('')
  const [deletingEpisodeId, setDeletingEpisodeId] = useState<string | null>(null)
  const [splitCount, setSplitCount] = useState(1)
  const [splitPoints, setSplitPoints] = useState<number[]>([])
  // "Đang dừng..." phải hiện xuyên suốt tới khi job THẬT SỰ dừng (không chỉ
  // trong lúc request cancel đang gửi) — nếu không, nút quay lại "Dừng" ngay
  // sau khi request xong dù job vẫn chạy tiếp vài chục giây, trông như bấm
  // không có tác dụng. Xoá cờ khi useJobStatus báo job đã chuyển trạng thái
  // xong hẳn (onSettled).
  const [pendingCancel, setPendingCancel] = useState<Partial<Record<StageName, boolean>>>({})

  const detailQuery = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => api.getProject(projectId),
    enabled: Boolean(projectId),
  })

  const settingsQuery = useQuery({ queryKey: ['settings'], queryFn: api.getSettings })

  // Nhớ giọng đọc/engine/audio_mode đã dùng lần trước cho dự án này — không
  // reset về mặc định mỗi lần vào lại trang (project.auto_voice/auto_engine/
  // auto_audio_mode được backend ghi lại sau mỗi lần chạy, xem main.py
  // `_start_tts`/`_start_assemble`).
  useEffect(() => {
    if (voiceInitialized) return
    const p = detailQuery.data?.project
    if (!p) return
    if (p.auto_voice) setVoice(p.auto_voice)
    if (p.auto_engine) setEngine(p.auto_engine)
    if (p.auto_tts_engine) setTtsEngine(p.auto_tts_engine)
    if (p.auto_audio_mode) setAudioMode(p.auto_audio_mode)
    if (p.auto_min_video_speed) setMinVideoSpeed(p.auto_min_video_speed)
    setVoiceInitialized(true)
  }, [detailQuery.data?.project, voiceInitialized])

  const splitMutation = useMutation({
    mutationFn: () => api.splitProject(projectId, splitPoints),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['project', projectId] }),
  })

  const setSplitCountAndReset = (n: number, duration: number) => {
    setSplitCount(n)
    setSplitPoints(n <= 1 ? [] : Array.from({ length: n - 1 }, (_, i) => (duration * (i + 1)) / n))
  }

  const createEpisodeMutation = useMutation({
    mutationFn: async (url: string) => {
      const created = await api.createEpisode(projectId)
      if (url.trim()) await api.ingestEpisodeUrl(projectId, created.episode.episode_id, url.trim())
      return created
    },
    onSuccess: () => {
      setNewEpisodeUrl('')
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
    },
  })

  const reorderEpisodesMutation = useMutation({
    mutationFn: (episodeIds: string[]) => api.reorderEpisodes(projectId, episodeIds),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['project', projectId] }),
  })

  const deleteEpisodeMutation = useMutation({
    mutationFn: (episodeId: string) => api.deleteEpisode(projectId, episodeId),
    onSuccess: () => {
      setDeletingEpisodeId(null)
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
    },
  })

  const clearPendingCancel = (stage: StageName) => setPendingCancel((prev) => ({ ...prev, [stage]: false }))
  const ingestJob = useJobStatus(projectId, 'ingest', undefined, () => clearPendingCancel('ingest'))
  const transcribeJob = useJobStatus(projectId, 'transcribe', undefined, () => clearPendingCancel('transcribe'))
  const translateJob = useJobStatus(projectId, 'translate', undefined, () => clearPendingCancel('translate'))
  const ttsJob = useJobStatus(projectId, 'tts', undefined, () => clearPendingCancel('tts'))
  const assembleJob = useJobStatus(projectId, 'assemble', undefined, () => clearPendingCancel('assemble'))

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['project', projectId] })

  const uploadMutation = useMutation({
    mutationFn: () => {
      if (!file) throw new Error('Chưa chọn file')
      return api.uploadVideo(projectId, file)
    },
    onSuccess: () => {
      setFile(null)
      if (fileRef.current) fileRef.current.value = ''
      refresh()
    },
  })

  const revealVideoMutation = useMutation({
    mutationFn: () => api.revealVideo(projectId),
  })

  const ingestUrlMutation = useMutation({
    mutationFn: () => {
      if (!shareUrl.trim()) throw new Error('Chưa dán link')
      return api.ingestUrl(projectId, shareUrl.trim())
    },
    onSuccess: () => {
      setShareUrl('')
      queryClient.invalidateQueries({ queryKey: ['job', projectId, 'ingest'] })
    },
  })

  const whisperMutation = useMutation({
    mutationFn: () => api.startTranscribe(projectId, engine),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['job', projectId, 'transcribe'] })
    },
  })

  const geminiMutation = useMutation({
    mutationFn: () => api.startTranslate(projectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['job', projectId, 'translate'] })
    },
  })

  const ttsMutation = useMutation({
    mutationFn: () => api.startTTS(projectId, voice, ttsEngine),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['job', projectId, 'tts'] })
    },
  })

  const retryTTSMutation = useMutation({
    mutationFn: () => api.retryTTS(projectId, voice),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['job', projectId, 'tts'] })
    },
  })

  const assembleMutation = useMutation({
    mutationFn: () => api.startAssemble(projectId, audioMode, minVideoSpeed),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['job', projectId, 'assemble'] })
    },
  })

  const updateCueMutation = useMutation({
    mutationFn: ({ cueId, text }: { cueId: number; text: string }) => api.updateCue(projectId, cueId, text),
    onSuccess: () => {
      setEditingCueId(null)
      refresh()
    },
  })

  const retranslateCueMutation = useMutation({
    mutationFn: (cueId: number) => api.retranslateCue(projectId, cueId),
    onSuccess: () => refresh(),
  })

  const voiceOptions = (ttsEngine === 'vieneu' ? settingsQuery.data?.tts_voices_vieneu : settingsQuery.data?.tts_voices) ?? []
  const selectedVoice = voiceOptions.some((v) => v.id === voice) ? voice : voiceOptions[0]?.id || ''

  const changeTtsEngine = (next: TTSEngine) => {
    setTtsEngine(next)
    setVoice('') // đổi engine thì đổi hẳn namespace giọng — về giọng đầu tiên của engine mới
  }

  const ttsCueMutation = useMutation({
    mutationFn: (cueId: number) => api.ttsCue(projectId, cueId, selectedVoice),
    onSuccess: () => refresh(),
  })

  const autoPipelineMutation = useMutation({
    mutationFn: (opts: {
      enabled: boolean
      engine?: TranscribeEngine
      ttsEngine?: TTSEngine
      voice?: string
      audioMode?: AudioMode
      minVideoSpeed?: number
    }) =>
      api.updateAutoPipeline(
        projectId,
        opts.enabled,
        opts.engine ?? engine,
        opts.ttsEngine ?? ttsEngine,
        opts.voice ?? selectedVoice,
        opts.audioMode ?? audioMode,
        opts.minVideoSpeed ?? minVideoSpeed,
      ),
    onSuccess: () => refresh(),
  })

  // Nếu "Tự động chạy hết pipeline" đang bật, đổi engine/giọng/audio_mode/
  // min_video_speed giữa chừng (khi pipeline mới chạy tới bước đầu, chưa tới
  // TTS/CapCut) phải đồng bộ lại cho backend ngay — nếu không, bước
  // auto-chain kế tiếp (main.py `_maybe_chain`) vẫn dùng giá trị auto_* CŨ đã
  // lưu, bỏ qua lựa chọn mới trên UI.
  const autoSyncSkipFirst = useRef(true)
  useEffect(() => {
    if (autoSyncSkipFirst.current) {
      autoSyncSkipFirst.current = false
      return
    }
    if (detailQuery.data?.project.auto_pipeline) {
      autoPipelineMutation.mutate({ enabled: true, engine, ttsEngine, voice: selectedVoice, audioMode, minVideoSpeed })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engine, ttsEngine, selectedVoice, audioMode, minVideoSpeed])

  const cancelMutation = useMutation({
    mutationFn: (stage: StageName) => api.cancelJob(projectId, stage),
    onSuccess: (_data, stage) => {
      queryClient.invalidateQueries({ queryKey: ['job', projectId, stage] })
      setPendingCancel((prev) => ({ ...prev, [stage]: true }))
    },
  })

  if (detailQuery.isLoading) return <div className="skeleton h-64 max-w-5xl" />
  if (detailQuery.error) {
    return (
      <p className="text-danger">
        {(detailQuery.error as Error).message} · <Link to="/">Về danh sách</Link>
      </p>
    )
  }

  const data = detailQuery.data
  if (!data) return null
  const { project, cues, entity_dict, video_url, tts_manifest } = data
  const ingest = project.stages.ingest
  const transcribe = project.stages.transcribe
  const translate = project.stages.translate
  const tts = project.stages.tts
  const assemble = project.stages.assemble
  const busyIngest = ingest.status === 'running' || ingestJob.data?.status === 'running'
  const busyWhisper = transcribe.status === 'running' || transcribeJob.data?.status === 'running'
  const busyGemini = translate.status === 'running' || translateJob.data?.status === 'running'
  const busyTTS = tts.status === 'running' || ttsJob.data?.status === 'running'
  const busyAssemble = assemble.status === 'running' || assembleJob.data?.status === 'running'
  const busyAny = busyIngest || busyWhisper || busyGemini || busyTTS || busyAssemble
  const manifestById = new Map(tts_manifest.map((m) => [m.id, m]))
  const isMulti = project.project_type === 'multi'
  const isSplit = project.split_mode
  const allEpisodesTTSDone = data.episodes.length > 0 && data.episodes.every((e) => e.episode.stages.tts.status === 'done')
  const splitEpisodes = [...data.episodes].sort((a, b) => a.episode.order - b.episode.order)

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <Link to="/" className="text-sm text-neutral-400">
          ← Dự án
        </Link>
        <h1 className="mt-2 text-2xl">{project.title}</h1>
        {cancelMutation.isError && <p className="mt-1 text-sm text-danger">{(cancelMutation.error as Error).message}</p>}
        <p className="font-mono text-xs text-neutral-500">{project.project_id}</p>
      </div>

      <section className="card flex flex-wrap items-center justify-between gap-3 p-4">
        <div>
          <label className="flex items-center gap-2 text-sm font-medium">
            <input
              type="checkbox"
              checked={project.auto_pipeline}
              disabled={autoPipelineMutation.isPending}
              onChange={(e) => autoPipelineMutation.mutate({ enabled: e.target.checked })}
            />
            Tự động chạy hết pipeline
          </label>
          <p className="mt-1 text-xs text-neutral-400">
            Bật thì sau khi có video gốc (upload/tải link), hệ thống tự chạy tiếp lần lượt Whisper → Gemini → TTS →
            CapCut cho tới hết, dùng engine/giọng đang chọn bên dưới ({engine === 'sensevoice' ? 'SenseVoice' : 'Whisper'}
            {' · '}
            {ttsEngine === 'vieneu' ? 'VieNeu-TTS' : 'CapCut TTS'}
            {selectedVoice ? ` · ${voiceOptions.find((v) => v.id === selectedVoice)?.label ?? selectedVoice}` : ''}).
            Dừng lại ở draft CapCut — review + export vẫn phải làm tay.
          </p>
        </div>
      </section>

      {isMulti && (
        <section className="card space-y-4 p-5">
          <h2 className="text-lg">Các tập</h2>

          <div className="flex flex-wrap items-end gap-3 rounded-lg border border-neutral-800 p-3">
            <label className="flex-1 text-sm text-neutral-300">
              Engine nhận diện
              <div className="mt-1 flex gap-4">
                <label className="flex items-center gap-1.5">
                  <input type="radio" checked={engine === 'whisper'} onChange={() => setEngine('whisper')} />
                  Whisper
                </label>
                <label className="flex items-center gap-1.5">
                  <input type="radio" checked={engine === 'sensevoice'} onChange={() => setEngine('sensevoice')} />
                  SenseVoice
                </label>
              </div>
            </label>
            <label className="flex-1 text-sm text-neutral-300">
              Engine TTS
              <div className="mt-1 flex gap-4">
                <label className="flex items-center gap-1.5">
                  <input type="radio" checked={ttsEngine === 'capcut'} onChange={() => changeTtsEngine('capcut')} />
                  CapCut TTS
                </label>
                <label className="flex items-center gap-1.5">
                  <input type="radio" checked={ttsEngine === 'vieneu'} onChange={() => changeTtsEngine('vieneu')} />
                  VieNeu-TTS
                </label>
              </div>
            </label>
            <label className="min-w-64 flex-1 text-sm text-neutral-300">
              Giọng đọc mặc định (áp dụng khi chạy TTS tập bất kỳ)
              <div className="mt-1 flex gap-2">
                <select className={`${inputClass} mt-0 flex-1`} value={selectedVoice} onChange={(e) => setVoice(e.target.value)}>
                  {voiceOptions.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.label}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className={secondaryButtonClass}
                  disabled={!selectedVoice}
                  onClick={() => {
                    setPreviewVoice(selectedVoice)
                    setPreviewNonce((n) => n + 1)
                  }}
                >
                  Nghe thử
                </button>
              </div>
            </label>
          </div>
          {previewVoice && (
            <audio
              key={previewNonce}
              className="h-8 w-full"
              autoPlay
              controls
              preload="auto"
              src={api.previewVoiceUrl(previewVoice, ttsEngine)}
            />
          )}

          {data.episodes.map((ep, i) => {
            const episodeIds = data.episodes.map((e) => e.episode.episode_id)
            const swapWith = (otherIndex: number) => {
              const ids = [...episodeIds]
              ;[ids[i], ids[otherIndex]] = [ids[otherIndex], ids[i]]
              reorderEpisodesMutation.mutate(ids)
            }
            return (
              <EpisodeCard
                key={ep.episode.episode_id}
                projectId={projectId}
                detail={ep}
                index={i}
                engine={engine}
                voice={selectedVoice}
                ttsEngine={ttsEngine}
                canMoveUp={i > 0}
                canMoveDown={i < data.episodes.length - 1}
                onMoveUp={() => swapWith(i - 1)}
                onMoveDown={() => swapWith(i + 1)}
                onDelete={() => setDeletingEpisodeId(ep.episode.episode_id)}
              />
            )
          })}

          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-dashed border-neutral-700 p-3">
            <input
              className={`${inputClass} mt-0 min-w-72 flex-1`}
              placeholder="Dán link Douyin/TikTok cho tập mới (có thể để trống rồi upload file sau)..."
              value={newEpisodeUrl}
              onChange={(e) => setNewEpisodeUrl(e.target.value)}
            />
            <button
              type="button"
              className={primaryButtonClass}
              disabled={createEpisodeMutation.isPending}
              onClick={() => createEpisodeMutation.mutate(newEpisodeUrl)}
            >
              {createEpisodeMutation.isPending ? 'Đang thêm...' : '+ Thêm tập'}
            </button>
          </div>
          {createEpisodeMutation.error && (
            <p className="text-sm text-danger">{(createEpisodeMutation.error as Error).message}</p>
          )}
        </section>
      )}

      {isSplit && (
        <section className="card space-y-4 p-5">
          <h2 className="text-lg">Các đoạn (đã chia từ video gốc — chạy tuần tự, mỗi đoạn 1 draft riêng)</h2>

          <div className="flex flex-wrap items-end gap-3 rounded-lg border border-neutral-800 p-3">
            <label className="flex-1 text-sm text-neutral-300">
              Engine nhận diện
              <div className="mt-1 flex gap-4">
                <label className="flex items-center gap-1.5">
                  <input type="radio" checked={engine === 'whisper'} onChange={() => setEngine('whisper')} />
                  Whisper
                </label>
                <label className="flex items-center gap-1.5">
                  <input type="radio" checked={engine === 'sensevoice'} onChange={() => setEngine('sensevoice')} />
                  SenseVoice
                </label>
              </div>
            </label>
            <label className="flex-1 text-sm text-neutral-300">
              Engine TTS
              <div className="mt-1 flex gap-4">
                <label className="flex items-center gap-1.5">
                  <input type="radio" checked={ttsEngine === 'capcut'} onChange={() => changeTtsEngine('capcut')} />
                  CapCut TTS
                </label>
                <label className="flex items-center gap-1.5">
                  <input type="radio" checked={ttsEngine === 'vieneu'} onChange={() => changeTtsEngine('vieneu')} />
                  VieNeu-TTS
                </label>
              </div>
            </label>
            <label className="min-w-64 flex-1 text-sm text-neutral-300">
              Giọng đọc mặc định
              <div className="mt-1 flex gap-2">
                <select className={`${inputClass} mt-0 flex-1`} value={selectedVoice} onChange={(e) => setVoice(e.target.value)}>
                  {voiceOptions.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.label}
                    </option>
                  ))}
                </select>
              </div>
            </label>
            <label className="min-w-48 flex-1 text-sm text-neutral-300">
              Âm thanh gốc
              <select
                className={`${inputClass} mt-0`}
                value={audioMode}
                onChange={(e) => setAudioMode(e.target.value as AudioMode)}
              >
                <option value="separated">Tách nhạc nền/SFX (demucs)</option>
                <option value="original">Giữ nguyên âm thanh gốc</option>
                <option value="mute">Tắt hoàn toàn âm thanh gốc</option>
              </select>
            </label>
            <label className="min-w-48 flex-1 text-sm text-neutral-300">
              Video chậm tối đa
              <select
                className={`${inputClass} mt-0`}
                value={minVideoSpeed}
                onChange={(e) => setMinVideoSpeed(Number(e.target.value))}
              >
                {MIN_VIDEO_SPEED_OPTIONS.map((v) => (
                  <option key={v} value={v}>
                    {Math.round((1 - v) * 100)}% ({v.toFixed(2)}x)
                  </option>
                ))}
              </select>
            </label>
          </div>

          {splitEpisodes.map((ep, i) => (
            <EpisodeCard
              key={ep.episode.episode_id}
              projectId={projectId}
              detail={ep}
              index={i}
              engine={engine}
              voice={selectedVoice}
              ttsEngine={ttsEngine}
              movable={false}
              standaloneAssemble
              audioMode={audioMode}
              minVideoSpeed={minVideoSpeed}
            />
          ))}
        </section>
      )}

      {(isMulti || isSplit) && Object.keys(entity_dict).length > 0 && (
        <section className="card p-5">
          <h2 className="mb-3 text-lg">Entity dict (dùng chung cả series)</h2>
          <table className="table">
            <thead>
              <tr>
                <th>Gốc (zh)</th>
                <th>Phiên âm / dịch (vi)</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(entity_dict).map(([k, v]) => (
                <tr key={k}>
                  <td>{k}</td>
                  <td className="text-accent-200">{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {!isMulti && !isSplit && (
      <>
      <section className="card p-5">
        <StageHeader
          name="ingest"
          record={ingest}
          busy={busyIngest}
          cancelling={(cancelMutation.isPending && cancelMutation.variables === 'ingest') || pendingCancel.ingest}
          onCancel={() => cancelMutation.mutate('ingest')}
        />
        <JobProgressBar job={ingestJob.data} formatCount={(n) => `${(n / (1024 * 1024)).toFixed(1)}MB`} />
        {ingest.status === 'done' && (
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm text-accent-300">
              {project.original_filename}
              {project.duration_sec ? ` · ${Math.round(project.duration_sec)}s` : ''}
            </p>
            <button type="button" className={secondaryButtonClass} onClick={() => revealVideoMutation.mutate()}>
              {revealVideoMutation.isPending ? 'Đang mở...' : 'Mở thư mục chứa file'}
            </button>
          </div>
        )}
        {ingest.error && <p className="mb-3 text-sm text-danger">{ingest.error}</p>}
        {video_url && (
          <video className="mb-4 max-h-72 w-full rounded-lg bg-black" src={video_url} controls preload="metadata" />
        )}

        <div className="mb-3 flex gap-4 text-sm text-neutral-300">
          <label className="flex items-center gap-1.5">
            <input type="radio" name="ingest-source" checked={ingestTab === 'file'} onChange={() => setIngestTab('file')} />
            Upload file
          </label>
          <label className="flex items-center gap-1.5">
            <input type="radio" name="ingest-source" checked={ingestTab === 'url'} onChange={() => setIngestTab('url')} />
            Dán link Douyin/TikTok
          </label>
        </div>

        {ingestTab === 'file' ? (
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept="video/mp4,video/webm,video/quicktime,video/x-matroska,.mp4,.mkv,.webm,.mov,.avi,.m4v"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <button
              type="button"
              className={primaryButtonClass}
              disabled={!file || uploadMutation.isPending || busyAny}
              onClick={() => uploadMutation.mutate()}
            >
              {uploadMutation.isPending ? 'Đang tải lên...' : ingest.status === 'done' ? 'Thay video' : 'Upload'}
            </button>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <input
              className={`${inputClass} min-w-72 flex-1`}
              placeholder="Dán link hoặc nguyên đoạn share Douyin/TikTok..."
              value={shareUrl}
              onChange={(e) => setShareUrl(e.target.value)}
            />
            <button
              type="button"
              className={primaryButtonClass}
              disabled={!shareUrl.trim() || ingestUrlMutation.isPending || busyAny}
              onClick={() => ingestUrlMutation.mutate()}
            >
              {ingestUrlMutation.isPending || busyIngest
                ? 'Đang tải...'
                : ingest.status === 'done'
                  ? 'Thay video'
                  : 'Tải xuống'}
            </button>
          </div>
        )}
        {uploadMutation.error && <p className="mt-2 text-sm text-danger">{(uploadMutation.error as Error).message}</p>}
        {ingestUrlMutation.error && (
          <p className="mt-2 text-sm text-danger">{(ingestUrlMutation.error as Error).message}</p>
        )}
      </section>

      {ingest.status === 'done' && transcribe.status !== 'done' && !busyAny && project.duration_sec && (
        <section className="card space-y-3 p-5">
          <h2 className="text-lg">Chia video thành nhiều đoạn (tùy chọn)</h2>
          <p className="text-sm text-neutral-400">
            Video dài ráp xong 1 draft CapCut duy nhất có thể quá nặng, CapCut không load nổi — chia thành nhiều đoạn,
            mỗi đoạn chạy hết pipeline rồi ráp thành 1 draft CapCut RIÊNG (chạy tuần tự, đoạn sau chỉ bắt đầu khi đoạn
            trước ráp draft xong).
          </p>
          <label className="block text-sm text-neutral-300">
            Chia thành mấy đoạn
            <input
              type="number"
              min={1}
              max={20}
              className={`${inputClass} max-w-32`}
              value={splitCount}
              onChange={(e) => setSplitCountAndReset(Math.min(20, Math.max(1, Number(e.target.value) || 1)), project.duration_sec ?? 0)}
            />
          </label>
          {splitCount > 1 && (
            <>
              <SplitTimeline duration={project.duration_sec} points={splitPoints} onChange={setSplitPoints} />
              <button
                type="button"
                className={primaryButtonClass}
                disabled={splitMutation.isPending}
                onClick={() => splitMutation.mutate()}
              >
                {splitMutation.isPending ? 'Đang chia...' : `Xác nhận chia thành ${splitCount} đoạn`}
              </button>
              {splitMutation.error && <p className="text-sm text-danger">{(splitMutation.error as Error).message}</p>}
            </>
          )}
        </section>
      )}

      <section className="card p-5">
        <StageHeader
          name="transcribe"
          record={transcribe}
          busy={busyWhisper}
          cancelling={(cancelMutation.isPending && cancelMutation.variables === 'transcribe') || pendingCancel.transcribe}
          onCancel={() => cancelMutation.mutate('transcribe')}
        />
        <p className="mb-3 text-sm text-neutral-400">
          Nhận diện giọng nói → <span className="mono">sub_zh.srt</span>
          {transcribe.engine && <> · lần chạy trước dùng <b>{transcribe.engine === 'sensevoice' ? 'SenseVoice' : 'Whisper'}</b></>}
        </p>
        <div className="mb-3 flex gap-4 text-sm text-neutral-300">
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="engine"
              checked={engine === 'whisper'}
              disabled={busyAny}
              onChange={() => setEngine('whisper')}
            />
            Whisper (faster-whisper medium, GPU)
          </label>
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="engine"
              checked={engine === 'sensevoice'}
              disabled={busyAny}
              onChange={() => setEngine('sensevoice')}
            />
            SenseVoice (funasr, CPU)
          </label>
        </div>
        {transcribe.progress && <p className="mb-2 text-sm text-neutral-300">{transcribe.progress}</p>}
        {transcribe.error && <p className="mb-2 text-sm text-danger">{transcribe.error}</p>}
        <JobProgressBar job={transcribeJob.data} />
        <button
          type="button"
          className={primaryButtonClass}
          disabled={ingest.status !== 'done' || busyAny || whisperMutation.isPending}
          onClick={() => whisperMutation.mutate()}
        >
          {busyWhisper
            ? 'Đang nhận diện...'
            : transcribe.status === 'done'
              ? `Chạy lại ${engine === 'sensevoice' ? 'SenseVoice' : 'Whisper'}`
              : `Chạy ${engine === 'sensevoice' ? 'SenseVoice' : 'Whisper'}`}
        </button>
        {whisperMutation.error && <p className="mt-2 text-sm text-danger">{(whisperMutation.error as Error).message}</p>}
      </section>


      <section className="card p-5">
        <StageHeader
          name="translate"
          record={translate}
          busy={busyGemini}
          cancelling={(cancelMutation.isPending && cancelMutation.variables === 'translate') || pendingCancel.translate}
          onCancel={() => cancelMutation.mutate('translate')}
        />
        <p className="mb-3 text-sm text-neutral-400">
          Gemini: entity dict + clean + dịch zh→vi → <span className="mono">sub_vi.srt</span> +{' '}
          <span className="mono">entity_dict.json</span>
        </p>
        {translate.progress && <p className="mb-2 text-sm text-neutral-300">{translate.progress}</p>}
        {translate.error && <p className="mb-2 text-sm text-danger">{translate.error}</p>}
        <JobProgressBar job={translateJob.data} />
        <button
          type="button"
          className={primaryButtonClass}
          disabled={
            transcribe.status !== 'done' ||
            busyAny ||
            geminiMutation.isPending
          }
          onClick={() => geminiMutation.mutate()}
        >
          {busyGemini ? 'Đang phân tích...' : translate.status === 'done' ? 'Chạy lại Gemini' : 'Chạy Gemini'}
        </button>
        {geminiMutation.error && <p className="mt-2 text-sm text-danger">{(geminiMutation.error as Error).message}</p>}
      </section>

      <section className="card p-5">
        <StageHeader
          name="tts"
          record={tts}
          busy={busyTTS}
          cancelling={(cancelMutation.isPending && cancelMutation.variables === 'tts') || pendingCancel.tts}
          onCancel={() => cancelMutation.mutate('tts')}
        />
        <p className="mb-3 text-sm text-neutral-400">
          Đọc từng câu <span className="mono">sub_vi.srt</span> → <span className="mono">audio/segment_NNN.mp3</span>
          {tts.engine && <> · lần chạy trước dùng <b>{tts.engine === 'vieneu' ? 'VieNeu-TTS' : 'CapCut TTS'}</b></>}
        </p>
        <div className="mb-3 flex gap-4 text-sm text-neutral-300">
          <label className="flex items-center gap-1.5">
            <input type="radio" checked={ttsEngine === 'capcut'} disabled={busyAny} onChange={() => changeTtsEngine('capcut')} />
            CapCut TTS (cloud)
          </label>
          <label className="flex items-center gap-1.5">
            <input type="radio" checked={ttsEngine === 'vieneu'} disabled={busyAny} onChange={() => changeTtsEngine('vieneu')} />
            VieNeu-TTS (local, tiếng Việt)
          </label>
        </div>
        <label className="mb-1 block text-sm text-neutral-300">
          Giọng đọc
          <div className="flex gap-2">
            <select
              className={`${inputClass} flex-1`}
              value={selectedVoice}
              onChange={(e) => setVoice(e.target.value)}
              disabled={busyAny}
            >
              {voiceOptions.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              className={secondaryButtonClass}
              disabled={!selectedVoice}
              onClick={() => {
                setPreviewVoice(selectedVoice)
                setPreviewNonce((n) => n + 1)
              }}
            >
              Nghe thử
            </button>
          </div>
        </label>
        {previewVoice && (
          <audio
            key={previewNonce}
            className="mb-3 h-8 w-full"
            autoPlay
            controls
            preload="auto"
            src={api.previewVoiceUrl(previewVoice, ttsEngine)}
          />
        )}
        {tts.progress && <p className="mb-2 text-sm text-neutral-300">{tts.progress}</p>}
        {tts.error && <p className="mb-2 text-sm text-danger">{tts.error}</p>}
        <JobProgressBar job={ttsJob.data} />
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className={primaryButtonClass}
            disabled={translate.status !== 'done' || busyAny || ttsMutation.isPending}
            onClick={() => ttsMutation.mutate()}
          >
            {busyTTS ? 'Đang đọc...' : tts.status === 'done' ? 'Chạy lại TTS' : 'Chạy TTS'}
          </button>
          {tts.status === 'done' && tts.progress?.includes('lỗi') && (
            <button
              type="button"
              className={secondaryButtonClass}
              disabled={busyAny || retryTTSMutation.isPending}
              onClick={() => retryTTSMutation.mutate()}
            >
              {busyTTS ? 'Đang thử lại...' : 'Thử lại câu lỗi'}
            </button>
          )}
        </div>
        {ttsMutation.error && <p className="mt-2 text-sm text-danger">{(ttsMutation.error as Error).message}</p>}
        {retryTTSMutation.error && <p className="mt-2 text-sm text-danger">{(retryTTSMutation.error as Error).message}</p>}

        {tts_manifest.length > 0 && (
          <div className="mt-4 max-h-72 overflow-auto rounded-lg border border-neutral-800">
            <table className="table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Thời lượng</th>
                  <th>Audio</th>
                </tr>
              </thead>
              <tbody>
                {tts_manifest.map((m) => (
                  <tr key={m.id}>
                    <td className="mono text-xs text-neutral-500">{m.id}</td>
                    <td className="mono text-xs text-neutral-400">{m.path ? `${(m.duration_ms / 1000).toFixed(1)}s` : '—'}</td>
                    <td>
                      {m.path ? (
                        <audio className="h-8 max-w-55" controls preload="none" src={`/api/projects/${projectId}/assets/audio/${m.path}`} />
                      ) : (
                        <span className="text-xs text-danger">{m.error ? 'lỗi' : 'rỗng'}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      </>
      )}

      {!isSplit && (
      <section className="card p-5">
        <StageHeader
          name="assemble"
          record={assemble}
          busy={busyAssemble}
          cancelling={(cancelMutation.isPending && cancelMutation.variables === 'assemble') || pendingCancel.assemble}
          onCancel={() => cancelMutation.mutate('assemble')}
        />
        <p className="mb-3 text-sm text-neutral-400">
          {isMulti
            ? 'Tách nhạc nền từng tập + ráp TẤT CẢ các tập nối tiếp nhau thành 1 draft CapCut duy nhất, ghi'
            : 'Tách nhạc nền/tiếng động (demucs) + ráp video gốc (tắt thoại) + giọng đọc TTS + phụ đề thành 1 draft, ghi'}{' '}
          thẳng vào thư mục CapCut thật:{' '}
          <span className="mono text-xs">{settingsQuery.data?.capcut_drafts_dir ?? '...'}</span>
        </p>
        {isMulti && !allEpisodesTTSDone && (
          <p className="mb-3 text-sm text-neutral-400">Cần mọi tập chạy xong TTS trước khi ráp draft chung.</p>
        )}
        {assemble.progress && <p className="mb-2 text-sm text-neutral-300">{assemble.progress}</p>}
        {assemble.error && <p className="mb-2 text-sm text-danger">{assemble.error}</p>}
        <label className="mb-3 block text-sm text-neutral-300">
          Âm thanh gốc
          <select
            className={`${inputClass} max-w-sm`}
            value={audioMode}
            disabled={busyAny}
            onChange={(e) => setAudioMode(e.target.value as AudioMode)}
          >
            <option value="separated">Tách nhạc nền/SFX bằng demucs (mặc định, khuyến nghị)</option>
            <option value="original">Giữ nguyên âm thanh gốc (không tách, không tắt — có cả thoại gốc)</option>
            <option value="mute">Tắt hoàn toàn âm thanh gốc (bỏ qua tách nhạc nền)</option>
          </select>
        </label>
        <label className="mb-3 block text-sm text-neutral-300">
          Video được chậm tối đa (để nhường thêm thời gian cho giọng đọc TTS)
          <select
            className={`${inputClass} max-w-sm`}
            value={minVideoSpeed}
            disabled={busyAny}
            onChange={(e) => setMinVideoSpeed(Number(e.target.value))}
          >
            {MIN_VIDEO_SPEED_OPTIONS.map((v) => (
              <option key={v} value={v}>
                Chậm tối đa {Math.round((1 - v) * 100)}% ({v.toFixed(2)}x)
                {v === DEFAULT_MIN_VIDEO_SPEED ? ' — mặc định' : ''}
              </option>
            ))}
          </select>
        </label>
        <JobProgressBar job={assembleJob.data} />
        <button
          type="button"
          className={primaryButtonClass}
          disabled={(isMulti ? !allEpisodesTTSDone : tts.status !== 'done') || busyAny || assembleMutation.isPending}
          onClick={() => assembleMutation.mutate()}
        >
          {busyAssemble ? 'Đang dựng...' : assemble.status === 'done' ? 'Dựng lại CapCut' : 'Dựng CapCut'}
        </button>
        {assembleMutation.error && <p className="mt-2 text-sm text-danger">{(assembleMutation.error as Error).message}</p>}
        {assemble.status === 'done' && (
          <p className="mt-2 text-sm text-accent-300">
            Xong — mở CapCut, tìm project tên <span className="mono">{project.project_id}</span> trong danh sách.
          </p>
        )}
      </section>
      )}

      {(cues.length > 0 || data.sub_zh) && (
        <section className="card p-5">
          <div className="mb-4 flex flex-wrap gap-2">
            <button type="button" className={tab === 'table' ? primaryButtonClass : secondaryButtonClass} onClick={() => setTab('table')}>
              Đối chiếu
            </button>
            <button type="button" className={tab === 'zh' ? primaryButtonClass : secondaryButtonClass} onClick={() => setTab('zh')}>
              sub_zh.srt
            </button>
            <button type="button" className={tab === 'vi' ? primaryButtonClass : secondaryButtonClass} onClick={() => setTab('vi')}>
              sub_vi.srt
            </button>
            <button type="button" className={tab === 'entity' ? primaryButtonClass : secondaryButtonClass} onClick={() => setTab('entity')}>
              Entity dict
            </button>
          </div>

          {tab === 'table' && (
            <div className="max-h-[480px] overflow-auto">
              <table className="table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Time</th>
                    <th>中文</th>
                    <th>Tiếng Việt</th>
                    <th>Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  {cues.map((c) => {
                    const manifestEntry = manifestById.get(c.id)
                    const isEditing = editingCueId === c.id
                    const isStale =
                      Boolean(manifestEntry?.path) &&
                      manifestEntry?.text !== undefined &&
                      manifestEntry.text !== (c.text_vi ?? '')
                    const rowBusyRetranslate = retranslateCueMutation.isPending && retranslateCueMutation.variables === c.id
                    const rowBusyTTS = ttsCueMutation.isPending && ttsCueMutation.variables === c.id
                    const rowBusySave = updateCueMutation.isPending && updateCueMutation.variables?.cueId === c.id
                    const rowBusy = rowBusyRetranslate || rowBusyTTS || rowBusySave
                    const actionsDisabled = translate.status !== 'done' || busyAny || rowBusy
                    return (
                      <tr key={c.id}>
                        <td className="mono text-xs text-neutral-500">{c.id}</td>
                        <td className="mono text-xs text-neutral-400">
                          {c.start} → {c.end}
                        </td>
                        <td className="text-sm">{c.text}</td>
                        <td className="text-sm text-accent-200">
                          {isEditing ? (
                            <div className="flex flex-col gap-1.5">
                              <textarea
                                className={`${inputClass} min-h-0`}
                                rows={2}
                                autoFocus
                                value={draftText}
                                onChange={(e) => setDraftText(e.target.value)}
                              />
                              <div className="flex gap-1.5">
                                <button
                                  type="button"
                                  className={secondaryButtonClass}
                                  disabled={rowBusySave}
                                  onClick={() => updateCueMutation.mutate({ cueId: c.id, text: draftText })}
                                >
                                  {rowBusySave ? 'Đang lưu...' : 'Lưu'}
                                </button>
                                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditingCueId(null)}>
                                  Huỷ
                                </button>
                              </div>
                            </div>
                          ) : (
                            <div className="flex items-start gap-2">
                              <span>{c.text_vi ?? '—'}</span>
                              {isStale && (
                                <span className="mt-0.5 inline-block shrink-0 rounded-md bg-danger-800 px-1.5 py-0.5 font-mono text-[10px] whitespace-nowrap text-danger-100">
                                  cần đọc lại
                                </span>
                              )}
                            </div>
                          )}
                        </td>
                        <td>
                          <div className="flex flex-col items-start gap-1.5">
                            <div className="flex flex-wrap gap-1.5">
                              <button
                                type="button"
                                className="btn btn-ghost btn-sm"
                                disabled={actionsDisabled || isEditing}
                                onClick={() => {
                                  setEditingCueId(c.id)
                                  setDraftText(c.text_vi ?? '')
                                }}
                              >
                                Sửa
                              </button>
                              <button
                                type="button"
                                className="btn btn-ghost btn-sm"
                                disabled={actionsDisabled}
                                onClick={() => retranslateCueMutation.mutate(c.id)}
                              >
                                {rowBusyRetranslate ? 'Đang dịch...' : 'Dịch lại'}
                              </button>
                              <button
                                type="button"
                                className="btn btn-ghost btn-sm"
                                disabled={actionsDisabled}
                                onClick={() => ttsCueMutation.mutate(c.id)}
                              >
                                {rowBusyTTS ? 'Đang đọc...' : 'Đọc lại'}
                              </button>
                            </div>
                            {manifestEntry?.path && (
                              <audio
                                className="h-7 w-full max-w-55"
                                controls
                                preload="none"
                                src={`/api/projects/${projectId}/assets/audio/${manifestEntry.path}`}
                              />
                            )}
                            {retranslateCueMutation.isError && retranslateCueMutation.variables === c.id && (
                              <p className="text-xs text-danger">{(retranslateCueMutation.error as Error).message}</p>
                            )}
                            {ttsCueMutation.isError && ttsCueMutation.variables === c.id && (
                              <p className="text-xs text-danger">{(ttsCueMutation.error as Error).message}</p>
                            )}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          {tab === 'zh' && <pre className="mono max-h-[480px] overflow-auto whitespace-pre-wrap text-xs text-neutral-300">{data.sub_zh}</pre>}
          {tab === 'vi' && <pre className="mono max-h-[480px] overflow-auto whitespace-pre-wrap text-xs text-neutral-300">{data.sub_vi ?? 'Chưa có'}</pre>}
          {tab === 'entity' &&
            (Object.keys(entity_dict).length === 0 ? (
              <p className="text-sm text-neutral-500">Chưa có entity dict.</p>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Gốc (zh)</th>
                    <th>Phiên âm / dịch (vi)</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(entity_dict).map(([k, v]) => (
                    <tr key={k}>
                      <td>{k}</td>
                      <td className="text-accent-200">{v}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ))}
        </section>
      )}

      {data.logs.length > 0 && (
        <section>
          <h2 className="mb-2 text-sm text-neutral-400">Log</h2>
          <ul className="space-y-1 font-mono text-xs text-neutral-500">
            {data.logs.map((l, i) => (
              <li key={`${l.ts}-${i}`}>
                {l.ts} · {l.stage} · {l.message}
              </li>
            ))}
          </ul>
        </section>
      )}

      <ConfirmDialog
        open={deletingEpisodeId !== null}
        title="Xoá tập?"
        message="Video, phụ đề và audio TTS của tập này sẽ bị xoá khỏi đĩa. Cần ráp lại draft CapCut sau khi xoá."
        confirmLabel="Xoá"
        danger
        onCancel={() => setDeletingEpisodeId(null)}
        onConfirm={() => deletingEpisodeId && deleteEpisodeMutation.mutate(deletingEpisodeId)}
      />
    </div>
  )
}
