/**
 * TasksPage: the work queue has to SHOW team-resolved work, and its team
 * filter must never lie about what it is showing (#822 phase 4).
 *
 * Two failures are guarded here, and this repo has shipped both before:
 *
 *  - **A task nobody is assigned to is not unowned.** Its evidence item's
 *    accountable team has it. A row that renders nothing where the owner goes
 *    is how an unassigned task reads as nobody's problem — the same shape as
 *    an assignment field that no query consumed and a queue that sat
 *    permanently empty.
 *  - **An unanswered filter must not fall back to the unfiltered list.** When
 *    the user has asked for one team and ownership has not resolved, showing
 *    everything presents the whole organisation's work under one team's name.
 *    Showing nothing claims the team has none. Neither is true, so the page
 *    says which it is instead.
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
import type { Team, TeamAssignment, TeamAssignmentMap } from '../../types'

vi.mock('../../data/apiClient', () => ({
  apiClient: { get: vi.fn(), patch: vi.fn(), post: vi.fn() },
  listTeamAssignments: vi.fn(),
  listTeams: vi.fn(),
  getTeam: vi.fn(),
  getOrgMemberSummaries: vi.fn(),
}))

// The comment thread is not under test and pulls in its own API surface.
vi.mock('../ModernCommentThread', () => ({
  ModernCommentThread: () => null,
}))

const mockApi = vi.mocked(apiClient)
const mockListAssignments = vi.mocked(listTeamAssignments)
const mockListTeams = vi.mocked(listTeams)
const mockGetTeam = vi.mocked(getTeam)
const mockMembers = vi.mocked(getOrgMemberSummaries)

const ORG = 'org-1'
const TRACKING_A = 'tracking-a'
const TRACKING_B = 'tracking-b'

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

function accountable(itemId: string, teamId: string, teamName: string): TeamAssignment {
  return {
    id: `assign-${itemId}`,
    type: 'evidence',
    item_id: itemId,
    team_id: teamId,
    organization_id: ORG,
    is_accountable: true,
    assigned_at: '2026-08-24T00:00:00',
    team: {
      id: teamId,
      name: teamName,
      is_active: true,
      function_id: 'fn-secops',
      function: { id: 'fn-secops', key: 'sec_ops', name: 'Security Operations', is_active: true },
      primary: {
        user_id: 'u-ana',
        membership_role: 'primary',
        user: { id: 'u-ana', email: 'ana@example.com', display_name: 'Ana Ruiz' },
      },
      delegate: null,
    },
  }
}

/** Unassigned on purpose — the case the daily scheduler used to skip outright. */
const UNASSIGNED_TASK = {
  id: 'task-1',
  evidence_tracking_id: TRACKING_A,
  evidence_id: 'E-001',
  task_type: 'collection',
  title: 'Collect CloudTrail export',
  priority: 'medium',
  due_date: '2026-09-30',
  status: 'not_started',
  assigned_user_id: null,
  owning_team_id: null,
}

const OVERRIDDEN_TASK = {
  id: 'task-2',
  evidence_tracking_id: TRACKING_B,
  evidence_id: 'E-002',
  task_type: 'review',
  title: 'Sign off the quarter',
  priority: 'high',
  due_date: '2026-09-30',
  status: 'not_started',
  assigned_user_id: null,
  owning_team_id: 'team-grc',
}

const ASSIGNMENTS: TeamAssignmentMap = {
  [TRACKING_A]: [accountable(TRACKING_A, 'team-soc', 'Security Operations')],
  [TRACKING_B]: [accountable(TRACKING_B, 'team-soc', 'Security Operations')],
}

beforeEach(() => {
  vi.clearAllMocks()
  mockApi.get.mockResolvedValue([UNASSIGNED_TASK, OVERRIDDEN_TASK])
  mockListTeams.mockResolvedValue(TEAMS)
  mockListAssignments.mockResolvedValue(ASSIGNMENTS)
  mockGetTeam.mockResolvedValue({
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
  })
  mockMembers.mockResolvedValue([])
})

function renderPage() {
  return render(<TasksPage organizationId={ORG} onNavigateToEvidence={vi.fn()} />)
}

describe('TasksPage owning team column', () => {
  it('shows the inherited team on a task nobody is assigned to', async () => {
    renderPage()

    expect(await screen.findByText('Collect CloudTrail export')).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByText('Inherited').length).toBeGreaterThan(0))
    expect(screen.getAllByText('Security Operations').length).toBeGreaterThan(0)
  })

  it('distinguishes an override from an inherited team', async () => {
    renderPage()

    expect(await screen.findByText('Sign off the quarter')).toBeInTheDocument()
    // Without this pill a deliberate setup/review split is indistinguishable
    // from a team that simply followed the parent.
    await waitFor(() => expect(screen.getByText('Override')).toBeInTheDocument(), {
      // Three chained reads (assignments, teams, team detail) resolve behind
      // this pill; on a loaded CI runner the 1s default has timed out.
      timeout: 5000,
    })
    // Scoped to the badge: "GRC" also appears as an option in the team filter,
    // and matching that would prove nothing about the row.
    const badge = screen.getByText('Override').parentElement as HTMLElement
    expect(within(badge).getByText('GRC')).toBeInTheDocument()
  })

  it('warns on a task whose evidence item has no accountable team either', async () => {
    mockApi.get.mockResolvedValue([UNASSIGNED_TASK])
    mockListAssignments.mockResolvedValue({ [TRACKING_A]: [] })

    renderPage()

    expect(await screen.findByText('Collect CloudTrail export')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('No owning team')).toBeInTheDocument())
  })
})

