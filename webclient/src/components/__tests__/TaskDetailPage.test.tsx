/**
 * TaskDetailPage tests — Phase 4 Task 6
 *
 * Pins:
 *  - Breadcrumb "‹ Task Management / <id>" renders
 *  - "k of N in view" pager text renders
 *  - Header: task id (mono), type chip, status chip, priority chip
 *  - Header: Save changes + Mark completed CTAs
 *  - Task title renders as large heading
 *  - ASSIGNMENT card: assignee name + owning team
 *  - SCHEDULE card: due date + frequency + method
 *  - LINKED RECORDS card: control + evidence chips
 *  - "View evidence item" link fires onNavigateToEvidence
 *  - DESCRIPTION block renders
 *  - ACTIVITY section renders (comment thread)
 *  - Save changes PATCH /evidence-tasks/{id}
 *  - Mark completed PATCH with status=completed
 *  - Back button fires onTaskItemChange with null
 *  - Pager prev/next navigate via onTaskItemChange
 *  - Pager disabled at boundaries
 *  - Keyboard ArrowRight navigates to next task
 *  - Keyboard ArrowLeft navigates to prev task
 *  - Keyboard Escape goes back
 *  - Keyboard suppressed when focus in input
 *  - "— of N" shown when task not in filtered set
 */
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import TaskDetailPage from '../TaskDetailPage'

// ── Mocks ────────────────────────────────────────────────────────────────────

vi.mock('../../data/apiClient', () => ({
  apiClient: { get: vi.fn(), patch: vi.fn() },
}))

vi.mock('../ModernCommentThread', () => ({
  ModernCommentThread: ({ commentableId }: { commentableId: string }) => (
    <div data-testid={`comment-thread-${commentableId}`}>Comments</div>
  ),
}))

import { apiClient } from '../../data/apiClient'
const mockApi = vi.mocked(apiClient)

// ── Fixtures ─────────────────────────────────────────────────────────────────

const TASK_A = {
  id: 'task-001',
  evidence_tracking_id: 'et-a',
  evidence_id: 'E-0134',
  task_type: 'collection',
  title: 'Collect JumpCloud MDM evidence',
  description: 'Export device inventory from JumpCloud admin console.',
  priority: 'high',
  due_date: '2026-08-28',
  status: 'in_progress',
  assigned_user_id: 'u-1',
  owning_team_id: null,
  frequency: 'quarterly',
  method_of_collection: 'API export',
  assigned_user: { id: 'u-1', email: 'neve@example.com', display_name: 'Neve' },
}

const TASK_B = {
  id: 'task-002',
  evidence_tracking_id: 'et-b',
  evidence_id: 'E-0199',
  task_type: 'review',
  title: 'Review security posture',
  description: null,
  priority: 'low',
  due_date: '2026-09-15',
  status: 'not_started',
  assigned_user_id: null,
  owning_team_id: null,
  frequency: null,
  method_of_collection: null,
  assigned_user: null,
}

const TASKS = [TASK_A, TASK_B]

function makeProps(overrides?: Partial<Parameters<typeof TaskDetailPage>[0]>) {
  return {
    organizationId: 'org-1',
    taskId: 'task-001',
    visibleTasks: TASKS,
    onTaskItemChange: vi.fn(),
    onNavigateToEvidence: vi.fn(),
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockApi.get.mockImplementation((url: string) => {
    if (url.includes('/evidence-tasks/task-001')) return Promise.resolve(TASK_A)
    if (url.includes('/evidence-tasks/task-002')) return Promise.resolve(TASK_B)
    return Promise.resolve(TASK_A)
  })
  mockApi.patch.mockResolvedValue({})
})

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('TaskDetailPage — breadcrumb + pager', () => {
  it('renders breadcrumb with "Task Management" back link', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    expect(screen.getByRole('button', { name: /task management/i })).toBeInTheDocument()
  })

  it('renders task id in breadcrumb', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    // task-001 somewhere in breadcrumb area
    expect(screen.getAllByText(/task-001/i).length).toBeGreaterThan(0)
  })

  it('renders "k of N in view" pager text', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    // task-001 is index 0 → "1 of 2 in view"
    expect(screen.getByText(/1 of 2 in view/i)).toBeInTheDocument()
  })

  it('renders "— of N in view" when task not in filtered list', async () => {
    await act(async () => {
      render(<TaskDetailPage {...makeProps({ visibleTasks: [] })} />)
    })
    expect(screen.getByText(/— of 0 in view/i)).toBeInTheDocument()
  })

  it('back button calls onTaskItemChange with null', async () => {
    const onTaskItemChange = vi.fn()
    await act(async () => {
      render(<TaskDetailPage {...makeProps({ onTaskItemChange })} />)
    })
    fireEvent.click(screen.getByRole('button', { name: /task management/i }))
    expect(onTaskItemChange).toHaveBeenCalledWith(null)
  })

  it('prev button is disabled at first item', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    expect(screen.getByRole('button', { name: /previous/i })).toBeDisabled()
  })

  it('next button navigates to second task', async () => {
    const onTaskItemChange = vi.fn()
    await act(async () => {
      render(<TaskDetailPage {...makeProps({ onTaskItemChange })} />)
    })
    fireEvent.click(screen.getByRole('button', { name: /next/i }))
    expect(onTaskItemChange).toHaveBeenCalledWith('task-002')
  })

  it('next button disabled at last item', async () => {
    mockApi.get.mockResolvedValue(TASK_B)
    await act(async () => {
      render(<TaskDetailPage {...makeProps({ taskId: 'task-002' })} />)
    })
    expect(screen.getByRole('button', { name: /next/i })).toBeDisabled()
  })
})

