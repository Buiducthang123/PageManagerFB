export type StageName = 'ingest' | 'transcribe' | 'translate' | 'tts' | 'assemble'
export type EpisodeStageName = 'ingest' | 'transcribe' | 'translate' | 'tts' | 'assemble'
export type StageStatus = 'pending' | 'running' | 'done' | 'failed'
export type ProjectType = 'single' | 'multi'
export type AudioMode = 'separated' | 'original' | 'mute'

// Tốc độ video tối thiểu khi cần chậm lại nhường thời gian cho giọng đọc TTS
// — khớp range 0.7-1.0 mà backend chấp nhận (StartAssembleRequest.min_video_speed).
export const MIN_VIDEO_SPEED_OPTIONS = [0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0]
export const DEFAULT_MIN_VIDEO_SPEED = 0.85

export const STAGE_ORDER: StageName[] = ['ingest', 'transcribe', 'translate', 'tts', 'assemble']

export const STAGE_LABELS: Record<StageName, string> = {
  ingest: 'Upload',
  transcribe: 'Whisper',
  translate: 'Gemini',
  tts: 'TTS',
  assemble: 'CapCut',
}

export interface StageRecord {
  status: StageStatus
  output: string | null
  progress: string | null
  error: string | null
  at: string | null
  engine: string | null
}

export type TranscribeEngine = 'whisper' | 'sensevoice' | 'ocr'

export const TRANSCRIBE_ENGINE_LABELS: Record<TranscribeEngine, string> = {
  whisper: 'Whisper',
  sensevoice: 'SenseVoice',
  ocr: 'OCR (phụ đề cứng)',
}
export type TTSEngine = 'capcut' | 'vieneu'

export interface Episode {
  episode_id: string
  order: number
  title: string | null
  original_filename: string | null
  video_relpath: string | null
  duration_sec: number | null
  stages: Record<EpisodeStageName, StageRecord>
  created_at: string
}

export interface ProjectState {
  project_id: string
  title: string
  created_at: string
  project_type: ProjectType
  original_filename: string | null
  video_relpath: string | null
  duration_sec: number | null
  stages: Record<StageName, StageRecord>
  episodes: Episode[]
  auto_pipeline: boolean
  auto_engine: TranscribeEngine
  auto_tts_engine: TTSEngine
  auto_voice: string
  auto_audio_mode: AudioMode
  auto_min_video_speed: number
  split_mode: boolean
  // "Xuất video trực tiếp" (ffmpeg, không qua CapCut) — hành động PHỤ, tách
  // riêng khỏi `stages`/STAGE_ORDER (xem app/models.py::ProjectState.export).
  export: StageRecord
  export_blur_region: number[] | null
}

export interface ProjectSummary {
  project_id: string
  title: string
  created_at: string
  current_stage: StageName | null
  project_type: ProjectType
}

export interface SrtCue {
  id: number
  start: string
  end: string
  text: string
  text_vi: string | null
}

export interface LogEntry {
  ts: string
  stage: string
  message: string
}

export interface TTSManifestEntry {
  id: number
  start: string
  end: string
  path: string | null
  duration_ms: number
  error?: string
  text?: string
}

export interface EpisodeDetail {
  episode: Episode
  current_stage: EpisodeStageName | null
  video_url: string | null
  cues: SrtCue[]
  sub_zh: string | null
  sub_vi: string | null
  tts_manifest: TTSManifestEntry[]
}

export interface ProjectDetail {
  project: ProjectState
  current_stage: StageName | null
  video_url: string | null
  cues: SrtCue[]
  entity_dict: Record<string, string>
  sub_zh: string | null
  sub_vi: string | null
  tts_manifest: TTSManifestEntry[]
  logs: LogEntry[]
  episodes: EpisodeDetail[]
}

export interface MergeItem {
  merge_id: string
  title: string
  created_at: string
  status: 'pending' | 'running' | 'done' | 'failed'
  error: string | null
  input_filenames: string[]
  output_filename: string | null
}

export type DownloadMode = 'single' | 'profile' | 'search' | 'info'

export interface DownloadVideoInfo {
  title: string
  caption: string
  author: string
  share_url: string
  play_url: string
  thumb_url: string | null
  duration_sec: number
  likes: number | null
  comments: number | null
  shares: number | null
}

