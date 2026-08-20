import { useQuery } from '@tanstack/react-query'
import { getWorkQueue, type WorkQueueResponse } from '../data/apiClient'

export function useWorkQueue(orgId?: string, assignedToMe?: boolean) {
  return useQuery<WorkQueueResponse>({
    queryKey: ['work-queue', orgId, assignedToMe ?? false],
    queryFn: () => getWorkQueue(orgId, assignedToMe),
    enabled: !!orgId,
    staleTime: 60_000,
    refetchOnWindowFocus: true,
  })
}
