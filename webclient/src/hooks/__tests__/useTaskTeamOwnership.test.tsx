/**
 * useTaskTeamOwnership: who owns a task, inherited or overridden (#822 phase 4).
 *
 * Three things are checked hardest, because each is a way the feature stops
 * being true without anything visibly breaking:
 *
 *  - **Inherit is not "unowned".** A task with no ``owning_team_id`` resolves
 *    to its evidence item's accountable team. Rendering that as empty is how
 *    an unassigned task reads as nobody's problem, which is the live defect
 *    this phase exists to fix.
 *  - **A failed read is not an answer.** ``resolved`` must stay false when the
 *    assignment map could not be fetched, so a filter keyed on ownership does
 *    not narrow to nothing and report it as "this team owns no work".
 *  - **Unknown membership is not empty membership.** ``has_owner: null`` and
 *    ``has_owner: false`` are different claims; only the second earns the
 *    "No primary" warning.
 */
import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  taskOwnershipWarnings,
  useTaskTeamOwnership,
} from '../useTaskTeamOwnership'
import { getTeam, listTeamAssignments, listTeams } from '../../data/apiClient'
import type { Team, TeamAssignment, TeamDetail } from '../../types'

vi.mock('../../data/apiClient', () => ({
  listTeamAssignments: vi.fn(),
  listTeams: vi.fn(),
  getTeam: vi.fn(),
}))

const mockListAssignments = vi.mocked(listTeamAssignments)
const mockListTeams = vi.mocked(listTeams)
const mockGetTeam = vi.mocked(getTeam)

const ORG = 'org-1'
const TRACKING = 'tracking-1'

function assignment(
  teamId: string,
  teamName: string,
  isAccountable: boolean,
  options: { primaryName?: string; delegateName?: string; active?: boolean } = {}
): TeamAssignment {
  return {
    id: `assign-${teamId}`,
    type: 'evidence',
    item_id: TRACKING,
    team_id: teamId,
    organization_id: ORG,
    is_accountable: isAccountable,
    assigned_at: '2026-08-24T00:00:00',
    team: {
      id: teamId,
      name: teamName,
      is_active: options.active ?? true,
      function_id: 'fn-secops',
      function: {
        id: 'fn-secops',
        key: 'security_operations',
        name: 'Security Operations',
        is_active: true,
      },
      primary: options.primaryName
        ? {
            user_id: 'u-primary',
            membership_role: 'primary',
            user: {
              id: 'u-primary',
              email: 'ana@example.com',
              display_name: options.primaryName,
            },
          }
        : null,
      delegate: options.delegateName
        ? {
            user_id: 'u-delegate',
            membership_role: 'delegate',
            user: {
              id: 'u-delegate',
              email: 'bo@example.com',
              display_name: options.delegateName,
            },
          }
        : null,
    },
  }
}

function team(id: string, name: string, isActive = true): Team {
  return {
    id,
    organization_id: ORG,
    function_id: 'fn-secops',
    name,
    description: null,
    is_active: isActive,
  }
}

const TEAMS = [team('team-soc', 'Security Operations'), team('team-grc', 'GRC')]

beforeEach(() => {
  vi.clearAllMocks()
  mockListTeams.mockResolvedValue(TEAMS)
  // A benign default so tests that do not care about override membership do
  // not log a failure they never asked for; the tests that do care prime it.
  mockGetTeam.mockResolvedValue({
    id: 'team-unprimed',
    organization_id: ORG,
    function_id: 'fn-secops',
    name: 'Unprimed Team',
    description: null,
    is_active: true,
    members: [],
    health: {
      has_primary: false,
      has_members: false,
      function_is_active: true,
      warnings: [],
    },
  } as TeamDetail)
})

