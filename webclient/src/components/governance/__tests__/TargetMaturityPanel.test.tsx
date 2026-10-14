import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TargetMaturityPanel from '../TargetMaturityPanel'

const state = vi.hoisted(() => ({ admin: false, row: null as Record<string, unknown> | null, error: null as Error | null }))
const api = vi.hoisted(() => ({ setTarget: vi.fn() }))

vi.mock('../../../hooks/useIsOrgAdmin', () => ({ useIsOrgAdmin: () => state.admin }))
vi.mock('../../../hooks/useScopedControlsQuery', () => ({
  useScopedControlsQuery: () => ({ isLoading: false, isError: !!state.error, error: state.error, data: { pages: [] } }),
  flattenScopedControlPages: () => ({ controls: state.row ? [state.row] : [] }),
}))
vi.mock('../../../data/apiClient', () => ({ setControlTargetMaturity: api.setTarget }))
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function renderPanel(onChange?: () => void) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  render(
    <QueryClientProvider client={client}>
      <TargetMaturityPanel organizationId="org-one" scfId="GOV-01" onChange={onChange} />
    </QueryClientProvider>,
  )
  return { invalidate }
}

describe('TargetMaturityPanel (PIG R5)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state.admin = false
    state.error = null
    state.row = { scf_id: 'GOV-01', is_scoped: true, target_maturity: 'L3', target_source: 'domain', target_maturity_reason: null }
  })

  it("shows an inherited domain target read-only to a non-admin", () => {
    renderPanel()
    expect(screen.getByText('L3 - Managed')).toBeInTheDocument()
    expect(screen.getByText(/Inherited from the GOV domain target/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Override target/ })).not.toBeInTheDocument()
  })

  it("shows the control's own override with its reason", () => {
    state.row = { scf_id: 'GOV-01', is_scoped: true, target_maturity: 'L5', target_source: 'control', target_maturity_reason: 'Crown jewel' }
    renderPanel()
    expect(screen.getByText('L5 - Optimized')).toBeInTheDocument()
    expect(screen.getByText(/Set on this control: Crown jewel/)).toBeInTheDocument()
  })

  it('says when neither the control nor its domain has a target', () => {
    state.row = { scf_id: 'GOV-01', is_scoped: true, target_maturity: null, target_source: null, target_maturity_reason: null }
    renderPanel()
    expect(screen.getByText('No target')).toBeInTheDocument()
    expect(screen.getByText(/Neither this control nor the GOV domain has a target/)).toBeInTheDocument()
  })

  it('offers no override on a control without a scope record, which the API would refuse', () => {
    state.admin = true
    state.row = { scf_id: 'GOV-01', is_scoped: false, target_maturity: null, target_source: null }
    renderPanel()
    expect(screen.getByText(/no scope record yet/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Override target/ })).not.toBeInTheDocument()
  })

  it('sends the level with its trimmed reason, refreshes the list and tells the page', async () => {
    state.admin = true
    api.setTarget.mockResolvedValue({})
    const onChange = vi.fn()
    const { invalidate } = renderPanel(onChange)
    fireEvent.click(screen.getByRole('button', { name: 'Override target' }))
    fireEvent.change(screen.getByLabelText('Target'), { target: { value: 'L4' } })
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: '  Holds payment data  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.setTarget).toHaveBeenCalledWith('org-one', 'GOV-01', 'L4', 'Holds payment data'))
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1))
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['scoped-controls', 'org-one'] })
  })

  it('refuses a level without a reason before calling the API', () => {
    state.admin = true
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Override target' }))
    fireEvent.change(screen.getByLabelText('Target'), { target: { value: 'L2' } })
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: '   ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(screen.getByRole('alert')).toHaveTextContent('A target override needs a reason.')
    expect(api.setTarget).not.toHaveBeenCalled()
  })

  it('clears an existing override with nulls', async () => {
    state.admin = true
    state.row = { scf_id: 'GOV-01', is_scoped: true, target_maturity: 'L5', target_source: 'control', target_maturity_reason: 'Crown jewel' }
    api.setTarget.mockResolvedValue({})
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Change override' }))
    expect(screen.getByLabelText('Target')).toHaveValue('L5')
    expect(screen.getByLabelText('Reason')).toHaveValue('Crown jewel')
    fireEvent.change(screen.getByLabelText('Target'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.setTarget).toHaveBeenCalledWith('org-one', 'GOV-01', null, null))
  })

  it('shows the API refusal message and keeps the form open', async () => {
    state.admin = true
    api.setTarget.mockRejectedValue(new Error('GOV-01 has no scope record in this organisation'))
    const onChange = vi.fn()
    renderPanel(onChange)
    fireEvent.click(screen.getByRole('button', { name: 'Override target' }))
    fireEvent.change(screen.getByLabelText('Target'), { target: { value: 'L1' } })
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'Low exposure' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('GOV-01 has no scope record in this organisation')
    expect(screen.getByLabelText('Target')).toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
  })

  it('shows a failed lookup as an error, never as no target', () => {
    state.admin = true
    state.error = new Error('Internal server error')
    state.row = null
    renderPanel()
    expect(screen.getByRole('alert')).toHaveTextContent('Internal server error')
    expect(screen.queryByText('No target')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Override target' })).not.toBeInTheDocument()
  })
})