export interface DownloadItem {
  download_id: string
  title: string
  mode: DownloadMode
  input: Record<string, unknown>
  created_at: string
  status: 'pending' | 'running' | 'done' | 'failed'
  error: string | null
  output_files: string[]
  // Song song với output_files (cùng thứ tự) — 1 phần tử có thể null nếu
  // đọc file metadata lỗi. Chỉ có khi status === 'done'.
  video_info?: (DownloadVideoInfo | null)[]
}

export interface JobStatus {
  registered: boolean
  orphaned: boolean
  status: 'running' | 'done' | 'failed' | 'cancelled' | null
  total: number
  done_count: number
  current_label: string | null
  items: { id: string; label: string; status: string; error: string | null }[]
  error: string | null
  started_at: number | null
}

export interface ModelOption {
  id: string
  label: string
}

export interface AppSettings {
  workspace_dir: string
  gemini_api_key_masked: string
  gemini_model: string
  whisper_model: string
  whisper_device: string
  whisper_language: string
  translate_pace: string
  gemini_models: ModelOption[]
  whisper_models: ModelOption[]
  whisper_languages: ModelOption[]
  translate_paces: ModelOption[]
  tts_voices: ModelOption[]
  tts_voices_vieneu: ModelOption[]
  whisper_cache_dir: string
  capcut_drafts_dir: string
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: init?.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

function postJson<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: 'POST', body: body !== undefined ? JSON.stringify(body) : undefined })
}