describe('useTaskTeamOwnership inheritance', () => {
  it('resolves a task with no owning team to its evidence item’s accountable team', async () => {
    mockListAssignments.mockResolvedValue({
      [TRACKING]: [assignment('team-soc', 'Security Operations', true, { primaryName: 'Ana Ruiz' })],
    })

    const tasks = [{ id: 't1', evidence_tracking_id: TRACKING, owning_team_id: null }]
    const { result } = renderHook(() => useTaskTeamOwnership(ORG, tasks))

    await waitFor(() => expect(result.current.resolved).toBe(true))

    const ownership = result.current.ownershipFor(tasks[0])
    expect(ownership.source).toBe('evidence')
    expect(ownership.team?.name).toBe('Security Operations')
    expect(ownership.team?.person_name).toBe('Ana Ruiz')
    // Inherited is a real answer, so it earns no warning at all.
    expect(taskOwnershipWarnings(ownership)).toEqual([])
  })

  it('treats an absent owning_team_id exactly like an explicit null', async () => {
    // A server that has not shipped the column yet must read as inheriting —
    // which is the truth — rather than as every task being unowned.
    mockListAssignments.mockResolvedValue({
      [TRACKING]: [assignment('team-soc', 'Security Operations', true)],
    })

    const tasks = [{ id: 't1', evidence_tracking_id: TRACKING }]
    const { result } = renderHook(() => useTaskTeamOwnership(ORG, tasks))

    await waitFor(() => expect(result.current.resolved).toBe(true))
    expect(result.current.ownershipFor(tasks[0]).source).toBe('evidence')
  })

  it('falls back to the delegate when the accountable team has no primary', async () => {
    mockListAssignments.mockResolvedValue({
      [TRACKING]: [
        assignment('team-soc', 'Security Operations', true, { delegateName: 'Bo Lee' }),
      ],
    })

    const tasks = [{ id: 't1', evidence_tracking_id: TRACKING, owning_team_id: null }]
    const { result } = renderHook(() => useTaskTeamOwnership(ORG, tasks))

    await waitFor(() => expect(result.current.resolved).toBe(true))
    expect(result.current.ownershipFor(tasks[0]).team?.person_name).toBe('Bo Lee')
  })

  it('ignores a consulted team — only the accountable one is inherited', async () => {
    mockListAssignments.mockResolvedValue({
      [TRACKING]: [
        assignment('team-grc', 'GRC', false, { primaryName: 'Consulted Person' }),
        assignment('team-soc', 'Security Operations', true, { primaryName: 'Ana Ruiz' }),
      ],
    })

    const tasks = [{ id: 't1', evidence_tracking_id: TRACKING, owning_team_id: null }]
    const { result } = renderHook(() => useTaskTeamOwnership(ORG, tasks))

    await waitFor(() => expect(result.current.resolved).toBe(true))
    expect(result.current.ownershipFor(tasks[0]).team?.name).toBe('Security Operations')
  })
})

describe('useTaskTeamOwnership override', () => {
  it('prefers the task’s own team over the evidence item’s', async () => {
    mockListAssignments.mockResolvedValue({
      [TRACKING]: [assignment('team-soc', 'Security Operations', true, { primaryName: 'Ana Ruiz' })],
    })
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
          user_id: 'u-grc',
          membership_role: 'primary',
          user: { id: 'u-grc', email: 'cy@example.com', display_name: 'Cy Okafor' },
        },
      ],
      health: {
        has_primary: true,
        has_members: true,
        function_is_active: true,
        warnings: [],
      },
    } as TeamDetail)

    const tasks = [{ id: 't1', evidence_tracking_id: TRACKING, owning_team_id: 'team-grc' }]
    const { result } = renderHook(() => useTaskTeamOwnership(ORG, tasks))

    await waitFor(() =>
      expect(result.current.ownershipFor(tasks[0]).team?.person_name).toBe('Cy Okafor')
    )
    const ownership = result.current.ownershipFor(tasks[0])
    expect(ownership.source).toBe('task')
    expect(ownership.team?.name).toBe('GRC')
  })

  it('reads one team per DISTINCT override, not one per task', async () => {
    mockListAssignments.mockResolvedValue({ [TRACKING]: [] })
    mockGetTeam.mockResolvedValue({
      id: 'team-grc',
      organization_id: ORG,
      function_id: 'fn-grc',
      name: 'GRC',
      description: null,
      is_active: true,
      members: [],
      health: {
        has_primary: false,
        has_members: false,
        function_is_active: true,
        warnings: [],
      },
    } as TeamDetail)

    const tasks = Array.from({ length: 25 }, (_, i) => ({
      id: `t${i}`,
      evidence_tracking_id: TRACKING,
      owning_team_id: 'team-grc',
    }))
    const { result } = renderHook(() => useTaskTeamOwnership(ORG, tasks))

    await waitFor(() => expect(mockGetTeam).toHaveBeenCalled())
    // Twenty-five rows, one team, one request. The N+1 this hook exists to
    // prevent would be twenty-five.
    expect(mockGetTeam).toHaveBeenCalledTimes(1)
  })
})

