// SPDX-License-Identifier: AGPL-3.0-only
/** The objectives register: columns, rows, gaps, roles, archive and restore, focus. */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ObjectivesRegister from '../ObjectivesRegister'
import { list, metric, objective, ORG, reading } from './fixtures'

const api = vi.hoisted(() => ({ listSecurityObjectives: vi.fn(), updateSecurityObjective: vi.fn() }))
vi.mock('../../../../data/securityObjectivesApi', () => api)
const role = vi.hoisted(() => ({ editor: true, admin: true }))
vi.mock('../../../../hooks/useHasOrgRole', () => ({
  useIsOrgEditor: () => role.editor,
  useHasOrgRole: () => role.admin,
}))
const confirmMock = vi.hoisted(() => vi.fn())
vi.mock('../../../../contexts/ConfirmContext', () => ({ useConfirm: () => confirmMock }))
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('../ObjectiveFormModal', () => ({ default: () => <div data-testid="objective-form" /> }))
vi.mock('../ConfirmObjectiveModal', () => ({ default: () => <div data-testid="confirm-modal" /> }))
vi.mock('../LogValueModal', () => ({ default: () => <div data-testid="log-modal" /> }))
vi.mock('../EvaluationModal', () => ({ default: () => <div data-testid="evaluation-modal" /> }))

const ACTIVE = objective()
const LOGGED = objective({
  id: 'obj-9', ref: 'OBJ-09', name: 'Train everyone',
  metric: metric({ kind: 'logged', measure_key: null, frequency: 'quarterly', name: 'Training completion (%)' }),
})
const GAPS = objective({
  id: 'obj-13', ref: 'OBJ-13', name: 'Enforce MFA', planned_actions: '', evaluation_method: '',
  objective_type: 'achieve', due_date: null, owning_team_id: null, owning_team_name: null,
  owner_text: 'Old Owner', metric: null, source: 'migrated',
  reading: reading({ status: 'not_measured', value: null, value_text: '—' }),
})
const ACHIEVE = objective({
  id: 'obj-12', ref: 'OBJ-12', name: 'Achieve certification', objective_type: 'achieve',
  due_date: '2027-03-31', metric: metric({ kind: 'milestone' }),
  reading: reading({ status: 'in_progress', value: 0, value_text: 'Not done', days_left: 133 }),
})
const PROPOSED = objective({
  id: 'obj-2', ref: 'OBJ-02', name: 'Test our response capability', lifecycle_status: 'proposed',
  owning_team_id: null, owning_team_name: null,
  reading: reading({ status: 'proposed', value: 2, value_text: '2 passed tests' }),
})
const ARCHIVED = objective({ id: 'obj-7', ref: 'OBJ-07', name: 'Old objective', archived_at: '2026-11-01T10:00:00' })

let client: QueryClient
function renderRegister(focus: string | null = null) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <ObjectivesRegister organizationId={ORG} focusObjectiveId={focus} />
    </QueryClientProvider>,
  )
}

const ACTION_NAMES = /^(Add objective|(Edit|Archive|Restore|Log value|Record evaluation|Review and confirm|Choose a team|Choose a metric)( .+)?)$/

