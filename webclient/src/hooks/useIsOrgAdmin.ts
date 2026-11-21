// SPDX-License-Identifier: AGPL-3.0-only
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
 *
 * The same lookup as ``useHasOrgRole(organizationId, 'admin')``, shared with
 * every other role hook on the screen: see ``orgRoleLookup``.
 */
import { useHasOrgRole } from './useHasOrgRole'

export function useIsOrgAdmin(organizationId: string | null | undefined): boolean {
  return useHasOrgRole(organizationId, 'admin')
}

export default useIsOrgAdmin
