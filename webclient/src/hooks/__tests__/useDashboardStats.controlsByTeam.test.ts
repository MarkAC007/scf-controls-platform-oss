// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Dashboard "Controls by owner" (#1277).
 *
 * The card grouped on the legacy free-text `owner` column, which nothing
 * writes any more, so every control read as Unassigned however many had an
 * accountable team. The grouping must follow the accountable team assignment,
 * the same thing the Control Library's owner column shows.
 */
import { describe, expect, it } from 'vitest'

import { countControlsByAccountableTeam } from '../useDashboardStats'
import type { ScopedControl, TeamAssignment } from '../../types'

function control(id: string | undefined, owner?: string): ScopedControl {
  return { id, scf_id: `SCF-${id ?? 'new'}`, selected: true, owner }
}

function assignment(itemId: string, teamName: string, primary?: string): TeamAssignment {
  return {
    id: `a-${itemId}`,
    type: 'control',
    item_id: itemId,
    team_id: `t-${teamName}`,
    organization_id: 'org-1',
    is_accountable: true,
    assigned_at: '2026-11-01T00:00:00Z',
    team: {
      id: `t-${teamName}`,
      name: teamName,
      primary: primary ? { user_id: 'u-1', user: { display_name: primary } } : null,
    },
  } as TeamAssignment
}

function lookup(assignments: TeamAssignment[]) {
  const byItem = new Map(assignments.map(a => [a.item_id, a]))
  return (itemId: string) => byItem.get(itemId) ?? null
}

describe('countControlsByAccountableTeam', () => {
  it('counts each control under its accountable team and the rest as Unassigned', () => {
    const controls = [control('c1'), control('c2'), control('c3'), control('c4')]
    const accountableFor = lookup([
      assignment('c1', 'Security Operations'),
      assignment('c2', 'Security Operations'),
      assignment('c3', 'Platform'),
    ])

    expect(countControlsByAccountableTeam(controls, accountableFor)).toEqual({
      'Security Operations': 2,
      Platform: 1,
      Unassigned: 1,
    })
  })

  it('ignores the legacy free-text owner column', () => {
    const controls = [control('c1', 'Old Owner'), control('c2', 'Old Owner')]
    const accountableFor = lookup([assignment('c1', 'Platform')])

    expect(countControlsByAccountableTeam(controls, accountableFor)).toEqual({
      Platform: 1,
      Unassigned: 1,
    })
  })

  it('labels a row with the team name alone, whoever leads the team', () => {
    const controls = [control('c1'), control('c2')]
    const accountableFor = lookup([
      assignment('c1', 'Platform', 'Ana Ruiz'),
      assignment('c2', 'Platform'),
    ])

    expect(countControlsByAccountableTeam(controls, accountableFor)).toEqual({ Platform: 2 })
  })

  it('counts a control with no database id as Unassigned', () => {
    const accountableFor = lookup([assignment('c1', 'Platform')])

    expect(countControlsByAccountableTeam([control(undefined)], accountableFor)).toEqual({
      Unassigned: 1,
    })
  })

  it('returns no rows when there are no controls', () => {
    expect(countControlsByAccountableTeam([], () => null)).toEqual({})
  })
})
