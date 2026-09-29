/**
 * DeleteOrganizationDialog + the consultant dashboard wiring behind it.
 *
 * What these tests defend:
 *   - the delete button is offered only when the dashboard has a handler;
 *   - the confirm button stays disabled until the organisation's exact name
 *     is typed AND the data-loss acknowledgement is ticked (both are required
 *     by the API, so the dialog must not let anyone think a cheaper door
 *     exists);
 *   - a failed delete keeps the dialog open with the error shown;
 *   - the dashboard passes the typed name through to onDeleteOrg.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import ConsultantDashboard from '../ConsultantDashboard'
import ClientCard from '../ClientCard'
import DeleteOrganizationDialog from '../DeleteOrganizationDialog'
import type { ClientSummary } from '../../../types'

function makeClient(overrides: Partial<ClientSummary> = {}): ClientSummary {
  return {
    organization_id: 'org-1',
    organization_name: 'Odin Vision',
    awaiting_admin: false,
    framework_readiness_percent: 78,
    controls_implemented: 214,
    controls_total: 288,
    controls_in_progress: 41,
    controls_at_risk: 0,
    evidence_tracked: 142,
    evidence_total: 198,
    last_activity_date: new Date().toISOString(),
    last_activity_by: 'Mark',
    primary_framework: 'ISO 27001',
    status: 'active',
    ...overrides,
  }
}

describe('DeleteOrganizationDialog', () => {
  it('enables the confirm button only when the exact name is typed and the loss is acknowledged', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    render(<DeleteOrganizationDialog organizationName="Odin Vision" onConfirm={onConfirm} onClose={vi.fn()} />)

    const confirm = screen.getByRole('button', { name: /delete organisation and all its data/i })
    const name = screen.getByLabelText(/type .* to confirm/i)
    const ack = screen.getByRole('checkbox')

    expect(confirm).toBeDisabled()

    await userEvent.type(name, 'odin vision')
    await userEvent.click(ack)
    // Case differs: still not a match.
    expect(confirm).toBeDisabled()

    await userEvent.clear(name)
    await userEvent.type(name, 'Odin Vision')
    expect(confirm).toBeEnabled()

    await userEvent.click(ack)
    expect(confirm).toBeDisabled()

    await userEvent.click(ack)
    await userEvent.click(confirm)
    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith('Odin Vision'))
  })

  it('keeps the dialog open and shows the error when the delete fails', async () => {
    const onConfirm = vi.fn().mockRejectedValue(new Error('An import is still running'))
    const onClose = vi.fn()
    render(<DeleteOrganizationDialog organizationName="Odin Vision" onConfirm={onConfirm} onClose={onClose} />)

    await userEvent.type(screen.getByLabelText(/type .* to confirm/i), 'Odin Vision')
    await userEvent.click(screen.getByRole('checkbox'))
    await userEvent.click(screen.getByRole('button', { name: /delete organisation and all its data/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('An import is still running')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
  })
})

describe('ClientCard — delete affordance', () => {
  it('renders no delete button without a handler', () => {
    render(<ClientCard client={makeClient()} />)
    expect(screen.queryByRole('button', { name: /delete organisation/i })).toBeNull()
  })

  it('hides delete for a client whose relationship is not active', () => {
    render(<ClientCard client={makeClient({ status: 'pending' })} isCurrentOrg={false} onDelete={vi.fn()} />)
    expect(screen.queryByRole('button', { name: /Delete organisation/ })).toBeNull()
  })

  it('offers delete when a handler is given and calls it with the client', async () => {
    const onDelete = vi.fn()
    render(<ClientCard client={makeClient()} onDelete={onDelete} />)
    await userEvent.click(screen.getByRole('button', { name: 'Delete organisation Odin Vision' }))
    expect(onDelete).toHaveBeenCalledWith(expect.objectContaining({ organization_id: 'org-1' }))
  })
})

describe('ConsultantDashboard — delete organisation flow', () => {
  it('opens the dialog from a card and forwards the org id and typed name to onDeleteOrg', async () => {
    const onDeleteOrg = vi.fn().mockResolvedValue(undefined)
    render(<ConsultantDashboard clients={[makeClient()]} onDeleteOrg={onDeleteOrg} />)

    await userEvent.click(screen.getByRole('button', { name: 'Delete organisation Odin Vision' }))
    expect(screen.getByRole('dialog', { name: /delete organisation/i })).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText(/type .* to confirm/i), 'Odin Vision')
    await userEvent.click(screen.getByRole('checkbox'))
    await userEvent.click(screen.getByRole('button', { name: /delete organisation and all its data/i }))

    await waitFor(() => expect(onDeleteOrg).toHaveBeenCalledWith('org-1', 'Odin Vision'))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('shows no delete buttons when the dashboard has no delete handler', () => {
    render(<ConsultantDashboard clients={[makeClient()]} />)
    expect(screen.queryByRole('button', { name: /delete organisation/i })).toBeNull()
  })
})
