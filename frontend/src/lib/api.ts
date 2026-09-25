export type StageName = 'ingest' | 'transcribe' | 'translate' | 'tts' | 'assemble'
export type EpisodeStageName = 'ingest' | 'transcribe' | 'translate' | 'tts' | 'assemble'
export type StageStatus = 'pending' | 'running' | 'done' | 'failed'
export type ProjectType = 'single' | 'multi'
export type AudioMode = 'separated' | 'original' | 'mute'

// Tốc độ video tối thiểu khi cần chậm lại nhường thời gian cho giọng đọc TTS
// — khớp range 0.7-1.0 mà backend chấp nhận (StartAssembleRequest.min_video_speed).
export const MIN_VIDEO_SPEED_OPTIONS = [0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0]
export const DEFAULT_MIN_VIDEO_SPEED = 0.85
// dB, chỉ áp dụng khi audioMode="original" — xem export_direct.ORIGINAL_AUDIO_VOLUME_DB
export const DEFAULT_ORIGINAL_AUDIO_VOLUME_DB = -13
// Cỡ chữ phụ đề mới — theo hệ toạ độ kịch bản 288px libass, KHÔNG phải px
// thật (xem app/stages/export_direct.py::DEFAULT_SUBTITLE_FONT_SIZE).
export const DEFAULT_SUBTITLE_FONT_SIZE = 6

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
  auto_original_audio_volume_db: number
  auto_subtitle_font_size: number
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

export type QueueItemStatus = 'pending' | 'processing' | 'ready' | 'posted' | 'failed' | 'skipped'

export interface QueueItem {
  aweme_id: string
  title: string
  title_vi: string | null
  play_url: string
  share_url: string
  thumb_url: string | null
  duration_sec: number
  discovered_at: string
  status: QueueItemStatus
  project_id: string | null
  posted_at: string | null
  platform_post_id: string | null
  error: string | null
  failed_stage: 'activate' | 'publish' | null
  files_cleaned_at?: string | null
}

export interface SocialProjectSummary {
  id: string
  title: string
  douyin_profile_url: string
  status: 'active' | 'paused'
  created_at: string
}

export interface SocialProjectState extends SocialProjectSummary {
  posts_per_day: number
  engine: string
  ocr_crop_region: number[] | null
  tts_engine: string
  voice: string
  audio_mode: string
  original_audio_volume_db: number
  music_volume_db: number
  subtitle_font_size: number
  min_video_speed: number
  use_viesnap_fallback: boolean
  crawl_via_browser: boolean
  queue: QueueItem[]
  last_crawl_at: string | null
  last_post_at: string | null
  next_post_at: string | null
  douyin_backoff_until: string | null
  douyin_backoff_level: number
  tiktok_session_path: string
  facebook_page_id: string
  facebook_page_token: string
}

export type MonitorDailyStatus =
  | 'paused'
  | 'processing'
  | 'stopped_failed'
  | 'done_today'
  | 'prepared'
  | 'resting'
  | 'no_pending'
  | 'waiting'

export interface MonitorPipelineEntry {
  order: number
  social_id: string
  social_title: string
  aweme_id: string | null
  video_title: string
  duration_sec: number | null
  posts_per_day: number
  posted_today: number
  need_today: number
  ready_count: number
  pending_count: number
  failed_count: number
  next_post_at: string | null
  status: MonitorDailyStatus
  reason: string | null
  ahead?: boolean
}

export interface MonitorPublishEntry {
  social_id: string
  social_title: string
  next_post_at: string | null
  last_post_at: string | null
  posts_per_day: number
  posted_today: number
  ready_count: number
  aweme_id: string | null
  video_title: string
  status: 'no_ready' | 'scheduled' | 'waiting_window' | 'due' | 'done_today'
}

export interface MonitorJobRow {
  social_id: string
  social_title: string
  aweme_id: string | null
  label: string | null
}

export interface CleanupPlanEntry {
  project_id: string
  social_id: string
  social_title: string
  aweme_id: string
  video_title: string
  size_mb: number
  is_current: boolean
  due_at: string | null
  protected_reason: string | null
  rule: string
  rule_label: string
  kept_by_user: boolean
}

export type SocialCleanupPlan = Record<
  string,
  {
    due_at: string | null
    size_mb: number
    rule_label: string
    protected_reason: string | null
    project_id: string
    kept_by_user: boolean
  }
