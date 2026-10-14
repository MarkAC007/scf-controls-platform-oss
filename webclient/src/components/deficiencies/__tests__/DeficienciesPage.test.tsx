import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DeficienciesPage from '../DeficienciesPage'
import type { Deficiency } from '../../../types'

const state = vi.hoisted(() => ({ editor: true }))
const api = vi.hoisted(() => ({
  listDeficiencies: vi.fn(),
  getDeficiencySummary: vi.fn(),
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
  auto_generated: false,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
  ...over,
})

const ALL = [
  make({ id: 'a', title: 'Overdue gap', status: 'open', due_date: '2020-01-01', scf_id: 'TESTCONTROL-01', domain: 'TESTCONTROL' }),
  make({ id: 'b', title: 'Being fixed', status: 'in_progress', scf_id: 'OTHERCTL-02', domain: 'OTHERCTL' }),
  make({ id: 'c', title: 'Risk only gap', status: 'open', risk_code: 'R-TEST-1' }),
  make({ id: 'd', title: 'Fixed gap', status: 'completed', scf_id: 'TESTCONTROL-01', domain: 'TESTCONTROL', retest_note: 'ok' }),
  make({ id: 'e', title: 'Raised in error', status: 'cancelled', scf_id: 'OTHERCTL-02', domain: 'OTHERCTL' }),
]

const SUMMARY = {
  open_total: 3,
  material_open_total: 1,
  unlinked_open: 1,
  by_control: [],
  by_domain: [
    { domain: 'OTHERCTL', open_count: 1 },
    { domain: 'TESTCONTROL', open_count: 1 },
  ],
}

function renderPage(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  const view = render(
    <QueryClientProvider client={client}>
      <DeficienciesPage organizationId="org-one" />
    </QueryClientProvider>,
  )
  return { client, unmount: view.unmount }
}

const stat = (testId: string) => screen.getByTestId(testId)

describe('DeficienciesPage (PIG R8)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state.editor = true
    api.listDeficiencies.mockImplementation(async (_org: string, filters: Record<string, unknown>) => {
      if (filters.status) return ALL.filter((d) => d.status === filters.status)
      if (filters.open_only) return ALL.filter((d) => d.status === 'open' || d.status === 'in_progress')
      if (filters.domain) return ALL.filter((d) => d.domain === filters.domain)
      if (filters.risk_code) return ALL.filter((d) => d.risk_code === filters.risk_code)
      return ALL
    })
    api.getDeficiencySummary.mockResolvedValue(SUMMARY)
    api.getOrgMembers.mockResolvedValue([])
    api.getRiskAssessments.mockResolvedValue([{ risk_code: 'R-TEST-1' }])
    api.fetchScopedControlsPage.mockResolvedValue({ controls: [], total: 0, offset: 0 })
  })

  it('counts deficiencies by status, overdue and material', async () => {
    renderPage()
    await screen.findByText('Overdue gap')
    await waitFor(() => expect(stat('deficiency-stat-open')).toHaveTextContent('3'))
    expect(stat('deficiency-stat-in-progress')).toHaveTextContent('1')
    expect(stat('deficiency-stat-overdue')).toHaveTextContent('1')
    expect(stat('deficiency-stat-material')).toHaveTextContent('1')
    expect(stat('deficiency-stat-completed')).toHaveTextContent('1')
    expect(stat('deficiency-stat-cancelled')).toHaveTextContent('1')
    expect(screen.getAllByRole('listitem')).toHaveLength(5)
    // The server counts any open one without a control, not only a risk's.
    expect(screen.getByText(/1 open deficiency is not raised against a control/)).toBeInTheDocument()
  })

  it('filters by status in the browser, and by domain and risk on the server', async () => {
    renderPage()
    await screen.findByText('Overdue gap')
    // Status is applied to the one query both views read (#1155), never sent.
    fireEvent.click(screen.getByRole('radio', { name: 'Completed' }))
    expect(await screen.findByText('Fixed gap')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('Overdue gap')).not.toBeInTheDocument())
    expect(screen.getAllByRole('listitem')).toHaveLength(1)

    fireEvent.click(screen.getByRole('radio', { name: 'Open or in progress' }))
    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(3))
    expect(screen.queryByText('Fixed gap')).not.toBeInTheDocument()
    expect(screen.queryByText('Raised in error')).not.toBeInTheDocument()
    expect(api.listDeficiencies).not.toHaveBeenCalledWith('org-one', expect.objectContaining({ status: expect.anything() }))
    expect(api.listDeficiencies).not.toHaveBeenCalledWith('org-one', expect.objectContaining({ open_only: true }))

    fireEvent.click(screen.getByRole('radio', { name: 'All' }))
    fireEvent.change(screen.getByLabelText('Domain'), { target: { value: 'OTHERCTL' } })
    await waitFor(() => expect(api.listDeficiencies).toHaveBeenCalledWith('org-one', { domain: 'OTHERCTL' }))

    fireEvent.change(screen.getByLabelText('Domain'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('Risk'), { target: { value: 'R-TEST-1' } })
    await waitFor(() => expect(api.listDeficiencies).toHaveBeenCalledWith('org-one', { risk_code: 'R-TEST-1' }))
    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(1))
  })

  it('narrows to overdue deficiencies and to a search', async () => {
    renderPage()
    await screen.findByText('Overdue gap')
    fireEvent.click(screen.getByRole('checkbox', { name: /Overdue only/ }))
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    fireEvent.click(screen.getByRole('checkbox', { name: /Overdue only/ }))
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'r-test' } })
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByText('Risk only gap')).toBeInTheDocument()
  })

  it('offers Raise to an editor and opens the form', async () => {
    renderPage()
    await screen.findByText('Overdue gap')
    fireEvent.click(screen.getByRole('button', { name: 'Raise deficiency' }))
    expect(screen.getByRole('dialog', { name: 'Raise deficiency' })).toBeInTheDocument()
  })

  it('offers a viewer neither Raise nor row actions', async () => {
    state.editor = false
    renderPage()
    await screen.findByText('Overdue gap')
    expect(screen.queryByRole('button', { name: 'Raise deficiency' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Edit/ })).not.toBeInTheDocument()
    expect(screen.getByText(/Only an editor or admin can raise/)).toBeInTheDocument()
  })

  it('says when the list or the counts cannot be loaded', async () => {
    api.listDeficiencies.mockRejectedValue(new Error('list down'))
    api.getDeficiencySummary.mockRejectedValue(new Error('summary down'))
    renderPage()
    const alerts = await screen.findAllByRole('alert')
    await waitFor(() => expect(screen.getAllByRole('alert').map((a) => a.textContent).join(' ')).toMatch(/summary down/))
    expect(alerts.map((a) => a.textContent).join(' ')).toMatch(/list down/)
  })

  it('says when nothing matches, distinct from none at all', async () => {
    api.listDeficiencies.mockResolvedValue([])
    renderPage()
    expect(await screen.findByText(/No deficiencies recorded/)).toBeInTheDocument()
    within(document.body).getByTestId('deficiency-stat-open')
  })

  it('says nothing matches when a filter leaves no rows (review LOW-6)', async () => {
    // None cancelled, so the Cancelled filter leaves nothing.
    api.listDeficiencies.mockResolvedValue(ALL.filter((d) => d.status !== 'cancelled'))
    renderPage()
    await screen.findByText('Overdue gap')
    fireEvent.click(screen.getByRole('radio', { name: 'Cancelled' }))
    expect(await screen.findByText('No deficiencies match these filters.')).toBeInTheDocument()
    expect(screen.queryByText(/No deficiencies recorded/)).not.toBeInTheDocument()
  })

  it('announces the counts strip by its name (review LOW-5)', async () => {
    renderPage()
    await screen.findByText('Overdue gap')
    expect(screen.getByRole('group', { name: 'Deficiency counts' })).toContainElement(stat('deficiency-stat-open'))
  })

  it('refetches the list and the counts on every visit, despite the app-wide stale time (review MED-1)', async () => {
    // The app's own default: five minutes. Deficiencies are raised by the
    // server too (a control at risk, rejected evidence), so a cached list lies.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 5 * 60 * 1000 } } })
    const first = renderPage(client)
    await screen.findByText('Overdue gap')
    await waitFor(() => expect(api.getDeficiencySummary).toHaveBeenCalledTimes(1))
    const lists = api.listDeficiencies.mock.calls.length
    first.unmount()
    renderPage(client)
    await waitFor(() => expect(api.getDeficiencySummary).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(api.listDeficiencies.mock.calls.length).toBeGreaterThan(lists))
  })

  it('keeps a half-written completion, and the list, when a refresh fails (review MED-2)', async () => {
    const { client } = renderPage()
    await screen.findByText('Overdue gap')
    fireEvent.click(screen.getByRole('button', { name: 'Complete "Overdue gap"' }))
    fireEvent.change(screen.getByLabelText('Retest note'), { target: { value: 'Retested on Friday' } })

    api.listDeficiencies.mockRejectedValue(new Error('gateway timeout'))
    await client.refetchQueries({ queryKey: ['deficiencies', 'org-one'] })

    expect(await screen.findByText(/Could not refresh the deficiencies: gateway timeout/)).toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Complete deficiency' })).toBeInTheDocument()
    expect(screen.getByLabelText('Retest note')).toHaveValue('Retested on Friday')
    expect(screen.getByTestId('deficiency-a')).toBeInTheDocument()
  })

  it('keeps an open edit when a refresh drops its row from the filter (review MED-2)', async () => {
    const { client } = renderPage()
    await screen.findByText('Overdue gap')
    fireEvent.click(screen.getByRole('radio', { name: 'Open' }))
    await waitFor(() => expect(screen.queryByText('Fixed gap')).not.toBeInTheDocument())
    fireEvent.click(await screen.findByRole('button', { name: 'Edit "Overdue gap"' }))
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Overdue gap, renamed' } })

    // Completed elsewhere: the Open list comes back without it.
    api.listDeficiencies.mockResolvedValue([])
    await client.refetchQueries({ queryKey: ['deficiencies', 'org-one'] })

    expect(await screen.findByText('No deficiencies match these filters.')).toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Edit deficiency' })).toBeInTheDocument()
    expect(screen.getByLabelText('Title')).toHaveValue('Overdue gap, renamed')
  })
})
