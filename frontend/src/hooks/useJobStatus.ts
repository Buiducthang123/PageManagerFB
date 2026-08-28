import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'

export function useJobStatus(projectId: string, stage: 'transcribe' | 'translate' | 'tts' | 'assemble') {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['job', projectId, stage],
    queryFn: () => api.jobStatus(projectId, stage),
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 1000 : false),
  })

  const prevStatus = useRef<string | null>(null)
  useEffect(() => {
    const status = query.data?.status ?? null
    if (prevStatus.current === 'running' && (status === 'done' || status === 'failed')) {
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
    }
    prevStatus.current = status
  }, [query.data?.status, projectId, queryClient])

  return query
}
