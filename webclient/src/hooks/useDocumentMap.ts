import { useQuery } from '@tanstack/react-query'
import { fetchDocumentMap } from '../data/apiClient'
import type { CDMDocumentMapResponse } from '../data/apiClient'

/**
 * Hook to fetch the document map — 33 domains plus the unmapped rail.
 *
 * The map moves when a document is uploaded or a mapping is accepted, neither
 * of which happens on this screen, so a 2-minute staleTime is generous without
 * risking a stale headline number in a steering meeting.
 */
export function useDocumentMap(orgId?: string) {
  return useQuery<CDMDocumentMapResponse>({
    queryKey: ['cdm-document-map', orgId],
    queryFn: () => fetchDocumentMap(orgId!),
    enabled: !!orgId,
    staleTime: 2 * 60 * 1000,
    refetchOnWindowFocus: false,
  })
}
