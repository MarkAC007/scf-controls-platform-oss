/**
 * useIsOrgAdmin — the courtesy gate for team-assignment controls.
 *
 * The case that earns this file is API-key mode: AuthContext deliberately has
 * no user profile there (`user: null` with an authenticated session), and the
 * membership lookup can never match a row. The bearer token is the
 * organisation's master credential — the backend authorises every admin write
 * with it — so failing closed hides controls that would succeed. The hook must
 * treat that mode as admin, and must NOT treat a merely not-yet-loaded real
 * session the same way.
 */
import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useIsOrgAdmin } from '../useIsOrgAdmin'

const mockUseAuth = vi.fn()
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}))

const mockGetOrgMemberships = vi.fn()
vi.mock('../../data/apiClient', () => ({
  getOrgMemberships: (...args: unknown[]) => mockGetOrgMemberships(...args),
}))

beforeEach(() => {
  vi.clearAllMocks()
  mockGetOrgMemberships.mockResolvedValue([])
})

describe('useIsOrgAdmin', () => {
  it('API-key mode (authenticated, no user profile) is admin without a membership call', () => {
    mockUseAuth.mockReturnValue({
      user: null,
      isPlatformAdmin: false,
      isAuthenticated: true,
      authReady: true,
    })
    const { result } = renderHook(() => useIsOrgAdmin('org-1'))
    expect(result.current).toBe(true)
    expect(mockGetOrgMemberships).not.toHaveBeenCalled()
  })

  it('a real session that has not loaded its profile yet is NOT admin', () => {
    // authReady=false is the not-yet-restored window — flashing admin UI here
    // is the failure mode the authReady check in the hook exists to prevent.
    mockUseAuth.mockReturnValue({
      user: null,
      isPlatformAdmin: false,
      isAuthenticated: true,
      authReady: false,
    })
    const { result } = renderHook(() => useIsOrgAdmin('org-1'))
    expect(result.current).toBe(false)
  })

  it('an org admin membership row resolves to admin', async () => {
    mockUseAuth.mockReturnValue({
      user: { db_id: 'u-1' },
      isPlatformAdmin: false,
      isAuthenticated: true,
      authReady: true,
    })
    mockGetOrgMemberships.mockResolvedValue([{ user_id: 'u-1', role: 'admin' }])
    const { result } = renderHook(() => useIsOrgAdmin('org-1'))
    await waitFor(() => expect(result.current).toBe(true))
  })

  it('a non-admin membership row stays non-admin', async () => {
    mockUseAuth.mockReturnValue({
      user: { db_id: 'u-1' },
      isPlatformAdmin: false,
      isAuthenticated: true,
      authReady: true,
    })
    mockGetOrgMemberships.mockResolvedValue([{ user_id: 'u-1', role: 'member' }])
    const { result } = renderHook(() => useIsOrgAdmin('org-1'))
    await waitFor(() => expect(mockGetOrgMemberships).toHaveBeenCalled())
    expect(result.current).toBe(false)
  })
})
