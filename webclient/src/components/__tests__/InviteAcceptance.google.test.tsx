/**
 * InviteAcceptance on a Google install — the regression half of #984.
 *
 * The OIDC branch is new; this one is the one that already works for every
 * existing tenant, and the only way to know the change did not disturb it is
 * to assert it from the opposite side of the same flag. Same component, same
 * mocks, OIDC_ENABLED false: the Google button must still be the thing on the
 * page, and the sessionStorage stash — which exists only to survive the OIDC
 * callback's replaceState — must not be written at all.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import InviteAcceptance, { PENDING_INVITE_KEY } from '../InviteAcceptance'
import { getOrgInvitePreview } from '../../data/apiClient'

vi.mock('../../data/authToken', () => ({
  OIDC_ENABLED: false,
}))

vi.mock('../OidcSignIn', () => ({
  default: () => <button>OIDC sign in</button>,
}))

vi.mock('../GoogleSignIn', () => ({
  default: () => <button>Google sign in</button>,
}))

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: false, authReady: true }),
}))

vi.mock('../../contexts/OrganizationContext', () => ({
  useOrganization: () => ({
    setCurrentOrgId: vi.fn(),
    refreshOrganizations: vi.fn(() => Promise.resolve()),
  }),
}))

vi.mock('../../data/apiClient', () => ({
  getInvitePreview: vi.fn(),
  acceptConsultantInvite: vi.fn(),
  getOrgInvitePreview: vi.fn(),
  acceptOrgInvite: vi.fn(),
}))

const PREVIEW = {
  organization_name: 'Odin Medical Ltd',
  inviter_name: 'Ada',
  inviter_email: 'ada@example.com',
  role: 'editor',
  expires_at: '2099-01-01T00:00:00Z',
  is_expired: false,
  status: 'pending' as const,
}

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  vi.mocked(getOrgInvitePreview).mockResolvedValue(PREVIEW as never)
})

async function renderInvite() {
  render(
    <InviteAcceptance
      token="tok-123"
      inviteType="org"
      onComplete={vi.fn()}
      onCancel={vi.fn()}
    />
  )
  await waitFor(() =>
    expect(screen.queryByText(/loading invitation details/i)).not.toBeInTheDocument()
  )
}

describe('with OIDC disabled', () => {
  it('still offers Google, verbatim', async () => {
    await renderInvite()
    expect(screen.getByRole('button', { name: /google sign in/i })).toBeInTheDocument()
    expect(screen.getByText(/sign in with google to accept this invitation/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /oidc sign in/i })).not.toBeInTheDocument()
  })

  it('writes nothing to session storage', async () => {
    await renderInvite()
    expect(sessionStorage.getItem(PENDING_INVITE_KEY)).toBeNull()
    expect(sessionStorage.length).toBe(0)
  })
})
