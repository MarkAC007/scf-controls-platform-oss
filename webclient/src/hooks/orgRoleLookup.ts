// SPDX-License-Identifier: AGPL-3.0-only
/**
 * orgRoleLookup — the one membership lookup behind every role hook.
 *
 * ``useHasOrgRole`` / ``useIsOrgEditor`` / ``useIsOrgAdmin`` used to fetch the
 * members list once per component, and each started from "no role" until its
 * own copy came back. A page with six gated components made six requests and
 * showed an admin the non-admin state six times. This module asks once per
 * organisation and user, lets every component wait on the same request, and
 * remembers the answer so a component mounted later starts with it.
 *
 * - Keyed by organisation AND user. An answer is never read under another key,
 *   and the whole cache is dropped on sign-out and when the user changes.
 * - A remembered answer is shown at once and re-checked in the background once
 *   it is older than ``ORG_ROLE_FRESH_MS``, so a role change is picked up
 *   without a request per component.
 * - Fails closed. An errored lookup reads as "no role", and is not remembered
 *   as checked: the next component to mount tries again.
 *
 * No React and no AuthContext import here, so AuthContext can clear the cache
 * on sign-out without an import cycle.
 */
import { getOrgMemberships } from '../data/apiClient'

/** How long an answer is used without asking again. */
export const ORG_ROLE_FRESH_MS = 30_000

/** Settled answers. ``null`` is "no role here": no member row, or the lookup failed. */
const known = new Map<string, string | null>()
/** When each answer was last confirmed. A failed lookup has no entry, so it is retried. */
const checkedAt = new Map<string, number>()
const inFlight = new Set<string>()
const listeners = new Set<() => void>()
/** Bumped on every clear, so a response requested before it is discarded. */
let generation = 0
let currentUserId: string | null = null

const keyOf = (organizationId: string, userId: string) => `${organizationId}\u0000${userId}`

function notify() {
  listeners.forEach(listener => listener())
}

export function subscribeOrgRoles(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/**
 * The user's role in the organisation: a role name, ``null`` when they hold
 * none (or the lookup failed), ``undefined`` while no answer has arrived.
 */
export function getKnownOrgRole(organizationId: string, userId: string): string | null | undefined {
  return known.get(keyOf(organizationId, userId))
}

/**
 * Make sure an answer is on its way or fresh. Safe to call from every
 * component: a request already in flight, or an answer newer than
 * ``ORG_ROLE_FRESH_MS``, makes this a no-op.
 */
export function ensureOrgRole(organizationId: string, userId: string): void {
  const key = keyOf(organizationId, userId)
  if (inFlight.has(key)) return
  const at = checkedAt.get(key)
  if (at !== undefined && Date.now() - at < ORG_ROLE_FRESH_MS) return

  inFlight.add(key)
  const requestedIn = generation
  const settle = (role: string | null, succeeded: boolean) => {
    if (requestedIn !== generation) return
    inFlight.delete(key)
    if (succeeded) checkedAt.set(key, Date.now())
    else checkedAt.delete(key)
    if (!known.has(key) || known.get(key) !== role) {
      known.set(key, role)
      notify()
    }
  }

  let request: ReturnType<typeof getOrgMemberships>
  try {
    request = getOrgMemberships(organizationId)
  } catch (err) {
    request = Promise.reject(err)
  }
  request.then(
    members => settle(members.find(m => m.user_id === userId)?.role ?? null, true),
    err => {
      console.error('Failed to resolve organisation role:', err)
      settle(null, false)
    },
  )
}

/** Forget every answer. Called on sign-out, and when the signed-in user changes. */
export function clearOrgRoleCache(): void {
  generation += 1
  currentUserId = null
  inFlight.clear()
  checkedAt.clear()
  if (known.size === 0) return
  known.clear()
  notify()
}

/**
 * Tell the cache who is signed in. A different user from the last one seen
 * drops everything held for the previous one.
 */
export function noteOrgRoleUser(userId: string): void {
  if (currentUserId !== null && currentUserId !== userId) clearOrgRoleCache()
  currentUserId = userId
}
