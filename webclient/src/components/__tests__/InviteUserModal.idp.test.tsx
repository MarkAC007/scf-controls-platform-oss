/**
 * InviteUserModal: the identity-provider half of the invite (#984).
 *
 * The bug this covers is that "Invite User" created a row in our database and
 * nothing in the bundled Keycloak, so the invitee had nowhere to sign in. The
 * backend now provisions the account and hands back a one-time temporary
 * password. That password exists in exactly one place — this success state —
 * and if the screen drops it, or hides it behind an auto-dismiss, the admin
 * has no second chance and the account is unreachable. So the assertions here
 * follow the password onto the screen and pin the affordance that copies it.
 *
 * The pending list is the other half: an admin looking at "three invitations
 * outstanding" needs to know which of those people can actually log in. That
 * is what the badge says, and `idp_status` is the only source for it.
 *
 * OIDC_ENABLED is false in this file (the vitest env sets no VITE_OIDC_ENABLED),
 * which makes it the Google-flow regression guard as well: the description copy
 * must be untouched here. The OIDC wording is pinned in
 * InviteUserModal.idpCopy.test.tsx, which mocks the flag on.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import InviteUserModal from '../InviteUserModal'
import { apiClient, getOrgInvites } from '../../data/apiClient'

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

const ORG_ID = 'org-1'

async function renderModal(props: Partial<{ onClose: () => void; onInviteSent: () => void }> = {}) {
  render(
    <InviteUserModal
      organizationId={ORG_ID}
      onClose={props.onClose ?? vi.fn()}
      onInviteSent={props.onInviteSent ?? vi.fn()}
    />
  )
  await waitFor(() =>
    expect(screen.queryByText(/loading pending invites/i)).not.toBeInTheDocument()
  )
}

/** Fill in the minimum a submit needs, then submit and wait for the POST. */
async function invite(user: ReturnType<typeof userEvent.setup>, email = 'new@example.com') {
  await user.type(screen.getByLabelText(/email address/i), email)
  await user.click(screen.getByRole('button', { name: /send invitation/i }))
  await waitFor(() => expect(apiClient.post).toHaveBeenCalled())
}

const PENDING = {
  id: 'i1',
  email: 'ada@example.com',
  role: 'editor',
  status: 'pending',
  created_at: '2026-08-01T00:00:00',
  expires_at: '2026-08-08T00:00:00',
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getOrgInvites).mockResolvedValue({ invites: [], total: 0 } as never)
  vi.mocked(apiClient.post).mockResolvedValue({} as never)
})

describe('the temporary password', () => {
  beforeEach(() => {
    vi.mocked(apiClient.post).mockResolvedValue({
      id: 'i9',
      email: 'new@example.com',
      role: 'viewer',
      idp_temporary_password: 'not-a-real-password',
      idp_status: 'provisioned',
    } as never)
  })

  it('shows the password the API issued', async () => {
    const user = userEvent.setup()
    await renderModal()
    await invite(user)
    const shown = await screen.findByTestId('idp-temp-password')
    expect(shown).toHaveTextContent('not-a-real-password')
  })

  it('says it is shown once and must be changed', async () => {
    // Without this, an admin reasonably assumes they can come back for it.
    const user = userEvent.setup()
    await renderModal()
    await invite(user)
    expect(await screen.findByText(/shown once/i)).toBeInTheDocument()
    expect(screen.getByText(/will not be shown again/i)).toBeInTheDocument()
    expect(screen.getByText(/change it at first sign-in/i)).toBeInTheDocument()
  })

  it('copies it to the clipboard and confirms', async () => {
    // userEvent.setup() installs its own navigator.clipboard stub, replacing
    // anything defined beforehand — so spy on the one it installs, after it.
    const user = userEvent.setup()
    const writeText = vi
      .spyOn(navigator.clipboard, 'writeText')
      .mockResolvedValue(undefined)
    await renderModal()
    await invite(user)
    await screen.findByTestId('idp-temp-password')
    await user.click(screen.getByRole('button', { name: /^copy$/i }))
    expect(writeText).toHaveBeenCalledWith('not-a-real-password')
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /copied/i })).toBeInTheDocument()
    )
  })

  it('does not dismiss itself out from under the admin', async () => {
    // The 2s auto-dismiss is fine for an emailed invite and fatal for a
    // one-time secret: the password would leave the screen before it could be
    // copied. When one is present the admin closes the modal themselves.
    const onInviteSent = vi.fn()
    const user = userEvent.setup()
    await renderModal({ onInviteSent })
    await invite(user)
    await screen.findByTestId('idp-temp-password')
    await new Promise(resolve => setTimeout(resolve, 2100))
    expect(onInviteSent).not.toHaveBeenCalled()
    expect(screen.getByTestId('idp-temp-password')).toBeInTheDocument()
  })

  it('takes the password off the screen when inviting another person', async () => {
    const user = userEvent.setup()
    await renderModal()
    await invite(user)
    await screen.findByTestId('idp-temp-password')
    await user.click(screen.getByRole('button', { name: /invite another/i }))
    expect(screen.queryByTestId('idp-temp-password')).not.toBeInTheDocument()
    expect(screen.getByLabelText(/email address/i)).toBeInTheDocument()
  })

  it('never writes it to browser storage', async () => {
    // A one-time secret that survives in localStorage is no longer one-time.
    const user = userEvent.setup()
    await renderModal()
    await invite(user)
    await screen.findByTestId('idp-temp-password')
    const dumped = JSON.stringify({ ...localStorage, ...sessionStorage })
    expect(dumped).not.toContain('not-a-real-password')
  })
})

