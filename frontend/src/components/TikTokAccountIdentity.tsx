import type { AccountStatus, TikTokAccount } from '../lib/api'

// Avatar + @username + trạng thái đăng nhập của 1 tài khoản TikTok — dùng
// chung cho trang Tài khoản và phần "Đăng bài TikTok" trong dự án tự động.

export const ACCOUNT_STATUS: Record<AccountStatus, { label: string; tag: string }> = {
  ok: { label: 'Đang đăng nhập', tag: 'tag-accent' },
  expired: { label: 'Hết đăng nhập', tag: 'tag-danger' },
  mismatch: { label: 'Sai tài khoản', tag: 'tag-danger' },
  error: { label: 'Không kiểm tra được', tag: 'tag-neutral' },
  unknown: { label: 'Chưa kiểm tra', tag: 'tag-outline' },
}

export const fmtCheckedAt = (iso: string | null) =>
  iso
    ? new Date(iso).toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })
    : 'chưa lần nào'

export default function TikTokAccountIdentity({ account }: { account: TikTokAccount }) {
  const st = ACCOUNT_STATUS[account.status]
  const initial = (account.username || account.label || '?').charAt(0).toUpperCase()
  return (
    <div className="flex min-w-0 items-center gap-3">
      {account.avatar_url ? (
        <img
          src={account.avatar_url}
          alt=""
          referrerPolicy="no-referrer"
          className="h-10 w-10 shrink-0 rounded-full border border-divider object-cover"
        />
      ) : (
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-divider text-sm text-neutral-400">
          {initial}
        </span>
      )}
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="mono truncate text-sm text-text">
            {account.username ? `@${account.username}` : 'Chưa rõ tài khoản'}
          </span>
          <span className={`tag ${st.tag}`}>{st.label}</span>
          {account.duplicate_uid && (
            <span className="tag tag-danger" title="Cùng 1 tài khoản TikTok đang đăng nhập ở profile khác">
              Trùng tài khoản
            </span>
          )}
        </div>
        <div className="truncate text-xs text-neutral-500">
          {[account.screen_name, account.label && account.label !== account.screen_name ? account.label : '']
            .filter(Boolean)
            .join(' · ') || '—'}
          {' · '}kiểm tra: {fmtCheckedAt(account.checked_at)}
        </div>
      </div>
    </div>
  )
}
