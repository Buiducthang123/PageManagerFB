import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type EpisodeStageName, type StageName } from '../lib/api'

export function useJobStatus(
  projectId: string,
  stage: StageName,
  episodeId?: string,
  onSettled?: () => void,
) {
  const queryClient = useQueryClient()
  const onSettledRef = useRef(onSettled)
  onSettledRef.current = onSettled
  const queryKey = episodeId ? ['job', projectId, episodeId, stage] : ['job', projectId, stage]
  const query = useQuery({
    queryKey,
    queryFn: () =>
      episodeId
        ? api.episodeJobStatus(projectId, episodeId, stage as EpisodeStageName)
        : api.jobStatus(projectId, stage),
    refetchInterval: (q) => {
      const status = q.state.data?.status
      if (status === 'running') return 1000
      // Chưa từng chạy (status null) — vẫn cần poll chậm vì stage kế tiếp có
      // thể tự khởi động ngầm (auto_pipeline) mà không qua bấm nút ở đây, nên
      // sẽ không có invalidateQueries nào báo cho query này refetch.
      if (status == null) return 3000
      return false
    },
  })

  const prevStatus = useRef<string | null>(null)
  useEffect(() => {
    const status = query.data?.status ?? null
    const prev = prevStatus.current
    const justSettled = prev === 'running' && (status === 'done' || status === 'failed' || status === 'cancelled')
    // Làm mới project ngay khi job CHUYỂN sang "running" (không chỉ lúc xong)
    // — nếu không, badge trạng thái trên header (đọc từ project.stages, được
    // fetch riêng) cứ đứng ở "chờ" cho tới khi job xong hẳn, dù khung tiến độ
    // bên dưới (đọc trực tiếp từ job này) đã hiện đang chạy thật — gây cảm
    // giác 2 chỗ mâu thuẫn nhau dù cùng 1 job.
    if (prev !== status && (status === 'running' || justSettled)) {
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
    }
    if (justSettled) {
      onSettledRef.current?.()
    }
    prevStatus.current = status
  }, [query.data?.status, projectId, queryClient])

  // Orphaned (job chết theo server restart) không đi qua nhánh status ở
  // trên (status luôn null khi orphaned) — nếu không invalidate riêng thì
  // project.stages.*.status cũ ("running") cứ treo mãi trên UI (nút vẫn
  // hiện "Đang chạy..." dù job đã chết thật), dù backend đã tự sửa lại state
  // ngay khi phát hiện (xem route /jobs/{stage}). Chỉ cần bắn 1 lần lúc mới
  // phát hiện orphaned, không lặp lại mỗi lần poll.
  const prevOrphaned = useRef(false)
  useEffect(() => {
    const orphaned = Boolean(query.data?.orphaned)
    if (orphaned && !prevOrphaned.current) {
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
    }
    prevOrphaned.current = orphaned
  }, [query.data?.orphaned, projectId, queryClient])

  return query
}
