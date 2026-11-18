// SPDX-License-Identifier: AGPL-3.0-only
/** Clause groups match by prefix; the strip summary matches the server's rules. */
import { describe, expect, it } from 'vitest'

import { applyFilters, clauseInGroup, summarise, teamOptions } from '../ismsMetricFilters'
import { objective, reading } from '../../isms/objectives/__tests__/fixtures'

describe('clause groups', () => {
  it('matches the clause number or a sub-clause, never a longer number', () => {
    expect(clauseInGroup('6', '6')).toBe(true)
    expect(clauseInGroup('6.1.3', '6')).toBe(true)
    expect(clauseInGroup('10.2', '10')).toBe(true)
    expect(clauseInGroup('1.0', '10')).toBe(false)
    expect(clauseInGroup('10.1', '1')).toBe(false)
    expect(clauseInGroup('A.5.1', 'A.')).toBe(true)
    expect(clauseInGroup('a.8.2', 'A.')).toBe(true)
    expect(clauseInGroup('8.2', 'A.')).toBe(false)
  })

  it('keeps an objective when any of its clauses is in the group', () => {
    const items = [objective({ id: 'a', iso_clauses: ['8.3', 'A.5.1'] }), objective({ id: 'b', iso_clauses: ['9.1'] })]
    expect(applyFilters(items, { team: '', clause: 'annex-a', status: '' }).tiles.map((o) => o.id)).toEqual(['a'])
    expect(applyFilters(items, { team: '', clause: '9', status: '' }).tiles.map((o) => o.id)).toEqual(['b'])
  })
})

describe('summarise', () => {
  it('counts as the server does, leaving archived objectives out', () => {
    const s = summarise([
      objective({ reading: reading({ status: 'off_track' }) }),
      objective({ reading: reading({ status: 'measurement_overdue' }) }),
      objective({ reading: reading({ status: 'proposed' }) }),
      objective({ reading: reading({ status: 'achieved' }) }),
      objective({ reading: reading({ status: 'on_track' }), archived_at: '2026-11-01T00:00:00' }),
    ])
    expect(s).toMatchObject({ objectives: 4, off_track: 1, on_track: 0, needs_attention: 2, overdue: 1, proposed: 1, achieved: 1 })
  })
})

describe('team options', () => {
  it('lists named teams by name, then No team when some objective has none', () => {
    const items = [
      objective({ owning_team_id: 't2', owning_team_name: 'Security' }),
      objective({ owning_team_id: 't1', owning_team_name: 'IT' }),
      objective({ owning_team_id: null, owning_team_name: null }),
    ]
    expect(teamOptions(items)).toEqual([
      { value: 't1', label: 'IT' }, { value: 't2', label: 'Security' }, { value: 'none', label: 'No team' },
    ])
  })
})
