/**
 * TasksPage — CREATED column, sortable headers, won't-do status.
 *
 * What these tests defend:
 *   - the list keeps the server's order until a header is clicked;
 *   - each sortable header's button exposes aria-pressed and the direction in
 *     its name, a click sorts, a second click flips direction; the CREATED
 *     column defaults to newest first;
 *   - the created date renders per row (and a dash when absent);
 *   - a won't-do task is closed: not overdue, shown with its own label, and
 *     offered in the expansion's status select;
 *   - descriptions in the expansion render links as links.
 */
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { TasksPage, sortTasks } from '../TasksPage'
import {
  apiClient,
  getOrgMemberSummaries,
  getTeam,
  listTeamAssignments,
  listTeams,
} from '../../data/apiClient'

vi.mock('../../data/apiClient', () => ({
  apiClient: { get: vi.fn(), patch: vi.fn(), post: vi.fn() },
  listTeamAssignments: vi.fn(),
  listTeams: vi.fn(),
  getTeam: vi.fn(),
  getOrgMemberSummaries: vi.fn(),
}))

vi.mock('../ModernCommentThread', () => ({
  ModernCommentThread: () => <div>Comments</div>,
}))

const mockApi = vi.mocked(apiClient)
const ORG = 'org-1'

const base = {
  evidence_tracking_id: 'tracking',
  task_type: 'collection',
  assigned_user_id: null,
  owning_team_id: null,
}

// Server order: A, B, C. Deliberately NOT sorted by anything.
const TASK_A = {
  ...base,
  id: 'a',
  evidence_id: 'EV-200',
  title: 'Alpha task',
  description: 'See https://docs.example.com/alpha',
  priority: 'low',
  due_date: '2030-06-01',
  created_at: '2026-09-10T10:00:00Z',
  status: 'not_started',
}
const TASK_B = {
  ...base,
  id: 'b',
  evidence_id: 'EV-100',
  title: 'Bravo task',
  description: null,
  priority: 'critical',
  due_date: '2030-01-01',
  created_at: '2026-09-20T10:00:00Z',
  status: 'in_progress',
}
const TASK_C = {
  ...base,
  id: 'c',
  evidence_id: 'EV-300',
  title: 'Charlie task',
  description: null,
  priority: 'high',
  due_date: '2020-01-01', // past, but won't do → not overdue
  created_at: null,
  status: 'wont_do',
}

beforeEach(() => {
  vi.clearAllMocks()
  mockApi.get.mockResolvedValue([TASK_A, TASK_B, TASK_C])
  vi.mocked(listTeams).mockResolvedValue([])
  vi.mocked(listTeamAssignments).mockResolvedValue({})
  vi.mocked(getTeam).mockResolvedValue({} as never)
  vi.mocked(getOrgMemberSummaries).mockResolvedValue([])
})

function renderPage() {
  return render(<TasksPage organizationId={ORG} onNavigateToEvidence={vi.fn()} />)
}

function rowTitles(): string[] {
  return screen.getAllByRole('listitem').map(row => within(row).getByText(/task$/).textContent ?? '')
}

/** The sortable header cell for a column, found via its sort button. */
function header(label: string): HTMLElement {
  const prefix = `Sort by ${label}`
  return screen.getByRole('button', { name: n => n === prefix || n.startsWith(`${prefix},`) }).closest('[data-sort]') as HTMLElement
}

describe('sortTasks', () => {
  const rows = [TASK_A, TASK_B, TASK_C] as never[]

  it('sorts by due date with missing values last', () => {
    const withMissing = [...rows, { ...TASK_A, id: 'd', due_date: undefined }] as never[]
    const ids = sortTasks(withMissing, 'due', 'asc').map((t: { id: string }) => t.id)
    expect(ids).toEqual(['c', 'b', 'a', 'd'])
    const desc = sortTasks(withMissing, 'due', 'desc').map((t: { id: string }) => t.id)
    expect(desc).toEqual(['a', 'b', 'c', 'd'])
  })

  it('sorts by priority rank, not alphabetically', () => {
    expect(sortTasks(rows, 'priority', 'desc').map((t: { id: string }) => t.id)).toEqual(['b', 'c', 'a'])
  })

  it('sorts by created with missing values last', () => {
    expect(sortTasks(rows, 'created', 'desc').map((t: { id: string }) => t.id)).toEqual(['b', 'a', 'c'])
  })

  it('keeps two rows with missing dates in their original order', () => {
    const twoMissing = [{ ...TASK_C, id: 'x' }, { ...TASK_C, id: 'y' }, TASK_A] as never[]
    expect(sortTasks(twoMissing, 'created', 'asc').map((t: { id: string }) => t.id)).toEqual(['a', 'x', 'y'])
    expect(sortTasks(twoMissing, 'created', 'desc').map((t: { id: string }) => t.id)).toEqual(['a', 'x', 'y'])
  })

  it('sorts by team through the resolved team name, unowned last', () => {
    const teamOf = (t: { id: string }) => ({ a: 'Security', b: '', c: 'Engineering' }[t.id] ?? '')
    expect(sortTasks(rows, 'team', 'asc', teamOf as never).map((t: { id: string }) => t.id)).toEqual(['b', 'c', 'a'])
  })
})

