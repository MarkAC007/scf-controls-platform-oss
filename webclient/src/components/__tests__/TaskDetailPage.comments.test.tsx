/**
 * TaskDetailPage — the real comment thread on a non-Scoping surface (#1154).
 *
 * What these tests defend:
 *   - the page's own ACTIVITY label stays the section label, and the thread
 *     below it uses its plain heading, not a second bench header strip
 *     (the strip is opt-in, for the Scoping notes tab only);
 *   - the thread's heading is a real h4, so the page keeps a heading element.
 */
import { render, screen, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import TaskDetailPage from '../TaskDetailPage'

vi.mock('../../data/apiClient', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

vi.mock('../../hooks/useIsOrgAdmin', () => ({
  useIsOrgAdmin: () => true,
}))

import { apiClient } from '../../data/apiClient'
const mockApi = vi.mocked(apiClient)

const TASK = {
  id: 'task-001',
  evidence_tracking_id: 'et-a',
  evidence_id: 'E-0134',
  task_type: 'collection',
  title: 'Collect device inventory',
  description: 'Export the device list',
  priority: 'high',
  due_date: '2026-08-28',
  status: 'in_progress',
  assigned_user_id: null,
  owning_team_id: null,
  frequency: 'quarterly',
  method_of_collection: 'API export',
  assigned_user: null,
}

beforeEach(() => {
  vi.clearAllMocks()
  mockApi.get.mockImplementation((url: string) => {
    if (url.startsWith('/comments')) return Promise.resolve([])
    if (url.includes('/members')) return Promise.resolve([])
    return Promise.resolve(TASK)
  })
})

describe('TaskDetailPage — comment thread', () => {
  it('renders the thread under ACTIVITY with a plain h4 heading and no bench strip', async () => {
    let container!: HTMLElement
    await act(async () => {
      ;({ container } = render(
        <TaskDetailPage
          organizationId="org-1"
          taskId="task-001"
          visibleTasks={[TASK] as never}
          onTaskItemChange={vi.fn()}
          onNavigateToEvidence={vi.fn()}
        />,
      ))
    })

    const activity = container.querySelector('.task-detail-activity-section') as HTMLElement
    expect(activity).not.toBeNull()
    expect(activity).toHaveTextContent('ACTIVITY')

    const thread = activity.querySelector('.comment-thread') as HTMLElement
    expect(thread).not.toBeNull()
    expect(thread.querySelector('.container-header')).toBeNull()
    expect(screen.getByRole('heading', { level: 4, name: 'Comments · 0' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Post comment' })).toBeInTheDocument()
  })
})
