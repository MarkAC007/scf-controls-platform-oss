// SPDX-License-Identifier: AGPL-3.0-only
/**
 * useOrgRoleState and the shared membership lookup behind every role hook (#1273).
 *
 * The boolean hooks answer false both for "not allowed" and for "not known
 * yet", so an admin was shown the non-admin state of every gated page until
 * the members call returned, and every gated component made that call for
 * itself. The contract under test:
 *
 * - three states: allowed, not allowed, not yet known;
 * - one lookup per organisation and user, shared by every component, with the
 *   answer remembered so a component mounted later starts with it;
 * - an answer is never used for another organisation or another user;
 * - an errored lookup fails closed and is tried again, not remembered.
 */
import { act, render, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useHasOrgRole, useIsOrgEditor, useOrgRoleState } from '../useHasOrgRole'
import type { OrgRole, OrgRoleState } from '../useHasOrgRole'
import { useIsOrgAdmin } from '../useIsOrgAdmin'
import { ORG_ROLE_FRESH_MS, clearOrgRoleCache } from '../orgRoleLookup'

const mockUseAuth = vi.fn()
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}))

const mockGetOrgMemberships = vi.fn()
vi.mock('../../data/apiClient', () => ({
  getOrgMemberships: (...args: unknown[]) => mockGetOrgMemberships(...args),
}))

function signedInAs(dbId: string, over: Record<string, unknown> = {}) {
  mockUseAuth.mockReturnValue({
    user: { db_id: dbId },
    isPlatformAdmin: false,
    canManageIntegrations: false,
    isAuthenticated: true,
    authReady: true,
    ...over,
  })
}

/** A members call the test settles by hand, so the "not yet known" window is observable. */
function deferredMembers() {
  let resolve!: (rows: Array<{ user_id: string; role: string }>) => void
  let reject!: (err: Error) => void
  const promise = new Promise<Array<{ user_id: string; role: string }>>((res, rej) => {
    resolve = res
    reject = rej
  })
  mockGetOrgMemberships.mockReturnValueOnce(promise)
  return { resolve, reject }
}

/** Every value the hook returned, first render included. */
function renderRoleState(org: string, min: OrgRole) {
  const seen: OrgRoleState[] = []
  const view = renderHook(() => {
    const state = useOrgRoleState(org, min)
    seen.push(state)
    return state
  })
  return { ...view, seen }
}

const UNKNOWN = { allowed: false, resolved: false }
const ALLOWED = { allowed: true, resolved: true }
const REFUSED = { allowed: false, resolved: true }

beforeEach(() => {
  vi.clearAllMocks()
  clearOrgRoleCache()
  mockGetOrgMemberships.mockResolvedValue([])
  signedInAs('u-1')
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useOrgRoleState: three states', () => {
  it('is not yet known until the lookup returns, then allowed', async () => {
    const members = deferredMembers()
    const { result } = renderRoleState('org-1', 'admin')
    expect(result.current).toEqual(UNKNOWN)

    await act(async () => members.resolve([{ user_id: 'u-1', role: 'admin' }]))
    expect(result.current).toEqual(ALLOWED)
  })

  it('is not yet known until the lookup returns, then not allowed', async () => {
    const members = deferredMembers()
    const { result } = renderRoleState('org-1', 'admin')
    expect(result.current).toEqual(UNKNOWN)

    await act(async () => members.resolve([{ user_id: 'u-1', role: 'editor' }]))
    expect(result.current).toEqual(REFUSED)
  })

  it('the boolean hooks read false while the role is not yet known', async () => {
    const members = deferredMembers()
    const admin = renderHook(() => useHasOrgRole('org-1', 'admin'))
    const editor = renderHook(() => useIsOrgEditor('org-1'))
    const legacy = renderHook(() => useIsOrgAdmin('org-1'))
    expect([admin.result.current, editor.result.current, legacy.result.current]).toEqual([false, false, false])

    await act(async () => members.resolve([{ user_id: 'u-1', role: 'admin' }]))
    expect([admin.result.current, editor.result.current, legacy.result.current]).toEqual([true, true, true])
  })

  it('a member with no row in this organisation resolves as not allowed', async () => {
    mockGetOrgMemberships.mockResolvedValue([{ user_id: 'someone-else', role: 'admin' }])
    const { result } = renderRoleState('org-1', 'viewer')
    await waitFor(() => expect(result.current).toEqual(REFUSED))
  })

  it('stays not yet known while the session or the organisation has not loaded', () => {
    signedInAs('u-1', { user: null, authReady: false })
    expect(renderRoleState('org-1', 'editor').result.current).toEqual(UNKNOWN)

    signedInAs('u-1')
    const noOrg = renderHook(() => useOrgRoleState(null, 'editor'))
    expect(noOrg.result.current).toEqual(UNKNOWN)
    expect(mockGetOrgMemberships).not.toHaveBeenCalled()
  })

  it('API-key mode is allowed and resolved at once, without a membership call', () => {
    signedInAs('u-1', { user: null })
    const { seen } = renderRoleState('org-1', 'admin')
    expect(seen[0]).toEqual(ALLOWED)
    expect(mockGetOrgMemberships).not.toHaveBeenCalled()
  })

  it('a platform admin is allowed and resolved at once', () => {
    signedInAs('u-1', { isPlatformAdmin: true })
    const { seen } = renderRoleState('org-1', 'admin')
    expect(seen[0]).toEqual(ALLOWED)
  })
})