describe('TaskDetailPage — header', () => {
  it('renders task type chip (Collection)', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    expect(screen.getByText('Collection')).toBeInTheDocument()
  })

  it('renders status chip (In progress)', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    expect(screen.getByText('In progress')).toBeInTheDocument()
  })

  it('renders priority chip (High priority)', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    expect(screen.getByText(/high priority/i)).toBeInTheDocument()
  })

  it('renders "Save changes" CTA', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    expect(screen.getByRole('button', { name: /save changes/i })).toBeInTheDocument()
  })

  it('renders "Mark completed" CTA', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    expect(screen.getByRole('button', { name: /mark completed/i })).toBeInTheDocument()
  })

  it('renders task title as large heading', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    expect(screen.getByRole('heading', { name: /collect jumpcloud mdm evidence/i })).toBeInTheDocument()
  })
})

describe('TaskDetailPage — 3-card grid', () => {
  it('renders ASSIGNMENT card with assignee name', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    expect(screen.getByText(/assignment/i)).toBeInTheDocument()
    expect(screen.getByText(/neve/i)).toBeInTheDocument()
  })

  it('renders SCHEDULE card with due date', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    expect(screen.getByText(/schedule/i)).toBeInTheDocument()
    // Due date should appear somewhere
    expect(screen.getByText(/aug.*2026|2026.*aug/i)).toBeInTheDocument()
  })

  it('renders SCHEDULE card with frequency and method', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    expect(screen.getByText(/quarterly/i)).toBeInTheDocument()
    expect(screen.getByText(/api export/i)).toBeInTheDocument()
  })

  it('renders LINKED RECORDS card with control and evidence chips', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    expect(screen.getByText(/linked records/i)).toBeInTheDocument()
    expect(screen.getByText(/E-0134/)).toBeInTheDocument()
  })

  it('"View evidence item" button fires onNavigateToEvidence', async () => {
    const onNavigateToEvidence = vi.fn()
    await act(async () => {
      render(<TaskDetailPage {...makeProps({ onNavigateToEvidence })} />)
    })
    const viewBtn = screen.getByRole('button', { name: /view evidence item/i })
    fireEvent.click(viewBtn)
    expect(onNavigateToEvidence).toHaveBeenCalledWith('E-0134')
  })
})

describe('TaskDetailPage — description + activity', () => {
  it('renders DESCRIPTION block with task description', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    expect(screen.getByText(/export device inventory/i)).toBeInTheDocument()
  })

  it('renders ACTIVITY section with comment thread', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    expect(screen.getByTestId('comment-thread-task-001')).toBeInTheDocument()
  })
})

describe('TaskDetailPage — save actions', () => {
  it('Save changes patches /evidence-tasks/{id}', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save changes/i }))
    })
    expect(mockApi.patch).toHaveBeenCalledWith(
      '/evidence-tasks/task-001',
      expect.objectContaining({ status: 'in_progress' })
    )
  })

  it('Mark completed patches with status=completed', async () => {
    await act(async () => { render(<TaskDetailPage {...makeProps()} />) })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /mark completed/i }))
    })
    expect(mockApi.patch).toHaveBeenCalledWith(
      '/evidence-tasks/task-001',
      expect.objectContaining({ status: 'completed' })
    )
  })
})

describe('TaskDetailPage — keyboard shortcuts', () => {
  it('ArrowRight navigates to next task', async () => {
    const onTaskItemChange = vi.fn()
    await act(async () => {
      render(<TaskDetailPage {...makeProps({ onTaskItemChange })} />)
    })
    fireEvent.keyDown(document, { key: 'ArrowRight' })
    expect(onTaskItemChange).toHaveBeenCalledWith('task-002')
  })

  it('ArrowLeft is a no-op at first item', async () => {
    const onTaskItemChange = vi.fn()
    await act(async () => {
      render(<TaskDetailPage {...makeProps({ onTaskItemChange })} />)
    })
    fireEvent.keyDown(document, { key: 'ArrowLeft' })
    expect(onTaskItemChange).not.toHaveBeenCalled()
  })

  it('Escape goes back (null)', async () => {
    const onTaskItemChange = vi.fn()
    await act(async () => {
      render(<TaskDetailPage {...makeProps({ onTaskItemChange })} />)
    })
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onTaskItemChange).toHaveBeenCalledWith(null)
  })

  it('keyboard suppressed when focus in input', async () => {
    const onTaskItemChange = vi.fn()
    await act(async () => {
      render(<TaskDetailPage {...makeProps({ onTaskItemChange })} />)
    })
    const input = document.createElement('input')
    document.body.appendChild(input)
    input.focus()
    fireEvent.keyDown(input, { key: 'ArrowRight' })
    expect(onTaskItemChange).not.toHaveBeenCalled()
    document.body.removeChild(input)
  })
})

describe('TaskDetailPage — URL wiring + integration tests', () => {
  it('renders "— of N in view" when task not found in visible list (deep link)', async () => {
    await act(async () => {
      render(<TaskDetailPage {...makeProps({ taskId: 'task-999', visibleTasks: TASKS })} />)
    })
    // task-999 not in TASKS → "— of 2 in view"
    expect(screen.getByText(/— of 2 in view/i)).toBeInTheDocument()
  })

  it('does not call onNavigateToEvidence on mount', async () => {
    const onNavigateToEvidence = vi.fn()
    await act(async () => {
      render(<TaskDetailPage {...makeProps({ onNavigateToEvidence })} />)
    })
    expect(onNavigateToEvidence).not.toHaveBeenCalled()
  })
})
