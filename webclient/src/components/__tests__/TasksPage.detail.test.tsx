/**
 * TasksPage → TaskDetailPage routing tests — Phase 4 Task 6
 *
 * The TasksPage now accepts taskItem + onTaskItemChange.
 * Row TITLE click → TaskDetailPage
 * Row expand (chevron) → row expansion (existing behavior unchanged)
 *
 * Pins:
 *  - Default (taskItem null): list shows, title click calls onTaskItemChange(id)
 *  - taskItem set: TaskDetailPage shows
 *  - Title click (not expand button) calls onTaskItemChange with the task id
 *  - Expansion chevron still toggles expansion (unchanged behavior)
 *  - Back in detail calls onTaskItemChange(null) → list visible
 */
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { TasksPage } from '../TasksPage'
import {
  apiClient,
  listTeamAssignments,
  listTeams,
  getOrgMemberSummaries,
} from '../../data/apiClient'

vi.mock('../../data/apiClient', () => ({
  apiClient: { get: vi.fn(), patch: vi.fn() },
  listTeamAssignments: vi.fn(),
  listTeams: vi.fn(),
  getOrgMemberSummaries: vi.fn(),
}))

vi.mock('../ModernCommentThread', () => ({
  ModernCommentThread: ({ commentableId }: { commentableId: string }) => (
    <div data-testid={`comment-thread-${commentableId}`}>Comments</div>
  ),
}))

vi.mock('../TaskDetailPage', () => ({
  default: ({ taskId, onTaskItemChange }: { taskId: string; onTaskItemChange: (id: string | null) => void }) => (
    <div data-testid={`task-detail-${taskId}`}>
      <button onClick={() => onTaskItemChange(null)}>Back to Tasks</button>
    </div>
  ),
}))

const mockApi = vi.mocked(apiClient)
const mockListTeams = vi.mocked(listTeams)
const mockListAssignments = vi.mocked(listTeamAssignments)
const mockMembers = vi.mocked(getOrgMemberSummaries)

const TASK_A = {
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
  assigned_user: { id: 'u-1', email: 'alice@example.com', display_name: 'Alice Smith' },
}

beforeEach(() => {
  vi.clearAllMocks()
  mockApi.get.mockResolvedValue([TASK_A])
  mockListTeams.mockResolvedValue([])
  mockListAssignments.mockResolvedValue({})
  mockMembers.mockResolvedValue([])
})

function renderPage(overrides?: Partial<Parameters<typeof TasksPage>[0]>) {
  return render(
    <TasksPage
      organizationId="org-1"
      onNavigateToEvidence={vi.fn()}
      taskItem={null}
      onTaskItemChange={vi.fn()}
      {...overrides}
    />
  )
}

describe('TasksPage — title click → detail routing', () => {
  it('list shows tasks when taskItem is null', async () => {
    await act(async () => { renderPage({ taskItem: null }) })
    expect(screen.getByText('Collect CloudTrail logs')).toBeInTheDocument()
    expect(screen.queryByTestId('task-detail-task-col-1')).not.toBeInTheDocument()
  })

  it('shows TaskDetailPage when taskItem is set', async () => {
    await act(async () => { renderPage({ taskItem: 'task-col-1' }) })
    expect(screen.getByTestId('task-detail-task-col-1')).toBeInTheDocument()
  })

  it('title click calls onTaskItemChange with the task id', async () => {
    const onTaskItemChange = vi.fn()
    await act(async () => { renderPage({ taskItem: null, onTaskItemChange }) })

    // Wait for tasks to load
    await screen.findByText('Collect CloudTrail logs')

    // Click the title (not the expand chevron)
    const titleBtn = screen.getByRole('button', { name: /collect cloudtrail logs/i })
    fireEvent.click(titleBtn)
    expect(onTaskItemChange).toHaveBeenCalledWith('task-col-1')
  })

  it('back in detail calls onTaskItemChange(null)', async () => {
    const onTaskItemChange = vi.fn()
    await act(async () => { renderPage({ taskItem: 'task-col-1', onTaskItemChange }) })

    fireEvent.click(screen.getByText('Back to Tasks'))
    expect(onTaskItemChange).toHaveBeenCalledWith(null)
  })

  it('expand chevron still toggles expansion (unchanged behavior)', async () => {
    await act(async () => { renderPage({ taskItem: null }) })

    await screen.findByText('Collect CloudTrail logs')

    const expandBtn = screen.getByRole('button', { name: /expand/i })
    await act(async () => { fireEvent.click(expandBtn) })

    // The expansion panel should show (not a detail page)
    await waitFor(() => {
      expect(screen.getByTestId('comment-thread-task-col-1')).toBeInTheDocument()
    })
    // Detail page should NOT be shown
    expect(screen.queryByTestId('task-detail-task-col-1')).not.toBeInTheDocument()
  })
})