describe('ObjectivesRegister', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    role.editor = true
    role.admin = true
    api.listSecurityObjectives.mockImplementation(async (_org: string, archived: boolean) =>
      list(archived ? [ACTIVE, LOGGED, GAPS, ACHIEVE, PROPOSED, ARCHIVED] : [ACTIVE, LOGGED, GAPS, ACHIEVE, PROPOSED]))
    api.updateSecurityObjective.mockResolvedValue(ACTIVE)
  })

  it('renders the nine columns in order and a full row', async () => {
    renderRegister()
    await screen.findByTestId('objective-obj-1')
    expect(screen.getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Ref', 'Security objective', 'What will be done', 'Resources required', 'Responsible team',
      'Complete by', 'How results are evaluated', 'Supporting ISMS metric', 'Now',
    ])
    const row = screen.getByTestId('objective-obj-1')
    for (const text of ['OBJ-01', 'Keep risk within appetite', '6.1.3', 'Assess every risk', 'Risk owners’ time',
      'Risk and Compliance', 'Ongoing', 'Review 31 Mar 2027', 'Risks whose residual level', 'Risks within appetite (%)',
      '14%', 'Off track']) {
      expect(row).toHaveTextContent(text)
    }
    expect(screen.getByTestId('obj-summary')).toHaveTextContent(/^14 objectives · 9 measured · 1 proposed$/)
  })

  it('says what is missing in the empty cells', async () => {
    renderRegister()
    const row = await screen.findByTestId('objective-obj-13')
    expect(within(row).getAllByText('Not written yet')).toHaveLength(2)
    expect(within(row).getByText('No date')).toBeInTheDocument()
    expect(within(row).getByText('No metric')).toBeInTheDocument()
    expect(within(row).getByText('“Old Owner” (text)')).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Choose a team OBJ-13' })).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Choose a metric OBJ-13' })).toBeInTheDocument()
    expect(within(row).getByText('Not measured')).toBeInTheDocument()
    expect(screen.getByTestId('objective-obj-12')).toHaveTextContent('31 Mar 2027')
    expect(screen.getByTestId('objective-obj-12')).toHaveTextContent('In progress · 133 days left')
  })

  it('marks proposed rows and lists them in the band with today’s values', async () => {
    renderRegister()
    const row = await screen.findByTestId('objective-obj-2')
    expect(row).toHaveClass('obj-row--proposed')
    expect(within(row).getByText('Proposed')).toBeInTheDocument()
    const band = screen.getByTestId('proposed-band')
    expect(band).toHaveTextContent('1 proposed objective is waiting for a team and a target')
    expect(band).toHaveTextContent('Test our response capability')
    expect(band).toHaveTextContent('2 passed tests')
    fireEvent.click(within(band).getByRole('button', { name: 'Review and confirm Test our response capability' }))
    expect(screen.getByTestId('confirm-modal')).toBeInTheDocument()
  })

  it('shows a viewer no actions', async () => {
    role.editor = false
    role.admin = false
    renderRegister()
    await screen.findByTestId('objective-obj-1')
    expect(screen.queryAllByRole('button', { name: ACTION_NAMES })).toHaveLength(0)
    expect(screen.queryByLabelText('Show archived')).toBeNull()
    expect(screen.getByRole('button', { name: 'Open ISMS metrics' })).toBeInTheDocument()
  })

  it('shows an editor who is not an admin no Review and confirm', async () => {
    role.admin = false
    renderRegister()
    await screen.findByTestId('objective-obj-2')
    expect(screen.queryAllByRole('button', { name: /Review and confirm/ })).toHaveLength(0)
    expect(screen.getAllByRole('button', { name: /^Edit OBJ-/ }).length).toBeGreaterThan(0)
  })

  it('offers Log value only on logged and milestone rows', async () => {
    renderRegister()
    await screen.findByTestId('objective-obj-1')
    expect(within(screen.getByTestId('objective-obj-1')).queryByRole('button', { name: /^Log value/ })).toBeNull()
    expect(within(screen.getByTestId('objective-obj-9')).getByRole('button', { name: 'Log value OBJ-09' })).toBeInTheDocument()
    expect(within(screen.getByTestId('objective-obj-12')).getByRole('button', { name: 'Log value OBJ-12' })).toBeInTheDocument()
    fireEvent.click(within(screen.getByTestId('objective-obj-9')).getByRole('button', { name: 'Log value OBJ-09' }))
    expect(screen.getByTestId('log-modal')).toBeInTheDocument()
  })

  it('offers no Log value on a closed objective', async () => {
    api.listSecurityObjectives.mockResolvedValue(list([
      { ...LOGGED, lifecycle_status: 'achieved', reading: reading({ status: 'achieved', value: 97, value_text: '97%' }) },
      { ...ACHIEVE, lifecycle_status: 'missed', reading: reading({ status: 'missed', value: 0, value_text: 'Not done' }) },
    ]))
    renderRegister()
    await screen.findByTestId('objective-obj-9')
    expect(screen.queryAllByRole('button', { name: /^Log value/ })).toHaveLength(0)
    expect(within(screen.getByTestId('objective-obj-9')).getByText('Achieved')).toBeInTheDocument()
    expect(within(screen.getByTestId('objective-obj-12')).getByText('Missed')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Record evaluation OBJ-09' })).toBeInTheDocument()
  })

  it('offers an editor who is not an admin Archive and Restore only on proposed objectives', async () => {
    role.admin = false
    const ARCHIVED_PROPOSED = { ...PROPOSED, id: 'obj-8', ref: 'OBJ-08', archived_at: '2026-11-01T10:00:00' }
    api.listSecurityObjectives.mockImplementation(async (_org: string, archived: boolean) =>
      list(archived ? [ACTIVE, PROPOSED, ARCHIVED, ARCHIVED_PROPOSED] : [ACTIVE, PROPOSED]))
    renderRegister()
    await screen.findByTestId('objective-obj-1')
    expect(within(screen.getByTestId('objective-obj-1')).queryByRole('button', { name: /^Archive/ })).toBeNull()
    expect(within(screen.getByTestId('objective-obj-2')).getByRole('button', { name: 'Archive OBJ-02' })).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Show archived'))
    await screen.findByTestId('objective-obj-7')
    expect(within(screen.getByTestId('objective-obj-7')).queryByRole('button', { name: /^Restore/ })).toBeNull()
    expect(within(screen.getByTestId('objective-obj-8')).getByRole('button', { name: 'Restore OBJ-08' })).toBeInTheDocument()
  })

  it('archives after asking, and refreshes the objectives', async () => {
    confirmMock.mockResolvedValue(true)
    renderRegister()
    const row = await screen.findByTestId('objective-obj-1')
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    fireEvent.click(within(row).getByRole('button', { name: 'Archive OBJ-01' }))
    await waitFor(() => expect(api.updateSecurityObjective).toHaveBeenCalledWith(ORG, 'obj-1', { archived: true }))
    expect(confirmMock).toHaveBeenCalledWith(expect.objectContaining({ title: 'Archive this objective?', destructive: true }))
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['security-objectives', ORG] }))
  })

  it('does not archive when the person says no', async () => {
    confirmMock.mockResolvedValue(false)
    renderRegister()
    const row = await screen.findByTestId('objective-obj-1')
    fireEvent.click(within(row).getByRole('button', { name: 'Archive OBJ-01' }))
    await waitFor(() => expect(confirmMock).toHaveBeenCalled())
    expect(api.updateSecurityObjective).not.toHaveBeenCalled()
  })

  it('shows archived objectives on request and offers Restore', async () => {
    renderRegister()
    await screen.findByTestId('objective-obj-1')
    fireEvent.click(screen.getByLabelText('Show archived'))
    const row = await screen.findByTestId('objective-obj-7')
    expect(api.listSecurityObjectives).toHaveBeenLastCalledWith(ORG, true)
    fireEvent.click(within(row).getByRole('button', { name: 'Restore OBJ-07' }))
    await waitFor(() => expect(api.updateSecurityObjective).toHaveBeenCalledWith(ORG, 'obj-7', { archived: false }))
  })

  it('shows a refused archive on its row', async () => {
    confirmMock.mockResolvedValue(true)
    api.updateSecurityObjective.mockRejectedValue(new Error('Not allowed'))
    renderRegister()
    const row = await screen.findByTestId('objective-obj-1')
    fireEvent.click(within(row).getByRole('button', { name: 'Archive OBJ-01' }))
    expect(await within(row).findByRole('alert')).toHaveTextContent('Not allowed')
  })

  it('M1: the toolbar counts proposed objectives by lifecycle, as the band does', async () => {
    // The summary says 0 proposed (it counts by reading status); the one proposed row has no metric.
    const noMetric = objective({ ...PROPOSED, metric: null, reading: reading({ status: 'not_measured', value: null, value_text: '—' }) })
    api.listSecurityObjectives.mockResolvedValue(list([ACTIVE, noMetric], { proposed: 0 }))
    renderRegister()
    expect(await screen.findByTestId('proposed-band')).toHaveTextContent('1 proposed objective is waiting')
    expect(screen.getByText(/ · 1 proposed$/)).toBeInTheDocument()
  })

  it('M5: every row action names the objective it acts on', async () => {
    renderRegister()
    await screen.findByTestId('objective-obj-1')
    expect(screen.getByRole('button', { name: 'Edit OBJ-01' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Archive OBJ-01' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Record evaluation OBJ-09' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Review and confirm OBJ-02' })).toBeInTheDocument()
    const names = screen.getAllByRole('button').map((b) => b.getAttribute('aria-label') ?? b.textContent)
    const rowNames = names.filter((n) => n && /^(Edit|Archive|Log value|Record evaluation|Restore) /.test(n))
    expect(new Set(rowNames).size).toBe(rowNames.length)
  })

  it('M4: scrolls to a linked objective once, not again when the list changes', async () => {
    const scroll = vi.fn()
    Element.prototype.scrollIntoView = scroll
    try {
      renderRegister('obj-1')
      await screen.findByTestId('objective-obj-1')
      await waitFor(() => expect(scroll).toHaveBeenCalledTimes(1))
      fireEvent.click(screen.getByLabelText('Show archived'))
      await screen.findByTestId('objective-obj-7')
      expect(scroll).toHaveBeenCalledTimes(1)
    } finally {
      delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView
    }
  })

  it('highlights the objective a link names', async () => {
    renderRegister('obj-9')
    expect(await screen.findByTestId('objective-obj-9')).toHaveClass('obj-row--focus')
    expect(screen.getByTestId('objective-obj-1')).not.toHaveClass('obj-row--focus')
  })

  describe('toolbar filters (design rows 11, 30)', () => {
    const rows = () => screen.getAllByTestId(/^objective-obj-/).map((r) => r.getAttribute('data-testid'))
    const statusPill = (name: string) => within(screen.getByRole('group', { name: 'Status' })).getByRole('button', { name })

    it('bolds only the objective count in the summary', async () => {
      renderRegister()
      const summary = await screen.findByTestId('obj-summary')
      expect(summary.querySelector('strong')!.textContent).toBe('14 objectives')
      expect(summary.textContent).toBe('14 objectives · 9 measured · 1 proposed')
    })

    it('offers a Team select and the mock-up status pills, All pressed first', async () => {
      renderRegister()
      await screen.findByTestId('objective-obj-1')
      const group = screen.getByRole('group', { name: 'Status' })
      expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual(['All', 'Off track', 'At risk', 'On track', 'Not measured'])
      expect(statusPill('All')).toHaveAttribute('aria-pressed', 'true')
      expect(screen.getByLabelText('Team')).toHaveValue('')
      expect(screen.getByRole('checkbox', { name: 'Show archived' })).toBeInTheDocument()
    })

    it('narrows the rows by status and says how many it shows', async () => {
      renderRegister()
      await screen.findByTestId('objective-obj-1')
      fireEvent.click(statusPill('Not measured'))
      expect(statusPill('Not measured')).toHaveAttribute('aria-pressed', 'true')
      expect(rows()).toEqual(['objective-obj-13'])
      expect(screen.getByTestId('obj-summary')).toHaveTextContent(/ · showing 1$/)
      fireEvent.click(statusPill('All'))
      expect(rows()).toHaveLength(5)
      expect(screen.getByTestId('obj-summary').textContent).not.toMatch(/showing/)
    })

    it('narrows the rows by team, with "No team" for unowned objectives', async () => {
      renderRegister()
      await screen.findByTestId('objective-obj-1')
      fireEvent.change(screen.getByLabelText('Team'), { target: { value: 'none' } })
      expect(rows().sort()).toEqual(['objective-obj-13', 'objective-obj-2'])
      fireEvent.change(screen.getByLabelText('Team'), { target: { value: 'team-ops' } })
      expect(rows()).not.toContain('objective-obj-13')
    })

    it('shows a clear-filters line when nothing matches, and never calls the server for a filter', async () => {
      renderRegister()
      await screen.findByTestId('objective-obj-1')
      const calls = api.listSecurityObjectives.mock.calls.length
      fireEvent.click(statusPill('On track'))
      expect(screen.getByTestId('obj-no-match')).toHaveTextContent('No objectives match these filters.')
      fireEvent.click(within(screen.getByTestId('obj-no-match')).getByRole('button', { name: 'Clear filters' }))
      expect(rows()).toHaveLength(5)
      expect(statusPill('All')).toHaveAttribute('aria-pressed', 'true')
      expect(api.listSecurityObjectives.mock.calls.length).toBe(calls)
    })
  })
})
