import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import MaterialityPanel from '../MaterialityPanel'

const state = vi.hoisted(() => ({ admin: false, row: null as Record<string, unknown> | null, error: null as Error | null }))
const api = vi.hoisted(() => ({ setMateriality: vi.fn() }))

vi.mock('../../../hooks/useIsOrgAdmin', () => ({ useIsOrgAdmin: () => state.admin }))
vi.mock('../../../hooks/useScopedControlsQuery', () => ({
  useScopedControlsQuery: () => ({ isLoading: false, isError: !!state.error, error: state.error, data: { pages: [] } }),
  flattenScopedControlPages: () => ({ controls: state.row ? [state.row] : [] }),
}))
vi.mock('../../../data/apiClient', () => ({ setControlMateriality: api.setMateriality }))
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function renderPanel(onChange?: () => void) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  render(
    <QueryClientProvider client={client}>
      <MaterialityPanel organizationId="org-one" scfId="GOV-01" onChange={onChange} />
    </QueryClientProvider>,
  )
  return { invalidate }
}

const weighted = (weighting: number, material: boolean) => ({
  scf_id: 'GOV-01', is_scoped: true, control_weighting: weighting,
  material, material_source: 'weighting', material_reason: null,
})

describe('MaterialityPanel (PIG R6)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state.admin = false
    state.error = null
    state.row = weighted(10, true)
  })

  it('shows a weighting-10 control as material by default, read-only to a non-admin', () => {
    renderPanel()
    expect(screen.getByText('Material')).toBeInTheDocument()
    expect(screen.getByText(/catalogue weighting is 10/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Override/ })).not.toBeInTheDocument()
  })

  it('shows a lighter control as not material by default', () => {
    state.row = weighted(7, false)
    renderPanel()
    expect(screen.getByText('Not material')).toBeInTheDocument()
    expect(screen.getByText(/catalogue weighting is 7/)).toBeInTheDocument()
  })

  it("shows the control's own override with its reason", () => {
    state.row = { ...weighted(7, true), material_source: 'control', material_reason: 'Holds card data' }
    renderPanel()
    expect(screen.getByText('Material')).toBeInTheDocument()
    expect(screen.getByText(/Set on this control: Holds card data/)).toBeInTheDocument()
  })

  it('offers no override on a control without a scope record, which the API would refuse', () => {
    state.admin = true
    state.row = { ...weighted(10, true), is_scoped: false }
    renderPanel()
    expect(screen.getByText('Material')).toBeInTheDocument()
    expect(screen.getByText(/no scope record yet/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Override/ })).not.toBeInTheDocument()
  })

  it('sends the override with its trimmed reason, refreshes the list and tells the page', async () => {
    state.admin = true
    api.setMateriality.mockResolvedValue({})
    const onChange = vi.fn()
    const { invalidate } = renderPanel(onChange)
    fireEvent.click(screen.getByRole('button', { name: 'Override materiality' }))
    fireEvent.change(screen.getByLabelText('Override'), { target: { value: 'false' } })
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: '  Covered by the parent control  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.setMateriality).toHaveBeenCalledWith('org-one', 'GOV-01', false, 'Covered by the parent control'))
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1))
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['scoped-controls', 'org-one'] })
    // A compensating control's standing turns on its control being material (PIG R9).
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['compensating-controls', 'org-one'] })
  })

  it('refuses an override without a reason before calling the API', () => {
    state.admin = true
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Override materiality' }))
    fireEvent.change(screen.getByLabelText('Override'), { target: { value: 'true' } })
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: '   ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(screen.getByRole('alert')).toHaveTextContent('A materiality override needs a reason.')
    expect(api.setMateriality).not.toHaveBeenCalled()
  })

  it('clears an override that leaves the control material with nulls', async () => {
    state.admin = true
    state.row = { ...weighted(10, false), material_source: 'control', material_reason: 'Out of our hands' }
    api.setMateriality.mockResolvedValue({})
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Change override' }))
    expect(screen.getByLabelText('Override')).toHaveValue('false')
    expect(screen.getByLabelText('Reason')).toHaveValue('Out of our hands')
    fireEvent.change(screen.getByLabelText('Override'), { target: { value: '' } })
    expect(screen.queryByLabelText('Reason')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.setMateriality).toHaveBeenCalledWith('org-one', 'GOV-01', null, null))
  })

  it('asks for a reason when clearing makes a lighter control not material, and sends it', async () => {
    state.admin = true
    state.row = { ...weighted(7, true), material_source: 'control', material_reason: 'Crown jewel' }
    api.setMateriality.mockResolvedValue({})
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Change override' }))
    fireEvent.change(screen.getByLabelText('Override'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Clearing this override makes the control not material, so it needs a reason.')
    expect(api.setMateriality).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: ' System retired ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.setMateriality).toHaveBeenCalledWith('org-one', 'GOV-01', null, 'System retired'))
  })

  it('keeps Save inert until the form changes, so an untouched Save cannot erase a kept decision', () => {
    state.admin = true
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Override materiality' }))
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Override'), { target: { value: 'false' } })
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
  })

  it("never carries the old reason over to the opposite value", () => {
    state.admin = true
    state.row = { ...weighted(7, true), material_source: 'control', material_reason: 'Holds card data' }
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Change override' }))
    expect(screen.getByLabelText('Reason')).toHaveValue('Holds card data')
    fireEvent.change(screen.getByLabelText('Override'), { target: { value: 'false' } })
    expect(screen.getByLabelText('Reason')).toHaveValue('')
    fireEvent.change(screen.getByLabelText('Override'), { target: { value: '' } })
    expect(screen.getByLabelText('Reason')).toHaveValue('')
  })

  it('shows the API refusal message and keeps the form open', async () => {
    state.admin = true
    api.setMateriality.mockRejectedValue(new Error("Changing whether a control is material is recorded against an approving admin; use an admin's own credentials"))
    const onChange = vi.fn()
    renderPanel(onChange)
    fireEvent.click(screen.getByRole('button', { name: 'Override materiality' }))
    fireEvent.change(screen.getByLabelText('Override'), { target: { value: 'false' } })
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'Low exposure' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('recorded against an approving admin')
    expect(screen.getByLabelText('Override')).toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
  })

  it('shows a failed lookup as an error, never as not material', () => {
    state.admin = true
    state.error = new Error('Internal server error')
    state.row = null
    renderPanel()
    expect(screen.getByRole('alert')).toHaveTextContent('Internal server error')
    expect(screen.queryByText('Not material')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Override materiality' })).not.toBeInTheDocument()
  })
})