>

export interface CleanupOverview {
  free_gb: number
  min_free_gb: number
  low_disk: boolean
  cleanup_after_hours: number
  last_cleanup: { at: string; projects_cleaned: number; freed_mb: number; free_gb: number } | null
  plan: CleanupPlanEntry[]
}

export interface SocialMonitor {
  now: string
  in_posting_window: boolean
  posting_windows: string[]
  processing: {
    social_id: string
    social_title: string
    aweme_id: string
    video_title: string
    project_id: string | null
    duration_sec: number | null
    stage: string | null
    stage_label: string
    progress: { done: number; total: number; label: string | null } | null
  }[]
  publishing: MonitorJobRow[]
  crawling: MonitorJobRow[]
  pipeline_queue: MonitorPipelineEntry[]
  daily_plan: MonitorPipelineEntry[]
  storage: {
    free_gb: number
    min_free_gb: number
    low_disk: boolean
    cleanup_after_hours: number
    last_cleanup: { at: string; projects_cleaned: number; freed_mb: number; free_gb: number } | null
    plan_summary: { due_count: number; due_mb: number; kept_count: number }
  }
  publish_plan: MonitorPublishEntry[]
  crawl_plan: {
    social_id: string
    social_title: string
    last_crawl_at: string | null
    next_crawl_at: string | null
    active: boolean
  }[]
  stats: {
    projects: number
    projects_active: number
    pending: number
    processing: number
    ready: number
    failed: number
    posted_today: number
  }
}