describe('TasksPage — created column and sorting', () => {
  it('keeps server order until a header is clicked, then sorts and flips', async () => {
    renderPage()
    await screen.findByText('Alpha task')
    expect(rowTitles()).toEqual(['Alpha task', 'Bravo task', 'Charlie task'])

    const dueHeader = header('due')
    expect(dueHeader).toHaveAttribute('data-sort', 'none')

    await userEvent.click(within(dueHeader).getByRole('button'))
    expect(dueHeader).toHaveAttribute('data-sort', 'ascending')
    expect(within(dueHeader).getByRole('button', { name: 'Sort by due, ascending' })).toHaveAttribute('aria-pressed', 'true')
    expect(rowTitles()).toEqual(['Charlie task', 'Bravo task', 'Alpha task'])

    await userEvent.click(within(dueHeader).getByRole('button'))
    expect(dueHeader).toHaveAttribute('data-sort', 'descending')
    expect(rowTitles()).toEqual(['Alpha task', 'Bravo task', 'Charlie task'])
  })

  it('CREATED sorts newest first on the first click and shows the date per row', async () => {
    renderPage()
    await screen.findByText('Alpha task')

    const created = header('created')
    await userEvent.click(within(created).getByRole('button'))
    expect(created).toHaveAttribute('data-sort', 'descending')
    expect(rowTitles()).toEqual(['Bravo task', 'Alpha task', 'Charlie task'])

    const rows = screen.getAllByRole('listitem')
    expect(within(rows[0]).getByText('Sep 20, 2026')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Sep 10, 2026')).toBeInTheDocument()
    expect(within(rows[2]).getByText('—')).toBeInTheDocument()
  })

  it('exposes a sort button on the TEAM header too', async () => {
    renderPage()
    await screen.findByText('Alpha task')
    const team = header('team')
    expect(team).toHaveAttribute('data-sort', 'none')
    await userEvent.click(within(team).getByRole('button'))
    expect(team).toHaveAttribute('data-sort', 'ascending')
  })

  it('sorts by title alphabetically', async () => {
    renderPage()
    await screen.findByText('Alpha task')
    const task = header('task')
    await userEvent.click(within(task).getByRole('button'))
    await userEvent.click(within(task).getByRole('button'))
    expect(rowTitles()).toEqual(['Charlie task', 'Bravo task', 'Alpha task'])
  })
})

describe("TasksPage — won't do", () => {
  it('is not counted as overdue and carries its own label and tick colour', async () => {
    renderPage()
    await screen.findByText('Charlie task')

    expect(screen.getByTestId('task-stat-overdue')).toHaveTextContent('0')
    const row = screen.getAllByRole('listitem')[2]
    expect(within(row).getByText("Won't do")).toBeInTheDocument()
    expect(within(row).queryByText(/overdue/i)).toBeNull()
    expect(row.querySelector('.task-row-tick--wont-do')).not.toBeNull()
  })

  it("offers Won't Do in the expansion's status select", async () => {
    renderPage()
    await screen.findByText('Alpha task')
    const row = screen.getAllByRole('listitem')[0]
    await userEvent.click(within(row).getByRole('button', { name: /expand/i }))
    const select = within(row).getByRole('combobox')
    expect(within(select).getByRole('option', { name: /won.t do/i })).toBeInTheDocument()
  })
})

describe('TasksPage — rich description in expansion', () => {
  it('renders a URL in the description as a link', async () => {
    renderPage()
    await screen.findByText('Alpha task')
    const row = screen.getAllByRole('listitem')[0]
    await userEvent.click(within(row).getByRole('button', { name: /expand/i }))
    const link = within(row).getByRole('link', { name: 'https://docs.example.com/alpha' })
    expect(link).toHaveAttribute('href', 'https://docs.example.com/alpha')
  })
})
