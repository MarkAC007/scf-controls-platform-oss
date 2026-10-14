import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DeficiencyPanel from '../DeficiencyPanel'
import type { Deficiency } from '../../../types'

const state = vi.hoisted(() => ({ editor: true }))
const api = vi.hoisted(() => ({
  listDeficiencies: vi.fn(),
  createDeficiency: vi.fn(),
  updateDeficiency: vi.fn(),
  getOrgMembers: vi.fn(),
  getRiskAssessments: vi.fn(),
  fetchScopedControlsPage: vi.fn(),
}))

vi.mock('../../../data/apiClient', () => api)
vi.mock('../../../hooks/useHasOrgRole', () => ({ useIsOrgEditor: () => state.editor }))
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const make = (over: Partial<Deficiency>): Deficiency => ({
  id: 'x',
  organization_id: 'org-one',
  title: 'x',
  priority: 'medium',
  status: 'open',
  scf_id: 'TESTCONTROL-01',
  auto_generated: false,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
  ...over,
})

function renderPanel(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const view = render(
    <QueryClientProvider client={client}>
      <DeficiencyPanel organizationId="org-one" scfId="TESTCONTROL-01" />
    </QueryClientProvider>,
  )
  return { client, invalidate, unmount: view.unmount }
}

describe('DeficiencyPanel (PIG R8)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state.editor = true
    api.listDeficiencies.mockResolvedValue([
      make({ id: 'a', title: 'Late gap', due_date: '2020-01-01' }),
      make({ id: 'b', title: 'Being fixed', status: 'in_progress' }),
      make({ id: 'c', title: 'Fixed', status: 'completed', retest_note: 'ok' }),
    ])
    api.getOrgMembers.mockResolvedValue([])
    api.getRiskAssessments.mockResolvedValue([])
  })

  it("lists this control's deficiencies, asked of the server by control, with counts", async () => {
    renderPanel()
    expect(await screen.findByText('Late gap')).toBeInTheDocument()
    expect(api.listDeficiencies).toHaveBeenCalledWith('org-one', { scf_id: 'TESTCONTROL-01' })
    expect(screen.getByTestId('control-deficiency-counts')).toHaveTextContent('2 open · 1 overdue · 1 closed')
    // On the control's own page its rows do not name or link the control again.
    expect(screen.queryByRole('link', { name: 'TESTCONTROL-01' })).not.toBeInTheDocument()
    expect(screen.getByTestId('deficiency-a')).not.toHaveTextContent('TESTCONTROL-01')
  })

  it('raises one against this control, fixed, and refreshes the lists', async () => {
    api.createDeficiency.mockResolvedValue(make({ id: 'new' }))
    const { invalidate } = renderPanel()
    await screen.findByText('Late gap')
    fireEvent.click(screen.getByRole('button', { name: 'Raise deficiency' }))
    expect(screen.queryByLabelText('Control')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'New gap' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Raise deficiency' }).slice(-1)[0])
    await waitFor(() =>
      expect(api.createDeficiency).toHaveBeenCalledWith('org-one', {
        title: 'New gap',
        priority: 'medium',
        scf_id: 'TESTCONTROL-01',
      }),
    )
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['deficiencies', 'org-one'] }))
  })

  it('offers a viewer no raise and says who can', async () => {
    state.editor = false
    renderPanel()
    await screen.findByText('Late gap')
    expect(screen.queryByRole('button', { name: 'Raise deficiency' })).not.toBeInTheDocument()
    expect(screen.getByText(/Only an editor or admin/)).toBeInTheDocument()
  })

  it('says when there are none', async () => {
    api.listDeficiencies.mockResolvedValue([])
    renderPanel()
    expect(await screen.findByText(/No deficiencies against this control/)).toBeInTheDocument()
  })

  it('shows a deficiency the server raised since the last visit (review MED-1)', async () => {
    // Setting the control to At Risk on the DETAILS tab raises one server-side;
    // coming back to GOVERNANCE must not show the cached "none".
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 5 * 60 * 1000 } } })
    api.listDeficiencies.mockResolvedValue([])
    const first = renderPanel(client)
    await screen.findByText(/No deficiencies against this control/)
    first.unmount()
    api.listDeficiencies.mockResolvedValue([make({ id: 'auto', title: 'Control TESTCONTROL-01 is at risk', source: 'control_at_risk' })])
    renderPanel(client)
    expect(await screen.findByText('Control TESTCONTROL-01 is at risk')).toBeInTheDocument()
  })

  it('keeps an open completion when a refresh empties the list (review MED-2)', async () => {
    const { client } = renderPanel()
    await screen.findByText('Late gap')
    fireEvent.click(screen.getByRole('button', { name: 'Complete "Late gap"' }))
    fireEvent.change(screen.getByLabelText('Retest note'), { target: { value: 'Retested' } })
    api.listDeficiencies.mockResolvedValue([])
    await client.refetchQueries({ queryKey: ['deficiencies', 'org-one'] })
    expect(await screen.findByText(/No deficiencies against this control/)).toBeInTheDocument()
    expect(screen.getByLabelText('Retest note')).toHaveValue('Retested')
  })

  it('keeps the list, with a warning, when a refresh fails (review MED-2)', async () => {
    const { client } = renderPanel()
    await screen.findByText('Late gap')
    api.listDeficiencies.mockRejectedValue(new Error('gateway timeout'))
    await client.refetchQueries({ queryKey: ['deficiencies', 'org-one'] })
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not refresh the deficiencies: gateway timeout')
    expect(screen.getByText('Late gap')).toBeInTheDocument()
  })

  it('says when they cannot be loaded', async () => {
    api.listDeficiencies.mockRejectedValue(new Error('list down'))
    renderPanel()
    expect(await screen.findByRole('alert')).toHaveTextContent('list down')
  })
})