export interface DouyinBrowserStatus {
  logged_in: boolean
  checked_at: string | null
  profile_ready: boolean
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
    originalAudioVolumeDb: number = DEFAULT_ORIGINAL_AUDIO_VOLUME_DB,
  ) =>
    request<ProjectSummary>(`/api/projects/${id}/auto-pipeline`, {
      method: 'PATCH',
      body: JSON.stringify({
        enabled,
        engine,
        tts_engine: ttsEngine,
        voice,
        audio_mode: audioMode,
        original_audio_volume_db: originalAudioVolumeDb,
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
  startAssemble: (
    id: string,
    audioMode: AudioMode,
    minVideoSpeed: number,
    originalAudioVolumeDb: number = DEFAULT_ORIGINAL_AUDIO_VOLUME_DB,
  ) =>
    postJson<{ status: string }>(`/api/projects/${id}/assemble`, {
      audio_mode: audioMode,
      min_video_speed: minVideoSpeed,
      original_audio_volume_db: originalAudioVolumeDb,
    }),
  jobStatus: (id: string, stage: StageName | 'export') =>
    request<JobStatus>(`/api/projects/${id}/jobs/${stage}`),
  cancelJob: (id: string, stage: StageName | 'export') =>
    postJson<{ status: string }>(`/api/projects/${id}/jobs/${stage}/cancel`),

  // --- Xuất video trực tiếp (ffmpeg, không qua CapCut) ---
  startExport: (
    id: string,
    audioMode: AudioMode,
    minVideoSpeed: number,
    originalAudioVolumeDb: number = DEFAULT_ORIGINAL_AUDIO_VOLUME_DB,
    subtitleFontSize: number = DEFAULT_SUBTITLE_FONT_SIZE,
  ) =>
    postJson<{ status: string }>(`/api/projects/${id}/export`, {
      audio_mode: audioMode,
      min_video_speed: minVideoSpeed,
      original_audio_volume_db: originalAudioVolumeDb,
      subtitle_font_size: subtitleFontSize,
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
  startEpisodeAssemble: (
    id: string,
    episodeId: string,
    audioMode: AudioMode,
    minVideoSpeed: number,
    originalAudioVolumeDb: number = DEFAULT_ORIGINAL_AUDIO_VOLUME_DB,
  ) =>
    postJson<{ status: string }>(`/api/projects/${id}/episodes/${episodeId}/assemble`, {
      audio_mode: audioMode,
      min_video_speed: minVideoSpeed,
      original_audio_volume_db: originalAudioVolumeDb,
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

  listSocial: () => request<SocialProjectSummary[]>('/api/social'),
  createSocial: (title: string, douyinProfileUrl: string, postsPerDay = 1) =>
    postJson<SocialProjectState>('/api/social', { title, douyin_profile_url: douyinProfileUrl, posts_per_day: postsPerDay }),
  getSocial: (id: string) => request<SocialProjectState>(`/api/social/${id}`),
  updateSocial: (
    id: string,
    body: Partial<{
      title: string
      status: 'active' | 'paused'
      posts_per_day: number
      engine: string
      tts_engine: string
      voice: string
      audio_mode: string
      original_audio_volume_db: number
      music_volume_db: number
      subtitle_font_size: number
      min_video_speed: number
      use_viesnap_fallback: boolean
      crawl_via_browser: boolean
    }>,
  ) => request<SocialProjectState>(`/api/social/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteSocial: (id: string) => request<void>(`/api/social/${id}`, { method: 'DELETE' }),
  updateSocialOcrCropRegion: (id: string, region: number[] | null) =>
    postJson<{ status: string }>(`/api/social/${id}/ocr-crop-region`, { region }),
  uploadSocialMusic: (id: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ status: string }>(`/api/social/${id}/music`, { method: 'POST', body: form })
  },
  deleteSocialMusic: (id: string) => request<void>(`/api/social/${id}/music`, { method: 'DELETE' }),
  uploadSocialLogo: (id: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ status: string }>(`/api/social/${id}/logo`, { method: 'POST', body: form })
  },
  deleteSocialLogo: (id: string) => request<void>(`/api/social/${id}/logo`, { method: 'DELETE' }),
  socialMusicUrl: (id: string) => `/api/social/${id}/music`,
  socialLogoUrl: (id: string) => `/api/social/${id}/logo`,
  crawlSocial: (id: string, limit: number | null = null) =>
    postJson<{ status: string }>(`/api/social/${id}/crawl`, { limit }),
  socialCrawlJobStatus: (id: string) => request<JobStatus>(`/api/social/${id}/jobs/crawl`),
  cancelSocialCrawl: (id: string) => postJson<{ status: string }>(`/api/social/${id}/jobs/crawl/cancel`),
  activateQueueItem: (id: string, awemeId: string) =>
    postJson<{ status: string; project_id: string }>(`/api/social/${id}/queue/${awemeId}/activate`),
  skipQueueItem: (id: string, awemeId: string) => postJson<{ status: string }>(`/api/social/${id}/queue/${awemeId}/skip`),
  unskipQueueItem: (id: string, awemeId: string) =>
    postJson<{ status: string }>(`/api/social/${id}/queue/${awemeId}/unskip`),
  socialMonitor: () => request<SocialMonitor>('/api/social-monitor'),
  socialCleanupPlan: (id: string) => request<SocialCleanupPlan>(`/api/social/${id}/cleanup-plan`),
  cleanupOverview: () => request<CleanupOverview>('/api/cleanup'),
  cleanupDelete: (projectIds: string[], mode: 'files' | 'project') =>
    postJson<{
      results: { project_id: string; ok: boolean; error?: string; freed_mb?: number }[]
      deleted: number
      freed_mb: number
    }>('/api/cleanup/delete', { project_ids: projectIds, mode }),
  setCleanupKeep: (projectId: string, keep: boolean) =>
    postJson<{ status: string }>('/api/cleanup/keep', { project_id: projectId, keep }),
  douyinBrowserStatus: () => request<DouyinBrowserStatus>('/api/douyin-browser/status'),
  douyinBrowserLogin: () => postJson<{ status: string }>('/api/douyin-browser/login'),
  douyinBrowserLoginJob: () => request<JobStatus>('/api/douyin-browser/login-job'),
  tiktokLoginStatus: (id: string) => request<{ logged_in: boolean }>(`/api/social/${id}/tiktok/status`),
  tiktokLogin: (id: string) => postJson<{ status: string }>(`/api/social/${id}/tiktok/login`),
  tiktokLoginJobStatus: (id: string) => request<JobStatus>(`/api/social/${id}/jobs/tiktok_login`),
  cancelTiktokLogin: (id: string) => postJson<{ status: string }>(`/api/social/${id}/jobs/tiktok_login/cancel`),
  publishQueueItem: (id: string, awemeId: string) =>
    postJson<{ status: string }>(`/api/social/${id}/queue/${awemeId}/publish`),
  publishQueueItemJobStatus: (id: string, awemeId: string) =>
    request<JobStatus>(`/api/social/${id}/queue/${awemeId}/jobs/publish`),

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
