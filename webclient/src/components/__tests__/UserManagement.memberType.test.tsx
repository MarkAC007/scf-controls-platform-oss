/**
 * UserManagement: the Type column renders, and it is editable by an admin only
 * (#822 phase 2, ISC-42).
 *
 * The admin gate here is a **display** decision, not an access control. The
 * API refuses a non-admin's PATCH with a 403 whatever this component renders
 * (backend ISC-30 covers that); hiding the select is a courtesy that keeps a
 * control that would fail off a viewer's screen. These tests assert what is on
 * screen, and nothing here should be read as evidence of a security boundary.
 *
 * The non-admin branch is worth its own test for a reason that is easy to miss:
 * it does not hide the value, it renders it as static text. A viewer who saw a
 * contractor badge elsewhere in the app and an empty Type column here would
 * reasonably conclude the badge came from nowhere.
 *
 * The write assertion — ``updateOrgMember`` called with member_type and NOT
 * role — is the one that costs something if it regresses. This screen holds a
 * copy of every member's role from whenever it last loaded; sending that back
 * alongside the type would silently revert a role another admin had changed in
 * between, and nothing on screen would show it happening.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import UserManagement from '../UserManagement'
import { apiClient, updateOrgMember } from '../../data/apiClient'
import { useIsOrgAdmin } from '../../hooks/useIsOrgAdmin'

vi.mock('../../data/apiClient', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
  updateOrgMember: vi.fn(() => Promise.resolve()),
  // Imported by InviteUserModal, which this component imports. Present so the
  // module graph resolves; the modal is not opened by these tests.
  getOrgInvites: vi.fn(() => Promise.resolve({ invites: [], total: 0 })),
  cancelOrgInvite: vi.fn(),
  getOrgMemberSummaries: vi.fn(() => Promise.resolve([])),
}))

// Mocked at the module boundary rather than by building an AuthContext: the
// question under test is "what does this render for an admin and for a
// non-admin", and how the component learns which one it has is
// useIsOrgAdmin's business, tested where it lives.
vi.mock('../../hooks/useIsOrgAdmin', () => ({
  useIsOrgAdmin: vi.fn(() => false),
}))

const ORG_ID = 'org-1'

const CONTRACTOR = {
  id: 'm1',
  organization_id: ORG_ID,
  user_id: 'u1',
  role: 'editor',
  member_type: 'external_contractor',
  joined_at: '2026-08-01T00:00:00',
  user: { id: 'u1', email: 'ada@example.com', display_name: 'Ada Lovelace' },
}

const STAFF = {
  id: 'm2',
  organization_id: ORG_ID,
  user_id: 'u2',
  role: 'admin',
  member_type: 'internal',
  joined_at: '2026-08-02T00:00:00',
  user: { id: 'u2', email: 'grace@example.com', display_name: 'Grace Hopper' },
}

function rowFor(name: string): HTMLElement {
  return screen.getByText(name).closest('tr') as HTMLElement
}

/**
 * The cell under a named column, for a named member.
 *
 * The word "Contractor" appears up to three times in one row — the badge
 * beside the name, the label on an admin's <option>, and the static text a
 * non-admin sees. A bare ``getByText('Contractor')`` therefore matches rows
 * that carry no badge and matches twice in the row that does, so every
 * assertion about the value has to say WHERE it expects to find it. The
 * column index is read from the header rather than hardcoded, so reordering
 * the columns moves these assertions with them instead of quietly pointing
 * them at the wrong cell.
 */
function cellFor(name: string, column: string): HTMLElement {
  const index = screen
    .getAllByRole('columnheader')
    .findIndex(header => header.textContent?.trim() === column)
  if (index < 0) {
    throw new Error(
      `no "${column}" column in the members table — the header was renamed or ` +
        'removed, and every assertion scoped to it is now testing nothing'
    )
  }
  return within(rowFor(name)).getAllByRole('cell')[index]
}

/** The badge, found the way a screen reader finds it. */
function badgeFor(name: string): HTMLElement | null {
  return within(cellFor(name, 'User')).queryByLabelText(
    `${name} is an external contractor`
  )
}