describe('the shared membership lookup', () => {
  it('makes one request for any number of components, whichever hook they call', async () => {
    const members = deferredMembers()
    const Admin = () => <span>{String(useHasOrgRole('org-1', 'admin'))}</span>
    const Editor = () => <span>{String(useIsOrgEditor('org-1'))}</span>
    const Legacy = () => <span>{String(useIsOrgAdmin('org-1'))}</span>
    const State = () => <span>{String(useOrgRoleState('org-1', 'viewer').resolved)}</span>

    const { container } = render(
      <>
        <Admin />
        <Editor />
        <Legacy />
        <State />
        <Admin />
      </>
    )
    expect(container.textContent).toBe('falsefalsefalsefalsefalse')
    await act(async () => members.resolve([{ user_id: 'u-1', role: 'editor' }]))

    expect(container.textContent).toBe('falsetruefalsetruefalse')
    expect(mockGetOrgMemberships).toHaveBeenCalledTimes(1)
    expect(mockGetOrgMemberships).toHaveBeenCalledWith('org-1')
  })

  it('a component mounted later starts with the known role and asks nothing', async () => {
    mockGetOrgMemberships.mockResolvedValue([{ user_id: 'u-1', role: 'admin' }])
    const first = renderRoleState('org-1', 'admin')
    await waitFor(() => expect(first.result.current).toEqual(ALLOWED))
    first.unmount()

    const later = renderRoleState('org-1', 'editor')
    expect(later.seen[0]).toEqual(ALLOWED)
    expect(later.seen.every(s => s.allowed && s.resolved)).toBe(true)
    expect(mockGetOrgMemberships).toHaveBeenCalledTimes(1)
  })

  it('does not use one organisation\'s answer for another', async () => {
    mockGetOrgMemberships.mockResolvedValueOnce([{ user_id: 'u-1', role: 'admin' }])
    const first = renderRoleState('org-1', 'admin')
    await waitFor(() => expect(first.result.current).toEqual(ALLOWED))

    mockGetOrgMemberships.mockResolvedValueOnce([{ user_id: 'u-1', role: 'viewer' }])
    const other = renderRoleState('org-2', 'admin')
    expect(other.seen[0]).toEqual(UNKNOWN)
    await waitFor(() => expect(other.result.current).toEqual(REFUSED))
    expect(other.seen.some(s => s.allowed)).toBe(false)
    expect(mockGetOrgMemberships).toHaveBeenLastCalledWith('org-2')
    expect(first.result.current).toEqual(ALLOWED)
  })

  it('does not use one user\'s answer for another, and forgets the first user', async () => {
    const rows = [
      { user_id: 'u-1', role: 'admin' },
      { user_id: 'u-2', role: 'viewer' },
    ]
    mockGetOrgMemberships.mockResolvedValue(rows)
    const first = renderRoleState('org-1', 'admin')
    await waitFor(() => expect(first.result.current).toEqual(ALLOWED))
    first.unmount()

    signedInAs('u-2')
    const second = renderRoleState('org-1', 'admin')
    expect(second.seen[0]).toEqual(UNKNOWN)
    await waitFor(() => expect(second.result.current).toEqual(REFUSED))
    expect(second.seen.some(s => s.allowed)).toBe(false)
    expect(mockGetOrgMemberships).toHaveBeenCalledTimes(2)
    second.unmount()

    // Back to the first user: their earlier answer went when the user changed.
    signedInAs('u-1')
    const again = renderRoleState('org-1', 'admin')
    expect(again.seen[0]).toEqual(UNKNOWN)
    await waitFor(() => expect(again.result.current).toEqual(ALLOWED))
  })

  it('a user change under a mounted component drops the old answer at once', async () => {
    mockGetOrgMemberships.mockResolvedValue([{ user_id: 'u-1', role: 'admin' }])
    const view = renderRoleState('org-1', 'admin')
    await waitFor(() => expect(view.result.current).toEqual(ALLOWED))

    const members = deferredMembers()
    signedInAs('u-2')
    view.rerender()
    expect(view.result.current).toEqual(UNKNOWN)
    await act(async () => members.resolve([{ user_id: 'u-1', role: 'admin' }]))
    expect(view.result.current).toEqual(REFUSED)
  })

  it('clearing the cache (sign-out) forgets every answer', async () => {
    mockGetOrgMemberships.mockResolvedValue([{ user_id: 'u-1', role: 'admin' }])
    const first = renderRoleState('org-1', 'admin')
    await waitFor(() => expect(first.result.current).toEqual(ALLOWED))
    first.unmount()

    clearOrgRoleCache()
    const members = deferredMembers()
    const later = renderRoleState('org-1', 'admin')
    expect(later.seen[0]).toEqual(UNKNOWN)
    await act(async () => members.resolve([{ user_id: 'u-1', role: 'admin' }]))
    expect(later.result.current).toEqual(ALLOWED)
  })

  it('an answer that lands after sign-out is thrown away', async () => {
    const members = deferredMembers()
    const view = renderRoleState('org-1', 'admin')
    clearOrgRoleCache()
    await act(async () => members.resolve([{ user_id: 'u-1', role: 'admin' }]))
    expect(view.result.current).toEqual(UNKNOWN)
  })

  it('an errored lookup fails closed and is tried again by the next component', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    mockGetOrgMemberships.mockRejectedValueOnce(new Error('500'))
    const first = renderRoleState('org-1', 'viewer')
    await waitFor(() => expect(first.result.current).toEqual(REFUSED))
    expect(first.seen.some(s => s.allowed)).toBe(false)
    first.unmount()

    mockGetOrgMemberships.mockResolvedValueOnce([{ user_id: 'u-1', role: 'admin' }])
    const second = renderRoleState('org-1', 'viewer')
    await waitFor(() => expect(second.result.current).toEqual(ALLOWED))
    expect(mockGetOrgMemberships).toHaveBeenCalledTimes(2)
  })

  it('an error on a re-check closes a gate that was open', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.useFakeTimers({ toFake: ['Date'] })
    mockGetOrgMemberships.mockResolvedValueOnce([{ user_id: 'u-1', role: 'admin' }])
    const first = renderRoleState('org-1', 'admin')
    await waitFor(() => expect(first.result.current).toEqual(ALLOWED))

    vi.setSystemTime(Date.now() + ORG_ROLE_FRESH_MS + 1)
    mockGetOrgMemberships.mockRejectedValueOnce(new Error('403'))
    renderRoleState('org-1', 'admin')
    await waitFor(() => expect(first.result.current).toEqual(REFUSED))
  })

  it('re-checks a remembered answer in the background once it is old, and picks up a role change', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    mockGetOrgMemberships.mockResolvedValueOnce([{ user_id: 'u-1', role: 'admin' }])
    const first = renderRoleState('org-1', 'admin')
    await waitFor(() => expect(first.result.current).toEqual(ALLOWED))
    first.unmount()

    // Still fresh: no request.
    vi.setSystemTime(Date.now() + ORG_ROLE_FRESH_MS - 1000)
    renderRoleState('org-1', 'admin').unmount()
    expect(mockGetOrgMemberships).toHaveBeenCalledTimes(1)

    // Old: the remembered answer is shown at once and checked behind it.
    vi.setSystemTime(Date.now() + 2000)
    const members = deferredMembers()
    const later = renderRoleState('org-1', 'admin')
    expect(later.seen[0]).toEqual(ALLOWED)
    expect(mockGetOrgMemberships).toHaveBeenCalledTimes(2)

    await act(async () => members.resolve([{ user_id: 'u-1', role: 'viewer' }]))
    expect(later.result.current).toEqual(REFUSED)
  })
})
