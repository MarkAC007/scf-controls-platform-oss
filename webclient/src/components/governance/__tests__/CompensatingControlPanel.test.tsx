import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ConfirmProvider, answerConfirm } from '../../../__tests__/confirmDialog'
import CompensatingControlPanel from '../CompensatingControlPanel'
import type { CompensatingControl } from '../../../types'

const state = vi.hoisted(() => ({ editor: true }))
const api = vi.hoisted(() => ({
  listCompensatingControls: vi.fn(),
  createCompensatingControl: vi.fn(),
  updateCompensatingControl: vi.fn(),
  deleteCompensatingControl: vi.fn(),
  fetchScopedControlsPage: vi.fn(),
  getRisksForControl: vi.fn(),
  getCustomRiskDefinitions: vi.fn(),
  getCustomRiskControlLinks: vi.fn(),
  getRiskAssessments: vi.fn(),
}))

vi.mock('../../../data/apiClient', () => api)
vi.mock('../../../hooks/useHasOrgRole', () => ({ useIsOrgEditor: () => state.editor }))
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const make = (over: Partial<CompensatingControl>): CompensatingControl => ({
  id: 'x',
  organization_id: 'org-one',
  scoped_control_id: 'sc-1',
  scf_id: 'TESTCONTROL-01',
  domain: 'TESTCONTROL',
  gap_description: 'gap',
  compensating_control: 'compensation',
  effectiveness_rating: 'partial',
  risk_reduction_notes: null,
  risk_code: null,
  risk_acceptance_state: null,
  material: false,
  justified: true,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
  ...over,
})

const page = (material: boolean | null) => ({
  controls: material === null ? [] : [{ scf_id: 'TESTCONTROL-01', is_scoped: true, material }],
  total: material === null ? 0 : 1,
  offset: 0,
})

function renderPanel(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  render(
    <QueryClientProvider client={client}>
      <ConfirmProvider>
        <CompensatingControlPanel organizationId="org-one" scfId="TESTCONTROL-01" />
      </ConfirmProvider>
    </QueryClientProvider>,
  )
  return { client, invalidate }
}

