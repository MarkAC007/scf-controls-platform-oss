import { useMemo } from 'react'
import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { getRiskCatalog, getThreatCatalog } from '../data/apiClient'
import type { RiskCodesFile } from '../types'

export const EMPTY_CODES_FILE: RiskCodesFile = { categories: {}, codes: {}, catalog_version: null }

export const riskCatalogKey = ['catalog', 'risks'] as const
export const threatCatalogKey = ['catalog', 'threats'] as const

/**
 * The SCF Risk Catalog as the server holds it (GET /api/catalog/risks).
 *
 * `catalog` is always an object, EMPTY_CODES_FILE until the query settles, so
 * callers can render; anything that must not act on a partial catalogue (the
 * register's lazy-create of assessment rows) waits on `isSuccess`. `isEmpty`
 * is the loaded-but-nothing-there case: an install whose catalogue predates
 * the table, pointing the operator at `refresh-risk-catalog`.
 */
export function useRiskCatalog() {
  const query = useQuery<RiskCodesFile>({
    queryKey: riskCatalogKey,
    queryFn: getRiskCatalog,
    staleTime: 5 * 60 * 1000,
  })
  return useCatalogView(query)
}

/** The SCF Threat Catalog (GET /api/catalog/threats), same contract. */
export function useThreatCatalog() {
  const query = useQuery<RiskCodesFile>({
    queryKey: threatCatalogKey,
    queryFn: getThreatCatalog,
    staleTime: 5 * 60 * 1000,
  })
  return useCatalogView(query)
}

function useCatalogView(query: UseQueryResult<RiskCodesFile>) {
  const catalog = query.data ?? EMPTY_CODES_FILE
  const oldCodeMap = useMemo(() => renumberingOf(catalog), [catalog])
  return {
    ...query,
    catalog,
    catalogVersion: catalog.catalog_version ?? null,
    isEmpty: query.isSuccess && Object.keys(catalog.codes).length === 0,
    oldCodeMap,
  }
}

/** old code → the code the publisher renumbered it to. */
export function renumberingOf(catalog: RiskCodesFile): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [code, info] of Object.entries(catalog.codes)) {
    if (info.old_code && info.old_code !== code) out[info.old_code] = code
  }
  return out
}

export interface CatalogCodeNote {
  kind: 'missing' | 'renumbered'
  label: string
  /** For 'renumbered': the code that now carries this risk. */
  successor?: string
}

/**
 * What the register should say about an assessment row's code against the
 * catalogue, read-only (ISC-47): a row whose code the current catalogue does
 * not define ("not in 2026.3"), or whose code the publisher renumbered
 * ("renumbered → R-AI-5"). Custom risks are never badged, and nothing is said
 * while the catalogue is unknown or empty, since then every code would be.
 */
export function catalogCodeNote(code: string, catalog: RiskCodesFile): CatalogCodeNote | null {
  if (code.startsWith('R-ORG-')) return null
  if (Object.keys(catalog.codes).length === 0) return null
  // A code the catalogue defines is current, even when the publisher also
  // reused it: in 2026.3 R-AM-3 is both a live risk and the old code of
  // R-AI-5, and a row carrying it cannot be told apart from the new meaning.
  if (catalog.codes[code]) return null
  const successor = renumberingOf(catalog)[code]
  if (successor) return { kind: 'renumbered', label: `renumbered → ${successor}`, successor }
  return {
    kind: 'missing',
    label: catalog.catalog_version ? `not in ${catalog.catalog_version}` : 'not in catalogue',
  }
}
