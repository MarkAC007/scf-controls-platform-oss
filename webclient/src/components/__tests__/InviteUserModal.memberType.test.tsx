/**
 * InviteUserModal: choosing an employment type for somebody who does not have
 * a membership yet (#822 phase 2, ISC-42).
 *
 * The assertion that earns its place here is the one about the **request**,
 * not the one about the widget. A selector that renders "Internal", defaults
 * correctly on screen and then omits the field from the POST looks entirely
 * healthy — the API supplies 'internal' itself, so the invite still works and
 * the only symptom is that nobody can ever be invited as a contractor. So
 * every test below that cares about a value follows it to the wire.
 *
 * Note the shape: `member_type` travels in the JSON **body** here, where the
 * PATCH on an existing member takes it as a query parameter. Two endpoints,
 * two shapes, on purpose. `sends the type in the body, not the query string`
 * pins that down, because copying the members-screen idiom over would produce
 * a request the invite endpoint ignores rather than rejects.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
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

/**
 * Render, and wait for the pending-invites fetch the modal fires on mount to
 * settle. Without the wait every test races that state update and React
 * complains about it outside act(), which buries a real failure in noise.
 */
async function renderModal() {
  render(
    <InviteUserModal
      organizationId={ORG_ID}
      onClose={vi.fn()}
      onInviteSent={vi.fn()}
    />
  )
  await waitFor(() =>
    expect(screen.queryByText(/loading pending invites/i)).not.toBeInTheDocument()
  )
}

/** The employment-type control, found by its visible label. */
function typeSelect(): HTMLSelectElement {
  return screen.getByLabelText(/employment type/i) as HTMLSelectElement
}

/** Fill in the minimum a submit needs, then submit. */
async function invite(user: ReturnType<typeof userEvent.setup>, email = 'new@example.com') {
  await user.type(screen.getByLabelText(/email address/i), email)
  await user.click(screen.getByRole('button', { name: /send invitation/i }))
  await waitFor(() => expect(apiClient.post).toHaveBeenCalled())
  return vi.mocked(apiClient.post).mock.calls[0]
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getOrgInvites).mockResolvedValue({ invites: [], total: 0 } as never)
  vi.mocked(apiClient.post).mockResolvedValue({} as never)
})

describe('the employment type control', () => {
  it('is on the form, labelled', async () => {
    await renderModal()
    expect(typeSelect()).toBeInTheDocument()
  })

  it('starts on internal', async () => {
    // The API defaults to 'internal' too. Starting anywhere else would mean
    // an admin who ignores this control changes behaviour by ignoring it.
    await renderModal()
    expect(typeSelect().value).toBe('internal')
  })

  it('offers exactly the two legal values', async () => {
    await renderModal()
    const values = within(typeSelect())
      .getAllByRole('option')
      .map(option => (option as HTMLOptionElement).value)
    expect(values).toEqual(['internal', 'external_contractor'])
  })

  it('says the type grants nothing', async () => {
    // ISC-21 where a user can actually read it. If this help text goes, the
    // control starts to look like a permission and gets used as one.
    await renderModal()
    const help = screen.getByText(/grants and\s+restricts nothing/i)
    expect(help).toBeInTheDocument()
  })

  it('does not narrow the roles a contractor may be given', async () => {
    // Being a contractor must not cost you a role. Same three either way.
    const user = userEvent.setup()
    await renderModal()
    const roleValues = () =>
      within(screen.getByLabelText(/^role/i))
        .getAllByRole('option')
        .map(option => (option as HTMLOptionElement).value)
    const before = roleValues()
    await user.selectOptions(typeSelect(), 'external_contractor')
    expect(roleValues()).toEqual(before)
    expect(before).toEqual(expect.arrayContaining(['admin', 'editor', 'viewer']))
  })
})

describe('what reaches the API', () => {
  it('sends internal when the admin leaves it alone', async () => {
    // The default has to travel, not merely display. Omitting the field would
    // produce a working invite and an unreachable contractor path.
    const user = userEvent.setup()
    await renderModal()
    const [, body] = await invite(user)
    expect(body).toMatchObject({ member_type: 'internal' })
  })

  it('sends the contractor value when it is chosen', async () => {
    const user = userEvent.setup()
    await renderModal()
    await user.selectOptions(typeSelect(), 'external_contractor')
    const [, body] = await invite(user)
    expect(body).toMatchObject({ member_type: 'external_contractor' })
  })

  it('sends the type in the body, not the query string', async () => {
    // The members PATCH takes ?member_type=. This endpoint does not, and a
    // query parameter it does not read is ignored in silence.
    const user = userEvent.setup()
    await renderModal()
    await user.selectOptions(typeSelect(), 'external_contractor')
    const [url, body] = await invite(user)
    expect(url).toBe(`/organizations/${ORG_ID}/invite`)
    expect(String(url)).not.toContain('member_type')
    expect(body).toHaveProperty('member_type')
  })

  it('still sends the role alongside it', async () => {
    // The two fields are independent, which means adding one must not have
    // displaced the other.
    const user = userEvent.setup()
    await renderModal()
    await user.selectOptions(screen.getByLabelText(/^role/i), 'admin')
    await user.selectOptions(typeSelect(), 'external_contractor')
    const [, body] = await invite(user)
    expect(body).toMatchObject({ role: 'admin', member_type: 'external_contractor' })
  })
})

describe('the confirmation', () => {
  it('repeats the contractor choice back', async () => {
    // The admin's last chance to notice they picked the wrong one.
    const user = userEvent.setup()
    await renderModal()
    await user.selectOptions(typeSelect(), 'external_contractor')
    await invite(user)
    await waitFor(() => expect(screen.getByText(/invitation sent/i)).toBeInTheDocument())
    expect(screen.getByText(/\(contractor\)/i)).toBeInTheDocument()
  })

  it('does not call an internal invitee a contractor', async () => {
    const user = userEvent.setup()
    await renderModal()
    await invite(user)
    await waitFor(() => expect(screen.getByText(/invitation sent/i)).toBeInTheDocument())
    expect(screen.queryByText(/contractor/i)).not.toBeInTheDocument()
  })
})

describe('the pending list', () => {
  const PENDING = {
    id: 'i1',
    email: 'ada@example.com',
    role: 'editor',
    status: 'pending',
    created_at: '2026-08-01T00:00:00',
    expires_at: '2026-08-08T00:00:00',
  }

  it('badges a pending contractor invite', async () => {
    // The type is chosen at invite time and only takes effect on acceptance,
    // so this row is the only place it can be seen in between.
    vi.mocked(getOrgInvites).mockResolvedValue({
      invites: [{ ...PENDING, member_type: 'external_contractor' }],
      total: 1,
    } as never)
    await renderModal()
    await waitFor(() =>
      expect(
        screen.getByLabelText('ada@example.com is an external contractor')
      ).toBeInTheDocument()
    )
  })

  it('leaves a pending internal invite unbadged', async () => {
    vi.mocked(getOrgInvites).mockResolvedValue({
      invites: [{ ...PENDING, member_type: 'internal' }],
      total: 1,
    } as never)
    await renderModal()
    await waitFor(() => expect(screen.getByText('ada@example.com')).toBeInTheDocument())
    expect(
      screen.queryByLabelText('ada@example.com is an external contractor')
    ).not.toBeInTheDocument()
    expect(screen.queryByText(/^contractor$/i)).not.toBeInTheDocument()
  })
})
