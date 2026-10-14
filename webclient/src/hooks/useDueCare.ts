import { useQuery, type QueryClient } from '@tanstack/react-query'
import { listControlValidations, listPostureSnapshots } from '../data/apiClient'
import type { ControlValidationFilters, ControlValidationList, PostureSnapshot } from '../types'
import { JOURNEY_QUERY_ROOT } from './useJourney'

/** Every validation-schedule query for an organisation sits under this key. */
export const controlValidationsRoot = (orgId: string) => ['control-validations', orgId] as const

/** Every posture-snapshot query for an organisation sits under this key. */
export const postureSnapshotsRoot = (orgId: string) => ['posture-snapshots', orgId] as const

/**
 * The validation schedule (PIG R10), filtered on the server.
 *
 * A control's state is computed against today's date when it is read, and it
 * moves on writes that know nothing of this key: a cadence override, a
 * control scoped in or out. So it is never fresh: every mount and every
 * return to the tab refetches.
 */
export function useControlValidations(orgId: string | undefined, filters: ControlValidationFilters = {}) {
  return useQuery<ControlValidationList>({
    queryKey: [...controlValidationsRoot(orgId ?? ''), 'list', filters],
    queryFn: () => listControlValidations(orgId!, filters),
    enabled: Boolean(orgId),
    staleTime: 0,
  })
}

/** After a validation is recorded: the schedule, and the journey, whose gate counts validations. */
export function invalidateControlValidations(client: QueryClient, orgId: string) {
  return Promise.all([
    client.invalidateQueries({ queryKey: controlValidationsRoot(orgId) }),
    client.invalidateQueries({ queryKey: [JOURNEY_QUERY_ROOT, orgId] }),
  ])
}

/**
 * The organisation's monthly posture snapshots, newest first. The schedule
 * takes one on the first of the month without anyone here knowing, so the
 * list is never fresh either.
 */
export function usePostureSnapshots(orgId: string | undefined) {
  return useQuery<PostureSnapshot[]>({
    queryKey: [...postureSnapshotsRoot(orgId ?? ''), 'list'],
    queryFn: () => listPostureSnapshots(orgId!),
    enabled: Boolean(orgId),
    staleTime: 0,
  })
}

export function invalidatePostureSnapshots(client: QueryClient, orgId: string) {
  return client.invalidateQueries({ queryKey: postureSnapshotsRoot(orgId) })
}
