import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import FrameworkScopingPage from '../FrameworkScopingPage'

const role = vi.hoisted(() => ({ editor: false, admin: false }))
const api = vi.hoisted(() => ({
  fetchSummary: vi.fn(),
  preview: vi.fn(),
  add: vi.fn(),
  remove: vi.fn(),
  reset: vi.fn(),
  setBasis: vi.fn(),
}))

vi.mock('../../../hooks/useHasOrgRole', () => ({
  useIsOrgEditor: () => role.editor,
}))
vi.mock('../../../hooks/useIsOrgAdmin', () => ({
  useIsOrgAdmin: () => role.admin,
}))
vi.mock('../../../data/apiClient', () => ({
  fetchFrameworkScopeSummary: api.fetchSummary,
  previewFrameworkScopeChange: api.preview,
  bulkScopeByFramework: api.add,
  bulkUnscopeByFramework: api.remove,
  resetAllScope: api.reset,
  setFrameworkBasis: api.setBasis,
}))
vi.mock('../../FrameworkLogo', () => ({
  FrameworkLogo: ({ frameworkName }: { frameworkName: string }) => <span>{frameworkName} logo</span>,
}))
vi.mock('react-hot-toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

const summary = {
  total: 1,
  selected_count: 1,
  basis_counts: { mcr: 7, dsr: 2, unclassified: 4 },
  frameworks: [
    {
      id: 'iso_27001_2022',
      name: 'ISO 27001:2022',
      family: 'international',
      mapped_control_count: 10,
      in_scope_count: 8,
      missing_count: 2,
      coverage_percentage: 80,
      expected_additions: 0,
      active: true,
      partial: true,
      source: 'bulk_scope',
      basis: 'mcr',
      obligation_type: 'contractual',
      obligation_source: 'Customer MSA clause 12',
    },
  ],
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <FrameworkScopingPage organizationId="org-one" />
    </QueryClientProvider>,
  )
}

describe('FrameworkScopingPage obligation basis (PIG R2)', () => {
  beforeEach(() => {
    role.editor = false
    role.admin = false
    vi.clearAllMocks()
    api.fetchSummary.mockResolvedValue(summary)
  })

  it('shows the basis, its obligation source and the in-scope basis counts to a viewer', async () => {
    renderPage()
    expect(await screen.findByText('Customer MSA clause 12', { exact: false })).toBeInTheDocument()
    expect(screen.getByText(/Contractual obligation/)).toBeInTheDocument()
    expect(screen.getByLabelText('In-scope controls by obligation basis')).toHaveTextContent('Mandatory (MCR) 7')
    expect(screen.getByLabelText('In-scope controls by obligation basis')).toHaveTextContent('Unclassified 4')
    expect(screen.queryByRole('button', { name: 'Set basis' })).not.toBeInTheDocument()
  })

  it('hides the basis editor from an editor who is not an admin', async () => {
    role.editor = true
    renderPage()
    await screen.findAllByText('ISO 27001:2022')
    expect(screen.queryByRole('button', { name: 'Set basis' })).not.toBeInTheDocument()
  })

  it('sends all three fields when an admin saves the basis', async () => {
    role.editor = true
    role.admin = true
    api.setBasis.mockResolvedValue({})
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Set basis' }))
    fireEvent.change(screen.getByLabelText('Basis'), { target: { value: 'dsr' } })
    fireEvent.change(screen.getByLabelText('Obligation type'), { target: { value: 'discretionary' } })
    fireEvent.change(screen.getByLabelText(/Source/), { target: { value: '  Board decision 2026-09  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save basis' }))
    await waitFor(() => expect(api.setBasis).toHaveBeenCalledTimes(1))
    expect(api.setBasis).toHaveBeenCalledWith('org-one', 'iso_27001_2022', {
      basis: 'dsr',
      obligation_type: 'discretionary',
      obligation_source: 'Board decision 2026-09',
    })
  })

  it('sends nulls to clear the basis', async () => {
    role.admin = true
    api.setBasis.mockResolvedValue({})
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Set basis' }))
    fireEvent.change(screen.getByLabelText('Basis'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('Obligation type'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText(/Source/), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save basis' }))
    await waitFor(() => expect(api.setBasis).toHaveBeenCalledWith('org-one', 'iso_27001_2022', {
      basis: null,
      obligation_type: null,
      obligation_source: null,
    }))
  })

  it('shows the API refusal and keeps the editor open', async () => {
    role.admin = true
    api.setBasis.mockRejectedValue(new Error("Framework 'iso_27001_2022' is not in scope"))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Set basis' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save basis' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("Framework 'iso_27001_2022' is not in scope")
    expect(screen.getByRole('button', { name: 'Save basis' })).toBeInTheDocument()
  })

  it('requires a rationale to remove an MCR framework and warns a non-admin', async () => {
    role.editor = true
    api.preview.mockResolvedValue({
      operation: 'remove',
      frameworks: ['iso_27001_2022'],
      mapped_controls: ['ctl-a'],
      new_controls: [],
      already_covered: [],
      shared_with_active_frameworks: [],
      individual_inclusions: [],
      explicitly_excluded: [],
      controls_leaving_scope: ['ctl-a'],
      orphaned_evidence: [],
      open_tasks_affected: 0,
      basis_downgrades: [{ scf_id: 'GOV-01', from_basis: 'mcr', to_basis: null }],
    })
    api.remove.mockRejectedValue(new Error('Removing a framework that carries an MCR obligation needs an organisation admin'))
    renderPage()
    fireEvent.click((await screen.findAllByRole('button', { name: 'Remove framework' }))[0])
    expect(await screen.findByText(/you are not one/)).toBeInTheDocument()
    expect(screen.getByText(/GOV-01 MCR→unclassified or out of scope/)).toBeInTheDocument()
    const confirm = screen.getByRole('button', { name: 'Confirm exact change' })
    expect(confirm).toBeDisabled()
    fireEvent.change(screen.getByLabelText(/Change rationale/), { target: { value: 'Contract ended' } })
    expect(confirm).toBeEnabled()
    fireEvent.click(confirm)
    await waitFor(() => expect(api.remove).toHaveBeenCalledTimes(1))
    expect(api.remove.mock.calls[0][0]).toMatchObject({ removal_reason: 'Contract ended' })
    const { toast } = await import('react-hot-toast')
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      'Removing a framework that carries an MCR obligation needs an organisation admin',
    ))
  })

  it('requires a rationale when the preview shows an MCR control downgrading on a DSR framework', async () => {
    role.editor = true
    api.fetchSummary.mockResolvedValue({ ...summary, frameworks: [{ ...summary.frameworks[0], basis: 'dsr' }] })
    api.preview.mockResolvedValue({
      operation: 'remove', frameworks: ['iso_27001_2022'], mapped_controls: ['ctl-a'], new_controls: [],
      already_covered: [], shared_with_active_frameworks: [], individual_inclusions: [], explicitly_excluded: [],
      controls_leaving_scope: ['ctl-a'], orphaned_evidence: [], open_tasks_affected: 0,
      basis_downgrades: [{ scf_id: 'IAC-01', from_basis: 'mcr', to_basis: 'dsr' }],
    })
    renderPage()
    fireEvent.click((await screen.findAllByRole('button', { name: 'Remove framework' }))[0])
    expect(await screen.findByText(/touches a mandatory \(MCR\) obligation/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirm exact change' })).toBeDisabled()
  })
})
