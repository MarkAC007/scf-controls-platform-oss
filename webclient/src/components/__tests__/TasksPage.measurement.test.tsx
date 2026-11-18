// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Measurement rows on the Tasks page (phase 3 B2): derived to-dos to log a logged metric's
 * value for this period, merged with the evidence tasks by due date.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { TasksPage } from '../TasksPage'
import { apiClient, getOrgMemberSummaries, getTeam, listTeamAssignments, listTeams } from '../../data/apiClient'
import type { MeasurementTask } from '../../data/securityObjectivesApi'
import { objective } from '../isms/objectives/__tests__/fixtures'

vi.mock('../../data/apiClient', () => ({
  apiClient: { get: vi.fn(), patch: vi.fn(), post: vi.fn() },
  listTeamAssignments: vi.fn(),
  listTeams: vi.fn(),
  getTeam: vi.fn(),
  getOrgMemberSummaries: vi.fn(),
}))
vi.mock('../ModernCommentThread', () => ({ ModernCommentThread: () => <div>Comments</div> }))
const api = vi.hoisted(() => ({ listMeasurementTasks: vi.fn(), getIsmsMetrics: vi.fn() }))
vi.mock('../../data/securityObjectivesApi', () => api)
const role = vi.hoisted(() => ({ editor: true }))
vi.mock('../../hooks/useHasOrgRole', () => ({
  useIsOrgEditor: () => role.editor,
  useHasOrgRole: () => role.editor,
}))
const logModal = vi.hoisted(() => vi.fn())
vi.mock('../isms/objectives/LogValueModal', () => ({
  default: (props: { objective: { id: string }; periodStart?: string }) => {
    logModal(props)
    return <div data-testid="log-modal" data-objective={props.objective.id} data-period={props.periodStart} />
  },
}))

const mockApi = vi.mocked(apiClient)
const ORG = 'org-1'
const OBJ = '9b2f0c1e-4a7d-4e2b-8c3f-1d2e3f4a5b6c'

const evidence = (id: string, due: string, title: string) => ({
  id, evidence_tracking_id: 't', evidence_id: `EV-${id}`, task_type: 'collection', title, priority: 'low',
  due_date: due, status: 'not_started', assigned_user_id: null, owning_team_id: null, created_at: null,
})

function measurement(over: Partial<MeasurementTask> = {}): MeasurementTask {
  return {
    id: `measure-${OBJ}-2026-10-01`, objective_id: OBJ, ref: 'OBJ-09', title: 'Log patching within SLA',
    metric_name: 'Patching within SLA', unit: 'percent', frequency: 'quarterly', period_start: '2026-10-01',
    due_date: '2030-03-01', status: 'not_started', first: false, mine: true,
    owning_team_id: 'team-1', owning_team_name: 'IT Operations', owner_name: 'Pat Example',
    completed_date: null, value_text: null,
    ...over,
  }
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <TasksPage organizationId={ORG} onNavigateToEvidence={vi.fn()} />
    </QueryClientProvider>,
  )
}

const rowTitles = () =>
  screen.getAllByRole('listitem').map((row) => row.querySelector('.tasks-row-title-text')?.textContent)
const measurementRow = () => screen.getByTestId(/^measurement-row-/)

beforeEach(() => {
  vi.clearAllMocks()
  role.editor = true
  window.history.replaceState(null, '', '/?tab=tasks')
  // Server order is due date, soonest first.
  mockApi.get.mockResolvedValue([evidence('b', '2030-01-01', 'Bravo task'), evidence('a', '2030-06-01', 'Alpha task')])
  api.listMeasurementTasks.mockResolvedValue([measurement()])
  api.getIsmsMetrics.mockResolvedValue({ items: [objective({ id: OBJ })], summary: {}, measured_at: '2026-11-18T02:00:00' })
  vi.mocked(listTeams).mockResolvedValue([])
  vi.mocked(listTeamAssignments).mockResolvedValue({})
  vi.mocked(getTeam).mockResolvedValue({} as never)
  vi.mocked(getOrgMemberSummaries).mockResolvedValue([])
})

