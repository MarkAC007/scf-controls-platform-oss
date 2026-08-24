/**
 * The list half of team ownership: one batch read, then filtering.
 *
 * The N+1 this guards against is not hypothetical — the controls list renders
 * hundreds of rows and a per-row "who owns this?" is seconds of wall clock
 * against a real organisation. So the request count is asserted with a list
 * big enough for the difference to be unmistakable: 250 rows, one request.
 *
 * The harness below is the integration the real lists perform — hook once at
 * the top, index, filter with the shared predicate, read per row — rather than
 * ControlScoping itself, which would need eight mocks and then assert on them.
 * `teamAssignments.n1.test.ts` holds the real lists to the same shape.
 */
import { useMemo, useState } from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TeamListFilters, { ALL } from '../TeamListFilters'
import {
  accountableTeamLabel,
  matchesTeamFilters,
  useTeamAssignments,
} from '../../hooks/useTeamAssignments'
import { listFunctions, listTeams, listTeamAssignments } from '../../data/apiClient'
import type { OrgFunction, Team, TeamAssignment, TeamAssignmentMap } from '../../types'

vi.mock('../../data/apiClient', () => ({
  listTeamAssignments: vi.fn(),
  listTeams: vi.fn(),
  listFunctions: vi.fn(),
}))

const mockListAssignments = vi.mocked(listTeamAssignments)
const mockListTeams = vi.mocked(listTeams)
const mockListFunctions = vi.mocked(listFunctions)

const ORG_ID = 'org-1'
const FN_SECURITY = 'fn-security'
const FN_OPS = 'fn-ops'

const FUNCTIONS: OrgFunction[] = [
  { id: FN_SECURITY, key: 'security', name: 'Security', description: null, display_order: 1, is_active: true },
  { id: FN_OPS, key: 'ops', name: 'Operations', description: null, display_order: 2, is_active: true },
]

const TEAMS: Team[] = [
  { id: 'team-soc', organization_id: ORG_ID, function_id: FN_SECURITY, name: 'Security Operations', description: null, is_active: true },
  { id: 'team-ir', organization_id: ORG_ID, function_id: FN_SECURITY, name: 'Incident Response', description: null, is_active: true },
  { id: 'team-desk', organization_id: ORG_ID, function_id: FN_OPS, name: 'Service Desk', description: null, is_active: true },
]

/** Mirrors the API's nested payload for one owning team. */
function assignment(
  id: string,
  teamId: string,
  isAccountable: boolean,
  primaryName?: string
): TeamAssignment {
  const team = TEAMS.find(t => t.id === teamId)!
  const fn = FUNCTIONS.find(f => f.id === team.function_id)!
  return {
    id,
    type: 'control',
    item_id: id,
    team_id: team.id,
    organization_id: ORG_ID,
    is_accountable: isAccountable,
    assigned_at: '2026-08-24T00:00:00',
    team: {
      id: team.id,
      name: team.name,
      is_active: true,
      function_id: fn.id,
      function: { id: fn.id, key: fn.key, name: fn.name, is_active: true },
      primary: primaryName
        ? {
            user_id: 'u1',
            membership_role: 'primary',
            user: { id: 'u1', email: 'ana@example.com', display_name: primaryName },
          }
        : null,
      delegate: null,
    },
  }
}

interface Row {
  id: string
  label: string
}

/**
 * A list wired as the evidence one is: the hook fires once for the page, every
 * row reads the resulting map, and nothing inside a row fetches.
 *
 * The controls list no longer filters this way — the server narrows it and the
 * rows returned are the answer. Evidence still needs the predicate, because
 * there the server's answer arrives as a SEPARATE request that can fail or be
 * in flight, and falling back to this map beats blanking the list. That is the
 * distinction: on controls the filtered rows are intrinsic to the response, on
 * evidence they are a second call.
 */
