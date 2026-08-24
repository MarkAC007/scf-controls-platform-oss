/**
 * TeamManagement: health signals are advisory. A team with no primary — and a
 * team with nobody on it at all — must render, warn, and stay usable.
 *
 * The promote-to-primary path is checked here for the thing that is easy to
 * get wrong: the incumbent is demoted by the backend inside the single PATCH,
 * so the UI must issue exactly one call, never a demote followed by a promote.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TeamManagement from '../TeamManagement'
import {
  addTeamMember,
  archiveTeam,
  createTeam,
  getOrgMembers,
  getTeam,
  listFunctions,
  listTeams,
  removeTeamMember,
  updateTeam,
  updateTeamMemberRole,
} from '../../data/apiClient'
import type { OrgFunction, Team, TeamDetail, TeamMember, TeamMembershipRole } from '../../types'

vi.mock('../../data/apiClient', () => ({
  listFunctions: vi.fn(),
  listTeams: vi.fn(),
  getTeam: vi.fn(),
  createTeam: vi.fn(),
  updateTeam: vi.fn(),
  archiveTeam: vi.fn(),
  addTeamMember: vi.fn(),
  updateTeamMemberRole: vi.fn(),
  removeTeamMember: vi.fn(),
  getOrgMembers: vi.fn(),
  // #822 phase 2: the contractor badge on each member row resolves member_type
  // through this. Empty is the honest default for a test that is not about
  // contractors — every row then renders exactly as it did before.
  getOrgMemberSummaries: vi.fn(() => Promise.resolve([])),
}))

vi.mock('react-hot-toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

const mockListFunctions = vi.mocked(listFunctions)
const mockListTeams = vi.mocked(listTeams)
const mockGetTeam = vi.mocked(getTeam)
const mockGetOrgMembers = vi.mocked(getOrgMembers)
const mockUpdateRole = vi.mocked(updateTeamMemberRole)
const mockAddMember = vi.mocked(addTeamMember)
const mockRemoveMember = vi.mocked(removeTeamMember)
const mockArchive = vi.mocked(archiveTeam)
const mockCreate = vi.mocked(createTeam)
const mockUpdateTeam = vi.mocked(updateTeam)

const ORG_ID = 'org-1'
const FUNCTION_ID = 'fn-security'

function fn(overrides: Partial<OrgFunction> = {}): OrgFunction {
  return {
    id: FUNCTION_ID,
    key: 'security',
    name: 'Security',
    description: 'Protects the organisation',
    display_order: 1,
    is_active: true,
    ...overrides,
  }
}

function member(
  userId: string,
  role: TeamMembershipRole,
  name: string
): TeamMember {
  return {
    id: `tm-${userId}`,
    team_id: 'team-1',
    user_id: userId,
    membership_role: role,
    user: { id: userId, email: `${userId}@example.com`, display_name: name },
  }
}

function team(overrides: Partial<TeamDetail> = {}): TeamDetail {
  return {
    id: 'team-1',
    organization_id: ORG_ID,
    function_id: FUNCTION_ID,
    name: 'Security Operations',
    description: 'Runs the SOC',
    is_active: true,
    members: [],
    health: {
      has_primary: false,
      has_members: false,
      function_is_active: true,
      warnings: [],
    },
    ...overrides,
  }
}

/** Wire the four load calls the component makes on mount. */
function primeLoad(teams: TeamDetail[], functions: OrgFunction[] = [fn()]) {
  mockListFunctions.mockResolvedValue(functions)
  mockListTeams.mockResolvedValue(teams as Team[])
  mockGetOrgMembers.mockResolvedValue([
    { id: 'u1', email: 'u1@example.com', display_name: 'Ada Lovelace' },
    { id: 'u2', email: 'u2@example.com', display_name: 'Grace Hopper' },
    { id: 'u3', email: 'u3@example.com', display_name: 'Alan Turing' },
  ])
  mockGetTeam.mockImplementation(async (_org: string, teamId: string) => {
    const found = teams.find(t => t.id === teamId)
    if (!found) throw new Error(`no such team ${teamId}`)
    return found
  })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('TeamManagement health warnings', () => {
  it('badges a team that has members but no primary', async () => {
    primeLoad([
      team({
        members: [member('u1', 'member', 'Ada Lovelace')],
        health: {
          has_primary: false,
          has_members: true,
          function_is_active: true,
          warnings: ['This team has no primary.'],
        },
      }),
    ])

    render(<TeamManagement organizationId={ORG_ID} />)

    expect(await screen.findByText('Security Operations')).toBeInTheDocument()
    expect(screen.getByText('No primary')).toBeInTheDocument()
    expect(screen.queryByText('No members')).not.toBeInTheDocument()
  })

  it('renders a team with zero members, warns, and does not block it', async () => {
    primeLoad([team({ members: [] })])

    render(<TeamManagement organizationId={ORG_ID} />)

    expect(await screen.findByText('Security Operations')).toBeInTheDocument()
    expect(screen.getByText('No members')).toBeInTheDocument()
    expect(screen.getByText('No primary')).toBeInTheDocument()
    expect(screen.getByText('0 members')).toBeInTheDocument()
    // Warning, not a block: the archive control is still live.
    expect(screen.getByRole('button', { name: 'Archive' })).toBeEnabled()
  })

  it('groups teams under their function, ordered by display_order', async () => {
    const opsFn = fn({ id: 'fn-ops', key: 'ops', name: 'Operations', display_order: 2 })
    primeLoad(
      [
        team({ id: 'team-1', name: 'Security Operations' }),
        team({ id: 'team-2', name: 'Service Desk', function_id: 'fn-ops' }),
      ],
      [opsFn, fn()]
    )

    render(<TeamManagement organizationId={ORG_ID} />)

    await screen.findByText('Security Operations')
    const headings = screen.getAllByRole('heading', { level: 2 }).map(h => h.textContent)
    expect(headings).toEqual(['Security', 'Operations'])
  })
})

describe('TeamManagement promote to primary', () => {
  it('confirms the demotion first, then issues exactly one PATCH', async () => {
    const user = userEvent.setup()
    const detail = team({
      members: [
        member('u1', 'primary', 'Ada Lovelace'),
        member('u2', 'member', 'Grace Hopper'),
      ],
      health: {
        has_primary: true,
        has_members: true,
        function_is_active: true,
        warnings: [],
      },
    })
    primeLoad([detail])
    mockUpdateRole.mockResolvedValue(member('u2', 'primary', 'Grace Hopper'))

    render(<TeamManagement organizationId={ORG_ID} />)

    await user.click(await screen.findByRole('button', { name: /Security Operations/ }))
    await user.selectOptions(
      screen.getByLabelText('Team role for Grace Hopper'),
      'primary'
    )

    // Nothing has been sent yet — the admin has to be told what it costs.
    expect(mockUpdateRole).not.toHaveBeenCalled()
    const confirmation = await screen.findByRole('alert')
    expect(confirmation).toHaveTextContent(/Ada Lovelace will be demoted to member/)

    await user.click(within(confirmation).getByRole('button', { name: 'Confirm' }))

    await waitFor(() => expect(mockUpdateRole).toHaveBeenCalledTimes(1))
    expect(mockUpdateRole).toHaveBeenCalledWith(ORG_ID, 'team-1', 'u2', 'primary')
  })

  it('sends nothing when the demotion is cancelled', async () => {
    const user = userEvent.setup()
    primeLoad([
      team({
        members: [
          member('u1', 'primary', 'Ada Lovelace'),
          member('u2', 'member', 'Grace Hopper'),
        ],
      }),
    ])

    render(<TeamManagement organizationId={ORG_ID} />)

    await user.click(await screen.findByRole('button', { name: /Security Operations/ }))
    await user.selectOptions(
      screen.getByLabelText('Team role for Grace Hopper'),
      'primary'
    )
    await user.click(
      within(await screen.findByRole('alert')).getByRole('button', { name: 'Cancel' })
    )

    expect(mockUpdateRole).not.toHaveBeenCalled()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('promotes without confirmation when the primary slot is empty', async () => {
    const user = userEvent.setup()
    primeLoad([team({ members: [member('u2', 'member', 'Grace Hopper')] })])
    mockUpdateRole.mockResolvedValue(member('u2', 'primary', 'Grace Hopper'))

    render(<TeamManagement organizationId={ORG_ID} />)

    await user.click(await screen.findByRole('button', { name: /Security Operations/ }))
    await user.selectOptions(
      screen.getByLabelText('Team role for Grace Hopper'),
      'primary'
    )

    await waitFor(() => expect(mockUpdateRole).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('TeamManagement membership and archiving', () => {
  it('offers only org members who are not already on the team', async () => {
    const user = userEvent.setup()
    primeLoad([team({ members: [member('u1', 'primary', 'Ada Lovelace')] })])

    render(<TeamManagement organizationId={ORG_ID} />)

    await user.click(await screen.findByRole('button', { name: /Security Operations/ }))
    const picker = screen.getByLabelText('Add a member to Security Operations')
    const options = within(picker).getAllByRole('option').map(o => o.textContent)
    expect(options).toEqual(['Select a person…', 'Grace Hopper', 'Alan Turing'])

    await user.selectOptions(picker, 'u2')
    await user.click(screen.getByRole('button', { name: 'Add member' }))

    await waitFor(() =>
      expect(mockAddMember).toHaveBeenCalledWith(ORG_ID, 'team-1', 'u2', 'member')
    )
  })

  it('removes a member after confirmation', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    primeLoad([team({ members: [member('u1', 'primary', 'Ada Lovelace')] })])

    render(<TeamManagement organizationId={ORG_ID} />)

    await user.click(await screen.findByRole('button', { name: /Security Operations/ }))
    await user.click(screen.getByRole('button', { name: 'Remove' }))

    await waitFor(() =>
      expect(mockRemoveMember).toHaveBeenCalledWith(ORG_ID, 'team-1', 'u1')
    )
  })

  it('archives rather than deletes, and says so before doing it', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    primeLoad([team()])
    mockArchive.mockResolvedValue(undefined)

    render(<TeamManagement organizationId={ORG_ID} />)

    await user.click(await screen.findByRole('button', { name: 'Archive' }))

    expect(confirmSpy.mock.calls[0][0]).toMatch(/nothing is deleted/)
    await waitFor(() => expect(mockArchive).toHaveBeenCalledWith(ORG_ID, 'team-1'))
  })

  it('restores an archived team by reactivating it', async () => {
    const user = userEvent.setup()
    primeLoad([team({ is_active: false })])
    mockUpdateTeam.mockResolvedValue(team({ is_active: true }))

    render(<TeamManagement organizationId={ORG_ID} />)

    await user.click(await screen.findByLabelText('Show archived teams'))
    await user.click(await screen.findByRole('button', { name: 'Restore' }))

    await waitFor(() =>
      expect(mockUpdateTeam).toHaveBeenCalledWith(ORG_ID, 'team-1', { is_active: true })
    )
  })

  it('creates a team against one or more required functions', async () => {
    const user = userEvent.setup()
    primeLoad([])
    mockCreate.mockResolvedValue(team({ id: 'team-new', name: 'Incident Response' }) as Team)

    render(<TeamManagement organizationId={ORG_ID} />)

    await user.click(await screen.findByRole('button', { name: 'New team' }))
    await user.type(screen.getByLabelText('Team name'), 'Incident Response')
    await user.selectOptions(screen.getByLabelText('Business functions'), FUNCTION_ID)
    await user.click(screen.getByRole('button', { name: 'Create team' }))

    await waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith(ORG_ID, {
        name: 'Incident Response',
        description: '',
        function_id: FUNCTION_ID,
        function_ids: [FUNCTION_ID],
      })
    )
  })

  it('sends every selected function while keeping the first as primary', async () => {
    const user = userEvent.setup()
    const opsFn = fn({ id: 'fn-ops', key: 'ops', name: 'Operations', display_order: 2 })
    primeLoad([], [fn(), opsFn])
    mockCreate.mockResolvedValue(team({ id: 'team-new' }) as Team)

    render(<TeamManagement organizationId={ORG_ID} />)

    await user.click(await screen.findByRole('button', { name: 'New team' }))
    await user.type(screen.getByLabelText('Team name'), 'Platform')
    await user.selectOptions(
      screen.getByLabelText('Business functions'),
      [FUNCTION_ID, 'fn-ops']
    )
    await user.click(screen.getByRole('button', { name: 'Create team' }))

    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith(ORG_ID, {
      name: 'Platform',
      description: '',
      function_id: FUNCTION_ID,
      function_ids: [FUNCTION_ID, 'fn-ops'],
    }))
  })
})
