// SPDX-License-Identifier: AGPL-3.0-only
import { useMemo } from 'react'
import { useQuery, type QueryClient } from '@tanstack/react-query'
import {
  getCustomRiskDefinitions,
  getDeficiencySummary,
  getOrgMembers,
  getRiskAssessments,
  getRiskCatalog,
  listDeficiencies,
} from '../data/apiClient'
import type {
  CustomRiskDefinition,
  Deficiency,
  DeficiencyFilters,
  DeficiencySummary,
  RiskAssessment,
  RiskCodesFile,
  UserSimple,
} from '../types'
import { JOURNEY_QUERY_ROOT } from './useJourney'
import { catalogCodeNote, renumberingOf, riskCatalogKey } from './useRiskCatalog'

/** Every deficiency query for an organisation sits under this key. */
export const deficienciesRoot = (orgId: string) => ['deficiencies', orgId] as const

/**
 * Every capability test query (PIG R11). Here, not with the tests' hooks,
 * because a test's finding counts are counts of its deficiencies: cancelling,
 * completing or reopening one moves them.
 */
export const capabilityTestsRoot = (orgId: string) => ['capability-tests', orgId] as const

/**
 * The server raises deficiencies itself (a control set at risk, evidence
 * rejected or insufficient) and materiality moves the counts, on writes that
 * know nothing of these keys. So these queries are never fresh: every mount
 * and every return to the tab refetches, rather than trusting the app's
 * five-minute stale time. Both are single cheap reads.
 */
const ALWAYS_REFETCH = { staleTime: 0 } as const

/** The organisation's deficiencies, filtered on the server (PIG R8). */
export function useDeficiencies(orgId: string | undefined, filters: DeficiencyFilters = {}) {
  return useQuery<Deficiency[]>({
    queryKey: [...deficienciesRoot(orgId ?? ''), 'list', filters],
    queryFn: () => listDeficiencies(orgId!, filters),
    enabled: Boolean(orgId),
    ...ALWAYS_REFETCH,
  })
}

/** Open deficiencies per control and per domain, and the material count. */
export function useDeficiencySummary(orgId: string | undefined) {
  return useQuery<DeficiencySummary>({
    queryKey: [...deficienciesRoot(orgId ?? ''), 'summary'],
    queryFn: () => getDeficiencySummary(orgId!),
    enabled: Boolean(orgId),
    ...ALWAYS_REFETCH,
  })
}

/**
 * After a deficiency is raised or changed: the lists, the counts, the
 * capability tests whose findings they are, and the journey, whose gates
 * count open and material deficiencies and recent capability tests. Also
 * after a capability test is recorded or corrected, since its findings are
 * deficiencies.
 */
export function invalidateDeficiencies(client: QueryClient, orgId: string) {
  return Promise.all([
    client.invalidateQueries({ queryKey: deficienciesRoot(orgId) }),
    client.invalidateQueries({ queryKey: capabilityTestsRoot(orgId) }),
    client.invalidateQueries({ queryKey: [JOURNEY_QUERY_ROOT, orgId] }),
  ])
}

/** The organisation's members, for an owner picker. */
export function useOrgMembersQuery(orgId: string | undefined, enabled = true) {
  return useQuery<UserSimple[]>({
    queryKey: ['org-members', orgId],
    queryFn: () => getOrgMembers(orgId!),
    enabled: Boolean(orgId) && enabled,
    staleTime: 60_000,
  })
}

/**
 * The risks the organisation has assessed: the only ones a deficiency may be
 * raised against. Same key and fetch as the risk register's link check.
 */
export function useAssessedRisks(orgId: string | undefined, enabled = true) {
  return useQuery<RiskAssessment[]>({
    queryKey: ['risk-assessments', orgId],
    queryFn: () => getRiskAssessments(undefined, orgId),
    enabled: Boolean(orgId) && enabled,
    staleTime: 60_000,
  })
}

/**
 * How to label a risk in a list of the assessed risks: "code · name".
 *
 * The name is not on the assessment: a catalogue risk's is in the SCF Risk
 * Catalog, a custom risk's on its definition. Both are read with the keys the
 * risk register uses, so an open register has usually loaded them already. A
 * code the catalogue no longer defines says so, in the register's own words
 * ("not in 2026.3"). A name is a help, never a gate: while either source
 * loads, or when one cannot be read, the label is the code alone.
 */
export function useRiskOptionLabel(orgId: string | undefined, enabled = true): (riskCode: string) => string {
  const catalogue = useQuery<RiskCodesFile>({
    queryKey: riskCatalogKey,
    queryFn: () => getRiskCatalog(),
    enabled,
    staleTime: 5 * 60 * 1000,
  })
  const custom = useQuery<CustomRiskDefinition[]>({
    queryKey: ['custom-risks', orgId],
    queryFn: () => getCustomRiskDefinitions(orgId),
    enabled: Boolean(orgId) && enabled,
    staleTime: 60_000,
  })
  return useMemo(() => {
    const catalog = catalogue.data
    const names: Record<string, string> = {}
    for (const [code, info] of Object.entries(catalog?.codes ?? {})) {
      if (info.title) names[code] = info.title
    }
    // An assessment made before the publisher renumbered a risk still carries the old code.
    if (catalog) {
      for (const [oldCode, code] of Object.entries(renumberingOf(catalog))) {
        if (!(oldCode in names) && names[code]) names[oldCode] = names[code]
      }
    }
    for (const def of custom.data ?? []) {
      if (def.title) names[def.risk_code] = def.title
    }
    return (riskCode: string) => {
      const name = names[riskCode]?.trim()
      if (name) return `${riskCode} · ${name}`
      const note = catalog ? catalogCodeNote(riskCode, catalog) : null
      return note?.kind === 'missing' ? `${riskCode} · ${note.label}` : riskCode
    }
  }, [catalogue.data, custom.data])
}

const pad = (n: number) => String(n).padStart(2, '0')

/** Today as YYYY-MM-DD in the browser's calendar. */
export function localIsoDate(d: Date = new Date()): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export const isOpenDeficiency = (d: Pick<Deficiency, 'status'>) =>
  d.status === 'open' || d.status === 'in_progress'

/** Open and past its due date. Due today is not overdue. */
export function isOverdue(d: Pick<Deficiency, 'status' | 'due_date'>, today: string = localIsoDate()): boolean {
  return isOpenDeficiency(d) && !!d.due_date && d.due_date < today
}