async function renderAs(admin: boolean, members = [CONTRACTOR, STAFF]) {
  vi.mocked(useIsOrgAdmin).mockReturnValue(admin)
  vi.mocked(apiClient.get).mockResolvedValue(members as never)
  render(<UserManagement organizationId={ORG_ID} />)
  await waitFor(() => expect(screen.getByText('Ada Lovelace')).toBeInTheDocument())
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('the Type column', () => {
  it('renders a Type header', async () => {
    await renderAs(true)
    expect(screen.getByRole('columnheader', { name: 'Type' })).toBeInTheDocument()
  })

  it('badges a contractor beside their name', async () => {
    await renderAs(true)
    expect(
      within(rowFor('Ada Lovelace')).getByLabelText(
        'Ada Lovelace is an external contractor'
      )
    ).toBeInTheDocument()
  })

  it('does not badge a permanent member', async () => {
    await renderAs(true)
    expect(badgeFor('Grace Hopper')).not.toBeInTheDocument()
    // Not just "no aria-label": nothing in the name cell calls them a
    // contractor at all. A badge that lost its accessible name would still
    // fail here.
    expect(
      within(cellFor('Grace Hopper', 'User')).queryByText(/contractor/i)
    ).not.toBeInTheDocument()
  })

  it('renders nothing for a member whose type the server did not send', async () => {
    // Older servers, and any row written before the column existed, come back
    // without the field. The honest reading is "internal", not "unknown".
    await renderAs(true, [{ ...CONTRACTOR, member_type: undefined } as never])
    expect(badgeFor('Ada Lovelace')).not.toBeInTheDocument()
    expect(
      within(cellFor('Ada Lovelace', 'User')).queryByText(/contractor/i)
    ).not.toBeInTheDocument()
  })
})

describe('who may change it', () => {
  it('gives an admin a selector showing the current value', async () => {
    await renderAs(true)
    const select = within(rowFor('Ada Lovelace')).getByLabelText(
      'Member type for Ada Lovelace'
    ) as HTMLSelectElement
    expect(select.value).toBe('external_contractor')
  })

  it('gives a non-admin no selector', async () => {
    await renderAs(false)
    expect(
      within(rowFor('Ada Lovelace')).queryByLabelText('Member type for Ada Lovelace')
    ).not.toBeInTheDocument()
  })

  it('still SHOWS a non-admin the value, as text', async () => {
    // Hiding it would make the badge elsewhere in the app look unexplained.
    await renderAs(false)
    expect(cellFor('Ada Lovelace', 'Type')).toHaveTextContent('Contractor')
    expect(cellFor('Grace Hopper', 'Type')).toHaveTextContent('Internal')
  })

  it('does not gate the ROLE selector on member type', async () => {
    // ISC-21 at the surface: being a contractor must not cost you the ability
    // to be given any role. Both rows get the same three options.
    await renderAs(true)
    const contractorRoles = within(rowFor('Ada Lovelace')).getAllByRole('option')
      .map(o => (o as HTMLOptionElement).value)
    expect(contractorRoles).toEqual(expect.arrayContaining(['admin', 'editor', 'viewer']))
  })
})

describe('changing it', () => {
  it('sends member_type alone, never the role alongside it', async () => {
    const user = userEvent.setup()
    await renderAs(true)
    const select = within(rowFor('Grace Hopper')).getByLabelText(
      'Member type for Grace Hopper'
    )
    await user.selectOptions(select, 'external_contractor')

    await waitFor(() => expect(updateOrgMember).toHaveBeenCalled())
    expect(updateOrgMember).toHaveBeenCalledWith(ORG_ID, 'u2', {
      member_type: 'external_contractor',
    })
    // Explicitly: no role. This screen's copy of the role can be stale, and
    // posting it back would revert another admin's change with nothing on
    // screen to show for it.
    const [, , changes] = vi.mocked(updateOrgMember).mock.calls[0]
    expect(changes).not.toHaveProperty('role')
  })

  it('reloads the members list afterwards', async () => {
    // Without this the row keeps rendering the old value and the admin cannot
    // tell whether the change took.
    const user = userEvent.setup()
    await renderAs(true)
    const before = vi.mocked(apiClient.get).mock.calls.length
    await user.selectOptions(
      within(rowFor('Grace Hopper')).getByLabelText('Member type for Grace Hopper'),
      'external_contractor'
    )
    await waitFor(() =>
      expect(vi.mocked(apiClient.get).mock.calls.length).toBeGreaterThan(before)
    )
  })

  it('offers exactly the two legal values', async () => {
    // A third option here would render fine and be refused by the CHECK
    // constraint on the way in.
    await renderAs(true)
    const select = within(rowFor('Ada Lovelace')).getByLabelText(
      'Member type for Ada Lovelace'
    )
    const values = within(select).getAllByRole('option')
      .map(o => (o as HTMLOptionElement).value)
    expect(values).toEqual(['internal', 'external_contractor'])
  })
})
