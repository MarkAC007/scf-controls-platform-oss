/**
 * useTaskTeamOwnership — who owns each task in a list, resolved once for the
 * whole list rather than once per row (#822 phase 4).
 *
 * A task inherits its evidence item's accountable team unless it names its
 * own. Resolving that needs two things a task row does not carry: the parent
 * evidence item's accountable team, and the name of the team an override
 * points at. Asking per row is the N+1 that phase 3 already had to unpick
 * once, so both are batched here and a row does a Map read.
 *
 * Three reads for a page of any size:
 *
 *  1. ``useTeamAssignments(org, 'evidence', { itemIds })`` — the parent items'
 *     teams, one request per page of new ids, nesting each team's function,
 *     primary and delegate. This is where the inherited answer comes from and
 *     it is the common case.
 *  2. ``listTeams`` — names for the teams overrides point at, which may be
 *     teams that own no evidence item at all and so appear nowhere in (1).
 *  3. ``getTeam`` for the DISTINCT teams named by overrides that (1) did not
 *     already describe. Bounded by the organisation's team count, not by the
 *     number of rows: ten thousand tasks overriding onto one team cost one
 *     request. This is what makes "this team has nobody on it" a claim the
 *     list can actually make instead of quietly omitting.
 *
 * **``has_owner: null`` is not ``has_owner: false``.** A team whose
 * membership has not been read yet is unknown, and unknown must not render
 * the same warning as a team that genuinely has nobody on it — that would
 * flash an accusation at every page load and take it back. The distinction is
 * carried in the type and both callers respect it.
 *
 * Failure is quiet in the same way phase 3's assignment map is: ownership is
 * one column of somebody else's list, so a failed read reports through
 * ``error`` and leaves the ownership unresolved rather than blanking the
 * list. Callers that FILTER on ownership must not treat that unresolved state
 * as "matches nothing" or as "matches everything" — see ``resolved``.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { getTeam, listTeams } from '../data/apiClient'
import type {
  Team,
  TeamAssignment,
  TaskOwningTeamSummary,
  TaskTeamOwnership,
} from '../types'
import { useTeamAssignments } from './useTeamAssignments'
import {
  assignmentAccountablePerson,
  assignmentAccountableUserId,
  assignmentFunctionName,
} from './useTeamAssignments'
import type { TeamWarning } from '../components/TeamWarningBadge'

/** The shape a task must expose to be resolvable. Anything wider is ignored. */
export interface OwnableTask {
  id: string
  evidence_tracking_id?: string | null
  /**
   * The override, or null/absent to inherit.
   *
   * An ABSENT key and an explicit ``null`` are both read as "inherit". The
   * API adds this field in this phase, so a payload from a server that has
   * not been upgraded must degrade to the inherited answer rather than
   * showing every task as unowned.
   */
  owning_team_id?: string | null
  /**
   * The override's team, nested by the API so a row can print it without a
   * second lookup.
   *
   * Preferred when present and IGNORED when absent — the payload gained this
   * in the same phase as ``owning_team_id``, and the two do not necessarily
   * arrive together. A client that required it would render every override as
   * an unnamed team against a server that ships the id first; a client that
   * ignored it would keep paying for a lookup the payload already answered.
   *
   * It carries no membership, so it names the team without claiming anything
   * about who is on it — ``has_owner`` stays null until the detail read lands.
   */
  owning_team?: {
    id: string
    name: string
    is_active?: boolean
    function?: { id: string; name: string } | null
  } | null
}

export interface UseTaskTeamOwnershipResult {
  /** Resolved ownership for one task. Never throws; unknown resolves to nulls. */
  ownershipFor: (task: OwnableTask) => TaskTeamOwnership
  /**
   * True once ownership can be answered for the tasks passed in.
   *
   * A filter keyed on ownership MUST wait for this. Filtering while it is
   * false either hides everything (an empty list the user reads as "no work")
   * or shows everything (an unfiltered list presented as a filtered one), and
   * the second is the worse lie.
   */
  resolved: boolean
  loading: boolean
  error: string | null
  /** Every team in the organisation, for a picker that wants to offer them. */
  teams: Team[]
  /** Re-read after a task's owning team changes. */
  reload: () => Promise<void>
}

