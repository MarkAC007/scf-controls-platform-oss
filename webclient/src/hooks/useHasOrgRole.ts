// SPDX-License-Identifier: AGPL-3.0-only
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
 * Three states, one lookup (#1273). The role is allowed, not allowed, or not
 * yet known, and ``useOrgRoleState`` says which. The boolean hooks keep their
 * signature and read ``false`` while it is not known. A component that SAYS
 * something to an under-privileged user ("only an admin can ...") must wait
 * for ``resolved`` before saying it, or an admin reads that notice on every
 * page load. The membership lookup itself lives in ``orgRoleLookup``: one
 * request per organisation and user, shared by every component and by
 * ``useIsOrgAdmin``, with the answer remembered between mounts.
 */
import { useEffect, useSyncExternalStore } from 'react'

import { useAuth } from '../contexts/AuthContext'
import { ensureOrgRole, getKnownOrgRole, noteOrgRoleUser, subscribeOrgRoles } from './orgRoleLookup'

export type OrgRole = 'viewer' | 'editor' | 'admin'

/** Ascending privilege. Mirrors the backend's ordering in ``auth.py``. */
const ROLE_RANK: Record<string, number> = {
  viewer: 0,
  editor: 1,
  admin: 2,
}

export interface OrgRoleState {
  /** Holds at least the role asked for. Always ``false`` while ``resolved`` is. */
  allowed: boolean
  /** ``false`` until the role is known: session, organisation or lookup still loading. */
  resolved: boolean
}

const UNKNOWN: OrgRoleState = { allowed: false, resolved: false }
const ALLOWED: OrgRoleState = { allowed: true, resolved: true }
const REFUSED: OrgRoleState = { allowed: false, resolved: true }

export function useOrgRoleState(
  organizationId: string | null | undefined,
  minRole: OrgRole,
): OrgRoleState {
  const { user, isPlatformAdmin, isAuthenticated, authReady } = useAuth()

  // API-key mode has no user profile at all (AuthContext sets user: null with
  // an authenticated session). That bearer token is the organisation's master
  // credential and the backend authorises every write with it, so a membership
  // lookup can only fail closed here for the wrong reason — hiding controls
  // that would succeed. Checked after authReady so a real session's
  // not-yet-loaded profile does not flash privileged UI.
  const apiKeyMode = authReady && isAuthenticated && user === null

  // ``db_id`` is the database user id — ``id`` is the identity-provider
  // subject and will never match a member row.
  const userDbId = user?.db_id ? String(user.db_id) : undefined

  // Neither of these needs a membership row, so neither waits for one.
  const passes = apiKeyMode || isPlatformAdmin
  const lookupOrg = !passes && organizationId && userDbId ? organizationId : undefined

  const role = useSyncExternalStore(subscribeOrgRoles, () =>
    lookupOrg && userDbId ? getKnownOrgRole(lookupOrg, userDbId) : undefined,
  )

  useEffect(() => {
    if (userDbId) noteOrgRoleUser(userDbId)
    if (lookupOrg && userDbId) ensureOrgRole(lookupOrg, userDbId)
  }, [lookupOrg, userDbId])

  if (passes) return ALLOWED
  if (role === undefined) return UNKNOWN
  const rank = role === null ? undefined : ROLE_RANK[role]
  return rank !== undefined && rank >= ROLE_RANK[minRole] ? ALLOWED : REFUSED
}

export function useHasOrgRole(
  organizationId: string | null | undefined,
  minRole: OrgRole,
): boolean {
  return useOrgRoleState(organizationId, minRole).allowed
}

/**
 * Editor or above — the rank the backend's evidence write endpoints require.
 */
export function useIsOrgEditor(organizationId: string | null | undefined): boolean {
  return useHasOrgRole(organizationId, 'editor')
}

export default useHasOrgRole
