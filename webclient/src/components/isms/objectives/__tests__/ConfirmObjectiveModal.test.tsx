// SPDX-License-Identifier: AGPL-3.0-only
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ConfirmObjectiveModal from '../ConfirmObjectiveModal'
import { metric, objective, ORG, reading } from './fixtures'

const api = vi.hoisted(() => ({ confirmSecurityObjective: vi.fn() }))
vi.mock('../../../../data/securityObjectivesApi', () => api)
const core = vi.hoisted(() => ({ listTeams: vi.fn(), getOrgMembers: vi.fn() }))
vi.mock('../../../../data/apiClient', () => core)
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const PROPOSED = objective({
  lifecycle_status: 'proposed', owning_team_id: null, owning_team_name: null,
  metric: metric({ target_value: null, warn_value: null }),
  reading: reading({ status: 'proposed', target_label: '' }),
})
const MILESTONE = objective({
  lifecycle_status: 'proposed', owning_team_id: 'team-ops', objective_type: 'achieve', due_date: null,
  metric: metric({ kind: 'milestone', unit: null, direction: null, target_value: null }),
  reading: reading({ status: 'proposed' }),
})

let client: QueryClient
function renderModal(o = PROPOSED, onClose = vi.fn()) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(
    <QueryClientProvider client={client}>
      <ConfirmObjectiveModal organizationId={ORG} objective={o} onClose={onClose} />
    </QueryClientProvider>,
  )
  return onClose
}

describe('ConfirmObjectiveModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    core.listTeams.mockResolvedValue([{ id: 'team-ops', name: 'Security Operations' }])
    api.confirmSecurityObjective.mockResolvedValue(objective())
  })

  it('M3: puts initial focus on the team select while the teams are still loading', () => {
    core.listTeams.mockReturnValue(new Promise(() => {}))
    renderModal()
    const select = screen.getByLabelText('Responsible team')
    expect(select).toBeEnabled()
    expect(select).toHaveFocus()
  })

  it('is blocked without a team', async () => {
    renderModal()
    await screen.findByRole('option', { name: 'Security Operations' })
    expect(screen.getByText('Choose a responsible team before confirming')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirm objective' })).toBeDisabled()
  })

  it('is blocked without a target on a live metric', async () => {
    renderModal()
    await screen.findByRole('option', { name: 'Security Operations' })
    fireEvent.change(screen.getByLabelText('Responsible team'), { target: { value: 'team-ops' } })
    expect(screen.getByText('Set a target before confirming')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirm objective' })).toBeDisabled()
  })

  it('needs a completion date for a milestone', async () => {
    renderModal(MILESTONE)
    expect(screen.getByText('Set a completion date before confirming')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Complete by'), { target: { value: '2027-03-31' } })
    expect(screen.getByRole('button', { name: 'Confirm objective' })).toBeEnabled()
  })

  it('sends only the keys that were set, then refreshes and closes', async () => {
    const onClose = renderModal()
    await screen.findByRole('option', { name: 'Security Operations' })
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    fireEvent.change(screen.getByLabelText('Responsible team'), { target: { value: 'team-ops' } })
    fireEvent.change(screen.getByLabelText(/^Target/), { target: { value: '90' } })
    fireEvent.click(screen.getByRole('button', { name: 'Confirm objective' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.confirmSecurityObjective).toHaveBeenCalledWith(ORG, 'obj-1', { owning_team_id: 'team-ops', target_value: 90 })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['security-objectives', ORG] })
  })

  it('shows a refusal from the server', async () => {
    api.confirmSecurityObjective.mockRejectedValue(new Error('Choose a responsible team before confirming'))
    const onClose = renderModal(MILESTONE)
    fireEvent.change(screen.getByLabelText('Complete by'), { target: { value: '2027-03-31' } })
    fireEvent.click(screen.getByRole('button', { name: 'Confirm objective' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Choose a responsible team before confirming')
    expect(onClose).not.toHaveBeenCalled()
  })
})
