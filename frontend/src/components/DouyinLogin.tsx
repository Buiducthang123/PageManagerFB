import { useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { secondaryButtonClass } from '../lib/ui'

/** Trạng thái + nút đăng nhập Douyin bằng Chrome thật (1 profile dùng chung cho
 * cả app: crawl dự án tự động và tầng dự phòng khi tải video từ link). */
export function useDouyinLogin() {
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
  useEffect(() => {
    if (loginStatus && loginStatus !== 'running') {
      queryClient.invalidateQueries({ queryKey: ['douyin-browser-status'] })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loginStatus])

  const status = statusQuery.data
  return {
    status,
    checkedAt: status?.checked_at ? new Date(status.checked_at).toLocaleString('vi-VN') : null,
    busy: loginStatus === 'running',
    loginMutation,
    jobLabel: loginJobQuery.data?.registered ? loginJobQuery.data.current_label : '',
  }
}

export function DouyinLoginButton({ login }: { login: ReturnType<typeof useDouyinLogin> }) {
  return (
    <button
      type="button"
      className={secondaryButtonClass}
      disabled={login.busy || login.loginMutation.isPending}
      onClick={() => login.loginMutation.mutate()}
    >
      {login.busy ? 'Đang chờ đăng nhập...' : 'Mở Chrome để đăng nhập Douyin'}
    </button>
  )
}

/** Khung ở trang Cài đặt. */
export default function DouyinLoginCard() {
  const login = useDouyinLogin()
  return (
    <section className="space-y-2 rounded-md border border-divider px-3 py-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-text">Đăng nhập Douyin</span>
        <span className={`tag ${login.status?.logged_in ? 'tag-accent' : 'tag-neutral'}`}>
          {login.status?.logged_in ? 'Đã đăng nhập' : 'Chưa đăng nhập'}
        </span>
        {login.checkedAt && <span className="text-xs text-neutral-500">kiểm tra lúc {login.checkedAt}</span>}
        <span className="ml-auto">
          <DouyinLoginButton login={login} />
        </span>
      </div>
      <p className="text-xs text-neutral-500">
        App tự tải video Douyin không cần đăng nhập. Đăng nhập giúp tải được cả khi cách thường bị lỗi, và ít gặp captcha
        hơn. Bấm nút, quét mã QR bằng app Douyin trong cửa sổ Chrome hiện ra, xong thì đóng cửa sổ. Cần cài Google Chrome.
      </p>
      {login.loginMutation.error && <p className="text-xs text-danger">{(login.loginMutation.error as Error).message}</p>}
      {login.jobLabel && <p className="text-xs text-neutral-400">{login.jobLabel}</p>}
    </section>
  )
}