/** Reduce an evidence item's accountable assignment to the summary shape. */
function summariseAssignment(assignment: TeamAssignment): TaskOwningTeamSummary | null {
  const team = assignment.team
  if (!team) return null
  const person = assignmentAccountablePerson(assignment)
  return {
    id: team.id,
    name: team.name,
    is_active: team.is_active,
    function_name: assignmentFunctionName(assignment),
    person_name: person,
    person_user_id: assignmentAccountableUserId(assignment),
    // The payload carries both slots, so their absence is an answer and not a
    // gap: this team really does have nobody answerable on it.
    has_owner: Boolean(team.primary ?? team.delegate),
  }
}

export function useTaskTeamOwnership(
  organizationId: string | null | undefined,
  tasks: OwnableTask[]
): UseTaskTeamOwnershipResult {
  const trackingIds = useMemo(() => {
    const ids = new Set<string>()
    for (const task of tasks) {
      if (task.evidence_tracking_id) ids.add(String(task.evidence_tracking_id))
    }
    return Array.from(ids).sort()
  }, [tasks])

  const {
    accountableFor,
    loading: assignmentsLoading,
    error: assignmentsError,
    reload: reloadAssignments,
  } = useTeamAssignments(organizationId, 'evidence', {
    enabled: !!organizationId,
    itemIds: trackingIds,
  })

  const [teams, setTeams] = useState<Team[]>([])
  const [teamsLoaded, setTeamsLoaded] = useState(false)
  const [teamsError, setTeamsError] = useState<string | null>(null)

  const loadTeams = useCallback(async () => {
    if (!organizationId) {
      setTeams([])
      setTeamsLoaded(false)
      return
    }
    try {
      setTeamsError(null)
      setTeams(await listTeams(organizationId))
    } catch (err: any) {
      console.error('Failed to load teams for task ownership:', err)
      setTeamsError(err?.message || 'Failed to load teams')
    } finally {
      setTeamsLoaded(true)
    }
  }, [organizationId])

  useEffect(() => {
    setTeamsLoaded(false)
    void loadTeams()
  }, [loadTeams])

  const teamsById = useMemo(() => new Map(teams.map(t => [t.id, t])), [teams])

  /* -- Membership for the teams that overrides point at ---------------------
   *
   * Keyed by team, never by task: the cost is the number of DISTINCT teams
   * named by overrides on this page, which is bounded by the organisation's
   * team count however many rows there are.
   * --------------------------------------------------------------------- */
  const [overrideDetail, setOverrideDetail] = useState<
    Map<string, TaskOwningTeamSummary>
  >(new Map())
  const requestedTeamIds = useRef<Set<string>>(new Set())

  // Ids named by an override on this page, minus any the parent-assignment
  // payload already described in full.
  const overrideTeamIds = useMemo(() => {
    const ids = new Set<string>()
    for (const task of tasks) {
      if (task.owning_team_id) ids.add(String(task.owning_team_id))
    }
    return Array.from(ids).sort()
  }, [tasks])

  const overrideKey = overrideTeamIds.join(',')

  useEffect(() => {
    if (!organizationId) {
      requestedTeamIds.current = new Set()
      setOverrideDetail(new Map())
      return
    }
    let cancelled = false
    const missing = overrideTeamIds.filter(id => !requestedTeamIds.current.has(id))
    if (missing.length === 0) return
    for (const id of missing) requestedTeamIds.current.add(id)
    void (async () => {
      for (const teamId of missing) {
        try {
          const detail = await getTeam(organizationId, teamId)
          if (cancelled) return
          const primary = detail.members?.find(m => m.membership_role === 'primary')
          const delegate = detail.members?.find(m => m.membership_role === 'delegate')
          const holder = primary ?? delegate ?? null
          setOverrideDetail(prev => {
            const next = new Map(prev)
            next.set(teamId, {
              id: detail.id,
              name: detail.name,
              is_active: detail.is_active,
              function_name: null,
              person_name: holder?.user?.display_name || holder?.user?.email || null,
              person_user_id: holder?.user_id ? String(holder.user_id) : null,
              has_owner: Boolean(holder),
            })
            return next
          })
        } catch (err) {
          // An unreadable team leaves ``has_owner`` unknown rather than
          // asserting the team is empty. The name still comes from
          // ``listTeams`` below, so the row degrades to naming the team
          // without claiming anything about who is on it.
          console.error('Failed to load team for task ownership:', err)
          if (cancelled) return
        }
      }
    })()
    return () => {
      cancelled = true
    }
    // ``overrideTeamIds`` is rebuilt every render; the joined key is what
    // actually changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [organizationId, overrideKey])

  const ownershipFor = useCallback(
    (task: OwnableTask): TaskTeamOwnership => {
      const overrideId = task.owning_team_id ? String(task.owning_team_id) : null
      if (overrideId) {
        const detail = overrideDetail.get(overrideId)
        if (detail) return { team: detail, source: 'task' }
        // The nested object, when the payload carries it, beats the team list
        // — it names the function too, and it is right even for a team the
        // list read has not returned yet.
        const nested = task.owning_team
        const known = teamsById.get(overrideId)
        if (nested && String(nested.id) === overrideId) {
          return {
            team: {
              id: overrideId,
              name: nested.name,
              is_active: nested.is_active ?? known?.is_active ?? true,
              function_name: nested.function?.name ?? null,
              person_name: null,
              person_user_id: null,
              // The nested object says nothing about membership, so this
              // stays unknown rather than becoming a claim that nobody is on
              // the team.
              has_owner: null,
            },
            source: 'task',
          }
        }
        return {
          team: known
            ? {
                id: known.id,
                name: known.name,
                is_active: known.is_active,
                function_name: null,
                person_name: null,
                person_user_id: null,
                // Not yet read. Distinct from "nobody is on this team", and
                // rendered as neither a warning nor a reassurance.
                has_owner: null,
              }
            : null,
          source: 'task',
        }
      }
      const trackingId = task.evidence_tracking_id
        ? String(task.evidence_tracking_id)
        : null
      if (!trackingId) return { team: null, source: null }
      const accountable = accountableFor(trackingId)
      if (!accountable) return { team: null, source: null }
      return { team: summariseAssignment(accountable), source: 'evidence' }
    },
    [accountableFor, overrideDetail, teamsById]
  )

  return {
    ownershipFor,
    /*
     * Both halves must have landed, and neither may have failed.
     *
     * A failed read is deliberately NOT resolved. It would otherwise resolve
     * every task to "no owning team" — an accusation, and for a filter, an
     * empty list that reads as "this team owns nothing" when the truth is
     * that we never found out. Unanswered and answered-empty are different
     * claims and this is the line between them.
     */
    resolved: teamsLoaded && !assignmentsLoading && (assignmentsError ?? teamsError) === null,
    loading: assignmentsLoading || !teamsLoaded,
    error: assignmentsError ?? teamsError,
    teams,
    reload: async () => {
      requestedTeamIds.current = new Set()
      setOverrideDetail(new Map())
      await Promise.all([reloadAssignments(), loadTeams()])
    },
  }
}

/**
 * The advisory badges a resolved ownership earns.
 *
 * Both are warnings and neither blocks anything, in keeping with every other
 * signal this feature renders: a task with no owning team is legal, and a
 * team with nobody on it is legal — the partial unique index caps ``primary``
 * at one per team but cannot require one to exist, so an empty team is a
 * permanent steady state rather than a transient one.
 *
 * ``has_owner === null`` earns nothing. Not knowing is not a finding.
 */
export function taskOwnershipWarnings(ownership: TaskTeamOwnership): TeamWarning[] {
  const warnings: TeamWarning[] = []
  if (!ownership.team) {
    warnings.push({
      key: 'no-owning-team',
      label: 'No owning team',
      title:
        'Neither this task nor its evidence item has an accountable team, so ' +
        'nobody inherits it. Assign an accountable team to the evidence item, ' +
        'or set one on the task.',
    })
    return warnings
  }
  if (ownership.team.has_owner === false) {
    warnings.push({
      key: 'no-primary',
      label: 'No primary',
      title:
        `${ownership.team.name} owns this task but has no primary or delegate, ` +
        'so the work reaches nobody. Add one under Users → Teams.',
    })
  }
  if (!ownership.team.is_active) {
    warnings.push({
      key: 'team-archived',
      label: 'Team archived',
      title:
        `${ownership.team.name} has been archived. It still owns this task; ` +
        'move the task to a live team or restore the team.',
    })
  }
  return warnings
}

export default useTaskTeamOwnership