describe('TasksPage owning-team filter', () => {
  it('keeps a task that only INHERITS the filtered team', async () => {
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByText('Collect CloudTrail export')).toBeInTheDocument()
    const filter = screen.getByLabelText('Filter tasks by owning team')
    await waitFor(() =>
      expect(
        within(filter).getByRole('option', { name: 'Security Operations' })
      ).toBeInTheDocument()
    )
    await user.selectOptions(filter, 'team-soc')

    // Inherited counts. A filter that matched only explicit overrides would
    // report Security Operations as owning almost nothing.
    await waitFor(() =>
      expect(screen.getByText('Collect CloudTrail export')).toBeInTheDocument()
    )
    // The overriding task belongs to GRC and must drop out.
    expect(screen.queryByText('Sign off the quarter')).not.toBeInTheDocument()
  })

  it('shows an empty state, not everything, when the filtered team owns nothing', async () => {
    const user = userEvent.setup()
    mockApi.get.mockResolvedValue([UNASSIGNED_TASK])
    renderPage()

    expect(await screen.findByText('Collect CloudTrail export')).toBeInTheDocument()
    const filter = screen.getByLabelText('Filter tasks by owning team')
    await waitFor(() =>
      expect(within(filter).getByRole('option', { name: 'GRC' })).toBeInTheDocument()
    )
    await user.selectOptions(filter, 'team-grc')

    await waitFor(() => expect(screen.getByText('No Tasks Found')).toBeInTheDocument())
    expect(screen.queryByText('Collect CloudTrail export')).not.toBeInTheDocument()
  })

  it('does not fall back to the unfiltered list while ownership is unresolved', async () => {
    const user = userEvent.setup()
    // Never resolves: the filter has been asked and cannot yet be answered.
    mockListAssignments.mockReturnValue(new Promise(() => {}))

    renderPage()

    expect(await screen.findByText('Collect CloudTrail export')).toBeInTheDocument()
    const filter = screen.getByLabelText('Filter tasks by owning team')
    await waitFor(() =>
      expect(
        within(filter).getByRole('option', { name: 'Security Operations' })
      ).toBeInTheDocument()
    )
    await user.selectOptions(filter, 'team-soc')

    await waitFor(() =>
      expect(screen.getByText('Resolving team ownership…')).toBeInTheDocument()
    )
    // Neither task may be on screen: showing them would present every team's
    // work under one team's name.
    expect(screen.queryByText('Collect CloudTrail export')).not.toBeInTheDocument()
    expect(screen.queryByText('Sign off the quarter')).not.toBeInTheDocument()
    // And the counts must not claim a total they cannot compute.
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
  })

  it('says the read failed rather than silently un-narrowing the list', async () => {
    const user = userEvent.setup()
    mockListAssignments.mockRejectedValue(new Error('backend down'))

    renderPage()

    expect(await screen.findByText('Collect CloudTrail export')).toBeInTheDocument()
    const filter = screen.getByLabelText('Filter tasks by owning team')
    await waitFor(() =>
      expect(
        within(filter).getByRole('option', { name: 'Security Operations' })
      ).toBeInTheDocument()
    )
    await user.selectOptions(filter, 'team-soc')

    await waitFor(() =>
      expect(screen.getByText(/Could not read team ownership/)).toBeInTheDocument()
    )
    // A failed read resolves nothing, so it must not resolve everything to
    // "no team" and hand back an empty list dressed up as an answer.
    expect(screen.queryByText('Collect CloudTrail export')).not.toBeInTheDocument()
  })

  it('shows every task again when the filter goes back to All', async () => {
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByText('Collect CloudTrail export')).toBeInTheDocument()
    const filter = screen.getByLabelText('Filter tasks by owning team')
    await waitFor(() =>
      expect(within(filter).getByRole('option', { name: 'GRC' })).toBeInTheDocument()
    )
    await user.selectOptions(filter, 'team-grc')
    await waitFor(() =>
      expect(screen.queryByText('Collect CloudTrail export')).not.toBeInTheDocument()
    )

    await user.selectOptions(filter, 'all')
    await waitFor(() =>
      expect(screen.getByText('Collect CloudTrail export')).toBeInTheDocument()
    )
    expect(screen.getByText('Sign off the quarter')).toBeInTheDocument()
  })
})
