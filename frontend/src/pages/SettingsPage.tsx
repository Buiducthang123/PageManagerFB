import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { inputClass, primaryButtonClass } from '../lib/ui'

export default function SettingsPage() {
  const settingsQuery = useQuery({ queryKey: ['settings'], queryFn: api.getSettings })
  const [workspaceDir, setWorkspaceDir] = useState('')
  const [geminiKey, setGeminiKey] = useState('')
  const [geminiModel, setGeminiModel] = useState('')
  const [whisperModel, setWhisperModel] = useState('')
  const [whisperDevice, setWhisperDevice] = useState('auto')
  const [whisperLanguage, setWhisperLanguage] = useState('zh')
  const [translatePace, setTranslatePace] = useState('full_meaning')
  const [hydrated, setHydrated] = useState(false)

  useEffect(() => {
    if (!settingsQuery.data || hydrated) return
    setWorkspaceDir(settingsQuery.data.workspace_dir)
    setGeminiModel(settingsQuery.data.gemini_model)
    setWhisperModel(settingsQuery.data.whisper_model)
    setWhisperDevice(settingsQuery.data.whisper_device)
    setWhisperLanguage(settingsQuery.data.whisper_language || 'zh')
    setTranslatePace(settingsQuery.data.translate_pace || 'full_meaning')
    setHydrated(true)
  }, [settingsQuery.data, hydrated])

  const saveMutation = useMutation({
    mutationFn: () =>
      api.updateSettings({
        workspace_dir: workspaceDir,
        gemini_api_key: geminiKey || undefined,
        gemini_model: geminiModel,
        whisper_model: whisperModel,
        whisper_device: whisperDevice,
        whisper_language: whisperLanguage,
        translate_pace: translatePace,
      }),
    onSuccess: () => setGeminiKey(''),
  })

  const data = settingsQuery.data

  return (
    <div className="mx-auto max-w-xl space-y-5">
      <h1 className="text-2xl">Cài đặt</h1>
      <p className="text-sm text-neutral-400">
        Key AI Studio lấy tại{' '}
        <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer">
          aistudio.google.com
        </a>
        . Gói Gemini App / Google One AI Pro không nâng quota API — key vẫn Free Tier
        (3.6 Flash ≈ 20 request/ngày) trừ khi{' '}
        <a href="https://aistudio.google.com/usage" target="_blank" rel="noreferrer">
          bật Billing Google Cloud
        </a>{' '}
        trên đúng project của key.
      </p>

      <label className="block text-sm text-neutral-300">
        Workspace
        <input className={inputClass} value={workspaceDir} onChange={(e) => setWorkspaceDir(e.target.value)} />
      </label>

      <label className="block text-sm text-neutral-300">
        Gemini API key
        <input
          className={inputClass}
          type="password"
          autoComplete="off"
          value={geminiKey}
          placeholder={data?.gemini_api_key_masked ? `Hiện tại: ${data.gemini_api_key_masked}` : 'Chưa cấu hình'}
          onChange={(e) => setGeminiKey(e.target.value)}
        />
      </label>

      <label className="block text-sm text-neutral-300">
        Gemini model
        <select className={inputClass} value={geminiModel} onChange={(e) => setGeminiModel(e.target.value)}>
          {(data?.gemini_models ?? []).map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </select>
      </label>

      <label className="block text-sm text-neutral-300">
        Whisper model
        <select className={inputClass} value={whisperModel} onChange={(e) => setWhisperModel(e.target.value)}>
          {(data?.whisper_models ?? []).map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </select>
      </label>

      <label className="block text-sm text-neutral-300">
        Whisper device
        <select className={inputClass} value={whisperDevice} onChange={(e) => setWhisperDevice(e.target.value)}>
          <option value="auto">auto (CUDA rồi fallback CPU)</option>
          <option value="cuda">cuda</option>
          <option value="cpu">cpu</option>
        </select>
      </label>

      <label className="block text-sm text-neutral-300">
        Ngôn ngữ gốc video
        <select className={inputClass} value={whisperLanguage} onChange={(e) => setWhisperLanguage(e.target.value)}>
          {(data?.whisper_languages ?? []).map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </select>
      </label>

      <label className="block text-sm text-neutral-300">
        Mức độ dịch (đủ ý vs đọc tự nhiên)
        <select className={inputClass} value={translatePace} onChange={(e) => setTranslatePace(e.target.value)}>
          {(data?.translate_paces ?? []).map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </select>
        <span className="mt-1 block text-xs text-neutral-500">
          Video thoại càng dồn dập (ít khoảng nghỉ) càng dễ phải đánh đổi giữa dịch đủ nghĩa và đọc
          TTS tự nhiên — chọn "Đủ ý hơn" nếu ưu tiên giữ cốt truyện, chấp nhận timing trôi nhẹ ở
          những câu quá gấp (assemble đã tự chậm video/tăng tốc giọng để bù, xem log khi ráp).
        </span>
      </label>

      {data?.whisper_cache_dir && (
        <p className="text-xs text-neutral-500">
          Cache Whisper/HuggingFace (ổ D, tránh C đầy):{' '}
          <span className="mono">{data.whisper_cache_dir}</span>
        </p>
      )}

      <button type="button" className={primaryButtonClass} disabled={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
        {saveMutation.isPending ? 'Đang lưu...' : 'Lưu'}
      </button>
      {saveMutation.isSuccess && <p className="text-sm text-accent-300">Đã lưu.</p>}
      {saveMutation.error && <p className="text-sm text-danger">{(saveMutation.error as Error).message}</p>}
    </div>
  )
}
