import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, type AudioMode, type EpisodeDetail, type EpisodeStageName, type TranscribeEngine, type TTSEngine } from '../lib/api'
import { inputClass, primaryButtonClass, secondaryButtonClass } from '../lib/ui'
import StatusBadge from './StatusBadge'
import JobProgressBar from './JobProgressBar'
import HorizontalStepper from './HorizontalStepper'
import MiniAudioPlayer from './MiniAudioPlayer'
import { runDurationLabel } from './DirectExportPanel'
import { useJobStatus } from '../hooks/useJobStatus'
import { USER_STAGE_LABELS, useLabels } from '../lib/labels'

const ADMIN_EPISODE_STAGE_LABELS: Record<EpisodeStageName, string> = {
  ingest: 'Video',
  transcribe: 'Whisper',
  translate: 'Gemini',
  tts: 'TTS',
  assemble: 'CapCut',
}

export default function EpisodeCard({
  projectId,
  detail,
  index,
  engine,
  voice,
  ttsEngine = 'capcut',
  movable = true,
  onDelete,
  onMoveUp,
  onMoveDown,
  canMoveUp,
  canMoveDown,
  standaloneAssemble = false,
  audioMode,
  originalAudioVolumeDb,
  minVideoSpeed,
}: {
  projectId: string
  detail: EpisodeDetail
  index: number
  engine: TranscribeEngine
  voice: string
  ttsEngine?: TTSEngine
  movable?: boolean
  onDelete?: () => void
  onMoveUp?: () => void
  onMoveDown?: () => void
  canMoveUp?: boolean
  canMoveDown?: boolean
  // Dự án "split" (video dài tự cắt thành nhiều đoạn): mỗi đoạn ráp draft
  // CapCut RIÊNG (không gộp chung như dự án "dài tập") — hiện thêm khối
  // "Ráp CapCut" ngay trong từng EpisodeCard.
  standaloneAssemble?: boolean
  audioMode?: AudioMode
  originalAudioVolumeDb?: number
  minVideoSpeed?: number
}) {
  const queryClient = useQueryClient()
  const labels = useLabels()
  const { episode } = detail
  const episodeId = episode.episode_id
  const [expanded, setExpanded] = useState(episode.stages.tts.status !== 'done')
  const [ingestTab, setIngestTab] = useState<'file' | 'url'>('url')
  const [shareUrl, setShareUrl] = useState('')
  const [editingCueId, setEditingCueId] = useState<number | null>(null)
  const [draftText, setDraftText] = useState('')
  // Xem comment tương ứng trong ProjectDetail.tsx — giữ "Đang dừng..." tới
  // khi job thật sự dừng, không chỉ trong lúc gửi request cancel.
  const [pendingCancel, setPendingCancel] = useState<Partial<Record<EpisodeStageName, boolean>>>({})
  const clearPendingCancel = (stage: EpisodeStageName) => setPendingCancel((prev) => ({ ...prev, [stage]: false }))
  const [pendingCancelExport, setPendingCancelExport] = useState(false)

  const ingestJob = useJobStatus(projectId, 'ingest', episodeId, () => clearPendingCancel('ingest'))
  const transcribeJob = useJobStatus(projectId, 'transcribe', episodeId, () => clearPendingCancel('transcribe'))
  const translateJob = useJobStatus(projectId, 'translate', episodeId, () => clearPendingCancel('translate'))
  const ttsJob = useJobStatus(projectId, 'tts', episodeId, () => clearPendingCancel('tts'))
  const assembleJob = useJobStatus(projectId, 'assemble', episodeId, () => clearPendingCancel('assemble'))
  const exportJob = useJobStatus(projectId, 'export', episodeId, () => setPendingCancelExport(false))

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['project', projectId] })
  const invalidateJob = (stage: EpisodeStageName) =>
    queryClient.invalidateQueries({ queryKey: ['job', projectId, episodeId, stage] })

  const ingestUrlMutation = useMutation({
    mutationFn: () => {
      if (!shareUrl.trim()) throw new Error('Chưa dán link')
      return api.ingestEpisodeUrl(projectId, episodeId, shareUrl.trim())
    },
    onSuccess: () => {
      setShareUrl('')
      invalidateJob('ingest')
    },
  })

  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadEpisodeVideo(projectId, episodeId, file),
    onSuccess: () => refresh(),
  })

  const transcribeMutation = useMutation({
    mutationFn: () => api.startEpisodeTranscribe(projectId, episodeId, engine),
    onSuccess: () => invalidateJob('transcribe'),
  })

  const translateMutation = useMutation({
    mutationFn: () => api.startEpisodeTranslate(projectId, episodeId),
    onSuccess: () => invalidateJob('translate'),
  })

  const ttsMutation = useMutation({
    mutationFn: () => api.startEpisodeTTS(projectId, episodeId, voice, ttsEngine),
    onSuccess: () => invalidateJob('tts'),
  })

  const retryTTSMutation = useMutation({
    mutationFn: () => api.retryEpisodeTTS(projectId, episodeId, voice),
    onSuccess: () => invalidateJob('tts'),
  })

  const assembleMutation = useMutation({
    mutationFn: () =>
      api.startEpisodeAssemble(projectId, episodeId, audioMode ?? 'original', minVideoSpeed ?? 0.85, originalAudioVolumeDb ?? -13),
    onSuccess: () => invalidateJob('assemble'),
  })

  const cancelMutation = useMutation({
    mutationFn: (stage: EpisodeStageName) => api.cancelEpisodeJob(projectId, episodeId, stage),
    onSuccess: (_d, stage) => {
      invalidateJob(stage)
      setPendingCancel((prev) => ({ ...prev, [stage]: true }))
    },
  })

  const invalidateExportJob = () => queryClient.invalidateQueries({ queryKey: ['job', projectId, episodeId, 'export'] })
  const exportMutation = useMutation({
    mutationFn: () =>
      api.startEpisodeExport(projectId, episodeId, audioMode ?? 'original', minVideoSpeed ?? 0.85, originalAudioVolumeDb ?? -13),
    onSuccess: () => invalidateExportJob(),
  })
  const cancelExportMutation = useMutation({
    mutationFn: () => api.cancelEpisodeExport(projectId, episodeId),
    onSuccess: () => {
      invalidateExportJob()
      setPendingCancelExport(true)
    },
  })
  const revealExportMutation = useMutation({
    mutationFn: () => api.revealEpisodeExport(projectId, episodeId),
  })

  const updateCueMutation = useMutation({
    mutationFn: ({ cueId, text }: { cueId: number; text: string }) => api.updateEpisodeCue(projectId, episodeId, cueId, text),
    onSuccess: () => {
      setEditingCueId(null)
      refresh()
    },
  })

  const retranslateCueMutation = useMutation({
    mutationFn: (cueId: number) => api.retranslateEpisodeCue(projectId, episodeId, cueId),
    onSuccess: () => refresh(),
  })

  const ttsCueMutation = useMutation({
    mutationFn: (cueId: number) => api.ttsEpisodeCue(projectId, episodeId, cueId, voice),
    onSuccess: () => refresh(),
  })

  const { ingest, transcribe, translate, tts, assemble } = episode.stages
  const busyIngest = ingest.status === 'running' || ingestJob.data?.status === 'running'
  const busyWhisper = transcribe.status === 'running' || transcribeJob.data?.status === 'running'
  const busyGemini = translate.status === 'running' || translateJob.data?.status === 'running'
  const busyTTS = tts.status === 'running' || ttsJob.data?.status === 'running'
  const busyAssemble = assemble.status === 'running' || assembleJob.data?.status === 'running'
  const exportRec = episode.export
  const busyExport = exportRec.status === 'running' || exportJob.data?.status === 'running'
  const busyAny = busyIngest || busyWhisper || busyGemini || busyTTS || (standaloneAssemble && (busyAssemble || busyExport))
  // Ghép từng câu phụ đề với audio TTS đã tạo (manifest keyed theo id cue) để
  // hiện trình nghe ngay trong bảng — trước đây tạo giọng xong không thấy audio
  // đâu vì FE không nối manifest → URL asset.
  const manifestById = new Map((detail.tts_manifest ?? []).map((m) => [m.id, m]))

  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <button type="button" className="flex items-center gap-2 text-left" onClick={() => setExpanded((v) => !v)}>
          <span className="text-neutral-500">{expanded ? '▾' : '▸'}</span>
          <span className="font-medium">
            Tập {index + 1}
            {episode.title ? ` — ${episode.title}` : ''}
          </span>
          {episode.original_filename && <span className="text-xs text-neutral-500">({episode.original_filename})</span>}
        </button>
        <div className="flex flex-wrap items-center gap-1.5">
          {/* Dự án split: rail + stepper (trong body) đã thể hiện đủ trạng thái
              từng bước → không lặp lại dãy badge ở header cho đỡ rối. Chỉ hiện
              badge tổng gọn. Dự án multi giữ dãy badge chi tiết như cũ. */}
          {standaloneAssemble ? (
            <StatusBadge status={exportRec.status === 'done' ? 'done' : episode.stages.tts.status} />
          ) : (
            (['ingest', 'transcribe', 'translate', 'tts'] as EpisodeStageName[]).map((s) => (
              <span key={s} className="flex items-center gap-1 text-[10px] text-neutral-500">
                {(labels.isAdmin ? ADMIN_EPISODE_STAGE_LABELS : USER_STAGE_LABELS)[s]}
                <StatusBadge status={episode.stages[s].status} />
              </span>
            ))
          )}
          {movable && (
            <>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={!canMoveUp || busyAny}
                title="Đưa lên trước 1 tập"
                onClick={onMoveUp}
              >
                ▲
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={!canMoveDown || busyAny}
                title="Đưa xuống sau 1 tập"
                onClick={onMoveDown}
              >
                ▼
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-sm text-danger"
                disabled={busyAny}
                title="Xoá tập"
                onClick={onDelete}
              >
                Xoá
              </button>
            </>
          )}
        </div>
      </div>

      {expanded && (
        <div className="mt-4 space-y-4 border-t border-neutral-800 pt-4">
          {standaloneAssemble && (
            <HorizontalStepper
              steps={[
                { id: 'ingest', label: (labels.isAdmin ? ADMIN_EPISODE_STAGE_LABELS : USER_STAGE_LABELS).ingest, status: ingest.status },
                { id: 'transcribe', label: (labels.isAdmin ? ADMIN_EPISODE_STAGE_LABELS : USER_STAGE_LABELS).transcribe, status: transcribe.status },
                { id: 'translate', label: (labels.isAdmin ? ADMIN_EPISODE_STAGE_LABELS : USER_STAGE_LABELS).translate, status: translate.status },
                { id: 'tts', label: (labels.isAdmin ? ADMIN_EPISODE_STAGE_LABELS : USER_STAGE_LABELS).tts, status: tts.status },
                { id: 'export', label: 'Xuất', status: exportRec.status },
              ]}
            />
          )}
          {detail.video_url && (
            <video className="max-h-56 w-full rounded-lg bg-black" src={detail.video_url} controls preload="metadata" />
          )}

          {ingest.status !== 'done' && (
            <div>
              <div className="mb-2 flex gap-4 text-sm text-neutral-300">
                <label className="flex items-center gap-1.5">
                  <input type="radio" checked={ingestTab === 'url'} onChange={() => setIngestTab('url')} />
                  Dán link
                </label>
                <label className="flex items-center gap-1.5">
                  <input type="radio" checked={ingestTab === 'file'} onChange={() => setIngestTab('file')} />
                  Upload file
                </label>
              </div>
              {ingestTab === 'url' ? (
                <div className="flex flex-wrap items-center gap-2">
                  <input
                    className={`${inputClass} min-w-64 flex-1`}
                    placeholder="Dán link Douyin/TikTok..."
                    value={shareUrl}
                    onChange={(e) => setShareUrl(e.target.value)}
                  />
                  <button
                    type="button"
                    className={primaryButtonClass}
                    disabled={!shareUrl.trim() || ingestUrlMutation.isPending || busyAny}
                    onClick={() => ingestUrlMutation.mutate()}
                  >
                    {busyIngest ? 'Đang tải...' : 'Tải xuống'}
                  </button>
                  {busyIngest && (
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm text-danger"
                      disabled={pendingCancel.ingest}
                      onClick={() => cancelMutation.mutate('ingest')}
                    >
                      {pendingCancel.ingest ? 'Đang dừng...' : 'Dừng'}
                    </button>
                  )}
                </div>
              ) : (
                <input
                  type="file"
                  accept="video/mp4,video/webm,video/quicktime,video/x-matroska,.mp4,.mkv,.webm,.mov,.avi,.m4v"
                  disabled={busyAny}
                  onChange={(e) => {
                    const f = e.target.files?.[0]
                    if (f) uploadMutation.mutate(f)
                  }}
                />
              )}
              {ingestUrlMutation.error && <p className="mt-2 text-sm text-danger">{(ingestUrlMutation.error as Error).message}</p>}
              {uploadMutation.error && <p className="mt-2 text-sm text-danger">{(uploadMutation.error as Error).message}</p>}
              <JobProgressBar job={ingestJob.data} formatCount={(n) => `${(n / (1024 * 1024)).toFixed(1)}MB`} />
            </div>
          )}
          {ingest.error && <p className="text-sm text-danger">{ingest.error}</p>}

          {ingest.status === 'done' && (
            <div className="flex flex-wrap items-center gap-2">
              <JobProgressBar job={transcribeJob.data} />
              <button
                type="button"
                className={transcribe.status === 'done' ? secondaryButtonClass : primaryButtonClass}
                disabled={busyAny || transcribeMutation.isPending}
                onClick={() => transcribeMutation.mutate()}
              >
                {busyWhisper ? 'Đang nhận diện...' : labels.isAdmin ? (transcribe.status === 'done' ? 'Chạy lại Whisper' : 'Chạy Whisper') : transcribe.status === 'done' ? 'Nhận diện lại' : 'Bắt đầu nhận diện'}
              </button>
              {busyWhisper && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm text-danger"
                  disabled={pendingCancel.transcribe}
                  onClick={() => cancelMutation.mutate('transcribe')}
                >
                  {pendingCancel.transcribe ? 'Đang dừng...' : 'Dừng'}
                </button>
              )}
              {transcribe.progress && <span className="text-xs text-neutral-400">{transcribe.progress}</span>}
            </div>
          )}
          {transcribe.error && <p className="text-sm text-danger">{transcribe.error}</p>}

          {transcribe.status === 'done' && (
            <div className="flex flex-wrap items-center gap-2">
              <JobProgressBar job={translateJob.data} />
              <button
                type="button"
                className={translate.status === 'done' ? secondaryButtonClass : primaryButtonClass}
                disabled={busyAny || translateMutation.isPending}
                onClick={() => translateMutation.mutate()}
              >
                {busyGemini ? 'Đang dịch...' : labels.isAdmin ? (translate.status === 'done' ? 'Chạy lại Gemini' : 'Chạy Gemini') : translate.status === 'done' ? 'Dịch lại' : 'Bắt đầu dịch'}
              </button>
              {busyGemini && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm text-danger"
                  disabled={pendingCancel.translate}
                  onClick={() => cancelMutation.mutate('translate')}
                >
                  {pendingCancel.translate ? 'Đang dừng...' : 'Dừng'}
                </button>
              )}
              {translate.progress && <span className="text-xs text-neutral-400">{translate.progress}</span>}
            </div>
          )}
          {translate.error && <p className="text-sm text-danger">{translate.error}</p>}

          {translate.status === 'done' && (
            <div className="flex flex-wrap items-center gap-2">
              <JobProgressBar job={ttsJob.data} />
              <button type="button" className={tts.status === 'done' ? secondaryButtonClass : primaryButtonClass} disabled={busyAny || ttsMutation.isPending} onClick={() => ttsMutation.mutate()}>
                {busyTTS ? 'Đang đọc...' : labels.isAdmin ? (tts.status === 'done' ? 'Chạy lại TTS' : 'Chạy TTS') : tts.status === 'done' ? 'Đọc lại' : 'Tạo giọng đọc'}
              </button>
              {tts.status === 'done' && tts.progress?.includes('lỗi') && (
                <button type="button" className={secondaryButtonClass} disabled={busyAny} onClick={() => retryTTSMutation.mutate()}>
                  Thử lại câu lỗi
                </button>
              )}
              {busyTTS && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm text-danger"
                  disabled={pendingCancel.tts}
                  onClick={() => cancelMutation.mutate('tts')}
                >
                  {pendingCancel.tts ? 'Đang dừng...' : 'Dừng'}
                </button>
              )}
              {tts.progress && <span className="text-xs text-neutral-400">{tts.progress}</span>}
            </div>
          )}
          {tts.error && <p className="text-sm text-danger">{tts.error}</p>}

          {standaloneAssemble && tts.status === 'done' && (
            <div className="flex flex-wrap items-center gap-2">
              <JobProgressBar job={assembleJob.data} />
              <button
                type="button"
                className={secondaryButtonClass}
                disabled={busyAny || assembleMutation.isPending}
                onClick={() => assembleMutation.mutate()}
              >
                {busyAssemble ? 'Đang dựng...' : assemble.status === 'done' ? 'Dựng lại CapCut' : 'Dựng CapCut'}
              </button>
              {busyAssemble && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm text-danger"
                  disabled={pendingCancel.assemble}
                  onClick={() => cancelMutation.mutate('assemble')}
                >
                  {pendingCancel.assemble ? 'Đang dừng...' : 'Dừng'}
                </button>
              )}
              {assemble.status === 'done' && (
                <span className="text-xs text-accent-300">
                  Xong — draft <span className="mono">{`${projectId}_p${index + 1}`}</span>
                </span>
              )}
            </div>
          )}
          {standaloneAssemble && assemble.error && <p className="text-sm text-danger">{assemble.error}</p>}

          {standaloneAssemble && tts.status === 'done' && (
            <div className="rounded-lg border border-neutral-800 p-3">
              <p className="mb-2 text-xs font-medium text-neutral-400">Xuất video (không qua CapCut)</p>
              <div className="flex flex-wrap items-center gap-2">
                <JobProgressBar job={exportJob.data} />
                <button
                  type="button"
                  className={exportRec.status === 'done' ? secondaryButtonClass : primaryButtonClass}
                  disabled={busyAny || exportMutation.isPending}
                  onClick={() => exportMutation.mutate()}
                >
                  {busyExport ? 'Đang xuất...' : exportRec.status === 'done' ? 'Xuất lại video' : 'Xuất video'}
                </button>
                {busyExport && (
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm text-danger"
                    disabled={pendingCancelExport}
                    onClick={() => cancelExportMutation.mutate()}
                  >
                    {pendingCancelExport ? 'Đang dừng...' : 'Dừng'}
                  </button>
                )}
                {exportRec.status === 'done' && (
                  <>
                    <button type="button" className={secondaryButtonClass} disabled={busyAny} onClick={() => revealExportMutation.mutate()}>
                      Mở thư mục
                    </button>
                    <span className="text-xs text-accent-300">
                      Xong — final.mp4
                      {runDurationLabel(episode.stages.ingest?.at, exportRec.at) && (
                        <span className="text-neutral-400"> · ⏱ {runDurationLabel(episode.stages.ingest?.at, exportRec.at)}</span>
                      )}
                    </span>
                  </>
                )}
              </div>
              {exportRec.error && <p className="mt-2 text-sm text-danger">{exportRec.error}</p>}
            </div>
          )}

          {detail.cues.length > 0 && (
            <div className="max-h-64 overflow-auto rounded-lg border border-neutral-800">
              <table className="table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>中文</th>
                    <th>Tiếng Việt</th>
                    <th>Giọng đọc</th>
                    <th>Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.cues.map((c) => {
                    const isEditing = editingCueId === c.id
                    const rowBusy =
                      (retranslateCueMutation.isPending && retranslateCueMutation.variables === c.id) ||
                      (ttsCueMutation.isPending && ttsCueMutation.variables === c.id) ||
                      (updateCueMutation.isPending && updateCueMutation.variables?.cueId === c.id)
                    const actionsDisabled = busyAny || rowBusy
                    const entry = manifestById.get(c.id)
                    const hasVi = Boolean((c.text_vi ?? '').trim())
                    return (
                      <tr key={c.id}>
                        <td className="mono text-xs text-neutral-500">{c.id}</td>
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
                                  onClick={() => updateCueMutation.mutate({ cueId: c.id, text: draftText })}
                                >
                                  Lưu
                                </button>
                                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditingCueId(null)}>
                                  Huỷ
                                </button>
                              </div>
                            </div>
                          ) : (
                            (c.text_vi ?? '—')
                          )}
                        </td>
                        <td>
                          {entry?.path ? (
                            <MiniAudioPlayer src={api.episodeCueAudioUrl(projectId, episodeId, entry.path)} />
                          ) : entry?.error ? (
                            <button
                              type="button"
                              className="btn btn-ghost btn-sm text-danger"
                              disabled={actionsDisabled}
                              title={entry.error}
                              onClick={() => ttsCueMutation.mutate(c.id)}
                            >
                              lỗi · tạo lại
                            </button>
                          ) : hasVi ? (
                            <button
                              type="button"
                              className="btn btn-ghost btn-sm text-danger"
                              disabled={actionsDisabled}
                              onClick={() => ttsCueMutation.mutate(c.id)}
                            >
                              chưa có · tạo
                            </button>
                          ) : (
                            <span className="text-xs text-neutral-600">—</span>
                          )}
                        </td>
                        <td>
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
                              Dịch lại
                            </button>
                            <button
                              type="button"
                              className="btn btn-ghost btn-sm"
                              disabled={actionsDisabled}
                              onClick={() => ttsCueMutation.mutate(c.id)}
                            >
                              Đọc lại
                            </button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
