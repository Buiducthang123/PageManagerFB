import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'

export function useJobStatus(projectId: string, stage: 'ingest' | 'transcribe' | 'translate' | 'tts' | 'assemble') {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['job', projectId, stage],
    queryFn: () => api.jobStatus(projectId, stage),
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
    if (prevStatus.current === 'running' && (status === 'done' || status === 'failed' || status === 'cancelled')) {
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
    }
    prevStatus.current = status
  }, [query.data?.status, projectId, queryClient])

  return query
}
