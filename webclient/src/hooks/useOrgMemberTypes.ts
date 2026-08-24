/**
 * useOrgMemberTypes — is this person, in THIS organisation, staff or a
 * contractor? (#822 phase 2)
 *
 * A per-organisation lookup rather than a field on the user object, because
 * ``member_type`` is a property of the membership: the same person can be
 * permanent staff at one organisation and an external contractor at another.
 * Hanging it off ``UserSimple`` would make a contractor badge follow somebody
 * between tenants, which is the exact thing the column is defined not to do.
 *
 * One request per organisation, shared by every badge on the screen. The
 * alternative — each row resolving its own author — is the N+1 that the team
 * assignment work already had to unpick once.
 *
 * Fails quiet, not closed-and-loud. An unresolved member yields ``undefined``,
 * which ``ContractorBadge`` renders as nothing. A missing label is the
 * ordinary case (everyone is internal by default), so a failed fetch costs a
 * badge, never a blocked screen or a wrong claim about somebody.
 *
 * The role in the payload is deliberately not returned. Nothing here decides
 * what anyone may do — ``useIsOrgAdmin`` answers that question and this must
 * not become a second answer to it.
 */
import { useCallback, useEffect, useState } from 'react'

import { getOrgMemberSummaries } from '../data/apiClient'
import type { MemberType } from '../types'

export interface OrgMemberTypes {
  /** ``member_type`` for a user id, or ``undefined`` while unknown. */
  memberTypeOf: (userId: string | null | undefined) => MemberType | undefined
  /** True once an answer — or a failure — has arrived. */
  loaded: boolean
}

export function useOrgMemberTypes(
  organizationId: string | null | undefined
): OrgMemberTypes {
  const [byUserId, setByUserId] = useState<Map<string, MemberType>>(new Map())
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let cancelled = false
    if (!organizationId) {
      setByUserId(new Map())
      setLoaded(false)
      return
    }
    // Switching organisations must not leave the previous tenant's labels on
    // screen against this tenant's people.
    setByUserId(new Map())
    setLoaded(false)
    getOrgMemberSummaries(organizationId)
      .then(members => {
        if (cancelled) return
        setByUserId(new Map(members.map(m => [String(m.user_id), m.member_type])))
        setLoaded(true)
      })
      .catch(err => {
        console.error('Failed to load organisation member types:', err)
        if (!cancelled) setLoaded(true)
      })
    return () => {
      cancelled = true
    }
  }, [organizationId])

  const memberTypeOf = useCallback(
    (userId: string | null | undefined): MemberType | undefined =>
      userId ? byUserId.get(String(userId)) : undefined,
    [byUserId]
  )

  return { memberTypeOf, loaded }
}

export default useOrgMemberTypes
