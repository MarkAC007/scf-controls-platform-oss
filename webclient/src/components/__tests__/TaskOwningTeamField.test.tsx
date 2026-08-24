/**
 * TaskOwningTeamField: the tri-state that is the whole point of #822 §6.
 *
 * The hardest thing to get right here is not the override — it is the default.
 * "Not set" means the task inherits its evidence item's accountable team, and
 * a field that renders that as an empty box has thrown away the one fact the
 * user needs before deciding whether to override: who has it now. So the
 * tests below assert that the inherited team is NAMED, in the picker and in
 * the sentence under it, without the user touching anything.
 *
 * Clearing an override must return the task to inheriting rather than
 * detaching it — there is no third "unowned" value and the field must never
 * offer one.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TaskOwningTeamField from '../TaskOwningTeamField'
import {
  getOrgMemberSummaries,
  getTeam,
  listTeamAssignments,
  listTeams,
} from '../../data/apiClient'
import type { Team, TeamAssignment, TeamDetail } from '../../types'

vi.mock('../../data/apiClient', () => ({
  listTeamAssignments: vi.fn(),
  listTeams: vi.fn(),
  getTeam: vi.fn(),
  getOrgMemberSummaries: vi.fn(),
}))

const mockListAssignments = vi.mocked(listTeamAssignments)
const mockListTeams = vi.mocked(listTeams)
const mockGetTeam = vi.mocked(getTeam)
const mockMembers = vi.mocked(getOrgMemberSummaries)

const ORG = 'org-1'
const TRACKING = 'tracking-1'

function team(id: string, name: string): Team {
  return {
    id,
    organization_id: ORG,
    function_id: 'fn-secops',
    name,
    description: null,
    is_active: true,
  }
}

const TEAMS = [team('team-soc', 'Security Operations'), team('team-grc', 'GRC')]

function accountable(
  teamId: string,
  teamName: string,
  primaryName?: string,
  primaryUserId = 'u-primary'
): TeamAssignment {
  return {
    id: `assign-${teamId}`,
    type: 'evidence',
    item_id: TRACKING,
    team_id: teamId,
    organization_id: ORG,
    is_accountable: true,
    assigned_at: '2026-08-24T00:00:00',
    team: {
      id: teamId,
      name: teamName,
      is_active: true,
      function_id: 'fn-secops',
      function: {
        id: 'fn-secops',
        key: 'security_operations',
        name: 'Security Operations',
        is_active: true,
      },
      primary: primaryName
        ? {
            user_id: primaryUserId,
            membership_role: 'primary',
            user: {
              id: primaryUserId,
              email: 'ana@example.com',
              display_name: primaryName,
            },
          }
        : null,
      delegate: null,
    },
  }
}

function grcDetail(primaryName: string | null): TeamDetail {
  return {
    id: 'team-grc',
    organization_id: ORG,
    function_id: 'fn-grc',
    name: 'GRC',
    description: null,
    is_active: true,
    members: primaryName
      ? [
          {
            id: 'm1',
            team_id: 'team-grc',
            user_id: 'u-grc',
            membership_role: 'primary',
            user: { id: 'u-grc', email: 'cy@example.com', display_name: primaryName },
          },
        ]
      : [],
    health: {
      has_primary: !!primaryName,
      has_members: !!primaryName,
      function_is_active: true,
      warnings: [],
    },
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockListTeams.mockResolvedValue(TEAMS)
  mockGetTeam.mockResolvedValue(grcDetail('Cy Okafor'))
  mockMembers.mockResolvedValue([])
})

function renderField(props: Partial<Parameters<typeof TaskOwningTeamField>[0]> = {}) {
  return render(
    <TaskOwningTeamField
      organizationId={ORG}
      evidenceTrackingId={TRACKING}
      value={null}
      onChange={vi.fn()}
      {...props}
    />
  )
}

describe('TaskOwningTeamField inheriting', () => {
  it('names the team it inherits instead of showing an empty box', async () => {
    mockListAssignments.mockResolvedValue({
      [TRACKING]: [accountable('team-soc', 'Security Operations', 'Ana Ruiz')],
    })

    renderField({ value: null })

    // The sentence, which is what a user reads before deciding to override.
    expect(
      await screen.findByText('Inherits from evidence item: Security Operations')
    ).toBeInTheDocument()
    // And the person who actually gets the work.
    expect(screen.getByText(/Ana Ruiz/)).toBeInTheDocument()
  })

  it('names the inherited team on the option itself, not just "Inherit"', async () => {
    mockListAssignments.mockResolvedValue({
      [TRACKING]: [accountable('team-soc', 'Security Operations', 'Ana Ruiz')],
    })

    renderField({ value: null })

    const picker = await screen.findByLabelText('Owning team for this task')
    await waitFor(() =>
      expect(
        within(picker).getByRole('option', {
          name: 'Inherit from evidence item (Security Operations)',
        })
      ).toBeInTheDocument()
    )
    expect(picker).toHaveValue('')
  })

  it('warns, rather than looking fine, when the evidence item has no accountable team', async () => {
    mockListAssignments.mockResolvedValue({ [TRACKING]: [] })

    renderField({ value: null })

    expect(
      await screen.findByText('Inherits from evidence item: no accountable team')
    ).toBeInTheDocument()
    expect(screen.getByText('No owning team')).toBeInTheDocument()
  })

  it('warns when the inherited team has nobody answerable on it', async () => {
    // Legal and permanent — a team with no primary is a steady state, not a
    // half-loaded one — so it warns instead of quietly resolving to nobody.
    mockListAssignments.mockResolvedValue({
      [TRACKING]: [accountable('team-soc', 'Security Operations')],
    })

    renderField({ value: null })

    expect(await screen.findByText('No primary')).toBeInTheDocument()
  })

  it('says a contractor is a contractor where it names one', async () => {
    mockListAssignments.mockResolvedValue({
      [TRACKING]: [accountable('team-soc', 'Security Operations', 'Ana Ruiz', 'u-ana')],
    })
    mockMembers.mockResolvedValue([
      {
        id: 'om-1',
        organization_id: ORG,
        user_id: 'u-ana',
        role: 'editor',
        member_type: 'external_contractor',
        user: { id: 'u-ana', email: 'ana@example.com', display_name: 'Ana Ruiz' },
      },
    ])

    renderField({ value: null })

    expect(
      await screen.findByLabelText('Ana Ruiz is an external contractor')
    ).toBeInTheDocument()
  })
})

describe('TaskOwningTeamField overriding', () => {
  it('reports an override as an override and says what it displaced', async () => {
    mockListAssignments.mockResolvedValue({
      [TRACKING]: [accountable('team-soc', 'Security Operations', 'Ana Ruiz')],
    })

    renderField({ value: 'team-grc' })

    expect(
      await screen.findByText('Overrides the evidence item: GRC')
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        'Without this override the task would follow its evidence item to Security Operations.'
      )
    ).toBeInTheDocument()
  })

  it('reports the chosen team id, not its name, when the user picks one', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    mockListAssignments.mockResolvedValue({
      [TRACKING]: [accountable('team-soc', 'Security Operations', 'Ana Ruiz')],
    })

    renderField({ value: null, onChange })

    const picker = await screen.findByLabelText('Owning team for this task')
    await waitFor(() =>
      expect(within(picker).getByRole('option', { name: 'GRC' })).toBeInTheDocument()
    )
    await user.selectOptions(picker, 'team-grc')

    expect(onChange).toHaveBeenCalledWith('team-grc')
  })

  it('returns to inheriting — null, not an empty string — when the override is cleared', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    mockListAssignments.mockResolvedValue({
      [TRACKING]: [accountable('team-soc', 'Security Operations', 'Ana Ruiz')],
    })

    renderField({ value: 'team-grc', onChange })

    const picker = await screen.findByLabelText('Owning team for this task')
    await waitFor(() => expect(picker).toHaveValue('team-grc'))
    await user.selectOptions(picker, '')

    // Null is the inherit signal the API expects. An empty string would be a
    // team id of '', and '' is not a team.
    expect(onChange).toHaveBeenCalledWith(null)
  })

  it('offers no way to make a task unowned', async () => {
    mockListAssignments.mockResolvedValue({
      [TRACKING]: [accountable('team-soc', 'Security Operations', 'Ana Ruiz')],
    })

    renderField({ value: null })

    const picker = await screen.findByLabelText('Owning team for this task')
    await waitFor(() => expect(within(picker).getAllByRole('option')).toHaveLength(3))
    const labels = within(picker)
      .getAllByRole('option')
      .map(option => option.textContent)
    // Inherit, plus the organisation's two teams. Nothing that detaches.
    expect(labels).toEqual([
      'Inherit from evidence item (Security Operations)',
      'GRC',
      'Security Operations',
    ])
  })
})