describe('useTaskTeamOwnership when nothing owns the task', () => {
  it('warns when neither the task nor its evidence item has a team', async () => {
    mockListAssignments.mockResolvedValue({ [TRACKING]: [] })

    const tasks = [{ id: 't1', evidence_tracking_id: TRACKING, owning_team_id: null }]
    const { result } = renderHook(() => useTaskTeamOwnership(ORG, tasks))

    await waitFor(() => expect(result.current.resolved).toBe(true))

    const ownership = result.current.ownershipFor(tasks[0])
    expect(ownership.team).toBeNull()
    expect(ownership.source).toBeNull()
    expect(taskOwnershipWarnings(ownership).map(w => w.label)).toEqual(['No owning team'])
  })

  it('warns when the owning team has nobody answerable on it', async () => {
    // Legal and permanent: the partial unique index caps `primary` at one per
    // team but cannot require one to exist, so this is a steady state and not
    // a loading artifact.
    mockListAssignments.mockResolvedValue({
      [TRACKING]: [assignment('team-soc', 'Security Operations', true)],
    })

    const tasks = [{ id: 't1', evidence_tracking_id: TRACKING, owning_team_id: null }]
    const { result } = renderHook(() => useTaskTeamOwnership(ORG, tasks))

    await waitFor(() => expect(result.current.resolved).toBe(true))
    expect(
      taskOwnershipWarnings(result.current.ownershipFor(tasks[0])).map(w => w.label)
    ).toEqual(['No primary'])
  })

  it('says nothing about a team whose membership is not known yet', () => {
    // has_owner: null is "we have not looked", which must not render as the
    // same accusation as "there is nobody there".
    expect(
      taskOwnershipWarnings({
        source: 'task',
        team: {
          id: 'team-grc',
          name: 'GRC',
          is_active: true,
          function_name: null,
          person_name: null,
          person_user_id: null,
          has_owner: null,
        },
      })
    ).toEqual([])
  })

  it('warns when the owning team has been archived', async () => {
    mockListAssignments.mockResolvedValue({
      [TRACKING]: [
        assignment('team-soc', 'Security Operations', true, {
          primaryName: 'Ana Ruiz',
          active: false,
        }),
      ],
    })

    const tasks = [{ id: 't1', evidence_tracking_id: TRACKING, owning_team_id: null }]
    const { result } = renderHook(() => useTaskTeamOwnership(ORG, tasks))

    await waitFor(() => expect(result.current.resolved).toBe(true))
    expect(
      taskOwnershipWarnings(result.current.ownershipFor(tasks[0])).map(w => w.label)
    ).toEqual(['Team archived'])
  })
})

describe('useTaskTeamOwnership when the read fails', () => {
  it('stays unresolved rather than answering "no team" for everything', async () => {
    mockListAssignments.mockRejectedValue(new Error('backend down'))

    const tasks = [{ id: 't1', evidence_tracking_id: TRACKING, owning_team_id: null }]
    const { result } = renderHook(() => useTaskTeamOwnership(ORG, tasks))

    await waitFor(() => expect(result.current.error).toBeTruthy())
    // The whole point: a filter reading this must not narrow to an empty list
    // and present it as "this team owns nothing".
    expect(result.current.resolved).toBe(false)
  })

  it('stays unresolved when the team list fails, even if assignments arrived', async () => {
    mockListAssignments.mockResolvedValue({ [TRACKING]: [] })
    mockListTeams.mockRejectedValue(new Error('teams unavailable'))

    const tasks = [{ id: 't1', evidence_tracking_id: TRACKING, owning_team_id: 'team-grc' }]
    const { result } = renderHook(() => useTaskTeamOwnership(ORG, tasks))

    await waitFor(() => expect(result.current.error).toBeTruthy())
    expect(result.current.resolved).toBe(false)
  })
})

