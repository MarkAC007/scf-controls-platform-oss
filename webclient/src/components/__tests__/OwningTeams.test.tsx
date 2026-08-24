/**
 * OwningTeams: which teams own a control or an evidence item.
 *
 * Two things are checked hardest here because both are easy to get wrong and
 * expensive when they are:
 *
 *  - "Teams own this, none is accountable" must WARN and stay usable. It is a
 *    legal state, and it is the state every item is in until somebody picks.
 *  - Promoting a team to accountable is ONE request — the POST upsert, since
 *    the API has no PATCH. The backend clears the incumbent in the same
 *    transaction, so a client that stood the old team down first, or that
 *    deleted and re-created, would race any other admin doing the same and
 *    could leave the item with nobody accountable if the second half failed.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import OwningTeams from '../OwningTeams'
import {
  assignTeamToItem,
  clearAccountableTeam,
  getItemTeamAssignments,
  listTeams,
  removeTeamAssignment,
  setAccountableTeam,
} from '../../data/apiClient'
import type { Team, TeamAssignment } from '../../types'

vi.mock('../../data/apiClient', () => ({
  getItemTeamAssignments: vi.fn(),
  listTeams: vi.fn(),
  assignTeamToItem: vi.fn(),
  setAccountableTeam: vi.fn(),
  clearAccountableTeam: vi.fn(),
  removeTeamAssignment: vi.fn(),
  // #822 phase 2: the accountable person may carry a contractor badge, which
  // is resolved through this. Empty means no badge, so these tests keep
  // asserting the ownership behaviour they were written for.
  getOrgMemberSummaries: vi.fn(() => Promise.resolve([])),
}))

vi.mock('react-hot-toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

const mockGetAssignments = vi.mocked(getItemTeamAssignments)
const mockListTeams = vi.mocked(listTeams)
const mockAssign = vi.mocked(assignTeamToItem)
const mockSetAccountable = vi.mocked(setAccountableTeam)
const mockClearAccountable = vi.mocked(clearAccountableTeam)
const mockRemove = vi.mocked(removeTeamAssignment)

const ORG_ID = 'org-1'
const CONTROL_ID = 'scoped-control-1'
const FN_SECURITY = 'fn-security'
const FN_OPS = 'fn-ops'

/** Mirrors the API's nested payload: the team, its function, and its primary. */
function assignment(
  id: string,
  teamId: string,
  teamName: string,
  isAccountable: boolean,
  options: { functionId?: string; functionName?: string; primaryName?: string } = {}
): TeamAssignment {
  const functionId = options.functionId ?? FN_SECURITY
  const functionName = options.functionName ?? 'Security'
  return {
    id,
    type: 'control',
    item_id: CONTROL_ID,
    team_id: teamId,
    organization_id: ORG_ID,
    is_accountable: isAccountable,
    assigned_at: '2026-08-24T00:00:00',
    team: {
      id: teamId,
      name: teamName,
      is_active: true,
      function_id: functionId,
      function: {
        id: functionId,
        key: functionName.toLowerCase(),
        name: functionName,
        is_active: true,
      },
      primary: options.primaryName
        ? {
            user_id: 'u1',
            membership_role: 'primary',
            user: { id: 'u1', email: 'ana@example.com', display_name: options.primaryName },
          }
        : null,
      delegate: null,
    },
  }
}

function team(id: string, name: string, functionId = FN_SECURITY): Team {
  return {
    id,
    organization_id: ORG_ID,
    function_id: functionId,
    name,
    description: null,
    is_active: true,
  }
}

const TEAMS = [
  team('team-soc', 'Security Operations'),
  team('team-ir', 'Incident Response'),
  team('team-desk', 'Service Desk', FN_OPS),
]

function primeLoad(assignments: TeamAssignment[]) {
  mockGetAssignments.mockResolvedValue(assignments)
  mockListTeams.mockResolvedValue(TEAMS)
}

