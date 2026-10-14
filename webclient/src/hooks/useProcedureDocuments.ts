import { useQuery } from '@tanstack/react-query'
import { listDocuments, type DocumentSummary } from '../data/documentsApi'

/**
 * The organisation's procedure documents, for the control's procedure link
 * (PIG R6). Keyed under ['documents', orgId] so the document pages'
 * invalidations — a transition, an edit, a generation — refresh it too.
 */
export function useProcedureDocuments(orgId: string, enabled = true) {
  return useQuery<DocumentSummary[]>({
    queryKey: ['documents', orgId, 'procedure'],
    queryFn: () => listDocuments(orgId, { documentType: 'procedure' }),
    enabled: Boolean(orgId) && enabled,
    staleTime: 60 * 1000,
  })
}
