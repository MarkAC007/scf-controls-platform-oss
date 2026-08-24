/**
 * TaskEditModal: the owning team has to reach the API, and "inherit" has to
 * be a value the API is told about (#822 §6).
 *
 * The trap this guards is the one a partial implementation falls into every
 * time: sending ``owning_team_id`` only when it is set. That makes the
 * override one-way — a user can move a task to GRC and can never move it
 * back, because the request that would clear it omits the field and the
 * server keeps what it had. Null is a value here, not an absence.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { TaskEditModal } from '../TaskEditModal'
import {
  apiClient,
  getOrgMemberSummaries,
  getTeam,
  listTeamAssignments,
  listTeams,
} from '../../data/apiClient'
import type { Team, TeamAssignment, TeamDetail } from '../../types'

vi.mock('../../data/apiClient', () => ({
  apiClient: { get: vi.fn(), patch: vi.fn(), post: vi.fn() },
  listTeamAssignments: vi.fn(),
  listTeams: vi.fn(),
  getTeam: vi.fn(),
  getOrgMemberSummaries: vi.fn(),
}))

const mockApi = vi.mocked(apiClient)
const mockListAssignments = vi.mocked(listTeamAssignments)
const mockListTeams = vi.mocked(listTeams)
const mockGetTeam = vi.mocked(getTeam)
const mockMembers = vi.mocked(getOrgMemberSummaries)

const ORG = 'org-1'
const TRACKING = 'tracking-1'

const TEAMS: Team[] = [
  {
    id: 'team-soc',
    organization_id: ORG,
    function_id: 'fn-secops',
    name: 'Security Operations',
    description: null,
    is_active: true,
  },
  {
    id: 'team-grc',
    organization_id: ORG,
    function_id: 'fn-grc',
    name: 'GRC',
    description: null,
    is_active: true,
  },
]

const ACCOUNTABLE: TeamAssignment = {
  id: 'assign-1',
  type: 'evidence',
  item_id: TRACKING,
  team_id: 'team-soc',
  organization_id: ORG,
  is_accountable: true,
  assigned_at: '2026-08-24T00:00:00',
  team: {
    id: 'team-soc',
    name: 'Security Operations',
    is_active: true,
    function_id: 'fn-secops',
    function: {
      id: 'fn-secops',
      key: 'security_operations',
      name: 'Security Operations',
      is_active: true,
    },
    primary: {
      user_id: 'u-ana',
      membership_role: 'primary',
      user: { id: 'u-ana', email: 'ana@example.com', display_name: 'Ana Ruiz' },
    },
    delegate: null,
  },
}

const GRC_DETAIL: TeamDetail = {
  id: 'team-grc',
  organization_id: ORG,
  function_id: 'fn-grc',
  name: 'GRC',
  description: null,
  is_active: true,
  members: [
    {
      id: 'm1',
      team_id: 'team-grc',
      user_id: 'u-cy',
      membership_role: 'primary',
      user: { id: 'u-cy', email: 'cy@example.com', display_name: 'Cy Okafor' },
    },
  ],
  health: { has_primary: true, has_members: true, function_is_active: true, warnings: [] },
}

function task(overrides: Record<string, unknown> = {}) {
  return {
    id: 'task-1',
    evidence_tracking_id: TRACKING,
    task_type: 'collection',
    title: 'Collect CloudTrail export',
    description: '',
    priority: 'medium',
    status: 'not_started',
    due_date: '2026-09-30',
    assigned_user_id: null,
    owning_team_id: null,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockApi.get.mockResolvedValue([])
  mockApi.patch.mockResolvedValue({})
  mockListTeams.mockResolvedValue(TEAMS)
  mockListAssignments.mockResolvedValue({ [TRACKING]: [ACCOUNTABLE] })
  mockGetTeam.mockResolvedValue(GRC_DETAIL)
  mockMembers.mockResolvedValue([])
})

function renderModal(taskOverrides: Record<string, unknown> = {}) {
  return render(
    <TaskEditModal
      task={task(taskOverrides)}
      organizationId={ORG}
      onClose={vi.fn()}
      onTaskUpdated={vi.fn()}
    />
  )
}

describe('TaskEditModal owning team', () => {
  it('shows what an unset task inherits rather than an empty field', async () => {
    renderModal()

    expect(
      await screen.findByText('Inherits from evidence item: Security Operations')
    ).toBeInTheDocument()
  })

  it('sends the override on save', async () => {
    const user = userEvent.setup()
    renderModal()

    const picker = await screen.findByLabelText('Owning team for this task')
    await waitFor(() => expect(picker).not.toBeDisabled())
    await user.selectOptions(picker, 'team-grc')
    await user.click(screen.getByRole('button', { name: 'Save Changes' }))

    await waitFor(() => expect(mockApi.patch).toHaveBeenCalledTimes(1))
    expect(mockApi.patch).toHaveBeenCalledWith(
      '/evidence-tasks/task-1',
      expect.objectContaining({ owning_team_id: 'team-grc' })
    )
  })

  it('sends null — not nothing — when an override is cleared back to inherit', async () => {
    const user = userEvent.setup()
    renderModal({ owning_team_id: 'team-grc' })

    const picker = await screen.findByLabelText('Owning team for this task')
    await waitFor(() => expect(picker).toHaveValue('team-grc'))
    await user.selectOptions(picker, '')
    await user.click(screen.getByRole('button', { name: 'Save Changes' }))

    await waitFor(() => expect(mockApi.patch).toHaveBeenCalledTimes(1))
    const body = mockApi.patch.mock.calls[0][1] as Record<string, unknown>
    // Present and null. Omitting the key would leave the task on GRC forever,
    // which makes the override a one-way door.
    expect('owning_team_id' in body).toBe(true)
    expect(body.owning_team_id).toBeNull()
  })

  it('leaves the team alone when the operator only changes the status', async () => {
    const user = userEvent.setup()
    renderModal({ owning_team_id: 'team-grc' })

    await waitFor(() =>
      expect(screen.getByLabelText('Owning team for this task')).toHaveValue('team-grc')
    )
    await user.click(screen.getByRole('button', { name: 'Save Changes' }))

    await waitFor(() => expect(mockApi.patch).toHaveBeenCalledTimes(1))
    expect(mockApi.patch).toHaveBeenCalledWith(
      '/evidence-tasks/task-1',
      expect.objectContaining({ owning_team_id: 'team-grc' })
    )
  })
})