describe('TasksPage measurement rows', () => {
  it('merges the measurement rows with the evidence tasks by due date', async () => {
    renderPage()
    await screen.findByText('Log patching within SLA')
    expect(rowTitles()).toEqual(['Bravo task', 'Log patching within SLA', 'Alpha task'])
  })

  it('maps My tasks to assigned_to_me and the status filter to include_done', async () => {
    renderPage()
    await screen.findByText('Log patching within SLA')
    expect(api.listMeasurementTasks).toHaveBeenLastCalledWith(ORG, { assignedToMe: true, includeDone: true })
    fireEvent.click(screen.getByRole('button', { name: 'All Tasks' }))
    await waitFor(() => expect(api.listMeasurementTasks).toHaveBeenLastCalledWith(ORG, { assignedToMe: false, includeDone: true }))
    fireEvent.click(screen.getByRole('radio', { name: 'Not started' }))
    await waitFor(() => expect(api.listMeasurementTasks).toHaveBeenLastCalledWith(ORG, { assignedToMe: false, includeDone: false }))
    // In progress can never hold a measurement task: nothing is asked and no row is shown.
    const calls = api.listMeasurementTasks.mock.calls.length
    fireEvent.click(screen.getByRole('radio', { name: 'In progress' }))
    // Wait for the reload to land, not for its loading state.
    await screen.findByText('Bravo task')
    expect(screen.queryByText('Log patching within SLA')).toBeNull()
    expect(api.listMeasurementTasks).toHaveBeenCalledTimes(calls)
  })

  it('shows only done measurement rows under Completed', async () => {
    api.listMeasurementTasks.mockResolvedValue([
      measurement(),
      measurement({ id: 'm-done', title: 'Log access review SLA', status: 'completed', completed_date: '2026-10-01', value_text: '43%' }),
    ])
    renderPage()
    await screen.findByText('Log patching within SLA')
    fireEvent.click(screen.getByRole('radio', { name: 'Completed' }))
    // Wait for the reload to land, not for its loading state.
    expect(await screen.findByText('Log access review SLA')).toBeInTheDocument()
    expect(screen.queryByText('Log patching within SLA')).toBeNull()
  })

  it('offers Measurement as a type, shown client-side and never sent as task_type', async () => {
    renderPage()
    await screen.findByText('Log patching within SLA')
    mockApi.get.mockClear()
    fireEvent.click(screen.getByRole('radio', { name: 'Measurement' }))
    // Wait for the reload to land, not for its loading state.
    expect(await screen.findByText('Log patching within SLA')).toBeInTheDocument()
    expect(screen.queryByText('Bravo task')).toBeNull()
    for (const [url] of mockApi.get.mock.calls) expect(String(url)).not.toContain('task_type=measurement')
    // Another type shows no measurement row.
    fireEvent.click(screen.getByRole('radio', { name: 'Collection' }))
    await screen.findByText('Bravo task')
    expect(screen.queryByText('Log patching within SLA')).toBeNull()
  })

  it('shows the title linked to the metric detail, the ref, the team, the due date and the first-measurement note', async () => {
    api.listMeasurementTasks.mockResolvedValue([measurement({ first: true })])
    renderPage()
    await screen.findByText('Log patching within SLA')
    const row = measurementRow()
    const link = within(row).getByRole('link', { name: 'Log patching within SLA' })
    const p = new URLSearchParams(link.getAttribute('href')!.split('?')[1])
    expect(p.get('tab')).toBe('capability-posture')
    expect(p.get('view')).toBe('isms-metrics')
    expect(p.get('metric')).toBe(OBJ)
    expect(within(row).getByText('OBJ-09')).toBeInTheDocument()
    expect(within(row).getByText('Owning team: IT Operations')).toBeInTheDocument()
    expect(within(row).getByText('Mar 1, 2030')).toBeInTheDocument()
    expect(within(row).getByText('First measurement')).toBeInTheDocument()
    expect(within(row).getByText('Measurement')).toBeInTheDocument()
  })

  it('names the owner when there is no team, and says nothing of a first measurement that is not', async () => {
    api.listMeasurementTasks.mockResolvedValue([measurement({ owning_team_id: null, owning_team_name: null })])
    renderPage()
    await screen.findByText('Log patching within SLA')
    expect(within(measurementRow()).getByText('Owner: Pat Example')).toBeInTheDocument()
    expect(within(measurementRow()).queryByText('First measurement')).toBeNull()
  })

  it('shows a done row with its value and date, and no Log value', async () => {
    api.listMeasurementTasks.mockResolvedValue([
      measurement({ status: 'completed', completed_date: '2026-10-01', value_text: '43%' }),
    ])
    renderPage()
    await screen.findByText('Log patching within SLA')
    const row = measurementRow()
    expect(within(row).getByText('Logged 43% on Oct 1, 2026')).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: 'Log value' })).toBeNull()
  })

  it('has no edit, status, assign or delete control on a measurement row', async () => {
    renderPage()
    await screen.findByText('Log patching within SLA')
    const row = measurementRow()
    expect(within(row).getAllByRole('button').map((b) => b.textContent)).toEqual(['Log value'])
    expect(within(row).queryByRole('combobox')).toBeNull()
    expect(within(row).queryByRole('textbox')).toBeNull()
  })

  it('hides Log value from a viewer', async () => {
    role.editor = false
    renderPage()
    await screen.findByText('Log patching within SLA')
    expect(within(measurementRow()).queryByRole('button', { name: 'Log value' })).toBeNull()
  })

  it('opens the log-value form for that objective and period, and refetches after a log', async () => {
    renderPage()
    await screen.findByText('Log patching within SLA')
    fireEvent.click(within(measurementRow()).getByRole('button', { name: 'Log value' }))
    const modal = await screen.findByTestId('log-modal')
    expect(modal).toHaveAttribute('data-objective', OBJ)
    expect(modal).toHaveAttribute('data-period', '2026-10-01')
    api.listMeasurementTasks.mockResolvedValue([])
    const before = api.listMeasurementTasks.mock.calls.length
    await act(async () => {
      logModal.mock.lastCall![0].onLogged()
      logModal.mock.lastCall![0].onClose()
    })
    await waitFor(() => expect(screen.queryByText('Log patching within SLA')).toBeNull())
    expect(api.listMeasurementTasks.mock.calls.length).toBe(before + 1)
    expect(screen.queryByTestId('log-modal')).toBeNull()
    expect(screen.getByText('Bravo task')).toBeInTheDocument()
  })

  it('still shows the evidence tasks, and says nothing, when the measurement call fails', async () => {
    api.listMeasurementTasks.mockRejectedValue(Object.assign(new Error('Not Found'), { status: 404 }))
    renderPage()
    expect(await screen.findByText('Bravo task')).toBeInTheDocument()
    expect(screen.getByText('Alpha task')).toBeInTheDocument()
    expect(screen.queryByTestId(/^measurement-row-/)).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByText(/Not Found|could not|failed/i)).toBeNull()
  })
})
