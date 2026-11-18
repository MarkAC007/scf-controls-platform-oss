// SPDX-License-Identifier: AGPL-3.0-only
import { keepPreviousData, useQuery, type QueryClient } from '@tanstack/react-query'
import { listTeams } from '../data/apiClient'
import {
  getIsmsMetrics,
  getMetricDetail,
  getMetricWording,
  listIsmsMeasures,
  listSecurityObjectives,
  type MeasureOut,
  type MetricDetail,
  type ObjectiveList,
  type Wording,
  type WordingRequest,
} from '../data/securityObjectivesApi'
import type { Team } from '../types'

/** Every security objective and ISMS metric query for an organisation sits under this key. */
export const securityObjectivesRoot = (orgId: string) => ['security-objectives', orgId] as const

/** Values are worked out on the server on every read, so no list is ever fresh. */
export function useSecurityObjectives(orgId: string, includeArchived = false) {
  return useQuery<ObjectiveList>({
    queryKey: [...securityObjectivesRoot(orgId), 'list', { includeArchived }],
    queryFn: () => listSecurityObjectives(orgId, includeArchived),
    enabled: Boolean(orgId),
    staleTime: 0,
  })
}

/** The dashboard: unarchived objectives with their readings. */
export function useIsmsMetrics(orgId: string) {
  return useQuery<ObjectiveList>({
    queryKey: [...securityObjectivesRoot(orgId), 'metrics'],
    queryFn: () => getIsmsMetrics(orgId),
    enabled: Boolean(orgId),
    staleTime: 0,
  })
}

/** One objective's metric detail: trend, breakdown and log. Under the root key, so every write refreshes it. */
export function useMetricDetail(orgId: string, objectiveId: string) {
  return useQuery<MetricDetail>({
    queryKey: [...securityObjectivesRoot(orgId), 'detail', objectiveId],
    queryFn: () => getMetricDetail(orgId, objectiveId),
    enabled: Boolean(orgId) && Boolean(objectiveId),
    staleTime: 0,
  })
}

/** The measure library, best word matches for `text` first, each with today's value. */
export function useIsmsMeasures(orgId: string, text: string, enabled = true) {
  return useQuery<MeasureOut[]>({
    queryKey: [...securityObjectivesRoot(orgId), 'measures', text],
    queryFn: () => listIsmsMeasures(orgId, text),
    enabled: Boolean(orgId) && enabled,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  })
}

/** Generated wording for a metric choice. Pure text, so never stale. */
export function useMetricWording(orgId: string, request: WordingRequest | null) {
  return useQuery<Wording>({
    queryKey: [...securityObjectivesRoot(orgId), 'wording', request],
    queryFn: () => getMetricWording(orgId, request!),
    enabled: Boolean(orgId) && request !== null,
    placeholderData: keepPreviousData,
    staleTime: Infinity,
  })
}

/** The organisation's teams. Same key and fetch as the engagement review panel, so the cache is shared. */
export function useTeamsQuery(orgId: string, enabled = true) {
  return useQuery<Team[]>({
    queryKey: ['teams', orgId],
    queryFn: () => listTeams(orgId),
    enabled: Boolean(orgId) && enabled,
    staleTime: 60_000,
  })
}

/**
 * After any objective write: the register and the dashboard, the ISMS profile
 * (its legacy `objectives` list is built from the table) and the documents
 * list (the regeneration fingerprint moves when an objective is edited).
 * The only invalidation any component makes.
 */
export function invalidateSecurityObjectives(client: QueryClient, orgId: string) {
  return Promise.all([
    client.invalidateQueries({ queryKey: securityObjectivesRoot(orgId) }),
    client.invalidateQueries({ queryKey: ['isms-profile', orgId] }),
    client.invalidateQueries({ queryKey: ['documents', orgId] }),
  ])
}
