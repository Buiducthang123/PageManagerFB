import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api, type QueueItem } from '../lib/api'
import { inputClass } from '../lib/ui'

// Xem / sửa / viết lại caption đăng TikTok của 1 video trong dự án tự động.
// Caption lưu vào video — lần đăng tới dùng đúng nội dung này. Chưa có caption
// thì app tự viết lúc đăng (tiêu đề thu hút + mô tả + câu hỏi + hashtag).
export default function CaptionDialog({
  socialId,
  item,
  onClose,
  onSaved,
}: {
  socialId: string
  item: QueueItem
  onClose: () => void
  onSaved: () => void
}) {
  const [text, setText] = useState(item.caption_vi ?? '')

  const generateMutation = useMutation({
    mutationFn: () => api.generateSocialCaption(socialId, item.aweme_id),
    onSuccess: (res) => {
      setText(res.caption)
      onSaved()
    },
  })
  const saveMutation = useMutation({
    mutationFn: () => api.saveSocialCaption(socialId, item.aweme_id, text),
    onSuccess: () => {
      onSaved()
      onClose()
    },
  })

  const busy = generateMutation.isPending || saveMutation.isPending
  const tooLong = text.length > 2200

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div className="dialog max-w-2xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="dialog-title">Caption đăng bài — {item.title_vi || item.title || item.aweme_id}</h3>
        <div className="dialog-body space-y-2">
          {!text && !generateMutation.isPending && (
            <p className="text-xs text-neutral-400">
              Chưa có caption. Bấm "Viết caption" để app viết từ nội dung video, hoặc để trống — app sẽ tự viết lúc đăng.
            </p>
          )}
          <textarea
            className={`${inputClass} min-h-72 w-full font-sans text-sm leading-relaxed`}
            value={text}
            disabled={busy}
            placeholder="Tiêu đề thu hút 🔥&#10;&#10;Mô tả nội dung...&#10;&#10;Câu hỏi cuối bài?&#10;&#10;#Hashtag #VietLien #KhongDau"
            onChange={(e) => setText(e.target.value)}
          />
          <div className="flex items-center justify-between text-xs text-neutral-500">
            <span>Hashtag nên ngắn, viết liền không dấu (vd #MucVayLon). Hashtag cố định của dự án được tự thêm khi viết.</span>
            <span className={tooLong ? 'text-danger' : ''}>{text.length}/2200</span>
          </div>
          {generateMutation.isPending && <p className="text-xs text-neutral-400">Đang viết caption từ nội dung video...</p>}
          {generateMutation.error && <p className="text-xs text-danger">{(generateMutation.error as Error).message}</p>}
          {saveMutation.error && <p className="text-xs text-danger">{(saveMutation.error as Error).message}</p>}
        </div>
        <div className="dialog-actions">
          <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => generateMutation.mutate()}>
            {text ? 'Viết lại' : 'Viết caption'}
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
            Đóng
          </button>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={busy || tooLong}
            onClick={() => saveMutation.mutate()}
          >
            Lưu caption
          </button>
        </div>
      </div>
    </div>
  )
}
