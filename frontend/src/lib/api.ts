export type StageName = 'ingest' | 'transcribe' | 'translate' | 'tts' | 'assemble'
export type StageStatus = 'pending' | 'running' | 'done' | 'failed'

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

export type TranscribeEngine = 'whisper' | 'sensevoice'

export interface ProjectState {
  project_id: string
  title: string
  created_at: string
  original_filename: string | null
  video_relpath: string | null
  duration_sec: number | null
  stages: Record<StageName, StageRecord>
}

export interface ProjectSummary {
  project_id: string
  title: string
  created_at: string
  current_stage: StageName | null
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
}

export interface JobStatus {
  registered: boolean
  orphaned: boolean
  status: 'running' | 'done' | 'failed' | null
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
  gemini_models: ModelOption[]
  whisper_models: ModelOption[]
  whisper_languages: ModelOption[]
  tts_voices: ModelOption[]
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
  createProject: (title: string) => postJson<ProjectSummary>('/api/projects', { title }),
  getProject: (id: string) => request<ProjectDetail>(`/api/projects/${id}`),
  renameProject: (id: string, title: string) =>
    request<ProjectSummary>(`/api/projects/${id}`, { method: 'PATCH', body: JSON.stringify({ title }) }),
  deleteProject: (id: string) => request<void>(`/api/projects/${id}`, { method: 'DELETE' }),

  uploadVideo: (id: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request(`/api/projects/${id}/ingest`, { method: 'POST', body: form })
  },
  startTranscribe: (id: string, engine: TranscribeEngine = 'whisper') =>
    postJson<{ status: string }>(`/api/projects/${id}/transcribe`, { engine }),
  startTranslate: (id: string) => postJson<{ status: string }>(`/api/projects/${id}/translate`),
  startTTS: (id: string, voice: string) => postJson<{ status: string }>(`/api/projects/${id}/tts`, { voice }),
  retryTTS: (id: string, voice: string) =>
    postJson<{ status: string }>(`/api/projects/${id}/tts`, { voice, retry_failed_only: true }),
  previewVoiceUrl: (voice: string) => `/api/tts/preview?voice=${encodeURIComponent(voice)}`,
  startAssemble: (id: string) => postJson<{ status: string }>(`/api/projects/${id}/assemble`),
  jobStatus: (id: string, stage: 'transcribe' | 'translate' | 'tts' | 'assemble') =>
    request<JobStatus>(`/api/projects/${id}/jobs/${stage}`),

  getSettings: () => request<AppSettings>('/api/settings'),
  updateSettings: (
    body: Partial<{
      workspace_dir: string
      gemini_api_key: string
      gemini_model: string
      whisper_model: string
      whisper_device: string
      whisper_language: string
    }>,
  ) => request<AppSettings>('/api/settings', { method: 'PUT', body: JSON.stringify(body) }),
}
