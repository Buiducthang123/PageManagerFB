import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type AiDevice, type CapcutDirStatus } from '../lib/api'
import { inputClass, primaryButtonClass, secondaryButtonClass } from '../lib/ui'
import ChangePasswordPanel from '../components/ChangePasswordPanel'
import DouyinLoginCard from '../components/DouyinLogin'
import { useIsAdmin } from '../lib/license'

const CAPCUT_STATUS_CLASS: Record<CapcutDirStatus, string> = {
  ok: 'text-accent-300',
  warning: 'text-neutral-300',
  error: 'text-danger',
}

export default function SettingsPage() {
  const isAdmin = useIsAdmin()
  const queryClient = useQueryClient()
  const settingsQuery = useQuery({ queryKey: ['settings'], queryFn: api.getSettings })
  const [workspaceDir, setWorkspaceDir] = useState('')
  const [capcutDir, setCapcutDir] = useState('')
  const [capcutCheck, setCapcutCheck] = useState<{ path: string; status: CapcutDirStatus; message: string } | null>(null)
  const [geminiKey, setGeminiKey] = useState('')
  const [geminiModel, setGeminiModel] = useState('')
  const [whisperModel, setWhisperModel] = useState('')
  const [aiDevice, setAiDevice] = useState<AiDevice>('auto')
  const [modelsDir, setModelsDir] = useState('')
  const [tempDir, setTempDir] = useState('')
  const [ttsConcurrency, setTtsConcurrency] = useState('3')
  const [demucsTimeoutMin, setDemucsTimeoutMin] = useState('30')
  const [geminiTest, setGeminiTest] = useState<{ status: CapcutDirStatus; message: string } | null>(null)
  const [whisperLanguage, setWhisperLanguage] = useState('zh')
  const [translatePace, setTranslatePace] = useState('full_meaning')
  const [hydrated, setHydrated] = useState(false)

  useEffect(() => {
    if (!settingsQuery.data || hydrated) return
    setWorkspaceDir(settingsQuery.data.workspace_dir)
    setCapcutDir(settingsQuery.data.capcut_drafts_dir)
    setGeminiModel(settingsQuery.data.gemini_model)
    setWhisperModel(settingsQuery.data.whisper_model)
    setAiDevice(settingsQuery.data.ai_device || 'auto')
    setModelsDir(settingsQuery.data.models_dir_custom)
    setTempDir(settingsQuery.data.temp_dir_custom)
    setTtsConcurrency(String(settingsQuery.data.tts_concurrency))
    setDemucsTimeoutMin(String(Math.round(settingsQuery.data.demucs_timeout_s / 60)))
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
        whisper_language: whisperLanguage,
        translate_pace: translatePace,
        capcut_drafts_dir: capcutDir.trim(),
        ai_device: aiDevice,
        models_dir: modelsDir.trim(),
        temp_dir: tempDir.trim(),
        tts_concurrency: Number(ttsConcurrency) || 3,
        demucs_timeout_s: Math.round((Number(demucsTimeoutMin) || 30) * 60),
      }),
    onSuccess: (saved) => {
      setGeminiKey('')
      setGeminiTest(null)
      setCapcutDir(saved.capcut_drafts_dir)
      setCapcutCheck(null)
      setModelsDir(saved.models_dir_custom)
      setTempDir(saved.temp_dir_custom)
      queryClient.setQueryData(['settings'], saved)
    },
  })

  const geminiTestMutation = useMutation({
    mutationFn: () => api.testGeminiKey(geminiKey.trim()),
    onSuccess: (res) => setGeminiTest(res),
    onError: (err) => setGeminiTest({ status: 'error', message: (err as Error).message }),
  })

  const checkMutation = useMutation({
    mutationFn: (path: string) => api.checkCapcutDraftsDir(path),
    onSuccess: (res, path) => setCapcutCheck({ path, ...res }),
  })

  const data = settingsQuery.data
  // Kết quả kiểm tra của đúng đường dẫn đang nhập: bấm "Kiểm tra" thì dùng kết
  // quả đó, chưa sửa gì thì dùng trạng thái server trả về lúc tải trang.
  const capcutStatus =
    capcutCheck && capcutCheck.path === capcutDir.trim()
      ? capcutCheck
      : data && capcutDir.trim() === data.capcut_drafts_dir
        ? { status: data.capcut_drafts_status, message: data.capcut_drafts_message }
        : null
  const detectCapcut = () => {
    const found = data?.capcut_drafts_detected?.[0]
    if (found) {
      setCapcutDir(found)
      checkMutation.mutate(found)
    } else {
      setCapcutCheck({ path: capcutDir.trim(), status: 'error', message: 'Không thấy thư mục draft mặc định của CapCut trên máy này — dán đường dẫn tay' })
    }
  }

  return (
    <div className="mx-auto max-w-xl space-y-5">
      <h1 className="text-2xl">Cài đặt</h1>
      <ChangePasswordPanel />
      <DouyinLoginCard />
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
        {data && data.workspace_disk_status !== 'ok' && (
          <span className={`mt-1 block text-xs ${CAPCUT_STATUS_CLASS[data.workspace_disk_status]}`}>
            {data.workspace_disk_status === 'error' ? '✗' : '!'} Ổ chứa workspace chỉ còn {data.workspace_free_gb} GB trống —
            dọn bớt ở trang "Tự dọn ổ đĩa" hoặc chuyển workspace sang ổ khác.
          </span>
        )}
      </label>

      <div className="text-sm text-neutral-300">
        <label htmlFor="capcut-drafts-dir">Thư mục draft CapCut</label>
        <div className="mt-1 flex gap-2">
          <input
            id="capcut-drafts-dir"
            className={`${inputClass} mono min-w-0 flex-1`}
            value={capcutDir}
            placeholder="vd C:\Users\<tên>\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft"
            onChange={(e) => setCapcutDir(e.target.value)}
          />
          <button type="button" className={`${secondaryButtonClass} shrink-0`} onClick={detectCapcut}>
            Tự dò
          </button>
          <button
            type="button"
            className={`${secondaryButtonClass} shrink-0`}
            disabled={!capcutDir.trim() || checkMutation.isPending}
            onClick={() => checkMutation.mutate(capcutDir.trim())}
          >
            {checkMutation.isPending ? 'Đang kiểm tra...' : 'Kiểm tra'}
          </button>
        </div>
        {capcutStatus && (
          <p className={`mt-1 text-xs ${CAPCUT_STATUS_CLASS[capcutStatus.status]}`}>
            {capcutStatus.status === 'ok' ? '✓ ' : capcutStatus.status === 'error' ? '✗ ' : '! '}
            {capcutStatus.message}
            {data?.capcut_drafts_source === 'auto' && capcutDir.trim() === data.capcut_drafts_dir && ' (tự dò thấy)'}
          </p>
        )}
        <span className="mt-1 block text-xs text-neutral-500">
          Nơi "Dựng CapCut" ghi draft để mở CapCut là thấy ngay. Mỗi máy một chỗ khác nhau: mở CapCut → Cài đặt →
          mục vị trí lưu bản nháp (Draft location), copy đường dẫn dán vào đây. Để trống = tự dò thư mục mặc định.
          Bấm <b>Lưu</b> bên dưới để áp dụng.
        </span>
      </div>

      <div className="text-sm text-neutral-300">
        <label htmlFor="gemini-key">Gemini API key</label>
        <div className="mt-1 flex gap-2">
          <input
            id="gemini-key"
            className="input min-w-0 flex-1"
            type="password"
            autoComplete="off"
            value={geminiKey}
            placeholder={data?.gemini_api_key_masked ? `Hiện tại: ${data.gemini_api_key_masked}` : 'Chưa cấu hình'}
            onChange={(e) => {
              setGeminiKey(e.target.value)
              setGeminiTest(null)
            }}
          />
          <button
            type="button"
            className={`${secondaryButtonClass} shrink-0`}
            disabled={geminiTestMutation.isPending || (!geminiKey.trim() && !data?.gemini_api_key_masked)}
            onClick={() => geminiTestMutation.mutate()}
          >
            {geminiTestMutation.isPending ? 'Đang thử...' : 'Thử key'}
          </button>
        </div>
        {geminiTest && (
          <p className={`mt-1 text-xs ${CAPCUT_STATUS_CLASS[geminiTest.status]}`}>
            {geminiTest.status === 'ok' ? '✓ ' : '✗ '}
            {geminiTest.message}
          </p>
        )}
        <span className="mt-1 block text-xs text-neutral-500">
          "Thử key" gọi Gemini 1 lần (tốn 1 request quota). Để trống ô thì thử key đang lưu.
        </span>
      </div>

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
        {isAdmin ? 'Whisper model' : 'Độ chính xác nhận diện giọng nói'}
        <select className={inputClass} value={whisperModel} onChange={(e) => setWhisperModel(e.target.value)}>
          {(data?.whisper_models ?? []).map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
              {isAdmin && ` [${m.id}]`}
            </option>
          ))}
        </select>
      </label>

      <label className="block text-sm text-neutral-300">
        Thiết bị xử lý AI
        <select className={inputClass} value={aiDevice} onChange={(e) => setAiDevice(e.target.value as AiDevice)}>
          <option value="auto">Tự động (khuyên dùng) — nhận diện giọng nói dùng card đồ hoạ nếu có, phần còn lại chạy CPU cho ổn định</option>
          <option value="cuda">Luôn dùng card đồ hoạ NVIDIA cho mọi việc (cần card nhiều bộ nhớ)</option>
          <option value="cpu">Không dùng card đồ hoạ — máy không có card NVIDIA</option>
        </select>
        <span className="mt-1 block text-xs text-neutral-500">
          {data?.gpu_available
            ? `Máy này có card ${data.gpu_name} (${Math.round(data.gpu_memory_mb / 1024)} GB).`
            : 'Không thấy card NVIDIA trên máy này — nên chọn "Không dùng card đồ hoạ".'}{' '}
          Áp dụng cho nhận diện giọng nói, giọng đọc trên máy, làm sạch video. Đổi xong cần khởi động lại app mới có tác
          dụng với phần đang chạy.
        </span>
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
          Video thoại càng dồn dập (ít khoảng nghỉ) càng dễ phải đánh đổi giữa dịch đủ nghĩa và giọng
          đọc tự nhiên — chọn "Đủ ý hơn" nếu ưu tiên giữ cốt truyện, chấp nhận lệch thời gian nhẹ ở
          những câu quá gấp (bước dựng đã tự làm chậm video/tăng tốc giọng để bù).
        </span>
      </label>

      <label className="block text-sm text-neutral-300">
        Thư mục chứa dữ liệu AI
        <input
          className={`${inputClass} mono`}
          value={modelsDir}
          placeholder={data ? `Mặc định: ${data.models_dir}` : ''}
          onChange={(e) => setModelsDir(e.target.value)}
        />
        <span className="mt-1 block text-xs text-neutral-500">
          Dữ liệu AI nặng vài GB — đặt ở ổ còn nhiều chỗ. Để trống = cạnh workspace. Đổi chỗ thì chép thư mục cũ
          sang (không thì app tự tải lại).
        </span>
      </label>

      <label className="block text-sm text-neutral-300">
        Thư mục tạm
        <input
          className={`${inputClass} mono`}
          value={tempDir}
          placeholder={data ? `Mặc định: ${data.temp_dir}` : ''}
          onChange={(e) => setTempDir(e.target.value)}
        />
        <span className="mt-1 block text-xs text-neutral-500">
          File trung gian khi xử lý video. Để trống = mặc định (tránh ổ C hay bị đầy).
        </span>
      </label>

      <details className="rounded-md border border-divider px-3 py-2 text-sm text-neutral-300">
        <summary className="cursor-pointer select-none">Nâng cao</summary>
        <div className="mt-3 space-y-4">
          <label className="block">
            Số câu tạo giọng đọc cùng lúc
            <input
              className={inputClass}
              type="number"
              min={1}
              max={16}
              value={ttsConcurrency}
              onChange={(e) => setTtsConcurrency(e.target.value)}
            />
            <span className="mt-1 block text-xs text-neutral-500">
              Mặc định 3. Mạng yếu hoặc hay bị lỗi giọng đọc thì giảm xuống 1–2.
            </span>
          </label>
          <label className="block">
            Thời gian chờ tối đa khi tách nhạc nền (phút)
            <input
              className={inputClass}
              type="number"
              min={5}
              max={240}
              value={demucsTimeoutMin}
              onChange={(e) => setDemucsTimeoutMin(e.target.value)}
            />
            <span className="mt-1 block text-xs text-neutral-500">
              Bước tách nhạc nền chạy quá mốc này thì coi như treo và huỷ. Máy yếu + video dài thì tăng lên.
            </span>
          </label>
        </div>
      </details>

      <button type="button" className={primaryButtonClass} disabled={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
        {saveMutation.isPending ? 'Đang lưu...' : 'Lưu'}
      </button>
      {saveMutation.isSuccess && <p className="text-sm text-accent-300">Đã lưu.</p>}
      {saveMutation.error && <p className="text-sm text-danger">{(saveMutation.error as Error).message}</p>}
    </div>
  )
}
