/**
 * useHasOrgRole — the courtesy gate at any rank.
 *
 * The case that earns this file is the one that made the evidence review
 * buttons dead: the backend authorises those writes at `editor`, and the only
 * client-side role primitive was admin-only. A gate that answers "are you an
 * admin?" to the question "may you review?" hides the control from precisely
 * the people the API would let through.
 *
 * So the contract under test is rank comparison, not equality — an admin
 * satisfies an editor requirement, a viewer does not — plus the inherited
 * fail-closed and API-key behaviours of `useIsOrgAdmin`.
 */
import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useHasOrgRole, useIsOrgEditor } from '../useHasOrgRole'

const mockUseAuth = vi.fn()
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}))

const mockGetOrgMemberships = vi.fn()
vi.mock('../../data/apiClient', () => ({
  getOrgMemberships: (...args: unknown[]) => mockGetOrgMemberships(...args),
}))

function signedInUser() {
  mockUseAuth.mockReturnValue({
    user: { db_id: 'u-1' },
    isPlatformAdmin: false,
    isAuthenticated: true,
    authReady: true,
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  mockGetOrgMemberships.mockResolvedValue([])
})

describe('useHasOrgRole', () => {
  it('an editor satisfies an editor requirement', async () => {
    signedInUser()
    mockGetOrgMemberships.mockResolvedValue([{ user_id: 'u-1', role: 'editor' }])

    const { result } = renderHook(() => useIsOrgEditor('org-1'))
    await waitFor(() => expect(result.current).toBe(true))
  })

  it('an admin satisfies an editor requirement — rank, not equality', async () => {
    // The bug this hook replaces: gating on `role === 'admin'` and gating on
    // `role === 'editor'` both get one of these two users wrong.
    signedInUser()
    mockGetOrgMemberships.mockResolvedValue([{ user_id: 'u-1', role: 'admin' }])

    const { result } = renderHook(() => useIsOrgEditor('org-1'))
    await waitFor(() => expect(result.current).toBe(true))
  })

  it('a viewer does not satisfy an editor requirement', async () => {
    signedInUser()
    mockGetOrgMemberships.mockResolvedValue([{ user_id: 'u-1', role: 'viewer' }])

    const { result } = renderHook(() => useIsOrgEditor('org-1'))
    await waitFor(() => expect(mockGetOrgMemberships).toHaveBeenCalled())
    expect(result.current).toBe(false)
  })

  it('an editor does NOT satisfy an admin requirement', async () => {
    signedInUser()
    mockGetOrgMemberships.mockResolvedValue([{ user_id: 'u-1', role: 'editor' }])

    const { result } = renderHook(() => useHasOrgRole('org-1', 'admin'))
    await waitFor(() => expect(mockGetOrgMemberships).toHaveBeenCalled())
    expect(result.current).toBe(false)
  })

  it('a user with no membership row in this org fails closed', async () => {
    signedInUser()
    mockGetOrgMemberships.mockResolvedValue([{ user_id: 'someone-else', role: 'admin' }])

    const { result } = renderHook(() => useIsOrgEditor('org-1'))
    await waitFor(() => expect(mockGetOrgMemberships).toHaveBeenCalled())
    expect(result.current).toBe(false)
  })

  it('a failed membership lookup fails closed rather than opening the gate', async () => {
    signedInUser()
    mockGetOrgMemberships.mockRejectedValue(new Error('500'))
    vi.spyOn(console, 'error').mockImplementation(() => {})

    const { result } = renderHook(() => useIsOrgEditor('org-1'))
    await waitFor(() => expect(mockGetOrgMemberships).toHaveBeenCalled())
    expect(result.current).toBe(false)
  })

  it('API-key mode passes without a membership call', () => {
    // Same reasoning as useIsOrgAdmin: that bearer token is the organisation's
    // master credential, so a lookup that can never match a row would fail
    // closed for the wrong reason and hide controls that would succeed.
    mockUseAuth.mockReturnValue({
      user: null,
      isPlatformAdmin: false,
      isAuthenticated: true,
      authReady: true,
    })
    const { result } = renderHook(() => useIsOrgEditor('org-1'))
    expect(result.current).toBe(true)
    expect(mockGetOrgMemberships).not.toHaveBeenCalled()
  })

  it('a session whose profile has not loaded yet does not flash privileged UI', () => {
    mockUseAuth.mockReturnValue({
      user: null,
      isPlatformAdmin: false,
      isAuthenticated: true,
      authReady: false,
    })
    const { result } = renderHook(() => useIsOrgEditor('org-1'))
    expect(result.current).toBe(false)
  })

  it('a platform admin passes at any rank', async () => {
    mockUseAuth.mockReturnValue({
      user: { db_id: 'u-1' },
      isPlatformAdmin: true,
      isAuthenticated: true,
      authReady: true,
    })
    const { result } = renderHook(() => useHasOrgRole('org-1', 'admin'))
    await waitFor(() => expect(result.current).toBe(true))
  })
})
