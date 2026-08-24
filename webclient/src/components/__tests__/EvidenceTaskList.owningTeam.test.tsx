/**
 * EvidenceTaskList: the task panel on an evidence item (#822 phase 4).
 *
 * This is the surface where the setup/collection/review split actually gets
 * made — three tasks on one evidence item, routinely three different
 * functions — so it is the surface where an override has to be legible at a
 * glance. A panel that shows three identical rows when one of them has been
 * handed to GRC has lost the only thing the column was added for.
 *
 * The unassigned row is checked deliberately. It is the one that used to
 * render nothing where the owner goes, which reads as nobody's problem when
 * in fact the evidence item's accountable team has it.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { EvidenceTaskList } from '../EvidenceTaskList'
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

vi.mock('../ModernCommentThread', () => ({ ModernCommentThread: () => null }))

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
    function: { id: 'fn-secops', key: 'sec_ops', name: 'Security Operations', is_active: true },
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

/** Collection inherits; review has been handed to GRC. The motivating case. */
const TASKS = [
  {
    id: 'task-collect',
    evidence_tracking_id: TRACKING,
    task_type: 'collection',
    title: 'Collect the export',
    priority: 'medium',
    status: 'not_started',
    due_date: '2026-12-31',
    owning_team_id: null,
  },
  {
    id: 'task-review',
    evidence_tracking_id: TRACKING,
    task_type: 'review',
    title: 'Review the export',
    priority: 'high',
    status: 'not_started',
    due_date: '2026-12-31',
    owning_team_id: 'team-grc',
  },
]

beforeEach(() => {
  vi.clearAllMocks()
  mockApi.get.mockResolvedValue(TASKS)
  mockListTeams.mockResolvedValue(TEAMS)
  mockListAssignments.mockResolvedValue({ [TRACKING]: [ACCOUNTABLE] })
  mockGetTeam.mockResolvedValue(GRC_DETAIL)
  mockMembers.mockResolvedValue([])
})

function renderList() {
  return render(
    <EvidenceTaskList
      evidenceTrackingId={TRACKING}
      evidenceId="E-001"
      organizationId={ORG}
    />
  )
}

describe('EvidenceTaskList owning team', () => {
  it('names the owning team on a task with no assignee', async () => {
    renderList()

    expect(await screen.findByText('Collect the export')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Inherited')).toBeInTheDocument())
    const inherited = screen.getByText('Inherited').parentElement as HTMLElement
    expect(within(inherited).getByText('Security Operations')).toBeInTheDocument()
    // The person the work actually reaches, named on the row.
    expect(within(inherited).getByText('Ana Ruiz')).toBeInTheDocument()
  })

  it('shows the setup/review split as a split, not as two identical rows', async () => {
    renderList()

    expect(await screen.findByText('Review the export')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Override')).toBeInTheDocument())
    const override = screen.getByText('Override').parentElement as HTMLElement
    expect(within(override).getByText('GRC')).toBeInTheDocument()
    // Both pills present at once — one inherited, one overridden.
    expect(screen.getByText('Inherited')).toBeInTheDocument()
  })

  it('warns when the evidence item has no accountable team to inherit', async () => {
    mockApi.get.mockResolvedValue([TASKS[0]])
    mockListAssignments.mockResolvedValue({ [TRACKING]: [] })

    renderList()

    expect(await screen.findByText('Collect the export')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('No owning team')).toBeInTheDocument())
  })

  it('claims nothing at all while the ownership read is still in flight', async () => {
    mockListAssignments.mockReturnValue(new Promise(() => {}))

    renderList()

    expect(await screen.findByText('Collect the export')).toBeInTheDocument()
    // "No owning team" here would be an accusation the page cannot yet
    // support, so an unresolved row says nothing rather than guessing.
    expect(screen.queryByText('No owning team')).not.toBeInTheDocument()
    expect(screen.queryByText('Inherited')).not.toBeInTheDocument()
  })
})
