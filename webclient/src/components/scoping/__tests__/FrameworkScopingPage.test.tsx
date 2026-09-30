import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
}))
vi.mock('../../FrameworkLogo', () => ({
  FrameworkLogo: ({ frameworkName }: { frameworkName: string }) => <span>{frameworkName} logo</span>,
}))
vi.mock('react-hot-toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

const summary = {
  total: 2,
  selected_count: 1,
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
      selected_at: '2026-09-17T10:00:00Z',
      selected_by: '00000000-0000-0000-0000-000000000001',
    },
    {
      id: 'soc2',
      name: 'SOC 2',
      family: 'industry',
      mapped_control_count: 6,
      in_scope_count: 1,
      missing_count: 5,
      coverage_percentage: 16.7,
      expected_additions: 5,
      active: false,
      partial: false,
    },
  ],
}

function renderPage(props: Partial<Parameters<typeof FrameworkScopingPage>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <FrameworkScopingPage organizationId="org-one" {...props} />
    </QueryClientProvider>,
  )
}

describe('FrameworkScopingPage roles and exact preview', () => {
  beforeEach(() => {
    role.editor = false
    role.admin = false
    vi.clearAllMocks()
    api.fetchSummary.mockResolvedValue(summary)
    api.preview.mockResolvedValue({
      operation: 'add',
      frameworks: ['soc2'],
      mapped_controls: ['ctl-a', 'ctl-b'],
      new_controls: ['ctl-a'],
      already_covered: ['ctl-b'],
      shared_with_active_frameworks: [],
      individual_inclusions: [],
      explicitly_excluded: ['ctl-excluded'],
      controls_leaving_scope: [],
    })
    api.add.mockResolvedValue({ message: 'added' })
    api.reset.mockResolvedValue({ message: 'reset', removed: 8 })
  })

  it('gives viewers read-only selected, partial, and unselected framework state', async () => {
    renderPage()
    expect((await screen.findAllByText('ISO 27001:2022')).length).toBeGreaterThan(1)
    fireEvent.click(screen.getByRole('button', { name: /Industry Standards/ }))
    expect(screen.getAllByText('Partial').length).toBeGreaterThan(1)
    expect(screen.getByText('Not selected')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove framework' })).not.toBeInTheDocument()
    expect(screen.queryByText('Danger zone')).not.toBeInTheDocument()
  })

  it('lets editors inspect the server-authoritative exact effect before adding', async () => {
    role.editor = true
    const onChanged = vi.fn()
    renderPage({ onChanged })
    fireEvent.click(await screen.findByRole('button', { name: /Industry Standards/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))

    expect(await screen.findByText('Exact change preview')).toBeInTheDocument()
    expect(api.preview).toHaveBeenCalledWith('org-one', 'add', ['soc2'])
    expect(screen.getByText('New controls entering scope')).toBeInTheDocument()
    expect(screen.getByText('Blocked by an explicit exclusion')).toBeInTheDocument()
    // Headline rows stay visible at zero because zero is a real answer for them.
    expect(screen.getByText('Already in scope via another active framework')).toBeInTheDocument()
    // An add can never empty a control out of scope, so that row is not rendered at all
    // rather than shown as a permanent zero.
    expect(screen.queryByText('Controls leaving scope')).not.toBeInTheDocument()
    // Nothing is individually included in this fixture, so the row stays out of the way.
    expect(screen.queryByText(/individually included/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Confirm exact change' }))
    await waitFor(() => expect(api.add).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
  })

  it('asks what to do with orphaned evidence on remove and sends the choice', async () => {
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
      orphaned_evidence: ['E-IAC-01', 'E-IAC-02'],
      open_tasks_affected: 3,
    })
    api.remove.mockResolvedValue({ message: 'removed' })
    renderPage()
    fireEvent.click((await screen.findAllByRole('button', { name: 'Remove framework' }))[0])

    expect(await screen.findByText('Exact change preview')).toBeInTheDocument()
    expect(screen.getByText(/Tracked evidence no longer required by any in-scope control \(3 open tasks\)/)).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /Keep tracking/ })).toBeChecked()
    fireEvent.click(screen.getByRole('radio', { name: /Stop tracking/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm exact change' }))
    await waitFor(() => expect(api.remove).toHaveBeenCalledTimes(1))
    expect(api.remove.mock.calls[0][0]).toMatchObject({
      frameworks: ['iso_27001_2022'],
      orphan_evidence_action: 'untrack',
    })
  })

  it('hides the orphan picker when nothing is orphaned', async () => {
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
    })
    renderPage()
    fireEvent.click((await screen.findAllByRole('button', { name: 'Remove framework' }))[0])
    expect(await screen.findByText('Exact change preview')).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: /Keep tracking/ })).not.toBeInTheDocument()
  })

  it('shows destructive reset only to admins', async () => {
    role.editor = true
    role.admin = true
    vi.spyOn(window, 'prompt').mockReturnValue('REMOVE ALL')
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Reset scope' }))
    await waitFor(() => expect(api.reset).toHaveBeenCalledWith('org-one'))
  })
})