function AssignmentList({ rows }: { rows: Row[] }) {
  const [teamId, setTeamId] = useState(ALL)
  const [functionId, setFunctionId] = useState(ALL)
  const itemIds = useMemo(() => rows.map(row => row.id), [rows])
  const { accountableFor, teamsFor, loading } = useTeamAssignments(ORG_ID, 'control', {
    itemIds,
  })

  const visible = rows.filter(row =>
    matchesTeamFilters(teamsFor(row.id), { teamId, functionId }, ALL)
  )

  return (
    <div>
      <TeamListFilters
        organizationId={ORG_ID}
        teamId={teamId}
        functionId={functionId}
        onTeamChange={setTeamId}
        onFunctionChange={setFunctionId}
      />
      <div data-testid="row-count">{loading ? 'loading' : String(visible.length)}</div>
      <ul>
        {visible.map(row => (
          <li key={row.id} data-testid={`row-${row.id}`}>
            {row.label}
            <span data-testid={`team-${row.id}`}>
              {accountableTeamLabel(accountableFor(row.id)) ?? 'No accountable team'}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function primeCatalogue() {
  mockListTeams.mockResolvedValue(TEAMS)
  mockListFunctions.mockResolvedValue(FUNCTIONS)
}

beforeEach(() => {
  vi.clearAllMocks()
  primeCatalogue()
})

describe('team assignments are batch-loaded, not fetched per row', () => {
  it('makes exactly one request for a 250-row list', async () => {
    const rows: Row[] = Array.from({ length: 250 }, (_, i) => ({
      id: `item-${i}`,
      label: `Control ${i}`,
    }))
    const map: TeamAssignmentMap = {}
    rows.forEach((row, i) => {
      map[row.id] = [assignment(`a-${i}`, i % 2 === 0 ? 'team-soc' : 'team-desk', true)]
    })
    mockListAssignments.mockResolvedValue(map)

    render(<AssignmentList rows={rows} />)

    await waitFor(() => expect(screen.getByTestId('row-count')).toHaveTextContent('250'))

    // One request for 250 rows. The number that must never grow with the row
    // count is this one; 250 rows costing 250 reads is the N+1 itself.
    expect(mockListAssignments).toHaveBeenCalledTimes(1)
    const [org, type, options] = mockListAssignments.mock.calls[0]!
    expect(org).toBe(ORG_ID)
    expect(type).toBe('control')
    // Scoped to the rows on screen rather than the whole organisation.
    expect(options!.itemIds).toHaveLength(250)
    // And the column is populated from that single read.
    expect(screen.getByTestId('team-item-0')).toHaveTextContent('Security Operations')
    expect(screen.getByTestId('team-item-1')).toHaveTextContent('Service Desk')
  })

  it('reads once more when a new page arrives, and only for the new rows', async () => {
    const page1: Row[] = Array.from({ length: 50 }, (_, i) => ({
      id: `item-${i}`,
      label: `Control ${i}`,
    }))
    const page2: Row[] = Array.from({ length: 100 }, (_, i) => ({
      id: `item-${i}`,
      label: `Control ${i}`,
    }))
    mockListAssignments.mockImplementation(async (_org, _type, options) => {
      const map: TeamAssignmentMap = {}
      for (const id of options?.itemIds ?? []) map[id] = [assignment(`a-${id}`, 'team-soc', true)]
      return map
    })

    const { rerender } = render(<AssignmentList rows={page1} />)
    await waitFor(() => expect(screen.getByTestId('row-count')).toHaveTextContent('50'))
    expect(mockListAssignments).toHaveBeenCalledTimes(1)

    // Scrolling forward: one more read, carrying only the ids page 1 did not
    // already answer. Re-asking for all 100 would make paging quadratic.
    rerender(<AssignmentList rows={page2} />)
    await waitFor(() => expect(screen.getByTestId('row-count')).toHaveTextContent('100'))
    expect(mockListAssignments).toHaveBeenCalledTimes(2)
    expect(mockListAssignments.mock.calls[1]![2]!.itemIds).toHaveLength(50)
    expect(mockListAssignments.mock.calls[1]![2]!.itemIds).not.toContain('item-0')

    // Page 1's rows keep their teams — the second read merged rather than
    // replaced, which is what stops the column blanking as you scroll.
    expect(screen.getByTestId('team-item-0')).toHaveTextContent('Security Operations')
    expect(screen.getByTestId('team-item-99')).toHaveTextContent('Security Operations')
  })

  it('scrolling back over rows already read costs nothing', async () => {
    const page1: Row[] = Array.from({ length: 50 }, (_, i) => ({ id: `item-${i}`, label: `C${i}` }))
    const page2: Row[] = Array.from({ length: 100 }, (_, i) => ({ id: `item-${i}`, label: `C${i}` }))
    mockListAssignments.mockImplementation(async (_org, _type, options) => {
      const map: TeamAssignmentMap = {}
      for (const id of options?.itemIds ?? []) map[id] = [assignment(`a-${id}`, 'team-soc', true)]
      return map
    })

    const { rerender } = render(<AssignmentList rows={page1} />)
    await waitFor(() => expect(screen.getByTestId('row-count')).toHaveTextContent('50'))
    rerender(<AssignmentList rows={page2} />)
    await waitFor(() => expect(screen.getByTestId('row-count')).toHaveTextContent('100'))
    expect(mockListAssignments).toHaveBeenCalledTimes(2)

    // Re-rendering the rows we already hold must not fetch again.
    rerender(<AssignmentList rows={page1} />)
    await waitFor(() => expect(screen.getByTestId('row-count')).toHaveTextContent('50'))
    expect(mockListAssignments).toHaveBeenCalledTimes(2)
  })

  it('asks for items with no teams only once, not on every scroll', async () => {
    const rows: Row[] = Array.from({ length: 30 }, (_, i) => ({ id: `item-${i}`, label: `C${i}` }))
    // Nothing owns any of them: the API omits absent items from the map, so a
    // hook recording only the keys it got back would re-ask for these forever.
    mockListAssignments.mockResolvedValue({})

    const { rerender } = render(<AssignmentList rows={rows} />)
    await waitFor(() => expect(screen.getByTestId('row-count')).toHaveTextContent('0'))
    expect(mockListAssignments).toHaveBeenCalledTimes(1)

    rerender(<AssignmentList rows={rows} />)
    await waitFor(() => expect(mockListAssignments).toHaveBeenCalledTimes(1))
  })

  it('shows the missing owner rather than a blank column', async () => {
    mockListAssignments.mockResolvedValue({
      'item-b': [assignment('a2', 'team-soc', false)],
    })

    render(
      <AssignmentList
        rows={[
          { id: 'item-a', label: 'Unowned' },
          { id: 'item-b', label: 'Owned, nobody accountable' },
        ]}
      />
    )

    await waitFor(() => expect(screen.getByTestId('row-count')).toHaveTextContent('2'))
    expect(screen.getByTestId('team-item-a')).toHaveTextContent('No accountable team')
    expect(screen.getByTestId('team-item-b')).toHaveTextContent('No accountable team')
  })

  it('renders the list unfiltered when the batch read fails', async () => {
    mockListAssignments.mockRejectedValue(new Error('backend down'))

    render(<AssignmentList rows={[{ id: 'item-a', label: 'Still here' }]} />)

    await waitFor(() => expect(screen.getByTestId('row-count')).toHaveTextContent('1'))
    expect(screen.getByTestId('row-item-a')).toBeInTheDocument()
  })
})

describe('filtering a list by team and by function', () => {
  const rows: Row[] = [
    { id: 'item-soc', label: 'Owned by Security Operations' },
    { id: 'item-ir', label: 'Owned by Incident Response' },
    { id: 'item-desk', label: 'Owned by Service Desk' },
    { id: 'item-none', label: 'Owned by nobody' },
  ]

  beforeEach(() => {
    mockListAssignments.mockResolvedValue({
      'item-soc': [assignment('a1', 'team-soc', true)],
      'item-ir': [assignment('a2', 'team-ir', true)],
      'item-desk': [assignment('a3', 'team-desk', true)],
    })
  })

  it('narrows to one team', async () => {
    const user = userEvent.setup()
    render(<AssignmentList rows={rows} />)

    await waitFor(() => expect(screen.getByTestId('row-count')).toHaveTextContent('4'))

    await user.selectOptions(screen.getByLabelText('Filter by owning team'), 'team-ir')

    await waitFor(() => expect(screen.getByTestId('row-count')).toHaveTextContent('1'))
    expect(screen.getByTestId('row-item-ir')).toBeInTheDocument()
    expect(screen.queryByTestId('row-item-soc')).not.toBeInTheDocument()
    // An item nobody owns is not "everyone's" — it drops out of a team filter.
    expect(screen.queryByTestId('row-item-none')).not.toBeInTheDocument()
  })

  it('narrows to one business function, keeping every team under it', async () => {
    const user = userEvent.setup()
    render(<AssignmentList rows={rows} />)

    await waitFor(() => expect(screen.getByTestId('row-count')).toHaveTextContent('4'))

    await user.selectOptions(screen.getByLabelText('Filter by business function'), FN_SECURITY)

    await waitFor(() => expect(screen.getByTestId('row-count')).toHaveTextContent('2'))
    expect(screen.getByTestId('row-item-soc')).toBeInTheDocument()
    expect(screen.getByTestId('row-item-ir')).toBeInTheDocument()
    expect(screen.queryByTestId('row-item-desk')).not.toBeInTheDocument()
  })

  it('offers only the teams under the chosen function', async () => {
    const user = userEvent.setup()
    render(<AssignmentList rows={rows} />)

    await waitFor(() => expect(screen.getByTestId('row-count')).toHaveTextContent('4'))

    const teamPicker = screen.getByLabelText('Filter by owning team')
    expect(within(teamPicker).getAllByRole('option').map(o => o.textContent)).toEqual([
      'All Teams',
      'Incident Response',
      'Security Operations',
      'Service Desk',
    ])

    await user.selectOptions(screen.getByLabelText('Filter by business function'), FN_OPS)

    await waitFor(() =>
      expect(within(teamPicker).getAllByRole('option').map(o => o.textContent)).toEqual([
        'All Teams',
        'Service Desk',
      ])
    )
  })

  it('clears a team selection stranded under a different function', async () => {
    const user = userEvent.setup()
    render(<AssignmentList rows={rows} />)

    await waitFor(() => expect(screen.getByTestId('row-count')).toHaveTextContent('4'))

    await user.selectOptions(screen.getByLabelText('Filter by owning team'), 'team-ir')
    await waitFor(() => expect(screen.getByTestId('row-count')).toHaveTextContent('1'))

    // Incident Response is a Security team, so this pair could only ever match
    // nothing. The team filter is dropped rather than left contradicting.
    await user.selectOptions(screen.getByLabelText('Filter by business function'), FN_OPS)

    await waitFor(() => expect(screen.getByTestId('row-count')).toHaveTextContent('1'))
    expect(screen.getByTestId('row-item-desk')).toBeInTheDocument()
    expect(screen.getByLabelText('Filter by owning team')).toHaveValue(ALL)
  })

  it('filtering never re-reads the assignments', async () => {
    const user = userEvent.setup()
    render(<AssignmentList rows={rows} />)

    await waitFor(() => expect(screen.getByTestId('row-count')).toHaveTextContent('4'))

    await user.selectOptions(screen.getByLabelText('Filter by owning team'), 'team-soc')
    await waitFor(() => expect(screen.getByTestId('row-count')).toHaveTextContent('1'))
    await user.selectOptions(screen.getByLabelText('Filter by owning team'), ALL)
    await waitFor(() => expect(screen.getByTestId('row-count')).toHaveTextContent('4'))

    expect(mockListAssignments).toHaveBeenCalledTimes(1)
  })
})
