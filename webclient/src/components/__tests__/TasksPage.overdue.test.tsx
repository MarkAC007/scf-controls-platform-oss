// SPDX-License-Identifier: AGPL-3.0-only
/**
 * TasksPage — one overdue rule for the "Overdue" tile and the row flags (#1282).
 *
 * What these tests defend:
 *   - a task is overdue when its due date is strictly before today, as calendar
 *     dates: a task due today is counted by neither the tile nor its row;
 *   - the tile and the rows cannot disagree, evidence and measurement rows alike;
 *   - a date-only due date is not shifted a day by the browser's time zone,
 *     west or east of UTC.
 */
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TasksPage, getDaysUntilDue, isTaskOverdue } from '../TasksPage'
import { apiClient, getOrgMemberSummaries, getTeam, listTeamAssignments, listTeams } from '../../data/apiClient'
import type { MeasurementTask } from '../../data/securityObjectivesApi'

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
vi.mock('../../hooks/useHasOrgRole', () => ({ useIsOrgEditor: () => true, useHasOrgRole: () => true }))

const mockApi = vi.mocked(apiClient)
const ORG = 'org-1'

const evidence = (id: string, due: string, title: string, status = 'not_started') => ({
  id, evidence_tracking_id: 't', evidence_id: `EV-${id}`, task_type: 'collection', title, priority: 'low',
  due_date: due, status, assigned_user_id: null, owning_team_id: null, created_at: null,
})

const measurement = (id: string, due: string, title: string): MeasurementTask => ({
  id, objective_id: 'obj', ref: 'OBJ-09', title, metric_name: 'Patching within SLA', unit: 'percent',
  frequency: 'quarterly', period_start: '2026-10-01', due_date: due, status: 'not_started', first: false,
  mine: true, owning_team_id: null, owning_team_name: null, owner_name: null, completed_date: null,
} as MeasurementTask)

/** Sets the browser's zone and clock. Node re-reads TZ when it is assigned; the check proves Date follows it. */
function clockAt(zone: string, utc: string) {
  vi.stubEnv('TZ', zone)
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(utc))
  const hour = new Intl.DateTimeFormat('en-US', { timeZone: zone, hour: 'numeric', hourCycle: 'h23' }).format(new Date())
  expect(new Date().getHours()).toBe(Number(hour))
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <TasksPage organizationId={ORG} onNavigateToEvidence={vi.fn()} />
    </QueryClientProvider>,
  )
}

const row = (title: string) => screen.getByText(title).closest('.tasks-row') as HTMLElement
const flaggedTitles = () =>
  Array.from(document.querySelectorAll('.tasks-row--overdue')).map(r => r.querySelector('.tasks-row-title-text')?.textContent)
const tile = () => screen.getByTestId('task-stat-overdue').querySelector('.tasks-stat-value')?.textContent

beforeEach(() => {
  vi.clearAllMocks()
  window.history.replaceState(null, '', '/?tab=tasks')
  api.listMeasurementTasks.mockResolvedValue([])
  api.getIsmsMetrics.mockResolvedValue({ items: [], summary: {}, measured_at: '2026-11-18T02:00:00' })
  vi.mocked(listTeams).mockResolvedValue([])
  vi.mocked(listTeamAssignments).mockResolvedValue({})
  vi.mocked(getTeam).mockResolvedValue({} as never)
  vi.mocked(getOrgMemberSummaries).mockResolvedValue([])
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllEnvs()
})

