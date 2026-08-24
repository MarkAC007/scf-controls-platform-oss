/**
 * useTeamAssignments — team ownership for a whole list, one request per page.
 *
 * The controls and evidence lists render hundreds of rows. Asking each row
 * which teams own it is an N+1 measured in seconds against a real
 * organisation, so the API exposes a batch endpoint returning assignments
 * indexed by item, and this hook is the only thing a list view should call.
 * A row does a Map read, never a fetch.
 *
 * If you find yourself wanting this hook inside a row component, that is the
 * N+1 coming back — index at the top of the list instead.
 *
 * **Two modes, and the difference is which list you are on.**
 *
 * Without ``itemIds`` it reads the whole organisation once. That is right for
 * a list that is already entirely in memory: evidence arrives complete inside
 * the scoping payload, so there is no page to scope to and no second request
 * to make.
 *
 * With ``itemIds`` it reads only those items, and accumulates. The controls
 * list is server-paginated, so asking for every assignment in the
 * organisation to render fifty rows fetches thousands of records to display
 * fifty. Ids already fetched are never re-requested, so scrolling costs one
 * request per new page and nothing for pages already seen — the invariant is
 * one request per page, never one per row.
 *
 * Accumulation is what makes paging work: a page-2 fetch merges into the map
 * rather than replacing it, so scrolling back to page 1 does not find an
 * empty badge column where the data used to be.
 *
 * Failure is deliberately quiet. Ownership is a column on somebody else's
 * list; if it cannot be loaded the list still has to render, so the error is
 * reported through ``error`` for a caller that wants to say something and the
 * lookup degrades to empty rather than throwing.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { listTeamAssignments } from '../data/apiClient'
import type { TeamAssignableType, TeamAssignment, TeamAssignmentMap } from '../types'

/**
 * The API rejects more than 1000 ids on one read (``MAX_ITEM_IDS``). A
 * controls page is 50 and the endpoint caps at 200, so a single page is never
 * close — this bound exists for the accumulated case, where a caller passes
 * everything loaded so far rather than just the newest page.
 */
const MAX_ITEM_IDS_PER_REQUEST = 500

export interface UseTeamAssignmentsOptions {
  enabled?: boolean
  /**
   * Read assignments for these items only, instead of the whole organisation.
   *
   * Pass the ids currently rendered. Ids already fetched are skipped, so
   * handing this the full accumulated list on every page is correct and costs
   * one request for the ids that are new. Omit it entirely for a list that is
   * not paginated.
   */
  itemIds?: string[] | null
}

export interface UseTeamAssignmentsResult {
  /** Assignments indexed by item id. A missing key means "no teams", not an error. */
  byItemId: TeamAssignmentMap
  /** The accountable team for an item, or null. At most one exists by construction. */
  accountableFor: (itemId: string) => TeamAssignment | null
  /** Every team owning an item, accountable or not. */
  teamsFor: (itemId: string) => TeamAssignment[]
  loading: boolean
  error: string | null
  /** Re-read after a mutation elsewhere on the page. */
  reload: () => Promise<void>
}

function chunk(ids: string[], size: number): string[][] {
  const out: string[][] = []
  for (let i = 0; i < ids.length; i += size) out.push(ids.slice(i, i + size))
  return out
}

export function useTeamAssignments(
  organizationId: string | null | undefined,
  type: TeamAssignableType,
  options: boolean | UseTeamAssignmentsOptions = true
): UseTeamAssignmentsResult {
  const { enabled = true, itemIds = null } =
    typeof options === 'boolean' ? { enabled: options, itemIds: null } : options

  const [byItemId, setByItemId] = useState<TeamAssignmentMap>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  /** Ids whose answer we already hold, including those that came back empty. */
  const fetchedIds = useRef<Set<string>>(new Set())
  /** Bumped on every reset so a slow reply from a previous org cannot land. */
  const generation = useRef(0)

  // A stable key for the id list: the caller rebuilds the array every render,
  // so depending on the array itself would refetch forever.
  const idsKey = itemIds ? itemIds.join(',') : null
  const scopeKey = `${organizationId ?? ''}|${type}|${enabled}`

  // Changing organisation or item type invalidates everything held.
  useEffect(() => {
    generation.current += 1
    fetchedIds.current = new Set()
    setByItemId({})
  }, [scopeKey])

  const fetchIds = useCallback(
    async (ids: string[] | null, mine: number) => {
      if (!organizationId || !enabled) {
        setByItemId({})
        return
      }
      try {
        setLoading(true)
        setError(null)
        if (ids === null) {
          const map = await listTeamAssignments(organizationId, type)
          if (generation.current !== mine) return
          setByItemId(map)
          return
        }
        for (const batch of chunk(ids, MAX_ITEM_IDS_PER_REQUEST)) {
          const map = await listTeamAssignments(organizationId, type, { itemIds: batch })
          if (generation.current !== mine) return
          // Merge, never replace: the rows already on screen keep their teams
          // when the next page arrives.
          setByItemId(prev => ({ ...prev, ...map }))
          // Every id asked for is now answered, including the ones that came
          // back absent because nothing owns them. Recording only the present
          // keys would re-request the unowned items on every scroll.
          for (const id of batch) fetchedIds.current.add(id)
        }
      } catch (err: any) {
        if (generation.current !== mine) return
        console.error('Failed to load team assignments:', err)
        setError(err?.message || 'Failed to load team assignments')
        if (ids === null) setByItemId({})
      } finally {
        if (generation.current === mine) setLoading(false)
      }
    },
    [organizationId, type, enabled]
  )

  useEffect(() => {
    if (idsKey === null) {
      void fetchIds(null, generation.current)
      return
    }
    const wanted = idsKey ? idsKey.split(',') : []
    const missing = wanted.filter(id => id && !fetchedIds.current.has(id))
    // Every id on this page is already answered — scrolling back costs nothing.
    if (missing.length === 0) return
    void fetchIds(missing, generation.current)
  }, [idsKey, fetchIds])

  const load = useCallback(async () => {
    // A mutation invalidates what we hold for the items we hold it for, so
    // re-ask for exactly those rather than falling back to the whole org.
    generation.current += 1
    const mine = generation.current
    const held = idsKey === null ? null : Array.from(fetchedIds.current)
    fetchedIds.current = new Set()
    await fetchIds(held, mine)
  }, [fetchIds, idsKey])

  const teamsFor = useCallback(
    (itemId: string): TeamAssignment[] => byItemId[itemId] ?? [],
    [byItemId]
  )

  const accountableFor = useCallback(
    (itemId: string): TeamAssignment | null =>
      (byItemId[itemId] ?? []).find(a => a.is_accountable) ?? null,
    [byItemId]
  )

  return { byItemId, accountableFor, teamsFor, loading, error, reload: load }
}

