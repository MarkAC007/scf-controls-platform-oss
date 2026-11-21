// SPDX-License-Identifier: AGPL-3.0-only
/**
 * UserManagement: Invite, the role selector and Remove are an admin's (#1273).
 *
 * Every one of those routes is admin-only on the API. Only the member-type
 * selector was gated before; an editor or a viewer could open the invite form
 * or pick a role and learn of the 403 afterwards.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import UserManagement from '../UserManagement'
import { ConfirmProvider } from '../../contexts/ConfirmContext'
import { apiClient } from '../../data/apiClient'
import { useIsOrgAdmin } from '../../hooks/useIsOrgAdmin'

vi.mock('../../data/apiClient', () => ({
  apiClient: { get: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  updateOrgMember: vi.fn(),
  getOrgInvites: vi.fn(() => Promise.resolve({ invites: [] })),
  cancelOrgInvite: vi.fn(),
  inviteOrgAuditor: vi.fn(),
  listOrgAuditors: vi.fn(() => Promise.resolve([])),
  disableOrgAuditor: vi.fn(),
}))
// One switch: this screen asks only "is the viewer an admin", so an editor and
// a viewer are the same case here. The ranks themselves are useIsOrgAdmin's business.
vi.mock('../../hooks/useIsOrgAdmin', () => ({ useIsOrgAdmin: vi.fn(() => false) }))

const ORG_ID = 'org-1'
const member = (id: string, name: string, memberRole: string) => ({
  id: `m-${id}`, organization_id: ORG_ID, user_id: id, role: memberRole, member_type: 'internal',
  joined_at: '2026-08-01T00:00:00', user: { id, email: `${id}@example.com`, display_name: name },
})
const MEMBERS = [member('u1', 'Alice Admin', 'admin'), member('u2', 'Bob Builder', 'editor'), member('u3', 'Vera Viewer', 'viewer')]

async function renderAs(admin: boolean) {
  vi.mocked(useIsOrgAdmin).mockReturnValue(admin)
  vi.mocked(apiClient.get).mockResolvedValue(MEMBERS as never)
  render(<UserManagement organizationId={ORG_ID} />, { wrapper: ConfirmProvider })
  await waitFor(() => expect(screen.getByText('Alice Admin')).toBeInTheDocument())
}

beforeEach(() => vi.clearAllMocks())

describe('UserManagement role gating', () => {
  it.each(['viewer', 'editor'])('offers a %s no Invite or Remove, and a role they can read but not change', async () => {
    await renderAs(false)

    expect(document.querySelector('.users-invite-btn')).toBeNull()
    expect(screen.queryByTitle('Remove from organization')).not.toBeInTheDocument()
    const roles = Array.from(document.querySelectorAll<HTMLSelectElement>('select.role-select'))
    expect(roles.map(s => s.value)).toEqual(['admin', 'editor', 'viewer'])
    roles.forEach(select => expect(select).toBeDisabled())
    expect(document.querySelector('select.member-type-select')).toBeNull()
  })

  it('offers an admin Invite, live role selectors and Remove on every row', async () => {
    await renderAs(true)

    expect(screen.getByRole('button', { name: /invite user/i })).toBeEnabled()
    expect(screen.getAllByTitle('Remove from organization')).toHaveLength(MEMBERS.length)
    const roles = Array.from(document.querySelectorAll<HTMLSelectElement>('select.role-select'))
    expect(roles).toHaveLength(MEMBERS.length)
    roles.forEach(select => expect(select).toBeEnabled())
    expect(document.querySelectorAll('select.member-type-select')).toHaveLength(MEMBERS.length)
  })
})
