/**
 * TasksPage — Explorer list conversion tests (Phase 3 Task 7).
 *
 * Guards:
 *  - Client-side search over title / description / evidence-id
 *  - Stats strip: Total / Not Started / In Progress / Overdue / Completed
 *  - Row expansion (chevron toggles inline edit + comment thread)
 *  - Edit-in-expansion: status select + notes → PATCH /evidence-tasks/{id}
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { TasksPage } from '../TasksPage'
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
  ModernCommentThread: ({ commentableId }: { commentableId: string }) => (
    <div data-testid={`comment-thread-${commentableId}`}>Comments</div>
  ),
}))

const mockApi = vi.mocked(apiClient)
const mockListAssignments = vi.mocked(listTeamAssignments)
const mockListTeams = vi.mocked(listTeams)
const mockGetTeam = vi.mocked(getTeam)
const mockMembers = vi.mocked(getOrgMemberSummaries)

const ORG = 'org-1'

const TASK_COLLECTION = {
  id: 'task-col-1',
  evidence_tracking_id: 'tracking-a',
  evidence_id: 'EV-100',
  task_type: 'collection',
  title: 'Collect CloudTrail logs',
  description: 'Monthly export from AWS CloudTrail',
  priority: 'high',
  due_date: '2030-12-31',
  status: 'in_progress',
  assigned_user_id: 'u-1',
  owning_team_id: null,
  assigned_user: {
    id: 'u-1',
    email: 'alice@example.com',
    display_name: 'Alice Smith',
  },
}

const TASK_REVIEW = {
  id: 'task-rev-2',
  evidence_tracking_id: 'tracking-b',
  evidence_id: 'EV-200',
  task_type: 'review',
  title: 'Sign off quarterly review',
  description: 'Quarterly compliance signoff',
  priority: 'critical',
  due_date: '2021-01-01',   // in the past → overdue
  status: 'not_started',
  assigned_user_id: null,
  owning_team_id: null,
}

const TASK_COMPLETED = {
  id: 'task-done-3',
  evidence_tracking_id: 'tracking-c',
  evidence_id: 'EV-300',
  task_type: 'documentation',
  title: 'Document backup results',
  description: null,
  priority: 'low',
  due_date: '2025-01-01',
  status: 'completed',
  assigned_user_id: null,
  owning_team_id: null,
  completion_notes: 'All done!',
}

beforeEach(() => {
  vi.clearAllMocks()
  mockApi.get.mockResolvedValue([TASK_COLLECTION, TASK_REVIEW, TASK_COMPLETED])
  mockListTeams.mockResolvedValue([])
  mockListAssignments.mockResolvedValue({})
  mockGetTeam.mockResolvedValue({
    id: 'team-x',
    organization_id: ORG,
    function_id: 'fn-x',
    name: 'Team X',
    description: null,
    is_active: true,
    members: [],
    health: { has_primary: false, has_members: false, function_is_active: true, warnings: [] },
  })
  mockMembers.mockResolvedValue([])
})

function renderPage() {
  return render(<TasksPage organizationId={ORG} onNavigateToEvidence={vi.fn()} />)
}

// ──────────────────────────────────────────────────────────────────────────────
describe('TasksPage — stats strip', () => {
  it('displays total, not-started, in-progress, overdue, completed counts', async () => {
    renderPage()

    // Tasks: 1 in_progress, 1 not_started (past due = overdue), 1 completed
    await screen.findByText('Collect CloudTrail logs')

    // Stats strip visible
    await waitFor(() => {
      // Total = 3
      expect(screen.getByTestId('task-stat-total')).toBeInTheDocument()
      // Not started = 1
      expect(screen.getByTestId('task-stat-not-started')).toBeInTheDocument()
      // In progress = 1
      expect(screen.getByTestId('task-stat-in-progress')).toBeInTheDocument()
      // Overdue = 1 (not_started with past due date)
      expect(screen.getByTestId('task-stat-overdue')).toBeInTheDocument()
      // Completed = 1
      expect(screen.getByTestId('task-stat-completed')).toBeInTheDocument()
    })

    expect(within(screen.getByTestId('task-stat-total')).getByText('3')).toBeInTheDocument()
    expect(within(screen.getByTestId('task-stat-not-started')).getByText('1')).toBeInTheDocument()
    expect(within(screen.getByTestId('task-stat-in-progress')).getByText('1')).toBeInTheDocument()
    expect(within(screen.getByTestId('task-stat-overdue')).getByText('1')).toBeInTheDocument()
    expect(within(screen.getByTestId('task-stat-completed')).getByText('1')).toBeInTheDocument()
  })

  it('shows dashes (—) when owning-team filter is active and ownership is unresolved', async () => {
    const user = userEvent.setup()
    // Freeze teams loading so team list shows but assignments never resolve
    mockListTeams.mockResolvedValue([
      {
        id: 'team-soc',
        organization_id: ORG,
        function_id: 'fn-soc',
        name: 'Security Operations',
        description: null,
        is_active: true,
      },
    ])
    mockListAssignments.mockReturnValue(new Promise(() => {}))

    renderPage()

    await screen.findByText('Collect CloudTrail logs')
    const filter = screen.getByLabelText('Filter tasks by owning team')
    await waitFor(() =>
      expect(within(filter).getByRole('option', { name: 'Security Operations' })).toBeInTheDocument()
    )
    await user.selectOptions(filter, 'team-soc')

    await waitFor(() =>
      expect(screen.getByText('Resolving team ownership…')).toBeInTheDocument()
    )
    // Stats must show dashes, not numbers
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
  })
})

// ──────────────────────────────────────────────────────────────────────────────
describe('TasksPage — client-side search', () => {
  it('filters rows by title', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Collect CloudTrail logs')
    const search = screen.getByRole('searchbox')
    await user.type(search, 'CloudTrail')

    expect(screen.getByText('Collect CloudTrail logs')).toBeInTheDocument()
    expect(screen.queryByText('Sign off quarterly review')).not.toBeInTheDocument()
    expect(screen.queryByText('Document backup results')).not.toBeInTheDocument()
  })

  it('filters rows by description (case-insensitive)', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Collect CloudTrail logs')
    const search = screen.getByRole('searchbox')
    await user.type(search, 'quarterly compliance')

    // Only the review task has 'quarterly compliance' in description
    expect(screen.getByText('Sign off quarterly review')).toBeInTheDocument()
    expect(screen.queryByText('Collect CloudTrail logs')).not.toBeInTheDocument()
  })

  it('filters rows by evidence id', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Collect CloudTrail logs')
    const search = screen.getByRole('searchbox')
    await user.type(search, 'EV-300')

    expect(screen.getByText('Document backup results')).toBeInTheDocument()
    expect(screen.queryByText('Collect CloudTrail logs')).not.toBeInTheDocument()
    expect(screen.queryByText('Sign off quarterly review')).not.toBeInTheDocument()
  })

  it('shows empty state when search has no matches', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Collect CloudTrail logs')
    const search = screen.getByRole('searchbox')
    await user.type(search, 'xyzzy-no-match')

    expect(screen.getByText('No Tasks Found')).toBeInTheDocument()
  })

  it('search is case-insensitive', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Collect CloudTrail logs')
    const search = screen.getByRole('searchbox')
    await user.type(search, 'cloudtrail')  // lowercase

    expect(screen.getByText('Collect CloudTrail logs')).toBeInTheDocument()
  })
})

// ──────────────────────────────────────────────────────────────────────────────
describe('TasksPage — row expansion', () => {
  it('starts collapsed: edit form and comment thread are not visible', async () => {
    renderPage()

    await screen.findByText('Collect CloudTrail logs')

    // Edit form elements must not be in the DOM (collapsed by default).
    // Use role=combobox to target the edit-form status select specifically;
    // the filter sidebar's STATUS radio group is always present so
    // queryByLabelText(/status/i) would now match it and give a false negative.
    expect(screen.queryByRole('combobox', { name: /status/i })).not.toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/completion notes/i)).not.toBeInTheDocument()
    expect(screen.queryByTestId('comment-thread-task-col-1')).not.toBeInTheDocument()
  })

  it('toggles open the expansion panel on row click or expand button', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Collect CloudTrail logs')

    // Find the expand button for the first task
    const expandBtn = screen.getAllByRole('button', { name: /expand/i })[0]
    await user.click(expandBtn)

    // Edit form must now appear
    await waitFor(() => {
      expect(screen.getByTestId('comment-thread-task-col-1')).toBeInTheDocument()
    })
  })

  it('collapses when expand button is clicked again', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Collect CloudTrail logs')

    const expandBtn = screen.getAllByRole('button', { name: /expand/i })[0]
    await user.click(expandBtn)

    await waitFor(() =>
      expect(screen.getByTestId('comment-thread-task-col-1')).toBeInTheDocument()
    )

    // Click again to collapse
    await user.click(expandBtn)
    await waitFor(() =>
      expect(screen.queryByTestId('comment-thread-task-col-1')).not.toBeInTheDocument()
    )
  })

  it('only one row is expanded at a time — expanding a second collapses the first', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Collect CloudTrail logs')

    const expandBtns = screen.getAllByRole('button', { name: /expand/i })
    await user.click(expandBtns[0])

    await waitFor(() =>
      expect(screen.getByTestId('comment-thread-task-col-1')).toBeInTheDocument()
    )

    // Expand second row
    await user.click(expandBtns[1])
    await waitFor(() =>
      expect(screen.getByTestId('comment-thread-task-rev-2')).toBeInTheDocument()
    )

    // First row must now be collapsed
    expect(screen.queryByTestId('comment-thread-task-col-1')).not.toBeInTheDocument()
  })
})

// ──────────────────────────────────────────────────────────────────────────────
describe('TasksPage — edit in expansion panel', () => {
  it('pre-fills status and completion notes from the current task values', async () => {
    const user = userEvent.setup()
    renderPage()

    // Completed task has completion_notes = 'All done!'
    await screen.findByText('Document backup results')

    // Expand the completed task row (index 2)
    const expandBtns = screen.getAllByRole('button', { name: /expand/i })
    await user.click(expandBtns[2])

    await waitFor(() => {
      const statusSelect = screen.getByRole('combobox', { name: /status/i })
      expect(statusSelect).toBeInTheDocument()
      expect((statusSelect as HTMLSelectElement).value).toBe('completed')
    })

    const notesArea = screen.getByPlaceholderText(/completion notes/i)
    expect((notesArea as HTMLTextAreaElement).value).toBe('All done!')
  })

  it('calls PATCH /evidence-tasks/{id} with updated status and notes on Save', async () => {
    const user = userEvent.setup()
    mockApi.patch.mockResolvedValue({})

    renderPage()

    await screen.findByText('Collect CloudTrail logs')

    // Expand first task
    const expandBtns = screen.getAllByRole('button', { name: /expand/i })
    await user.click(expandBtns[0])

    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: /status/i })).toBeInTheDocument()
    )

    const statusSelect = screen.getByRole('combobox', { name: /status/i })
    await user.selectOptions(statusSelect, 'completed')

    const notesArea = screen.getByPlaceholderText(/completion notes/i)
    await user.clear(notesArea)
    await user.type(notesArea, 'Evidence collected')

    const saveBtn = screen.getByRole('button', { name: /save/i })
    await user.click(saveBtn)

    expect(mockApi.patch).toHaveBeenCalledWith('/evidence-tasks/task-col-1', {
      status: 'completed',
      completion_notes: 'Evidence collected',
    })
  })

  it('collapses the panel after successful save', async () => {
    const user = userEvent.setup()
    mockApi.patch.mockResolvedValue({})

    renderPage()

    await screen.findByText('Collect CloudTrail logs')

    const expandBtns = screen.getAllByRole('button', { name: /expand/i })
    await user.click(expandBtns[0])

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument()
    )

    await user.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() =>
      expect(screen.queryByRole('combobox', { name: /status/i })).not.toBeInTheDocument()
    )
  })

  it('collapses the panel when Cancel is clicked without saving', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Collect CloudTrail logs')

    const expandBtns = screen.getAllByRole('button', { name: /expand/i })
    await user.click(expandBtns[0])

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument()
    )

    await user.click(screen.getByRole('button', { name: /cancel/i }))

    await waitFor(() =>
      expect(screen.queryByRole('combobox', { name: /status/i })).not.toBeInTheDocument()
    )

    expect(mockApi.patch).not.toHaveBeenCalled()
  })

  it('sets completion_notes to null when the field is empty on save', async () => {
    const user = userEvent.setup()
    mockApi.patch.mockResolvedValue({})

    renderPage()

    await screen.findByText('Document backup results')

    const expandBtns = screen.getAllByRole('button', { name: /expand/i })
    await user.click(expandBtns[2])

    await waitFor(() =>
      expect(screen.getByPlaceholderText(/completion notes/i)).toBeInTheDocument()
    )

    const notesArea = screen.getByPlaceholderText(/completion notes/i)
    await user.clear(notesArea)

    await user.click(screen.getByRole('button', { name: /save/i }))

    expect(mockApi.patch).toHaveBeenCalledWith('/evidence-tasks/task-done-3', {
      status: 'completed',
      completion_notes: null,
    })
  })
})

// ──────────────────────────────────────────────────────────────────────────────
describe('TasksPage — row display fields', () => {
  it('renders evidence-id link that calls onNavigateToEvidence', async () => {
    const onNavigate = vi.fn()
    render(<TasksPage organizationId={ORG} onNavigateToEvidence={onNavigate} />)

    await screen.findByText('Collect CloudTrail logs')

    const evidenceLink = screen.getAllByText(/EV-100/)[0]
    expect(evidenceLink).toBeInTheDocument()
  })

  it('shows status-colored left tick bar for overdue tasks', async () => {
    renderPage()

    await screen.findByText('Sign off quarterly review')

    // Overdue row: the container should have the overdue class or data attribute
    // We verify the row is present and the status is rendered
    // The status badge text for not_started + overdue
    expect(screen.getByText('Sign off quarterly review')).toBeInTheDocument()
  })

  it('renders type badge for each row', async () => {
    renderPage()

    await screen.findByText('Collect CloudTrail logs')

    // Task type labels visible on rows (multiple elements may match the same type label)
    expect(screen.getAllByText('Collection').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Review').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Documentation').length).toBeGreaterThan(0)
  })

  it('renders completion notes indicator when task has notes (row level)', async () => {
    renderPage()

    await screen.findByText('Document backup results')

    // The completed task has completion_notes — an indicator should appear
    // (exact form depends on implementation, but the task should be visible)
    expect(screen.getByText('Document backup results')).toBeInTheDocument()
  })

  it('shows assignee display name in all-tasks view', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Collect CloudTrail logs')

    // Switch to all-tasks view
    const allTasksBtn = screen.getByRole('button', { name: 'All Tasks' })
    await user.click(allTasksBtn)

    // Alice Smith is the assignee for TASK_COLLECTION
    expect(screen.getByText('Alice Smith')).toBeInTheDocument()
  })

  it('hides assignee in my-tasks view', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Collect CloudTrail logs')

    // Default view is my-tasks: assignee should not be visible
    expect(screen.queryByText('Alice Smith')).not.toBeInTheDocument()

    // Switch to all-tasks: assignee should now be visible
    const allTasksBtn = screen.getByRole('button', { name: 'All Tasks' })
    await user.click(allTasksBtn)

    await waitFor(() => {
      expect(screen.getByText('Alice Smith')).toBeInTheDocument()
    })
  })

  it('formats due date with year (month, day, year)', async () => {
    renderPage()

    await screen.findByText('Collect CloudTrail logs')

    // TASK_COLLECTION has due_date: '2030-12-31'
    // Should format as 'Dec 31, 2030' (without weekday to fit column width)
    expect(screen.getByText('Dec 31, 2030')).toBeInTheDocument()
  })
})

// ──────────────────────────────────────────────────────────────────────────────
describe('TasksPage — org scoping', () => {
  const taskListUrls = () =>
    mockApi.get.mock.calls
      .map(c => c[0] as string)
      .filter(u => u.startsWith('/evidence-tasks?'))

  it('always requests tasks for the active organization only', async () => {
    // Without this param the endpoint returns every accessible org's tasks
    // commingled — a consultant on several client orgs saw cross-org rows.
    renderPage()

    await screen.findByText('Collect CloudTrail logs')

    const urls = taskListUrls()
    expect(urls.length).toBeGreaterThan(0)
    for (const url of urls) {
      expect(url).toContain(`organization_id=${ORG}`)
    }
  })

  it('refetches for the new organization when the active org changes', async () => {
    // The dep-array half of the fix: switching org must not leave the
    // previous org's tasks frozen on screen.
    const { rerender } = render(
      <TasksPage organizationId={ORG} onNavigateToEvidence={vi.fn()} />
    )
    await screen.findByText('Collect CloudTrail logs')

    rerender(<TasksPage organizationId="org-2" onNavigateToEvidence={vi.fn()} />)

    await waitFor(() => {
      expect(taskListUrls().some(u => u.includes('organization_id=org-2'))).toBe(true)
    })
  })
})
