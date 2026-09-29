/**
 * TaskDetailPage — won't-do and delete.
 *
 * What these tests defend:
 *   - Delete is shown to org admins only (the API refuses below admin, so a
 *     member must not be offered a button that will fail);
 *   - Delete asks first; confirming calls DELETE /evidence-tasks/{id}, tells
 *     the list via onTaskDeleted, and leaves the page via onTaskItemChange(null);
 *   - a failed delete keeps the dialog open with the error;
 *   - "Won't do" PATCHes status=wont_do and closes the task (the complete and
 *     won't-do buttons then disable, like a completed task);
 *   - the description renders links as links.
 */
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import TaskDetailPage from '../TaskDetailPage'

vi.mock('../../data/apiClient', () => ({
  apiClient: { get: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

let admin = true
vi.mock('../../hooks/useIsOrgAdmin', () => ({
  useIsOrgAdmin: () => admin,
}))

vi.mock('../ModernCommentThread', () => ({
  ModernCommentThread: () => <div>Comments</div>,
}))

import { apiClient } from '../../data/apiClient'
const mockApi = vi.mocked(apiClient)

const TASK = {
  id: 'task-001',
  evidence_tracking_id: 'et-a',
  evidence_id: 'E-0134',
  task_type: 'collection',
  title: 'Collect device inventory',
  description: 'Export from the console: https://admin.example.com/devices then run `export --all`',
  priority: 'high',
  due_date: '2026-08-28',
  status: 'in_progress',
  assigned_user_id: null,
  owning_team_id: null,
  frequency: 'quarterly',
  method_of_collection: 'API export',
  assigned_user: null,
}

function makeProps(overrides?: Partial<Parameters<typeof TaskDetailPage>[0]>) {
  return {
    organizationId: 'org-1',
    taskId: 'task-001',
    visibleTasks: [TASK],
    onTaskItemChange: vi.fn(),
    onNavigateToEvidence: vi.fn(),
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  admin = true
  mockApi.get.mockResolvedValue(TASK)
  mockApi.patch.mockResolvedValue({})
  mockApi.delete.mockResolvedValue(undefined)
})

describe('TaskDetailPage — delete', () => {
  it('hides Delete from non-admins', async () => {
    admin = false
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    expect(screen.queryByRole('button', { name: 'Delete task' })).toBeNull()
  })

  it('asks for confirmation, then deletes and leaves the page', async () => {
    const onTaskDeleted = vi.fn()
    const onTaskItemChange = vi.fn()
    await act(async () => {
      render(<TaskDetailPage {...makeProps({ onTaskDeleted, onTaskItemChange })} />)
    })

    fireEvent.click(screen.getByRole('button', { name: 'Delete task' }))
    expect(mockApi.delete).not.toHaveBeenCalled()
    const dialog = screen.getByRole('dialog', { name: 'Delete task' })
    expect(dialog).toHaveTextContent(/cannot be undone/i)

    // Two buttons match by name (the header CTA and the confirm); the confirm
    // is the one inside the dialog.
    const confirm = Array.from(dialog.querySelectorAll('button')).find(b => b.textContent === 'Delete task')!
    fireEvent.click(confirm)

    await waitFor(() => expect(mockApi.delete).toHaveBeenCalledWith('/evidence-tasks/task-001'))
    await waitFor(() => expect(onTaskDeleted).toHaveBeenCalledWith('task-001'))
    expect(onTaskItemChange).toHaveBeenCalledWith(null)
  })

  it('keeps the dialog open and shows the error when the delete fails', async () => {
    mockApi.delete.mockRejectedValueOnce(new Error('Admin role required'))
    const onTaskDeleted = vi.fn()
    await act(async () => { render(<TaskDetailPage {...makeProps({ onTaskDeleted })} />) })

    fireEvent.click(screen.getByRole('button', { name: 'Delete task' }))
    const dialog = screen.getByRole('dialog', { name: 'Delete task' })
    const confirm = Array.from(dialog.querySelectorAll('button')).find(b => b.textContent === 'Delete task')!
    fireEvent.click(confirm)

    expect(await screen.findByRole('alert')).toHaveTextContent('Admin role required')
    expect(screen.getByRole('dialog', { name: 'Delete task' })).toBeInTheDocument()
    expect(onTaskDeleted).not.toHaveBeenCalled()
  })

  it('Cancel closes the dialog without calling the API', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    fireEvent.click(screen.getByRole('button', { name: 'Delete task' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(mockApi.delete).not.toHaveBeenCalled()
  })
})

describe("TaskDetailPage — won't do", () => {
  it('PATCHes status=wont_do and closes the task', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })

    const wontDo = screen.getByRole('button', { name: "Mark won't do" })
    expect(wontDo).toBeEnabled()
    await act(async () => { fireEvent.click(wontDo) })

    expect(mockApi.patch).toHaveBeenCalledWith('/evidence-tasks/task-001', expect.objectContaining({ status: 'wont_do' }))
    await waitFor(() => expect(screen.getByRole('button', { name: "Mark won't do" })).toBeDisabled())
    expect(screen.getByRole('button', { name: 'Mark completed' })).toBeDisabled()
    expect(screen.getByText("Won't do")).toBeInTheDocument()
  })
})

describe('TaskDetailPage — rich description', () => {
  it('renders a URL in the description as a link and backticks as code', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    const link = screen.getByRole('link', { name: 'https://admin.example.com/devices' })
    expect(link).toHaveAttribute('href', 'https://admin.example.com/devices')
    expect(link).toHaveAttribute('target', '_blank')
    expect(document.querySelector('.task-detail-description-text code')?.textContent).toBe('export --all')
  })
})