describe('CompensatingControlPanel (PIG R9)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state.editor = true
    api.fetchScopedControlsPage.mockResolvedValue(page(false))
    api.getRisksForControl.mockResolvedValue({ scf_id: 'TESTCONTROL-01', catalog_risk_codes: [], assessments: [] })
    api.getCustomRiskDefinitions.mockResolvedValue([])
    api.getRiskAssessments.mockResolvedValue([])
    api.listCompensatingControls.mockResolvedValue([
      make({
        id: 'a',
        gap_description: 'No MFA on the legacy VPN',
        compensating_control: 'VPN reachable only from the office network',
        effectiveness_rating: 'full',
        risk_code: 'R-TEST-1',
        risk_acceptance_state: 'approved',
        material: true,
        justified: true,
        risk_reduction_notes: 'Cuts exposure to one site',
      }),
      make({ id: 'b', risk_code: 'R-TEST-2', risk_acceptance_state: 'expired', material: true, justified: false }),
      make({ id: 'c', risk_code: 'R-TEST-3', risk_acceptance_state: 'unapproved' }),
      make({ id: 'd', risk_code: 'R-TEST-4', risk_acceptance_state: null }),
      make({ id: 'e' }),
    ])
  })

  it("lists this control's compensating controls, asked of the server by control", async () => {
    renderPanel()
    const a = await screen.findByTestId('compensating-a')
    expect(api.listCompensatingControls).toHaveBeenCalledWith('org-one', { scf_id: 'TESTCONTROL-01' })
    expect(a).toHaveTextContent('No MFA on the legacy VPN')
    expect(a).toHaveTextContent('VPN reachable only from the office network')
    expect(a).toHaveTextContent('Full effectiveness')
    expect(within(a).getByRole('link', { name: 'R-TEST-1' })).toBeInTheDocument()
    expect(a).toHaveTextContent('Cuts exposure to one site')
  })

  it('never trusts a cached list: the server recomputes each standing on every read', async () => {
    // The app's client holds data fresh for five minutes; this list opts out.
    const { client } = renderPanel(
      new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 5 * 60 * 1000 } } }),
    )
    await screen.findByTestId('compensating-a')
    const [query] = client.getQueryCache().findAll({ queryKey: ['compensating-controls', 'org-one', 'list'] })
    expect(query.state.status).toBe('success')
    expect(query.isStale()).toBe(true)
  })

  it("shows each record's acceptance state as the server reports it now", async () => {
    renderPanel()
    expect(await screen.findByTestId('compensating-a')).toHaveTextContent('Risk accepted · approved')
    expect(screen.getByTestId('compensating-b')).toHaveTextContent('Acceptance expired')
    expect(screen.getByTestId('compensating-c')).toHaveTextContent('Accepted · not approved')
    expect(screen.getByTestId('compensating-e')).toHaveTextContent('No risk named')
  })

  it('says why a named risk has no acceptance: unlinked, unassessed or not accepted', async () => {
    api.listCompensatingControls.mockResolvedValue([
      make({ id: 'gone', risk_code: 'R-GONE' }),
      make({ id: 'raw', risk_code: 'R-RAW' }),
      make({ id: 'open', risk_code: 'R-OPEN' }),
    ])
    api.getRisksForControl.mockResolvedValue({
      scf_id: 'TESTCONTROL-01',
      catalog_risk_codes: ['R-RAW', 'R-OPEN'],
      assessments: [],
    })
    api.getRiskAssessments.mockResolvedValue([{ risk_code: 'R-OPEN', treatment_status: 'analysed' }])
    renderPanel()
    expect(await screen.findByText('Risk no longer linked')).toBeInTheDocument()
    expect(screen.getByTestId('compensating-gone')).toHaveTextContent('Risk no longer linked')
    expect(screen.getByTestId('compensating-raw')).toHaveTextContent('Risk not assessed')
    expect(screen.getByTestId('compensating-open')).toHaveTextContent('Risk not accepted')
  })

  it('claims no reason for a missing acceptance it could not check', async () => {
    api.getRisksForControl.mockRejectedValue(new Error('Server exploded'))
    renderPanel()
    const d = await screen.findByTestId('compensating-d')
    await waitFor(() => expect(api.getRisksForControl).toHaveBeenCalled())
    await waitFor(() => expect(d).toHaveTextContent('Not accepted, or no longer linked'))
    expect(d).not.toHaveTextContent('Risk not accepted')
    expect(d).not.toHaveTextContent('Risk no longer linked')
  })

  it("reads the control's linked risks only when a record's acceptance needs explaining", async () => {
    api.listCompensatingControls.mockResolvedValue([
      make({ id: 'a', risk_code: 'R-TEST-1', risk_acceptance_state: 'approved' }),
      make({ id: 'e' }),
    ])
    renderPanel()
    await screen.findByTestId('compensating-a')
    expect(api.getRisksForControl).not.toHaveBeenCalled()
    expect(api.getCustomRiskDefinitions).not.toHaveBeenCalled()
    expect(api.getRiskAssessments).not.toHaveBeenCalled()
  })

  it("takes materiality from the server's rows over the scope lookup", async () => {
    api.fetchScopedControlsPage.mockResolvedValue(page(false))
    renderPanel()
    await screen.findByTestId('compensating-a')
    expect(await screen.findByText(/This control is material: compensating for it needs/)).toBeInTheDocument()
  })

  it('names each row by its risk and a short reference, not its whole gap', async () => {
    api.listCompensatingControls.mockResolvedValue([
      make({ id: '0b1c2d3e-aaaa-bbbb', risk_code: 'R-TEST-1', gap_description: 'Same long gap text' }),
      make({ id: '9f8e7d6c-cccc-dddd', gap_description: 'Same long gap text' }),
    ])
    renderPanel()
    const first = await screen.findByTestId('compensating-0b1c2d3e-aaaa-bbbb')
    const second = screen.getByTestId('compensating-9f8e7d6c-cccc-dddd')
    expect(within(first).getByRole('button', { name: 'Edit compensating control R-TEST-1 (ref 0b1c2d3e)' })).toBeInTheDocument()
    expect(within(first).getByRole('button', { name: 'Withdraw compensating control R-TEST-1 (ref 0b1c2d3e)' })).toBeInTheDocument()
    expect(within(second).getByRole('button', { name: 'Edit compensating control with no risk (ref 9f8e7d6c)' })).toBeInTheDocument()
    expect(within(second).getByRole('button', { name: 'Withdraw compensating control with no risk (ref 9f8e7d6c)' })).toBeInTheDocument()
    expect(first).toHaveTextContent('Ref 0b1c2d3e')
    expect(screen.queryByRole('button', { name: /Same long gap text/ })).not.toBeInTheDocument()
  })

  it('keeps each withdrawal busy, and its error, on its own row', async () => {
    const pending: Record<string, { resolve: () => void; reject: (e: Error) => void }> = {}
    api.deleteCompensatingControl.mockImplementation(
      (_org: string, id: string) =>
        new Promise<void>((resolve, reject) => {
          pending[id] = { resolve, reject }
        }),
    )
    renderPanel()
    const a = await screen.findByTestId('compensating-a')
    const c = screen.getByTestId('compensating-c')
    fireEvent.click(within(a).getByRole('button', { name: /Withdraw/ }))
    await answerConfirm(true)
    fireEvent.click(within(c).getByRole('button', { name: /Withdraw/ }))
    await answerConfirm(true)
    await waitFor(() => expect(api.deleteCompensatingControl).toHaveBeenCalledTimes(2))
    expect(within(a).getByRole('button', { name: /Withdraw/ })).toBeDisabled()
    expect(within(c).getByRole('button', { name: /Withdraw/ })).toBeDisabled()

    pending.a.reject(new Error('refused a'))
    expect(await within(a).findByRole('alert')).toHaveTextContent('refused a')
    expect(within(c).getByRole('button', { name: /Withdraw/ })).toBeDisabled()
    expect(within(c).getByRole('button', { name: /Withdraw/ })).toHaveTextContent('Withdrawing…')

    pending.c.reject(new Error('refused c'))
    expect(await within(c).findByRole('alert')).toHaveTextContent('refused c')
    expect(within(a).getByRole('alert')).toHaveTextContent('refused a')
  })

  it('flags a record its material control no longer justifies, and only that one', async () => {
    renderPanel()
    const b = await screen.findByTestId('compensating-b')
    expect(b).toHaveTextContent('Not justified')
    expect(b).toHaveTextContent(/needs an accepted risk linked to it with an approval in force/)
    expect(screen.getByTestId('compensating-a')).not.toHaveTextContent('Not justified')
    expect(screen.getByTestId('compensating-e')).not.toHaveTextContent('Not justified')
  })

  it('says when the control is material that compensating needs an approved acceptance', async () => {
    api.fetchScopedControlsPage.mockResolvedValue(page(true))
    api.listCompensatingControls.mockResolvedValue([])
    renderPanel()
    expect(await screen.findByText('No compensating controls for this control.')).toBeInTheDocument()
    expect(await screen.findByText(/This control is material: compensating for it needs/)).toBeInTheDocument()
  })

  it('withdraws a record only after confirmation, then refreshes the list', async () => {
    api.deleteCompensatingControl.mockResolvedValue(undefined)
    const { invalidate } = renderPanel()
    const a = await screen.findByTestId('compensating-a')
    const withdraw = within(a).getByRole('button', { name: /Withdraw/ })

    fireEvent.click(withdraw)
    expect(await answerConfirm(false)).toBe(
      'Withdraw this compensating control for TESTCONTROL-01? It is deleted: only the audit log keeps what it said.',
    )
    expect(api.deleteCompensatingControl).not.toHaveBeenCalled()

    fireEvent.click(withdraw)
    const message = await answerConfirm(true)
    await waitFor(() => expect(api.deleteCompensatingControl).toHaveBeenCalledWith('org-one', 'a'))
    expect(message).toMatch(/audit log/)
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['compensating-controls', 'org-one'] }))
  })

  it("shows a refused withdrawal (a viewer key's 403) on the record's row", async () => {
    api.deleteCompensatingControl.mockRejectedValue(new Error('Insufficient role: editor required'))
    renderPanel()
    const a = await screen.findByTestId('compensating-a')
    fireEvent.click(within(a).getByRole('button', { name: /Withdraw/ }))
    await answerConfirm(true)
    expect(await within(a).findByRole('alert')).toHaveTextContent('Insufficient role: editor required')
  })

  it('opens the record form fixed to this control, and the edit form on a row', async () => {
    renderPanel()
    await screen.findByTestId('compensating-a')
    fireEvent.click(screen.getByRole('button', { name: 'Record compensating control' }))
    expect(screen.getByRole('dialog', { name: 'Record compensating control' })).toHaveTextContent('TESTCONTROL-01')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    fireEvent.click(within(screen.getByTestId('compensating-a')).getByRole('button', { name: /Edit/ }))
    expect(screen.getByRole('dialog', { name: 'Edit compensating control' })).toBeInTheDocument()
    expect(screen.getByLabelText('Gap')).toHaveValue('No MFA on the legacy VPN')
  })

  it('offers a viewer nothing to change', async () => {
    state.editor = false
    renderPanel()
    await screen.findByTestId('compensating-a')
    expect(screen.queryByRole('button', { name: 'Record compensating control' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Withdraw/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Edit/ })).not.toBeInTheDocument()
    expect(screen.getByText(/Only an editor or admin can record or change/)).toBeInTheDocument()
  })

  it('says so when the list cannot be loaded', async () => {
    api.listCompensatingControls.mockRejectedValue(new Error('Server exploded'))
    renderPanel()
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the compensating controls: Server exploded')
  })
})