function renderPanel(props: Partial<Parameters<typeof OwningTeams>[0]> = {}) {
  return render(
    <OwningTeams
      organizationId={ORG_ID}
      assignableType="control"
      assignableId={CONTROL_ID}
      canManage
      {...props}
    />
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('OwningTeams accountability warning', () => {
  it('badges an item whose teams include no accountable one', async () => {
    primeLoad([
      assignment('a1', 'team-soc', 'Security Operations', false),
      assignment('a2', 'team-ir', 'Incident Response', false),
    ])

    renderPanel()

    expect(await screen.findByText('Security Operations')).toBeInTheDocument()
    expect(screen.getByText('No accountable team')).toBeInTheDocument()
    // Advisory, not a block: the write controls are still live.
    expect(screen.getByRole('button', { name: 'Add team' })).toBeInTheDocument()
  })

  it('drops the badge once a team is accountable', async () => {
    primeLoad([
      assignment('a1', 'team-soc', 'Security Operations', true),
      assignment('a2', 'team-ir', 'Incident Response', false),
    ])

    renderPanel()

    expect(await screen.findByText('Security Operations')).toBeInTheDocument()
    expect(screen.queryByText('No accountable team')).not.toBeInTheDocument()
  })

  it('does not badge an item that no team owns yet', async () => {
    primeLoad([])

    renderPanel()

    expect(await screen.findByText(/No teams own this control yet/)).toBeInTheDocument()
    expect(screen.queryByText('No accountable team')).not.toBeInTheDocument()
  })
})

describe('OwningTeams marking a team accountable', () => {
  it('un-marks the incumbent in the UI and issues exactly one POST', async () => {
    const user = userEvent.setup()
    primeLoad([
      assignment('a1', 'team-soc', 'Security Operations', true),
      assignment('a2', 'team-ir', 'Incident Response', false),
    ])
    mockSetAccountable.mockResolvedValue(
      assignment('a2', 'team-ir', 'Incident Response', true)
    )

    renderPanel()

    const incumbent = await screen.findByLabelText(
      'Make Security Operations accountable for this control'
    )
    const challenger = screen.getByLabelText(
      'Make Incident Response accountable for this control'
    )
    expect(incumbent).toBeChecked()
    expect(challenger).not.toBeChecked()

    await user.click(challenger)

    await waitFor(() => expect(challenger).toBeChecked())
    // The incumbent is stood down by the same request that promoted the
    // challenger, and the UI must show that without being told twice.
    expect(incumbent).not.toBeChecked()

    expect(mockSetAccountable).toHaveBeenCalledTimes(1)
    // The upsert names the item and the team, not the assignment row: the
    // same call promotes a team whether or not it was already assigned.
    expect(mockSetAccountable).toHaveBeenCalledWith(ORG_ID, {
      type: 'control',
      itemId: CONTROL_ID,
      teamId: 'team-ir',
    })
    // Emphatically NOT a demote-then-promote pair, and not a DELETE-then-POST
    // either — both are the two-call race the upsert exists to remove.
    expect(mockClearAccountable).not.toHaveBeenCalled()
    expect(mockRemove).not.toHaveBeenCalled()
  })

  it('restores the incumbent when the request fails', async () => {
    const user = userEvent.setup()
    primeLoad([
      assignment('a1', 'team-soc', 'Security Operations', true),
      assignment('a2', 'team-ir', 'Incident Response', false),
    ])
    mockSetAccountable.mockRejectedValue(new Error('nope'))

    renderPanel()

    const incumbent = await screen.findByLabelText(
      'Make Security Operations accountable for this control'
    )
    await user.click(
      screen.getByLabelText('Make Incident Response accountable for this control')
    )

    await waitFor(() => expect(incumbent).toBeChecked())
    expect(mockSetAccountable).toHaveBeenCalledTimes(1)
  })
})

describe('OwningTeams membership', () => {
  it('offers only teams that do not already own the item', async () => {
    const user = userEvent.setup()
    primeLoad([assignment('a1', 'team-soc', 'Security Operations', true)])
    mockAssign.mockResolvedValue(assignment('a2', 'team-ir', 'Incident Response', false))

    renderPanel()

    const picker = await screen.findByLabelText('Add an owning team to this control')
    const options = within(picker).getAllByRole('option').map(o => o.textContent)
    expect(options).toEqual(['Select a team…', 'Incident Response', 'Service Desk'])

    await user.selectOptions(picker, 'team-ir')
    await user.click(screen.getByRole('button', { name: 'Add team' }))

    await waitFor(() =>
      expect(mockAssign).toHaveBeenCalledWith(ORG_ID, {
        type: 'control',
        item_id: CONTROL_ID,
        team_id: 'team-ir',
      })
    )
    // Added without an accountability claim — that is a separate, deliberate act.
    expect(mockSetAccountable).not.toHaveBeenCalled()
  })

  it('removes a team without touching the team itself', async () => {
    const user = userEvent.setup()
    primeLoad([assignment('a1', 'team-soc', 'Security Operations', true)])
    mockRemove.mockResolvedValue(undefined)

    renderPanel()

    await user.click(
      await screen.findByLabelText('Remove Security Operations from this control')
    )

    await waitFor(() => expect(mockRemove).toHaveBeenCalledWith(ORG_ID, 'a1'))
  })
})

describe('OwningTeams permissions', () => {
  it('hides every write control from a non-admin but still shows who owns what', async () => {
    primeLoad([
      assignment('a1', 'team-soc', 'Security Operations', true),
      assignment('a2', 'team-ir', 'Incident Response', false),
    ])

    renderPanel({ canManage: false })

    expect(await screen.findByText('Security Operations')).toBeInTheDocument()
    expect(screen.getByText('Incident Response')).toBeInTheDocument()
    expect(screen.getByText('Accountable')).toBeInTheDocument()

    expect(screen.queryByRole('button', { name: 'Add team' })).not.toBeInTheDocument()
    expect(
      screen.queryByLabelText('Add an owning team to this control')
    ).not.toBeInTheDocument()
    expect(
      screen.queryByLabelText('Make Incident Response accountable for this control')
    ).not.toBeInTheDocument()
    expect(
      screen.queryByLabelText('Remove Security Operations from this control')
    ).not.toBeInTheDocument()
    expect(
      screen.getByText(/Only organisation admins can change which teams own/)
    ).toBeInTheDocument()
  })

  it('names the team’s primary from the same payload, with no second request', async () => {
    primeLoad([
      assignment('a1', 'team-soc', 'Security Operations', true, {
        primaryName: 'Ana Ruiz',
      }),
    ])

    renderPanel()

    expect(await screen.findByText('Security Operations')).toBeInTheDocument()
    expect(screen.getByText('Ana Ruiz')).toBeInTheDocument()
    // One read for the assignments, one for the add-picker's team list. No
    // roster fetch — the primary rides along inside the assignment.
    expect(mockGetAssignments).toHaveBeenCalledTimes(1)
    expect(mockListTeams).toHaveBeenCalledTimes(1)
  })

  it('renders a team whose primary and delegate are both empty', async () => {
    // Every team looks like this the moment it is created.
    primeLoad([assignment('a1', 'team-soc', 'Security Operations', true)])

    renderPanel()

    expect(await screen.findByText('Security Operations')).toBeInTheDocument()
    expect(screen.getByText('Security')).toBeInTheDocument()
  })

  it('says teams grant no access, on every render', async () => {
    primeLoad([assignment('a1', 'team-soc', 'Security Operations', true)])

    renderPanel()

    expect(
      await screen.findByText(/Teams grant no access — permissions come from organisation roles/)
    ).toBeInTheDocument()
  })

  it('names the evidence item rather than a control when owning evidence', async () => {
    primeLoad([])

    renderPanel({ assignableType: 'evidence', assignableId: 'evidence-tracking-1' })

    expect(
      await screen.findByText(/No teams own this evidence item yet/)
    ).toBeInTheDocument()
    expect(mockGetAssignments).toHaveBeenCalledWith(
      ORG_ID,
      'evidence',
      'evidence-tracking-1'
    )
  })
})
