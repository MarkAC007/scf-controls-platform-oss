import { useQueries, useQuery, type QueryClient } from '@tanstack/react-query'
import {
  getCustomRiskControlLinks,
  getCustomRiskDefinitions,
  getRisksForControl,
  listCompensatingControls,
} from '../data/apiClient'
import type { CompensatingControl, CompensatingControlFilters, RiskAssessment } from '../types'
import { customRiskLinksKey } from './useCustomRiskLinks'
import { useAssessedRisks } from './useDeficiencies'

/** Every compensating-control query for an organisation sits under this key. */
export const compensatingControlsRoot = (orgId: string) => ['compensating-controls', orgId] as const

/**
 * The organisation's compensating controls (PIG R9), filtered on the server.
 *
 * Each row's material, risk_acceptance_state and justified are computed by
 * the server when it is read, and they move on writes that know nothing of
 * this key: a materiality override, a risk accepted, approved, withdrawn or
 * expiring, a risk linked or unlinked. So the list is never fresh: every
 * mount and every return to the tab refetches it.
 */
export function useCompensatingControls(orgId: string | undefined, filters: CompensatingControlFilters = {}) {
  return useQuery<CompensatingControl[]>({
    queryKey: [...compensatingControlsRoot(orgId ?? ''), 'list', filters],
    queryFn: () => listCompensatingControls(orgId!, filters),
    enabled: Boolean(orgId),
    staleTime: 0,
  })
}

export function invalidateCompensatingControls(client: QueryClient, orgId: string) {
  return client.invalidateQueries({ queryKey: compensatingControlsRoot(orgId) })
}

export interface LinkedRisks {
  /** Assessed risks linked to the control, by code; empty until everything has loaded. */
  risks: RiskAssessment[]
  /** Every risk code linked to the control, assessed or not; null until everything has loaded. */
  linkedCodes: Set<string> | null
  isLoading: boolean
  /** The first failure, when any read failed: the list may then be short. */
  error: unknown
}

/**
 * The risks a compensating control on this control may name: the risks
 * linked to it (the catalogue's mapping, and the organisation's custom risks
 * linked to it) that the organisation has assessed, as the server's check
 * reads them. The catalogue's come from risks-for-control; no read lists a
 * control's custom risk links, so each custom risk's links are read (the
 * same queries the risk pages use, so their writes refresh these).
 *
 * The catalogue and custom-risk reads are never fresh: a risk linked on
 * another page moments ago must be offered on the next opening.
 */
export function useControlLinkedRisks(orgId: string | undefined, scfId: string, enabled = true): LinkedRisks {
  const on = Boolean(orgId) && enabled
  const catalogue = useQuery({
    queryKey: ['risks-for-control', orgId, scfId],
    queryFn: () =>
      getRisksForControl(scfId, orgId).catch((err: unknown) => {
        // A control the catalogue does not know has no catalogue risks. apiFetch
        // carries no status, so its 404 is known by the detail text alone.
        if (err instanceof Error && /not found in SCF catalog/.test(err.message)) {
          return { scf_id: scfId, catalog_risk_codes: [], assessments: [] }
        }
        throw err
      }),
    enabled: on,
    staleTime: 0,
  })
  const customRisks = useQuery({
    queryKey: ['custom-risks', orgId],
    queryFn: () => getCustomRiskDefinitions(orgId),
    enabled: on,
    staleTime: 0,
  })
  const links = useQueries({
    queries: (customRisks.data ?? []).map((risk) => ({
      queryKey: customRiskLinksKey(orgId ?? '', risk.risk_code),
      queryFn: () => getCustomRiskControlLinks(risk.risk_code, orgId!),
      enabled: on,
    })),
  })
  const assessed = useAssessedRisks(orgId, enabled)

  const all = [catalogue, customRisks, assessed, ...links]
  const isLoading = on && all.some((q) => q.isLoading)
  const failed = all.find((q) => q.isError)
  const error = failed?.error ?? null
  if (isLoading || !assessed.data) return { risks: [], linkedCodes: null, isLoading, error }

  const target = scfId.toUpperCase()
  const linked = new Set(catalogue.data?.catalog_risk_codes ?? [])
  ;(customRisks.data ?? []).forEach((risk, i) => {
    if (links[i]?.data?.some((link) => link.scf_id.toUpperCase() === target)) linked.add(risk.risk_code)
  })
  const risks = assessed.data
    .filter((a) => linked.has(a.risk_code))
    .sort((a, b) => a.risk_code.localeCompare(b.risk_code))
  return { risks, linkedCodes: linked, isLoading: false, error }
}
