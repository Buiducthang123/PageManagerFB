import { useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { secondaryButtonClass } from '../lib/ui'

interface Props {
  enabled: boolean
  onToggle: (enabled: boolean) => void
  toggling: boolean
}

// Crawl Douyin bằng Chrome thật (1 profile dùng chung cho mọi dự án tự động):
// bật/tắt cho dự án này, và mở cửa sổ để đăng nhập Douyin 1 lần.
export default function DouyinBrowserPanel({ enabled, onToggle, toggling }: Props) {
  const queryClient = useQueryClient()

  const statusQuery = useQuery({ queryKey: ['douyin-browser-status'], queryFn: api.douyinBrowserStatus })
  const loginJobQuery = useQuery({
    queryKey: ['douyin-browser-login-job'],
    queryFn: api.douyinBrowserLoginJob,
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 2000 : false),
  })

  const loginMutation = useMutation({
    mutationFn: api.douyinBrowserLogin,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['douyin-browser-login-job'] }),
  })

  const loginStatus = loginJobQuery.data?.status
  const busyLogin = loginStatus === 'running'
  useEffect(() => {
    if (loginStatus && loginStatus !== 'running') {
      queryClient.invalidateQueries({ queryKey: ['douyin-browser-status'] })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loginStatus])

  const status = statusQuery.data
  const checkedAt = status?.checked_at ? new Date(status.checked_at).toLocaleString('vi-VN') : null

  return (
    <section className="card mb-6 space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-sm text-neutral-300">
          <input type="checkbox" checked={enabled} disabled={toggling} onChange={(e) => onToggle(e.target.checked)} />
          Crawl Douyin bằng Chrome thật
        </label>
        <span className={`tag ${status?.logged_in ? 'tag-accent' : 'tag-neutral'}`}>
          {status?.logged_in ? 'Đã đăng nhập Douyin' : 'Chưa đăng nhập (chạy như khách)'}
        </span>
        {checkedAt && <span className="text-xs text-neutral-500">kiểm tra lúc {checkedAt}</span>}
        <button
          type="button"
          className={`${secondaryButtonClass} ml-auto`}
          disabled={busyLogin || loginMutation.isPending}
          onClick={() => loginMutation.mutate()}
        >
          {busyLogin ? 'Đang chờ đăng nhập...' : 'Mở Chrome để đăng nhập Douyin'}
        </button>
      </div>
      <p className="text-xs text-neutral-500">
        Mở trang kênh trong Chrome, cuộn như người xem và lấy dữ liệu từ chính các request trang Douyin tự gọi — không cần
        douyin-downloader. Cửa sổ Chrome tự bật lên mỗi lần crawl, đừng đóng nó khi đang chạy. Chạy như khách vẫn lấy được
        video; đăng nhập (quét QR bằng app Douyin, xong thì đóng cửa sổ) giúp ít gặp captcha hơn. Không dùng lại được phiên
        từ profile Chrome thường của bạn — Chrome mã hoá cookie gắn với từng thư mục profile.
      </p>
      {loginMutation.error && <p className="text-sm text-danger">{(loginMutation.error as Error).message}</p>}
      {loginJobQuery.data?.registered && loginJobQuery.data.current_label && (
        <p className="text-xs text-neutral-400">{loginJobQuery.data.current_label}</p>
      )}
    </section>
  )
}
