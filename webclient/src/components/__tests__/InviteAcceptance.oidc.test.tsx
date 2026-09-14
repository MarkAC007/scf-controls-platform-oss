/**
 * InviteAcceptance on an OIDC install (#984).
 *
 * Two defects live here, and both end with an invitee who cannot accept.
 *
 * 1. The page offered a Google button on installs that have no Google. The
 *    invitee's only route in was a control that does nothing.
 *
 * 2. The OIDC callback in AuthContext does `replaceState({}, '', '/')`, which
 *    throws `?invite=` away. So the invitee signs in successfully and lands on
 *    a dashboard with no memory of the invitation they came to accept. The fix
 *    is a sessionStorage stash written before the redirect; this file pins that
 *    it is written, and only while the visitor is unauthenticated (re-stashing
 *    after acceptance would resurrect a spent invite on the next login).
 */
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import InviteAcceptance, { PENDING_INVITE_KEY } from '../InviteAcceptance'
import { getOrgInvitePreview } from '../../data/apiClient'

vi.mock('../../data/authToken', () => ({
  OIDC_ENABLED: true,
}))

vi.mock('../OidcSignIn', () => ({
  default: () => <button>OIDC sign in</button>,
}))

vi.mock('../GoogleSignIn', () => ({
  default: () => <button>Google sign in</button>,
}))

const auth = { isAuthenticated: false, authReady: true }
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => auth,
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

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  auth.isAuthenticated = false
  auth.authReady = true
  vi.mocked(getOrgInvitePreview).mockResolvedValue(PREVIEW as never)
})

describe('the sign-in prompt', () => {
  it('offers the OIDC button, not Google', async () => {
    await renderInvite()
    expect(screen.getByRole('button', { name: /oidc sign in/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /google sign in/i })).not.toBeInTheDocument()
  })

  it('tells them a temporary password is what to use', async () => {
    // They were handed one by their admin; nothing else on this page says so.
    await renderInvite()
    expect(screen.getByText(/temporary password/i)).toBeInTheDocument()
    expect(screen.queryByText(/sign in with google/i)).not.toBeInTheDocument()
  })
})

describe('surviving the OIDC round trip', () => {
  it('stashes the invite before the redirect can drop it', async () => {
    await renderInvite()
    await waitFor(() => expect(sessionStorage.getItem(PENDING_INVITE_KEY)).not.toBeNull())
    expect(JSON.parse(sessionStorage.getItem(PENDING_INVITE_KEY) as string)).toEqual({
      token: 'tok-123',
      inviteType: 'org',
    })
  })

  it('does not stash once the visitor is signed in', async () => {
    // By then the token has been consumed by App; re-stashing it would make a
    // spent invitation reappear on the next unrelated login.
    auth.isAuthenticated = true
    await renderInvite()
    expect(sessionStorage.getItem(PENDING_INVITE_KEY)).toBeNull()
  })
})
