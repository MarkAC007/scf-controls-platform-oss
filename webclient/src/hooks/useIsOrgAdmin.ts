/**
 * useIsOrgAdmin — is the person looking at this screen an admin of this
 * organisation?
 *
 * Used to decide whether to render the team-assignment controls at all.
 * That is a courtesy, not a boundary: the API refuses a non-admin's write
 * whatever this returns, and nothing here should ever be the only thing
 * standing between a viewer and a mutation.
 *
 * Fails closed. A members call that errors, a profile that has not landed
 * yet, or a membership row that cannot be matched all yield ``false`` — the
 * cost is a hidden button and a "you need to be an admin" note, which is
 * recoverable; the cost of failing open is a control that 403s on click.
 *
 * The role read here is ``organization_members.role`` and it has nothing to
 * do with teams. Team membership grants no permissions (Issue #822); it never
 * appears in this file and must not start to.
 */
import { useEffect, useState } from 'react'

import { useAuth } from '../contexts/AuthContext'
import { getOrgMemberships } from '../data/apiClient'

export function useIsOrgAdmin(organizationId: string | null | undefined): boolean {
  const { user, isPlatformAdmin, isAuthenticated, authReady } = useAuth()
  const [isOrgAdmin, setIsOrgAdmin] = useState(false)

  // API-key mode has no user profile at all (AuthContext sets user: null with
  // an authenticated session). That bearer token is the organisation's master
  // credential and the backend authorises every admin write with it, so a
  // membership lookup can only fail closed here for the wrong reason — hiding
  // controls that would succeed. The signal is checked after authReady so a
  // real session's not-yet-loaded profile does not flash admin UI.
  const apiKeyMode = authReady && isAuthenticated && user === null

  // The user's row in this organisation. ``db_id`` is the database user id —
  // ``id`` is the identity-provider subject and will never match a member row.
  const userDbId = user?.db_id

  useEffect(() => {
    let cancelled = false
    if (!organizationId || !userDbId) {
      setIsOrgAdmin(false)
      return
    }
    getOrgMemberships(organizationId)
      .then(members => {
        if (cancelled) return
        const mine = members.find(m => m.user_id === String(userDbId))
        setIsOrgAdmin(mine?.role === 'admin')
      })
      .catch(err => {
        console.error('Failed to resolve organisation role:', err)
        if (!cancelled) setIsOrgAdmin(false)
      })
    return () => {
      cancelled = true
    }
  }, [organizationId, userDbId])

  return apiKeyMode || isOrgAdmin || isPlatformAdmin
}

export default useIsOrgAdmin