describe('getDaysUntilDue', () => {
  it('counts whole calendar days, whatever the time of day', () => {
    clockAt('UTC', '2026-11-20T00:00:01Z')
    expect(getDaysUntilDue('2026-11-19')).toBe(-1)
    expect(getDaysUntilDue('2026-11-20')).toBe(0)
    expect(getDaysUntilDue('2026-11-21')).toBe(1)
    vi.setSystemTime(new Date('2026-11-20T23:59:59Z'))
    expect(getDaysUntilDue('2026-11-19')).toBe(-1)
    expect(getDaysUntilDue('2026-11-20')).toBe(0)
    expect(getDaysUntilDue('2026-11-21')).toBe(1)
  })

  it('reads a timestamp by its date part', () => {
    clockAt('UTC', '2026-11-20T15:00:00Z')
    expect(getDaysUntilDue('2026-11-20T00:00:00')).toBe(0)
    expect(getDaysUntilDue('2026-11-19T23:59:59Z')).toBe(-1)
  })

  it('does not shift the due date a day west of UTC', () => {
    // 18:00 on 20 Nov in Los Angeles; the due date's midnight UTC passed ten hours ago.
    clockAt('America/Los_Angeles', '2026-11-21T02:00:00Z')
    expect(getDaysUntilDue('2026-11-20')).toBe(0)
    expect(getDaysUntilDue('2026-11-19')).toBe(-1)
  })

  it('does not shift the due date a day east of UTC', () => {
    // 01:00 on 21 Nov in Auckland; still 20 Nov in UTC.
    clockAt('Pacific/Auckland', '2026-11-20T12:00:00Z')
    expect(getDaysUntilDue('2026-11-20')).toBe(-1)
    expect(getDaysUntilDue('2026-11-21')).toBe(0)
  })

  it('counts across a daylight-saving change', () => {
    // Clocks went back in Los Angeles on 1 Nov 2026, a 25-hour day.
    clockAt('America/Los_Angeles', '2026-11-02T20:00:00Z')
    expect(getDaysUntilDue('2026-10-31')).toBe(-2)
    expect(getDaysUntilDue('2026-11-01')).toBe(-1)
  })
})

describe('isTaskOverdue', () => {
  beforeEach(() => clockAt('UTC', '2026-11-20T15:00:00Z'))

  it('is true only for an open task due before today', () => {
    expect(isTaskOverdue({ due_date: '2026-11-19', status: 'not_started' })).toBe(true)
    expect(isTaskOverdue({ due_date: '2026-11-19', status: 'in_progress' })).toBe(true)
    expect(isTaskOverdue({ due_date: '2026-11-20', status: 'not_started' })).toBe(false)
    expect(isTaskOverdue({ due_date: '2026-11-21', status: 'not_started' })).toBe(false)
  })

  it('is false for a closed task and for a task with no due date', () => {
    expect(isTaskOverdue({ due_date: '2026-11-19', status: 'completed' })).toBe(false)
    expect(isTaskOverdue({ due_date: '2026-11-19', status: 'wont_do' })).toBe(false)
    expect(isTaskOverdue({ due_date: undefined as never, status: 'not_started' })).toBe(false)
  })
})

describe('TasksPage — overdue tile and row flags', () => {
  beforeEach(() => {
    mockApi.get.mockResolvedValue([
      evidence('y', '2026-11-19', 'Yesterday task'),
      evidence('d', '2026-11-19', 'Done task', 'completed'),
      evidence('t', '2026-11-20', 'Today task'),
      evidence('m', '2026-11-21', 'Tomorrow task'),
    ])
    api.listMeasurementTasks.mockResolvedValue([
      measurement('measure-y', '2026-11-19', 'Yesterday measurement'),
      measurement('measure-t', '2026-11-20', 'Today measurement'),
    ])
  })

  it('does not count a task due today: the tile matches the flagged rows', async () => {
    clockAt('UTC', '2026-11-20T15:00:00Z')
    renderPage()
    await screen.findByText('Today measurement')

    expect(flaggedTitles()).toEqual(['Yesterday task', 'Yesterday measurement'])
    expect(tile()).toBe('2')
    expect(row('Today task')).not.toHaveClass('tasks-row--overdue')
    expect(row('Today task').querySelector('.tasks-due-countdown')).toHaveTextContent(/^0 days$/)
    expect(row('Yesterday task').querySelector('.tasks-due-countdown')).toHaveTextContent('1 days overdue')
  })

  it.each([
    // 18:00 on 20 Nov locally: today's tasks are still due today.
    ['America/Los_Angeles', '2026-11-21T02:00:00Z', ['Yesterday task', 'Yesterday measurement']],
    // 01:00 on 21 Nov locally: yesterday's and the 20th's tasks are past due, the 21st's is due today.
    ['Pacific/Auckland', '2026-11-20T12:00:00Z', ['Yesterday task', 'Yesterday measurement', 'Today task', 'Today measurement']],
  ])('agrees between tile and rows in %s', async (zone, utc, overdue) => {
    clockAt(zone, utc)
    renderPage()
    await screen.findByText('Today measurement')

    expect(flaggedTitles().sort()).toEqual([...overdue].sort())
    expect(tile()).toBe(String(overdue.length))
  })
})