export const api = {
  listProjects: () => request<ProjectSummary[]>('/api/projects'),
  createProject: (title: string, projectType: ProjectType = 'single') =>
    postJson<ProjectSummary>('/api/projects', { title, project_type: projectType }),
  getProject: (id: string) => request<ProjectDetail>(`/api/projects/${id}`),
  renameProject: (id: string, title: string) =>
    request<ProjectSummary>(`/api/projects/${id}`, { method: 'PATCH', body: JSON.stringify({ title }) }),
  deleteProject: (id: string) => request<void>(`/api/projects/${id}`, { method: 'DELETE' }),

  uploadVideo: (id: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request(`/api/projects/${id}/ingest`, { method: 'POST', body: form })
  },
  ingestUrl: (id: string, url: string) => postJson<{ status: string }>(`/api/projects/${id}/ingest/url`, { url }),
  revealVideo: (id: string) => postJson<{ status: string }>(`/api/projects/${id}/reveal-video`),
  updateAutoPipeline: (
    id: string,
    enabled: boolean,
    engine: TranscribeEngine,
    ttsEngine: TTSEngine,
    voice: string,
    audioMode: AudioMode,
    minVideoSpeed: number,
  ) =>
    request<ProjectSummary>(`/api/projects/${id}/auto-pipeline`, {
      method: 'PATCH',
      body: JSON.stringify({
        enabled,
        engine,
        tts_engine: ttsEngine,
        voice,
        audio_mode: audioMode,
        min_video_speed: minVideoSpeed,
      }),
    }),
  startTranscribe: (id: string, engine: TranscribeEngine = 'whisper', cropRegion?: number[] | null) =>
    postJson<{ status: string }>(`/api/projects/${id}/transcribe`, { engine, crop_region: cropRegion ?? null }),
  startTranslate: (id: string) => postJson<{ status: string }>(`/api/projects/${id}/translate`),
  startTTS: (id: string, voice: string, engine: TTSEngine = 'capcut') =>
    postJson<{ status: string }>(`/api/projects/${id}/tts`, { voice, engine }),
  retryTTS: (id: string, voice: string) =>
    postJson<{ status: string }>(`/api/projects/${id}/tts`, { voice, retry_failed_only: true }),
  previewVoiceUrl: (voice: string, engine: TTSEngine = 'capcut') =>
    `/api/tts/preview?voice=${encodeURIComponent(voice)}&engine=${engine}`,
  updateCue: (id: string, cueId: number, text_vi: string) =>
    request<SrtCue>(`/api/projects/${id}/cues/${cueId}`, { method: 'PATCH', body: JSON.stringify({ text_vi }) }),
  retranslateCue: (id: string, cueId: number) =>
    postJson<SrtCue>(`/api/projects/${id}/cues/${cueId}/retranslate`),
  ttsCue: (id: string, cueId: number, voice: string) =>
    postJson<TTSManifestEntry>(`/api/projects/${id}/cues/${cueId}/tts`, { voice }),
  startAssemble: (id: string, audioMode: AudioMode, minVideoSpeed: number) =>
    postJson<{ status: string }>(`/api/projects/${id}/assemble`, {
      audio_mode: audioMode,
      min_video_speed: minVideoSpeed,
    }),
  jobStatus: (id: string, stage: StageName | 'export') =>
    request<JobStatus>(`/api/projects/${id}/jobs/${stage}`),
  cancelJob: (id: string, stage: StageName | 'export') =>
    postJson<{ status: string }>(`/api/projects/${id}/jobs/${stage}/cancel`),

  // --- Xuất video trực tiếp (ffmpeg, không qua CapCut) ---
  startExport: (id: string, audioMode: AudioMode, minVideoSpeed: number) =>
    postJson<{ status: string }>(`/api/projects/${id}/export`, {
      audio_mode: audioMode,
      min_video_speed: minVideoSpeed,
    }),
  updateExportBlurRegion: (id: string, region: number[] | null) =>
    postJson<{ status: string }>(`/api/projects/${id}/export/blur-region`, { region }),
  detectExportBlurRegion: (id: string) =>
    postJson<{ status: string; region: number[] }>(`/api/projects/${id}/export/detect-blur-region`, {}),
  revealExportVideo: (id: string) => postJson<{ status: string }>(`/api/projects/${id}/export/reveal`),
  uploadExportMusic: (id: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ status: string }>(`/api/projects/${id}/export/music`, { method: 'POST', body: form })
  },
  deleteExportMusic: (id: string) => request<void>(`/api/projects/${id}/export/music`, { method: 'DELETE' }),
  uploadExportLogo: (id: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ status: string }>(`/api/projects/${id}/export/logo`, { method: 'POST', body: form })
  },
  deleteExportLogo: (id: string) => request<void>(`/api/projects/${id}/export/logo`, { method: 'DELETE' }),
  exportVideoUrl: (id: string) => `/api/projects/${id}/assets/export/final.mp4`,
  exportMusicUrl: (id: string) => `/api/projects/${id}/export/music`,
  exportLogoUrl: (id: string) => `/api/projects/${id}/export/logo`,

  // --- Episodes (dự án dài tập) ---
  createEpisode: (id: string, opts?: { title?: string; insertAfterEpisodeId?: string }) =>
    postJson<EpisodeDetail>(`/api/projects/${id}/episodes`, {
      title: opts?.title ?? '',
      insert_after_episode_id: opts?.insertAfterEpisodeId ?? null,
    }),
  reorderEpisodes: (id: string, episodeIds: string[]) =>
    request<EpisodeDetail[]>(`/api/projects/${id}/episodes/reorder`, {
      method: 'PATCH',
      body: JSON.stringify({ episode_ids: episodeIds }),
    }),
  deleteEpisode: (id: string, episodeId: string) =>
    request<void>(`/api/projects/${id}/episodes/${episodeId}`, { method: 'DELETE' }),
  uploadEpisodeVideo: (id: string, episodeId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request(`/api/projects/${id}/episodes/${episodeId}/ingest`, { method: 'POST', body: form })
  },
  ingestEpisodeUrl: (id: string, episodeId: string, url: string) =>
    postJson<{ status: string }>(`/api/projects/${id}/episodes/${episodeId}/ingest/url`, { url }),
  startEpisodeTranscribe: (id: string, episodeId: string, engine: TranscribeEngine = 'whisper', cropRegion?: number[] | null) =>
    postJson<{ status: string }>(`/api/projects/${id}/episodes/${episodeId}/transcribe`, {
      engine,
      crop_region: cropRegion ?? null,
    }),
  startEpisodeTranslate: (id: string, episodeId: string) =>
    postJson<{ status: string }>(`/api/projects/${id}/episodes/${episodeId}/translate`),
  startEpisodeTTS: (id: string, episodeId: string, voice: string, engine: TTSEngine = 'capcut') =>
    postJson<{ status: string }>(`/api/projects/${id}/episodes/${episodeId}/tts`, { voice, engine }),
  retryEpisodeTTS: (id: string, episodeId: string, voice: string) =>
    postJson<{ status: string }>(`/api/projects/${id}/episodes/${episodeId}/tts`, { voice, retry_failed_only: true }),
  startEpisodeAssemble: (id: string, episodeId: string, audioMode: AudioMode, minVideoSpeed: number) =>
    postJson<{ status: string }>(`/api/projects/${id}/episodes/${episodeId}/assemble`, {
      audio_mode: audioMode,
      min_video_speed: minVideoSpeed,
    }),
  splitProject: (id: string, splitPointsS: number[]) =>
    postJson<EpisodeDetail[]>(`/api/projects/${id}/split`, { split_points_s: splitPointsS }),
  updateEpisodeCue: (id: string, episodeId: string, cueId: number, text_vi: string) =>
    request<SrtCue>(`/api/projects/${id}/episodes/${episodeId}/cues/${cueId}`, {
      method: 'PATCH',
      body: JSON.stringify({ text_vi }),
    }),
  retranslateEpisodeCue: (id: string, episodeId: string, cueId: number) =>
    postJson<SrtCue>(`/api/projects/${id}/episodes/${episodeId}/cues/${cueId}/retranslate`),
  ttsEpisodeCue: (id: string, episodeId: string, cueId: number, voice: string) =>
    postJson<TTSManifestEntry>(`/api/projects/${id}/episodes/${episodeId}/cues/${cueId}/tts`, { voice }),
  episodeJobStatus: (id: string, episodeId: string, stage: EpisodeStageName) =>
    request<JobStatus>(`/api/projects/${id}/episodes/${episodeId}/jobs/${stage}`),
  cancelEpisodeJob: (id: string, episodeId: string, stage: EpisodeStageName) =>
    postJson<{ status: string }>(`/api/projects/${id}/episodes/${episodeId}/jobs/${stage}/cancel`),

  // --- Ghép video (đứng riêng, không thuộc project nào) ---
  listMerges: () => request<MergeItem[]>('/api/merges'),
  createMerge: (title: string, files: File[]) => {
    const form = new FormData()
    form.append('title', title)
    files.forEach((f) => form.append('files', f))
    return request<{ merge_id: string; status: string }>('/api/merges', { method: 'POST', body: form })
  },
  mergeJobStatus: (mergeId: string) => request<JobStatus>(`/api/merges/${mergeId}/jobs/status`),
  cancelMergeJob: (mergeId: string) => postJson<{ status: string }>(`/api/merges/${mergeId}/jobs/cancel`),
  downloadMergeUrl: (mergeId: string) => `/api/merges/${mergeId}/download`,
  deleteMerge: (mergeId: string) => request<void>(`/api/merges/${mergeId}`, { method: 'DELETE' }),

  // --- Tải video riêng (Douyin, đứng riêng không thuộc project nào) ---
  listDownloads: () => request<DownloadItem[]>('/api/downloads'),
  pickDownloadFolder: () => postJson<{ path: string | null }>('/api/downloads/pick-folder'),
  createSingleDownload: (url: string, destDir = '', title = '') =>
    postJson<{ download_id: string; status: string }>('/api/downloads', { mode: 'single', url, dest_dir: destDir, title }),
  createProfileDownload: (url: string, modes: string[], number: Record<string, number>, destDir = '', title = '') =>
    postJson<{ download_id: string; status: string }>('/api/downloads', {
      mode: 'profile',
      url,
      modes,
      number,
      dest_dir: destDir,
      title,
    }),
  // Chỉ lấy thông tin (title/caption/thumb/link/thống kê), KHÔNG tải video.
  createInfoScanDownload: (url: string, modes: string[], number: Record<string, number>, title = '') =>
    postJson<{ download_id: string; status: string }>('/api/downloads', {
      mode: 'info',
      url,
      modes,
      number,
      title,
    }),
  createSearchDownload: (keyword: string, searchMax: number, destDir = '', title = '') =>
    postJson<{ download_id: string; status: string }>('/api/downloads', {
      mode: 'search',
      keyword,
      search_max: searchMax,
      dest_dir: destDir,
      title,
    }),
  downloadJobStatus: (downloadId: string) => request<JobStatus>(`/api/downloads/${downloadId}/jobs/status`),
  cancelDownloadJob: (downloadId: string) => postJson<{ status: string }>(`/api/downloads/${downloadId}/jobs/cancel`),
  downloadFileUrl: (downloadId: string, filename: string) =>
    `/api/downloads/${downloadId}/files/${encodeURIComponent(filename)}`,
  revealDownload: (downloadId: string) => postJson<{ status: string }>(`/api/downloads/${downloadId}/reveal`),
  deleteDownload: (downloadId: string) => request<void>(`/api/downloads/${downloadId}`, { method: 'DELETE' }),

  getSettings: () => request<AppSettings>('/api/settings'),
  updateSettings: (
    body: Partial<{
      workspace_dir: string
      gemini_api_key: string
      gemini_model: string
      whisper_model: string
      whisper_device: string
      whisper_language: string
      translate_pace: string
    }>,
  ) => request<AppSettings>('/api/settings', { method: 'PUT', body: JSON.stringify(body) }),
}
