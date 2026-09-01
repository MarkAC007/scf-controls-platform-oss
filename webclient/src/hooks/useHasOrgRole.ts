/**
 * useHasOrgRole — does the person looking at this screen hold at least this
 * role in this organisation?
 *
 * The same question ``useIsOrgAdmin`` answers, asked at any rank. It exists
 * because the backend's evidence write endpoints require ``editor``
 * (``require_org_role("editor")`` on per-file review, per-window review, and
 * assess-bulk), while the only role primitive the client had was admin-only.
 * Gating an editor's controls on admin would hide buttons from exactly the
 * people the API authorises — the review buttons on the evidence file list
 * were dead for that reason.
 *
 * Same contract as ``useIsOrgAdmin`` in every other respect:
 *
 * - A courtesy, not a boundary. The API refuses an under-privileged write
 *   whatever this returns, and nothing here should ever be the only thing
 *   standing between a viewer and a mutation.
 * - Fails closed. An errored members call, a profile that has not landed, or
 *   an unmatched membership row all yield ``false``.
 * - The role read is ``organization_members.role``. Team membership grants no
 *   permissions (Issue #822) and must never appear in this file.
 *
 * Note for a future tidy-up: ``useIsOrgAdmin`` predates this hook and still
 * carries its own copy of the membership lookup. It could become
 * ``useHasOrgRole(orgId, 'admin')`` outright — deliberately not done here, to
 * keep this change off a file other work is touching.
 */
import { useEffect, useState } from 'react'

import { useAuth } from '../contexts/AuthContext'
import { getOrgMemberships } from '../data/apiClient'

export type OrgRole = 'viewer' | 'editor' | 'admin'

/** Ascending privilege. Mirrors the backend's ordering in ``auth.py``. */
const ROLE_RANK: Record<string, number> = {
  viewer: 0,
  editor: 1,
  admin: 2,
}

export function useHasOrgRole(
  organizationId: string | null | undefined,
  minRole: OrgRole,
): boolean {
  const { user, isPlatformAdmin, isAuthenticated, authReady } = useAuth()
  const [hasRole, setHasRole] = useState(false)

  // API-key mode has no user profile at all (AuthContext sets user: null with
  // an authenticated session). That bearer token is the organisation's master
  // credential and the backend authorises every write with it, so a membership
  // lookup can only fail closed here for the wrong reason — hiding controls
  // that would succeed. Checked after authReady so a real session's
  // not-yet-loaded profile does not flash privileged UI.
  const apiKeyMode = authReady && isAuthenticated && user === null

  // ``db_id`` is the database user id — ``id`` is the identity-provider
  // subject and will never match a member row.
  const userDbId = user?.db_id

  useEffect(() => {
    let cancelled = false
    if (!organizationId || !userDbId) {
      setHasRole(false)
      return
    }
    getOrgMemberships(organizationId)
      .then(members => {
        if (cancelled) return
        const mine = members.find(m => m.user_id === String(userDbId))
        const rank = mine ? ROLE_RANK[mine.role] : undefined
        setHasRole(rank !== undefined && rank >= ROLE_RANK[minRole])
      })
      .catch(err => {
        console.error('Failed to resolve organisation role:', err)
        if (!cancelled) setHasRole(false)
      })
    return () => {
      cancelled = true
    }
  }, [organizationId, userDbId, minRole])

  return apiKeyMode || hasRole || isPlatformAdmin
}

/**
 * Editor or above — the rank the backend's evidence write endpoints require.
 */
export function useIsOrgEditor(organizationId: string | null | undefined): boolean {
  return useHasOrgRole(organizationId, 'editor')
}

export default useHasOrgRole
