import { useQuery } from '@tanstack/react-query'
import { getCustomRiskControlLinks, type CustomRiskControlLink } from '../data/apiClient'
import { listDocuments, type DocumentSummary } from '../data/documentsApi'

export const customRiskLinksKey = (orgId: string, riskCode: string) =>
  ['custom-risk-controls', orgId, riskCode] as const

/**
 * A custom risk's control links with the standard's document on each (PIG R7).
 * controls-for-risk lists scoped controls only and carries no document.
 */
export function useCustomRiskLinks(orgId: string | undefined, riskCode: string, enabled = true) {
  return useQuery<CustomRiskControlLink[]>({
    queryKey: customRiskLinksKey(orgId ?? '', riskCode),
    queryFn: () => getCustomRiskControlLinks(riskCode, orgId!),
    enabled: Boolean(orgId) && enabled,
  })
}

/**
 * Every document in the organisation, for a custom risk's supporting
 * document. Keyed under ['documents', orgId] so the document pages'
 * invalidations refresh it too, as useProcedureDocuments is.
 */
export function useOrgDocuments(orgId: string | undefined, enabled = true) {
  return useQuery<DocumentSummary[]>({
    queryKey: ['documents', orgId, 'all'],
    queryFn: () => listDocuments(orgId!),
    enabled: Boolean(orgId) && enabled,
    staleTime: 60 * 1000,
  })
}