/**
 * The API nests the override's team on the task (#822 phase 4, confirmed by
 * the lead as p4api's contract). It is an optimisation, not a dependency:
 * the id and the nested object do not necessarily ship together, so the hook
 * has to be right whichever arrives first.
 */
describe('useTaskTeamOwnership with a nested owning_team', () => {
  // The detail read is left pending throughout. That is the point: the nested
  // object exists so a row can name its team BEFORE (or without) that read,
  // and a test where the read had already landed would prove nothing.
  beforeEach(() => {
    mockGetTeam.mockReturnValue(new Promise(() => {}))
  })

  it('names the override from the payload without a second lookup', async () => {
    const { result } = renderHook(() =>
      useTaskTeamOwnership(ORG, [
        {
          id: 'task-1',
          evidence_tracking_id: TRACKING,
          owning_team_id: 'team-grc',
          owning_team: {
            id: 'team-grc',
            name: 'GRC',
            is_active: true,
            function: { id: 'fn-grc', name: 'Governance, Risk & Compliance' },
          },
        },
      ])
    )

    await waitFor(() => expect(result.current.resolved).toBe(true))
    const ownership = result.current.ownershipFor({
      id: 'task-1',
      evidence_tracking_id: TRACKING,
      owning_team_id: 'team-grc',
      owning_team: {
        id: 'team-grc',
        name: 'GRC',
        is_active: true,
        function: { id: 'fn-grc', name: 'Governance, Risk & Compliance' },
      },
    })

    expect(ownership.team?.name).toBe('GRC')
    expect(ownership.source).toBe('task')
    // The function comes free with the nested object; the team-list fallback
    // cannot supply it.
    expect(ownership.team?.function_name).toBe('Governance, Risk & Compliance')
    // And it must NOT claim the team has somebody on it — the nested object
    // carries no membership at all.
    expect(ownership.team?.has_owner).toBeNull()
  })

  it('still resolves the override when the payload omits the nested object', async () => {
    const { result } = renderHook(() =>
      useTaskTeamOwnership(ORG, [
        { id: 'task-1', evidence_tracking_id: TRACKING, owning_team_id: 'team-grc' },
      ])
    )

    await waitFor(() => expect(result.current.resolved).toBe(true))
    const ownership = result.current.ownershipFor({
      id: 'task-1',
      evidence_tracking_id: TRACKING,
      owning_team_id: 'team-grc',
    })

    // A server that ships the id before the nested object must not render
    // every override as an unnamed team.
    expect(ownership.team?.name).toBe('GRC')
    expect(ownership.source).toBe('task')
  })

  it('ignores a nested object that does not match the id it is filed under', async () => {
    const { result } = renderHook(() =>
      useTaskTeamOwnership(ORG, [
        { id: 'task-1', evidence_tracking_id: TRACKING, owning_team_id: 'team-grc' },
      ])
    )

    await waitFor(() => expect(result.current.resolved).toBe(true))
    const ownership = result.current.ownershipFor({
      id: 'task-1',
      evidence_tracking_id: TRACKING,
      owning_team_id: 'team-grc',
      // Stale or mismatched: the id is the authority, not the nested blob.
      owning_team: { id: 'team-soc', name: 'Security Operations' },
    })

    expect(ownership.team?.id).toBe('team-grc')
    expect(ownership.team?.name).not.toBe('Security Operations')
  })

  it('prefers a completed detail read over the nested object', async () => {
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

    const task = {
      id: 'task-1',
      evidence_tracking_id: TRACKING,
      owning_team_id: 'team-grc',
      owning_team: { id: 'team-grc', name: 'GRC', is_active: true },
    }
    const { result } = renderHook(() => useTaskTeamOwnership(ORG, [task]))

    await waitFor(() =>
      expect(result.current.ownershipFor(task).team?.person_name).toBe('Cy Okafor')
    )
    // The nested object carries no membership, so if it won here the row
    // could never name the person the work actually reaches.
    expect(result.current.ownershipFor(task).team?.has_owner).toBe(true)
  })
})
