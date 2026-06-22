import { useQuery } from '@tanstack/react-query'
import { getWorkQueue, type WorkQueueResponse } from '../data/apiClient'

export function useWorkQueue(orgId?: string) {
  return useQuery<WorkQueueResponse>({
    queryKey: ['work-queue', orgId],
    queryFn: () => getWorkQueue(orgId),
    enabled: !!orgId,
    staleTime: 60_000,
    refetchOnWindowFocus: true,
  })
}
