import { DouyinLoginButton, useDouyinLogin } from './DouyinLogin'

interface Props {
  enabled: boolean
  onToggle: (enabled: boolean) => void
  toggling: boolean
}

// Crawl Douyin bằng Chrome thật (1 profile dùng chung cho mọi dự án tự động):
// bật/tắt cho dự án này, và mở cửa sổ để đăng nhập Douyin 1 lần.
export default function DouyinBrowserPanel({ enabled, onToggle, toggling }: Props) {
  const login = useDouyinLogin()
  const { status, checkedAt } = login

  return (
    <section className="card mb-6 space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-sm text-neutral-300">
          <input type="checkbox" checked={enabled} disabled={toggling} onChange={(e) => onToggle(e.target.checked)} />
          Quét kênh Douyin bằng Chrome
        </label>
        <span className={`tag ${status?.logged_in ? 'tag-accent' : 'tag-neutral'}`}>
          {status?.logged_in ? 'Đã đăng nhập Douyin' : 'Chưa đăng nhập (chạy như khách)'}
        </span>
        {checkedAt && <span className="text-xs text-neutral-500">kiểm tra lúc {checkedAt}</span>}
        <span className="ml-auto">
          <DouyinLoginButton login={login} />
        </span>
      </div>
      <p className="text-xs text-neutral-500">
        Mở trang kênh trong Chrome và cuộn như người xem để lấy danh sách video. Cửa sổ Chrome tự bật lên mỗi lần quét,
        đừng đóng nó khi đang chạy. Chưa đăng nhập vẫn quét được; đăng nhập (quét QR bằng app Douyin, xong thì đóng cửa sổ)
        giúp ít gặp captcha hơn. Phiên đăng nhập ở Chrome thường của bạn không dùng lại được ở đây — cần đăng nhập riêng 1
        lần.
      </p>
      {login.loginMutation.error && <p className="text-sm text-danger">{(login.loginMutation.error as Error).message}</p>}
      {login.jobLabel && <p className="text-xs text-neutral-400">{login.jobLabel}</p>}
    </section>
  )
}
