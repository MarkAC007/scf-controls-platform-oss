import { useEffect, useState } from 'react'
import { getEvidenceTracking } from '../data/apiClient'
import type { MemberType } from '../types'

/**
 * The evidence the server says a team (or function) is assigned to.
 *
 * The controls list pushes its team filter into the paginated query because a
 * client-side filter over a paginated list narrows the loaded page and presents
 * it as the whole list. Evidence is not paginated, so that argument does not
 * apply — but the filter is pushed to the server here anyway, so that "assigned
 * to this team" is decided in one place for both lists rather than by two
 * implementations that can drift apart.
 *
 * Returns a set of evidence TRACKING row ids, or ``null`` when no filter is
 * active or the answer has not arrived yet. Null means "do not narrow on me",
 * which is what keeps a slow or failed request from blanking the list: the
 * caller falls back to the assignment map it already holds, which carries the
 * same semantics.
 */
export function useTeamFilteredEvidence(
  organizationId: string | null | undefined,
  teamId: string | undefined,
  functionId: string | undefined,
  /**
   * Accountable owner's member type (#822 phase 2). Answered by the same
   * endpoint and combined with the team filters server-side, so it belongs
   * here rather than in a second hook that would have to intersect two sets in
   * the browser and get the "still loading" case wrong twice.
   *
   * Unlike the team filters this one has NO client-side fallback: nothing the
   * evidence list already holds knows who leads an accountable team or how
   * they are employed. So a failure that would silently un-narrow the list is
   * reported instead — see the catch below.
   */
  accountableOwnerType?: MemberType
): { trackingIds: Set<string> | null; loading: boolean; error: string | null } {
  const [trackingIds, setTrackingIds] = useState<Set<string> | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!organizationId || (!teamId && !functionId && !accountableOwnerType)) {
      setTrackingIds(null)
      setError(null)
      return
    }

    // A filter change mid-flight must not have the earlier answer land on top
    // of the later one and narrow to the wrong team.
    let current = true
    setLoading(true)
    setError(null)

    getEvidenceTracking(organizationId, {
      team_id: teamId,
      function_id: functionId,
      accountable_owner_type: accountableOwnerType,
    })
      .then(rows => {
        if (!current) return
        setTrackingIds(new Set(rows.map(row => row.id).filter((id): id is string => !!id)))
      })
      .catch((err: any) => {
        if (!current) return
        // Degrade to the client-side map rather than showing an empty list:
        // the two agree on what a team owns, so the only thing lost is the
        // server being the one to say so.
        setTrackingIds(null)
        setError(err?.message || 'Could not filter evidence by team')
      })
      .finally(() => {
        if (current) setLoading(false)
      })

    return () => {
      current = false
    }
  }, [organizationId, teamId, functionId, accountableOwnerType])

  return { trackingIds, loading, error }
}