describe('when the API issues no password', () => {
  it('shows no password block at all', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      id: 'i9',
      idp_temporary_password: null,
      idp_status: 'provisioned',
    } as never)
    const user = userEvent.setup()
    await renderModal()
    await invite(user)
    await waitFor(() => expect(screen.getByText(/invitation sent/i)).toBeInTheDocument())
    expect(screen.queryByTestId('idp-temp-password')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^copy$/i })).not.toBeInTheDocument()
  })

  it('explains that the account already existed', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      id: 'i9',
      idp_temporary_password: null,
      idp_status: 'provisioned',
    } as never)
    const user = userEvent.setup()
    await renderModal()
    await invite(user)
    expect(
      await screen.findByText(/already have an identity-provider account/i)
    ).toBeInTheDocument()
  })

  it('says the IdP owns the account on an external install', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      id: 'i9',
      idp_temporary_password: null,
      idp_status: 'external',
    } as never)
    const user = userEvent.setup()
    await renderModal()
    await invite(user)
    expect(
      await screen.findByText(/managed by your identity provider/i)
    ).toBeInTheDocument()
  })

  it('leaves a backend without the fields rendering as before', async () => {
    // The API this modal talks to may predate #984. Absent fields must read as
    // "nothing to say", not as an empty password box.
    const onInviteSent = vi.fn()
    const user = userEvent.setup()
    await renderModal({ onInviteSent })
    await invite(user)
    await waitFor(() => expect(screen.getByText(/invitation sent/i)).toBeInTheDocument())
    expect(screen.queryByTestId('idp-temp-password')).not.toBeInTheDocument()
    await waitFor(() => expect(onInviteSent).toHaveBeenCalled(), { timeout: 3000 })
  })
})

describe('the pending list badges', () => {
  it('names the three states and stays silent on a fourth', async () => {
    vi.mocked(getOrgInvites).mockResolvedValue({
      invites: [
        { ...PENDING, id: 'a', email: 'a@example.com', idp_status: 'provisioned' },
        { ...PENDING, id: 'b', email: 'b@example.com', idp_status: 'not_in_idp' },
        { ...PENDING, id: 'c', email: 'c@example.com', idp_status: 'external' },
        { ...PENDING, id: 'd', email: 'd@example.com' },
      ],
      total: 4,
    } as never)
    await renderModal()
    await waitFor(() => expect(screen.getByText('a@example.com')).toBeInTheDocument())

    expect(screen.getByText('IdP account')).toBeInTheDocument()
    expect(screen.getByText('No IdP account')).toBeInTheDocument()
    expect(screen.getByText('External IdP')).toBeInTheDocument()
    // Four invites, three badges: the one without a status renders nothing.
    expect(document.querySelectorAll('.idp-badge')).toHaveLength(3)
  })

  it('never puts a password in the list', async () => {
    // GET /invites must not carry one, and the row must not render one even if
    // a future backend regression put it there.
    vi.mocked(getOrgInvites).mockResolvedValue({
      invites: [{ ...PENDING, idp_status: 'provisioned', idp_temporary_password: 'leaked-secret' }],
      total: 1,
    } as never)
    await renderModal()
    await waitFor(() => expect(screen.getByText('ada@example.com')).toBeInTheDocument())
    expect(screen.queryByText(/leaked-secret/)).not.toBeInTheDocument()
  })
})

describe('the Google-flow install', () => {
  it('keeps its description copy untouched', async () => {
    // OIDC_ENABLED is false here. #984 must be invisible on a Google install.
    await renderModal()
    expect(
      screen.getByText(/sign in using their Google account/i)
    ).toBeInTheDocument()
    expect(screen.queryByText(/temporary password/i)).not.toBeInTheDocument()
  })
})
