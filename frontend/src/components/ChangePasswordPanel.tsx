import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api } from '../lib/api'
import { useLicense } from '../lib/license'
import { inputClass, secondaryButtonClass } from '../lib/ui'

/** Đổi mật khẩu tài khoản đăng nhập (kế hoạch 15.3) — mật khẩu tạm admin gửi
 * qua Zalo nên đổi ngay. Chỉ hiện khi app đang bật đăng nhập. */
export default function ChangePasswordPanel() {
  const { license } = useLicense()
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const mutation = useMutation({
    mutationFn: () => api.changePassword(password),
    onSuccess: () => {
      setPassword('')
      setConfirm('')
    },
  })
  if (!license || license.mode === 'disabled' || !license.email) return null
  const mismatch = confirm.length > 0 && password !== confirm
  const tooShort = password.length > 0 && password.length < 8

  return (
    <details className="rounded-md border border-divider px-3 py-2 text-sm text-neutral-300">
      <summary className="cursor-pointer select-none">
        Tài khoản: <span className="text-text">{license.email}</span> — đổi mật khẩu
      </summary>
      <div className="mt-3 space-y-3">
        <label className="block">
          Mật khẩu mới
          <input
            className={inputClass}
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <label className="block">
          Nhập lại mật khẩu mới
          <input
            className={inputClass}
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </label>
        {tooShort && <p className="text-xs text-danger">Tối thiểu 8 ký tự</p>}
        {mismatch && <p className="text-xs text-danger">Hai lần nhập không khớp</p>}
        <button
          type="button"
          className={secondaryButtonClass}
          disabled={mutation.isPending || password.length < 8 || password !== confirm}
          onClick={() => mutation.mutate()}
        >
          {mutation.isPending ? 'Đang đổi...' : 'Đổi mật khẩu'}
        </button>
        {mutation.isSuccess && <p className="text-xs text-accent-300">Đã đổi mật khẩu.</p>}
        {mutation.error && <p className="text-xs text-danger">{(mutation.error as Error).message}</p>}
      </div>
    </details>
  )
}
