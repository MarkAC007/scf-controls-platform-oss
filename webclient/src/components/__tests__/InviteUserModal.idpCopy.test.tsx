/**
 * InviteUserModal description copy on an OIDC install (#984).
 *
 * Its own file because OIDC_ENABLED is read at module scope in
 * `src/data/authToken.ts` — by the time a test could flip an env var the
 * module has already resolved it. Mocking the module is the only honest way
 * to exercise the true branch, and a mock declared here cannot leak into
 * InviteUserModal.idp.test.tsx, which guards the Google-flow copy.
 *
 * What is actually at stake: on a bundled-Keycloak install the old sentence
 * ("sign in using their Google account") is simply false, and it is the
 * sentence that tells an admin where the temporary password is about to come
 * from. Wrong here and the admin closes the modal looking for an email.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import InviteUserModal from '../InviteUserModal'
import { getOrgInvites } from '../../data/apiClient'

vi.mock('../../data/authToken', () => ({
  OIDC_ENABLED: true,
}))

vi.mock('../../data/apiClient', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(() => Promise.resolve({})),
    patch: vi.fn(),
    delete: vi.fn(),
  },
  getOrgInvites: vi.fn(() => Promise.resolve({ invites: [], total: 0 })),
  cancelOrgInvite: vi.fn(),
}))

async function renderModal() {
  render(
    <InviteUserModal organizationId="org-1" onClose={vi.fn()} onInviteSent={vi.fn()} />
  )
  await waitFor(() =>
    expect(screen.queryByText(/loading pending invites/i)).not.toBeInTheDocument()
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getOrgInvites).mockResolvedValue({ invites: [], total: 0 } as never)
})

describe('with OIDC enabled', () => {
  it('says an account is created and a password shown once', async () => {
    await renderModal()
    expect(
      screen.getByText(/account is created for them in the identity provider/i)
    ).toBeInTheDocument()
    expect(screen.getByText(/temporary password is shown here once/i)).toBeInTheDocument()
  })

  it('promises they join the organisation at first sign-in', async () => {
    // The backend honours a provisioned invite when that account signs in,
    // so the admin need not chase the invitee to click a link.
    await renderModal()
    expect(screen.getByText(/join this organisation straight away/i)).toBeInTheDocument()
  })

  it('drops the Google sentence', async () => {
    // Leaving it in is worse than saying nothing: it names a sign-in method
    // the install does not have.
    await renderModal()
    expect(screen.queryByText(/Google account/i)).not.toBeInTheDocument()
  })
})