describe('FrameworkScopingPage framework browser', () => {
  beforeEach(() => {
    role.editor = false
    role.admin = false
    vi.clearAllMocks()
    api.fetchSummary.mockResolvedValue({
      total: 4,
      selected_count: 1,
      frameworks: [
        // API order: selected first — the page must not inherit it as group order.
        { ...summary.frameworks[0] },
        { ...summary.frameworks[1] },
        { ...summary.frameworks[1], id: 'scf_misc', name: 'SCF Misc', family: 'other' },
        { ...summary.frameworks[1], id: 'usa_federal_sox_2002', name: 'SOX 2002', family: 'us_federal' },
      ],
    })
  })

  it('orders groups by family, with Other last, whatever the API order', async () => {
    renderPage()
    await screen.findByRole('button', { name: /International Standards/ })
    const labels = Array.from(
      document.querySelectorAll('.framework-browser-group-label'),
      (label) => label.textContent,
    )
    expect(labels).toEqual(['International Standards', 'Industry Standards', 'US Federal', 'Other'])
  })

  it('opens only groups holding a selected framework, with counts on every header', async () => {
    renderPage()
    const international = await screen.findByRole('button', { name: /International Standards/ })
    expect(international).toHaveAttribute('aria-expanded', 'true')
    expect(international).toHaveTextContent('1 selected')
    const federal = screen.getByRole('button', { name: /US Federal/ })
    expect(federal).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('SOX 2002')).not.toBeInTheDocument()

    fireEvent.click(federal)
    expect(federal).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('SOX 2002')).toBeInTheDocument()
  })

  it('shows the framework id as a tooltip, not as row text', async () => {
    renderPage()
    const table = await screen.findByRole('table', { name: 'International Standards' })
    expect(within(table).queryByText('iso_27001_2022')).not.toBeInTheDocument()
    expect(within(table).getByTitle('iso_27001_2022')).toHaveTextContent('ISO 27001:2022')
  })

  it('opens every matching group while searching, by name or id', async () => {
    renderPage()
    fireEvent.change(await screen.findByRole('searchbox', { name: 'Search frameworks' }), {
      target: { value: 'usa_federal' },
    })
    expect(screen.getByText('SOX 2002')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /International Standards/ })).not.toBeInTheDocument()

    fireEvent.change(screen.getByRole('searchbox', { name: 'Search frameworks' }), {
      target: { value: 'nothing-matches' },
    })
    expect(screen.getByText(/No framework matches/)).toBeInTheDocument()
  })

  it('filters to selected or available frameworks, with counts', async () => {
    renderPage()
    const available = await screen.findByRole('button', { name: /Available/ })
    expect(available).toHaveTextContent('3')
    fireEvent.click(available)
    expect(available).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('SOX 2002')).toBeInTheDocument()
    expect(screen.getByText('SCF Misc')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /International Standards/ })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /^Selected/ }))
    expect(screen.queryByText('SOX 2002')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /International Standards/ })).toBeInTheDocument()
  })

  it('draws each row\'s coverage as a meter, the empty track being the gap', async () => {
    renderPage()
    const table = await screen.findByRole('table', { name: 'International Standards' })
    const meter = within(table).getByRole('meter')
    expect(meter).toHaveAttribute('aria-valuenow', '8')
    expect(meter).toHaveAttribute('aria-valuemax', '10')
    // No status breakdown from this backend: the in-scope part reads as not started.
    expect(meter).toHaveAccessibleName('8 of 10 mapped controls in scope · 8 not started · 2 not in scope')
    expect(within(table).getByText('8/10')).toBeInTheDocument()
  })

  it('splits the in-scope part of the bar by implementation status', async () => {
    api.fetchSummary.mockResolvedValue({
      ...summary,
      frameworks: [
        {
          ...summary.frameworks[0],
          status_counts: { implemented: 5, in_progress: 2, at_risk: 1, not_started: 0 },
        },
      ],
    })
    renderPage()
    const table = await screen.findByRole('table', { name: 'International Standards' })
    const meter = within(table).getByRole('meter')
    expect(meter).toHaveAccessibleName(
      '8 of 10 mapped controls in scope · 5 implemented, 2 in progress, 1 at risk · 2 not in scope',
    )
    const widths = Array.from(meter.children, (segment) => [
      segment.className.replace('framework-coverage-segment framework-coverage-segment--', ''),
      (segment as HTMLElement).style.width,
    ])
    expect(widths).toEqual([
      ['implemented', '50%'],
      ['in_progress', '20%'],
      ['at_risk', '10%'],
    ])
  })
})
