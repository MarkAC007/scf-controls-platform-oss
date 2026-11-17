// SPDX-License-Identifier: AGPL-3.0-only
/**
 * InviteUserModal: the "External auditor" choice. It is not a membership role:
 * it posts to the organisation's auditor list, never to the membership invite
 * endpoint (which keeps the email-domain rule), and the Employment Type
 * control disappears because an auditor has no membership to label.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import InviteUserModal from '../InviteUserModal'
import { apiClient, inviteOrgAuditor } from '../../data/apiClient'

vi.mock('../../data/apiClient', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(() => Promise.resolve({})), patch: vi.fn(), delete: vi.fn() },
  getOrgInvites: vi.fn(() => Promise.resolve({ invites: [], total: 0 })),
  cancelOrgInvite: vi.fn(),
  inviteOrgAuditor: vi.fn(),
}))

const ORG_ID = 'org-1'

async function renderModal() {
  render(<InviteUserModal organizationId={ORG_ID} onClose={vi.fn()} onInviteSent={vi.fn()} />)
  await waitFor(() => expect(screen.queryByText(/loading pending invites/i)).not.toBeInTheDocument())
}

beforeEach(() => { vi.clearAllMocks() })

describe('inviting an external auditor', () => {
  it('posts to the auditor list, not the membership invite', async () => {
    vi.mocked(inviteOrgAuditor).mockResolvedValue({ idp_temporary_password: null } as never)
    await renderModal()
    await userEvent.type(screen.getByLabelText(/email address/i), 'lead@audit-firm.example')
    await userEvent.selectOptions(screen.getByLabelText(/role/i), 'auditor')
    await userEvent.type(screen.getByLabelText(/audit firm/i), 'Audit Firm')
    await userEvent.click(screen.getByRole('button', { name: /send invitation/i }))
    await waitFor(() => expect(inviteOrgAuditor).toHaveBeenCalledWith(ORG_ID, {
      email: 'lead@audit-firm.example', display_name: undefined, firm: 'Audit Firm', message: undefined,
    }))
    expect(apiClient.post).not.toHaveBeenCalled()
    expect(await screen.findByText(/is on your external auditor list/i)).toBeInTheDocument()
  })

  it('hides Employment Type and explains the scope', async () => {
    await renderModal()
    expect(screen.getByLabelText(/employment type/i)).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText(/role/i), 'auditor')
    expect(screen.queryByLabelText(/employment type/i)).not.toBeInTheDocument()
    expect(screen.getByTestId('auditor-role-note')).toHaveTextContent(/takes no seat/i)
  })

  it('shows a one-time password when the sign-in account was created', async () => {
    vi.mocked(inviteOrgAuditor).mockResolvedValue({ idp_temporary_password: 'Tmp-123' } as never)
    await renderModal()
    await userEvent.type(screen.getByLabelText(/email address/i), 'lead@audit-firm.example')
    await userEvent.selectOptions(screen.getByLabelText(/role/i), 'auditor')
    await userEvent.click(screen.getByRole('button', { name: /send invitation/i }))
    expect(await screen.findByTestId('idp-temp-password')).toHaveTextContent('Tmp-123')
  })
})
