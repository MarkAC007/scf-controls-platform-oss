import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ObligationBasisPanel from '../ObligationBasisPanel'

const state = vi.hoisted(() => ({ admin: false, row: null as Record<string, unknown> | null, error: null as Error | null }))
const api = vi.hoisted(() => ({ setOverride: vi.fn() }))

vi.mock('../../../hooks/useIsOrgAdmin', () => ({ useIsOrgAdmin: () => state.admin }))
vi.mock('../../../hooks/useScopedControlsQuery', () => ({
  useScopedControlsQuery: () => ({ isLoading: false, isError: !!state.error, error: state.error, data: { pages: [] } }),
  flattenScopedControlPages: () => ({ controls: state.row ? [state.row] : [] }),
}))
vi.mock('../../../data/apiClient', () => ({ setControlBasisOverride: api.setOverride }))
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ObligationBasisPanel organizationId="org-one" scfId="GOV-01" />
    </QueryClientProvider>,
  )
}

describe('ObligationBasisPanel (PIG R2)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state.admin = false
    state.error = null
    state.row = { scf_id: 'GOV-01', obligation_basis: 'mcr', basis_source: 'framework', basis_override_reason: null }
  })

  it('shows a derived basis read-only to a non-admin', () => {
    renderPanel()
    expect(screen.getByText('Mandatory (MCR)')).toBeInTheDocument()
    expect(screen.getByText(/Derived from the classified frameworks/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Override basis/ })).not.toBeInTheDocument()
  })

  it('says when no classified framework maps the control', () => {
    state.row = { scf_id: 'GOV-01', obligation_basis: null, basis_source: null }
    renderPanel()
    expect(screen.getByText('Unclassified')).toBeInTheDocument()
    expect(screen.getByText(/No classified framework in scope maps this control/)).toBeInTheDocument()
  })

  it('sends the override with its trimmed reason', async () => {
    state.admin = true
    api.setOverride.mockResolvedValue({})
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Override basis' }))
    fireEvent.change(screen.getByLabelText('Basis'), { target: { value: 'dsr' } })
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: '  Not a contractual duty here  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.setOverride).toHaveBeenCalledWith('org-one', 'GOV-01', 'dsr', 'Not a contractual duty here'))
  })

  it('refuses an override without a reason before calling the API', () => {
    state.admin = true
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Override basis' }))
    fireEvent.change(screen.getByLabelText('Basis'), { target: { value: 'dsr' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(screen.getByRole('alert')).toHaveTextContent('A basis override needs a reason.')
    expect(api.setOverride).not.toHaveBeenCalled()
  })

  it('clears an existing override with nulls', async () => {
    state.admin = true
    state.row = { scf_id: 'GOV-01', obligation_basis: 'dsr', basis_source: 'override', basis_override_reason: 'Board said so' }
    api.setOverride.mockResolvedValue({})
    renderPanel()
    expect(screen.getByText(/Set on this control: Board said so/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Change override' }))
    fireEvent.change(screen.getByLabelText('Basis'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.setOverride).toHaveBeenCalledWith('org-one', 'GOV-01', null, null))
  })

  it('shows the API refusal message', async () => {
    state.admin = true
    api.setOverride.mockRejectedValue(new Error("Control 'GOV-01' is not in scope"))
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Override basis' }))
    fireEvent.change(screen.getByLabelText('Basis'), { target: { value: 'mcr' } })
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'Regulator letter' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("Control 'GOV-01' is not in scope")
  })

  it('shows a failed lookup as an error, never as Unclassified', () => {
    state.admin = true
    state.error = new Error('Internal server error')
    state.row = null
    renderPanel()
    expect(screen.getByRole('alert')).toHaveTextContent('Internal server error')
    expect(screen.queryByText('Unclassified')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Override basis' })).not.toBeInTheDocument()
  })
})
