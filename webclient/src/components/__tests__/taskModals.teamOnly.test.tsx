/**
 * Neither task modal offers a person, and neither sends one.
 *
 * The create and edit forms are separate assertions because they fail
 * differently. Create simply must not offer the field. Edit must additionally
 * not *re-send* what is already stored: it used to echo `assigned_user_id` back
 * on every save, and the API now refuses a non-null assignee -- so a task
 * carrying a person from before the rule would 422 on a title edit, an error
 * about a field the operator never touched.
 *
 * The payload assertions are on `Object.keys`, not on the value. A body
 * carrying `assigned_user_id: null` would pass a value check while still being
 * the wrong request: on create the key does not exist at all, and on edit
 * sending it would clear an assignee nobody asked to clear.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { TaskCreationModal } from '../TaskCreationModal'
import { TaskEditModal } from '../TaskEditModal'
import { apiClient } from '../../data/apiClient'

vi.mock('../../data/apiClient', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
}))

vi.mock('../TaskOwningTeamField', () => ({
  default: ({ idPrefix }: { idPrefix: string }) => (
    <div data-testid={`owning-team-field-${idPrefix}`}>Owning team</div>
  ),
}))

const mockApi = vi.mocked(apiClient)

const ORG = 'org-modals'
const TRACKING = 'tracking-modals'

const MEMBERS = [
  {
    user: {
      id: 'u-someone',
      email: 'someone@example.com',
      display_name: 'Someone Assignable',
    },
  },
]

beforeEach(() => {
  vi.clearAllMocks()
  mockApi.get.mockResolvedValue(MEMBERS)
  mockApi.post.mockResolvedValue({})
  mockApi.patch.mockResolvedValue({})
})

/** Every control that would let an operator pick a person. */
function expectsNoUserPicker() {
  expect(screen.queryByLabelText(/assign to/i)).not.toBeInTheDocument()
  expect(screen.queryByText(/^assign to$/i)).not.toBeInTheDocument()
  expect(screen.queryByText(/^assigned to$/i)).not.toBeInTheDocument()
  expect(screen.queryByText('Unassigned')).not.toBeInTheDocument()
  expect(
    screen.queryByText('Someone Assignable')
  ).not.toBeInTheDocument()
}

describe('TaskCreationModal', () => {
  it('offers a team and no person', async () => {
    render(
      <TaskCreationModal
        evidenceTrackingId={TRACKING}
        evidenceId="E-MODAL"
        organizationId={ORG}
        onClose={vi.fn()}
        onTaskCreated={vi.fn()}
      />
    )

    expect(
      await screen.findByTestId('owning-team-field-new-task')
    ).toBeInTheDocument()
    expectsNoUserPicker()
  })

  it('posts a body with no assigned_user_id key at all', async () => {
    const user = userEvent.setup()
    render(
      <TaskCreationModal
        evidenceTrackingId={TRACKING}
        evidenceId="E-MODAL"
        organizationId={ORG}
        onClose={vi.fn()}
        onTaskCreated={vi.fn()}
      />
    )

    await screen.findByTestId('owning-team-field-new-task')
    await user.click(screen.getByRole('button', { name: /create task/i }))

    await waitFor(() => expect(mockApi.post).toHaveBeenCalled())
    const [url, body] = mockApi.post.mock.calls[0]
    expect(url).toBe('/evidence-tasks')
    expect(Object.keys(body as object)).not.toContain('assigned_user_id')
    expect(Object.keys(body as object)).toContain('owning_team_id')
  })
})

describe('TaskEditModal', () => {
  const TASK_WITH_LEGACY_ASSIGNEE = {
    id: 'task-legacy',
    evidence_tracking_id: TRACKING,
    task_type: 'collection',
    title: 'Collect the export',
    description: '',
    priority: 'medium',
    status: 'not_started',
    due_date: '2026-12-31',
    owning_team_id: null,
    assigned_user_id: 'u-departed',
  }

  it('offers a team and no person, even on a task that carries one', async () => {
    render(
      <TaskEditModal
        task={TASK_WITH_LEGACY_ASSIGNEE}
        organizationId={ORG}
        onClose={vi.fn()}
        onTaskUpdated={vi.fn()}
      />
    )

    expect(
      await screen.findByTestId('owning-team-field-task-task-legacy')
    ).toBeInTheDocument()
    expectsNoUserPicker()
  })

  it('saves a task with a stored assignee without re-sending it', async () => {
    // The regression that matters. A non-null re-send is now a 422, so a save
    // that echoed the stored value back would break editing any pre-cutover
    // task -- and it would break it on a field the operator did not touch.
    const user = userEvent.setup()
    render(
      <TaskEditModal
        task={TASK_WITH_LEGACY_ASSIGNEE}
        organizationId={ORG}
        onClose={vi.fn()}
        onTaskUpdated={vi.fn()}
      />
    )

    await screen.findByTestId('owning-team-field-task-task-legacy')
    await user.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => expect(mockApi.patch).toHaveBeenCalled())
    const [url, body] = mockApi.patch.mock.calls[0]
    expect(url).toBe('/evidence-tasks/task-legacy')
    // Omitted, not nulled: clearing an assignee is a deliberate act with its
    // own path, never a side effect of saving an unrelated edit.
    expect(Object.keys(body as object)).not.toContain('assigned_user_id')
  })
})