/**
 * Does an item pass the team / function filters?
 *
 * "All" on both is the common case and short-circuits before touching the
 * assignments, so an unfiltered list pays nothing for the feature.
 *
 * An item with no owning teams fails any active filter. That is the intended
 * reading: "show me what Security owns" should not also show the unowned, and
 * the missing-owner case has its own signal in the accountable column.
 *
 * Team OR function, both matched against ANY owning team — not just the
 * accountable one. A team that owns a control still owns it when another team
 * is answerable, and hiding it from that team's own filtered list would be a
 * lie about who is doing the work.
 */
export function matchesTeamFilters(
  assignments: TeamAssignment[],
  filters: { teamId?: string; functionId?: string },
  allValue: string = 'all'
): boolean {
  const teamId = filters.teamId ?? allValue
  const functionId = filters.functionId ?? allValue
  if (teamId === allValue && functionId === allValue) return true
  if (assignments.length === 0) return false
  if (teamId !== allValue && !assignments.some(a => a.team_id === teamId)) return false
  if (
    functionId !== allValue &&
    !assignments.some(a => assignmentFunctionId(a) === functionId)
  ) {
    return false
  }
  return true
}

/* ---- Reading the nested payload -------------------------------------------
 *
 * The API nests the team, its function, and its primary and delegate inside
 * each assignment so that a row can render everything it needs from the one
 * batch read. These accessors are where that nesting is unpacked; components
 * call them rather than spelling `a.team?.function?.name` at every site, and
 * every one of them tolerates a half-populated payload, because an ownership
 * badge must degrade rather than throw.
 * ------------------------------------------------------------------------- */

/** The owning team's name, or null if the payload did not carry it. */
export function assignmentTeamName(
  assignment: TeamAssignment | null | undefined
): string | null {
  return assignment?.team?.name ?? null
}

/** The business function the owning team sits under. Drives the function filter. */
export function assignmentFunctionId(
  assignment: TeamAssignment | null | undefined
): string | null {
  return assignment?.team?.function?.id ?? assignment?.team?.function_id ?? null
}

export function assignmentFunctionName(
  assignment: TeamAssignment | null | undefined
): string | null {
  return assignment?.team?.function?.name ?? null
}

/**
 * The person answerable on the owning team: its primary, or its delegate when
 * there is no primary.
 *
 * Null is ordinary. A team with neither is what every team looks like the
 * moment it is created, so callers render the team alone rather than treating
 * this as missing data.
 */
export function assignmentAccountablePerson(
  assignment: TeamAssignment | null | undefined
): string | null {
  const holder = assignment?.team?.primary ?? assignment?.team?.delegate ?? null
  if (!holder) return null
  return holder.user?.display_name || holder.user?.email || null
}

/**
 * The user id behind ``assignmentAccountablePerson``, so a caller can say
 * something about that person rather than only print their name (#822 phase 2).
 *
 * Split out instead of widening the function above, which several callers use
 * purely as a display string and one of them concatenates into a label.
 * Resolving a name back to a user by matching the printed text would be the
 * wrong shape — two people can share a display name, and 'Unassigned' is a
 * legal one.
 */
export function assignmentAccountableUserId(
  assignment: TeamAssignment | null | undefined
): string | null {
  const holder = assignment?.team?.primary ?? assignment?.team?.delegate ?? null
  return holder?.user_id ? String(holder.user_id) : null
}

/**
 * The accountable-team column's text: "Security Operations (Ana Ruiz)", or
 * just the team when nobody on it is named. Null when no team is accountable,
 * which callers render as their own "none" state rather than an empty cell.
 */
export function accountableTeamLabel(
  assignment: TeamAssignment | null | undefined
): string | null {
  const team = assignmentTeamName(assignment)
  if (!team) return null
  const person = assignmentAccountablePerson(assignment)
  return person ? `${team} (${person})` : team
}

export default useTeamAssignments
