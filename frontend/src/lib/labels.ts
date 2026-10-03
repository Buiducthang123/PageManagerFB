import {
  STAGE_LABELS,
  TRANSCRIBE_ENGINE_LABELS,
  USER_TRANSCRIBE_ENGINE_LABELS,
  type StageName,
  type TTSEngine,
} from './api'
import { useIsAdmin } from './license'

/** Nhãn hiển thị. Admin thấy tên kỹ thuật (Whisper, VieNeu, demucs...) để gỡ lỗi,
 * user thường thấy tên theo chức năng — không lộ engine/thư viện/dịch vụ bên trong. */

export const USER_STAGE_LABELS: Record<StageName, string> = {
  ingest: 'Video gốc',
  transcribe: 'Nhận diện lời thoại',
  translate: 'Dịch',
  tts: 'Giọng đọc',
  assemble: 'CapCut',
}

export const TTS_ENGINE_LABELS: Record<TTSEngine, string> = { capcut: 'CapCut TTS', vieneu: 'VieNeu-TTS' }
export const USER_TTS_ENGINE_LABELS: Record<TTSEngine, string> = { capcut: 'Giọng CapCut', vieneu: 'Giọng đọc trên máy' }

export function useLabels() {
  const isAdmin = useIsAdmin()
  return {
    isAdmin,
    stage: isAdmin ? STAGE_LABELS : USER_STAGE_LABELS,
    transcribe: isAdmin ? TRANSCRIBE_ENGINE_LABELS : USER_TRANSCRIBE_ENGINE_LABELS,
    tts: isAdmin ? TTS_ENGINE_LABELS : USER_TTS_ENGINE_LABELS,
    /** audio_mode = "separated" */
    separatedAudio: isAdmin ? 'Tách nhạc nền/SFX bằng demucs' : 'Tách nhạc nền (bỏ giọng gốc)',
  }
}
