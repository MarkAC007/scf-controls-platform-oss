/**
 * TeamListFilters — narrow a list to the work one team, or one business
 * function, owns.
 *
 * Two selects that stay coherent with each other: pick a function and the
 * team select narrows to that function's teams, because "Security" plus
 * "Service Desk" is a combination that can only ever return nothing.
 *
 * The teams catalogue is loaded here, once, and the filtering itself is done
 * by the list against the batch-loaded assignment map — no row asks anything
 * about itself.
 *
 * Filtering by team is not a permission check and confers nothing. It is the
 * same kind of statement as filtering by domain: show me this slice.
 */
import { useEffect, useMemo, useState } from 'react'

import { listFunctions, listTeams } from '../data/apiClient'
import type { OrgFunction, Team } from '../types'

export const ALL = 'all'

interface TeamListFiltersProps {
  organizationId: string
  /** Selected team id, or ``ALL``. */
  teamId: string
  /** Selected function id, or ``ALL``. */
  functionId: string
  onTeamChange: (teamId: string) => void
  onFunctionChange: (functionId: string) => void
  className?: string
}

export default function TeamListFilters({
  organizationId,
  teamId,
  functionId,
  onTeamChange,
  onFunctionChange,
  className,
}: TeamListFiltersProps) {
  const [teams, setTeams] = useState<Team[]>([])
  const [functions, setFunctions] = useState<OrgFunction[]>([])

  useEffect(() => {
    let cancelled = false
    Promise.all([listTeams(organizationId), listFunctions()])
      .then(([teamList, fns]) => {
        if (cancelled) return
        setTeams(teamList)
        setFunctions(fns)
      })
      .catch(err => {
        // A filter that cannot load is a filter with no options, not a broken
        // list. The list behind it renders unfiltered either way.
        console.error('Failed to load team filter options:', err)
      })
    return () => {
      cancelled = true
    }
  }, [organizationId])

  /** Only functions that actually have a team — the rest would filter to nothing. */
  const usedFunctions = useMemo(() => {
    const withTeams = new Set(teams.map(team => team.function_id))
    return functions
      .filter(fn => withTeams.has(fn.id))
      .sort((a, b) => {
        const orderA = a.display_order ?? Number.MAX_SAFE_INTEGER
        const orderB = b.display_order ?? Number.MAX_SAFE_INTEGER
        if (orderA !== orderB) return orderA - orderB
        return a.name.localeCompare(b.name)
      })
  }, [teams, functions])

  const selectableTeams = useMemo(() => {
    const scoped = functionId === ALL
      ? teams
      : teams.filter(team => team.function_id === functionId)
    return [...scoped].sort((a, b) => a.name.localeCompare(b.name))
  }, [teams, functionId])

  // Choosing a function can strand a team selection under a different one.
  // Clear it rather than leaving a filter pair that matches nothing.
  useEffect(() => {
    if (teamId === ALL) return
    if (!selectableTeams.some(team => team.id === teamId)) onTeamChange(ALL)
  }, [teamId, selectableTeams, onTeamChange])

  return (
    <div className={className ?? 'team-list-filters'}>
      <select
        aria-label="Filter by business function"
        className="filter-select"
        value={functionId}
        onChange={e => onFunctionChange(e.target.value)}
      >
        <option value={ALL}>All Functions</option>
        {usedFunctions.map(fn => (
          <option key={fn.id} value={fn.id}>{fn.name}</option>
        ))}
      </select>
      <select
        aria-label="Filter by owning team"
        className="filter-select"
        value={teamId}
        onChange={e => onTeamChange(e.target.value)}
      >
        <option value={ALL}>All Teams</option>
        {selectableTeams.map(team => (
          <option key={team.id} value={team.id}>{team.name}</option>
        ))}
      </select>
    </div>
  )
}
