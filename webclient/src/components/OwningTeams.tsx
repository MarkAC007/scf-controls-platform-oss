/**
 * OwningTeams — which teams own this control or evidence item, and which one
 * is answerable for it.
 *
 * Phase 1 said who is on a team. This says what a team owns. Several teams
 * may own one item — a control is rarely one department's problem — and at
 * most one of them is accountable. At most, not exactly: an item nobody has
 * assigned is legal, and is the state every item starts in, so the missing
 * accountable team is a warning badge and never a block.
 *
 * Sits beside the existing per-user pickers rather than replacing them. Those
 * name a person; this names a team. Both are true at once and neither is
 * derived from the other.
 *
 * Teams grant no permissions. Naming a team here changes nothing about what
 * its members may do, in this screen or anywhere else: access is decided by
 * ``organization_members.role`` alone. ``canManage`` hides the write controls
 * from non-admins as a courtesy — the API refuses them regardless, and that
 * refusal, not this prop, is the security boundary.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'react-hot-toast'

import {
  assignTeamToItem,
  getItemTeamAssignments,
  listTeams,
  removeTeamAssignment,
  setAccountableTeam,
} from '../data/apiClient'
import type { Team, TeamAssignableType, TeamAssignment } from '../types'
import {
  assignmentAccountablePerson,
  assignmentAccountableUserId,
  assignmentFunctionName,
  assignmentTeamName,
} from '../hooks/useTeamAssignments'
import { useOrgMemberTypes } from '../hooks/useOrgMemberTypes'
import { ContractorBadge } from './ContractorBadge'
import { TeamWarningBadges } from './TeamWarningBadge'
import type { TeamWarning } from './TeamWarningBadge'

interface OwningTeamsProps {
  organizationId: string
  assignableType: TeamAssignableType
  /**
   * The database id of the scoped control or evidence tracking row — not the
   * SCF id and not the catalogue evidence id. An item that has never been
   * saved has no database row, so callers must not render this at all until
   * they have one.
   */
  assignableId: string
  /** Show the add / remove / accountable controls. Display only; see the module note. */
  canManage?: boolean
  /** Called after any successful change, so a list view can re-read its batch. */
  onChange?: () => void
}

/** "this control" / "this evidence item" — used in confirmations and labels. */
function itemNoun(type: TeamAssignableType): string {
  return type === 'control' ? 'this control' : 'this evidence item'
}

export default function OwningTeams({
  organizationId,
  assignableType,
  assignableId,
  canManage = false,
  onChange,
}: OwningTeamsProps) {
  const [assignments, setAssignments] = useState<TeamAssignment[]>([])
  const [teams, setTeams] = useState<Team[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [draftTeamId, setDraftTeamId] = useState('')

  // The named person on an owning team may be a contractor. Saying so where
  // the name is shown is the point of #822: it is visible next to the person
  // answerable for the item, not buried on a settings screen. It changes
  // nothing about who may be accountable.
  const { memberTypeOf } = useOrgMemberTypes(organizationId)

  const loadAll = useCallback(async () => {
    try {
      setLoading(true)
      setError(null)
      // Two reads, not three: the assignment payload nests each team's
      // function, so there is no functions catalogue to fetch. `listTeams`
      // is here only to populate the "add a team" picker with the teams that
      // are NOT yet assigned.
      const [items, teamList] = await Promise.all([
        getItemTeamAssignments(organizationId, assignableType, assignableId),
        listTeams(organizationId),
      ])
      setAssignments(items)
      setTeams(teamList)
    } catch (err: any) {
      console.error('Failed to load owning teams:', err)
      setError(err?.message || 'Failed to load owning teams')
    } finally {
      setLoading(false)
    }
  }, [organizationId, assignableType, assignableId])

  useEffect(() => {
    void loadAll()
  }, [loadAll])

  const teamsById = useMemo(() => new Map(teams.map(team => [team.id, team])), [teams])

  /** Teams that could still be added — the organisation's, minus the ones already here. */
  const availableTeams = useMemo(() => {
    const taken = new Set(assignments.map(a => a.team_id))
    return teams
      .filter(team => team.is_active && !taken.has(team.id))
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [teams, assignments])

  const accountable = assignments.find(a => a.is_accountable) ?? null

  /**
   * Display name for an assignment. The nested team is the source; the loaded
   * team list is a fallback for a payload that arrived without it.
   */
  const teamNameOf = (assignment: TeamAssignment): string =>
    assignmentTeamName(assignment) ||
    teamsById.get(assignment.team_id)?.name ||
    'Unknown team'

  const warnings: TeamWarning[] = []
  if (assignments.length > 0 && !accountable) {
    warnings.push({
      key: 'no-accountable',
      label: 'No accountable team',
      title:
        `Teams own ${itemNoun(assignableType)} but none is accountable, so nobody is ` +
        'answerable for it. Mark one accountable when you know which.',
    })
  }

  /**
   * Make one owning team the accountable one.
   *
   * Exactly one request — a POST, because the API has no PATCH and does not
   * need one: the POST is an upsert that updates the accountability of a team
   * already assigned. The backend clears the incumbent inside that same
   * transaction, having locked the item first, so the UI mirrors the swap
   * locally and does NOT follow up with a call to stand the old team down.
   * A two-call sequence would leave the item with nobody accountable if the
   * second half failed, and would race any other admin doing the same thing.
   */
  const handleMakeAccountable = async (assignment: TeamAssignment) => {
    if (assignment.is_accountable) return
    const previous = assignments
    setBusy(true)
    setAssignments(prev =>
      prev.map(a => ({ ...a, is_accountable: a.id === assignment.id }))
    )
    try {
      await setAccountableTeam(organizationId, {
        type: assignableType,
        itemId: assignableId,
        teamId: assignment.team_id,
      })
      toast.success(`${teamNameOf(assignment)} is accountable for ${itemNoun(assignableType)}`)
      onChange?.()
    } catch (err: any) {
      console.error('Failed to set accountable team:', err)
      // Put the incumbent back — the optimistic flip above was a guess about
      // what the server would do, and it guessed wrong.
      setAssignments(previous)
      toast.error(err?.message || 'Failed to set the accountable team')
    } finally {
      setBusy(false)
    }
  }

  const handleAdd = async () => {
    if (!draftTeamId) return
    setBusy(true)
    try {
      await assignTeamToItem(organizationId, {
        type: assignableType,
        item_id: assignableId,
        team_id: draftTeamId,
      })
      setDraftTeamId('')
      setAssignments(await getItemTeamAssignments(organizationId, assignableType, assignableId))
      toast.success('Team added')
      onChange?.()
    } catch (err: any) {
      console.error('Failed to assign team:', err)
      toast.error(err?.message || 'Failed to add team')
    } finally {
      setBusy(false)
    }
  }

  const handleRemove = async (assignment: TeamAssignment) => {
    setBusy(true)
    try {
      await removeTeamAssignment(organizationId, assignment.id)
      setAssignments(await getItemTeamAssignments(organizationId, assignableType, assignableId))
      toast.success(`${teamNameOf(assignment)} removed`)
      onChange?.()
    } catch (err: any) {
      console.error('Failed to remove team assignment:', err)
      toast.error(err?.message || 'Failed to remove team')
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return (
      <div className="owning-teams">
        <div className="owning-teams-header">
          <span className="owning-teams-title">Owning teams</span>
        </div>
        <div className="loading-state">Loading owning teams…</div>
      </div>
    )
  }

  return (
    <div className="owning-teams">
      <div className="owning-teams-header">
        <span className="owning-teams-title">Owning teams</span>
        <TeamWarningBadges warnings={warnings} />
      </div>

      <p className="owning-teams-hint">
        Real teams from your organisation, managed under Users → Teams. Several can
        own {itemNoun(assignableType)}; one of them is accountable. Teams grant no
        access — permissions come from organisation roles.
      </p>

      {error && (
        <div className="error-banner">
          <span>{error}</span>
          <button type="button" onClick={() => void loadAll()}>Retry</button>
        </div>
      )}

      {assignments.length === 0 ? (
        <div className="owning-teams-empty">
          No teams own {itemNoun(assignableType)} yet.
        </div>
      ) : (
        <ul className="owning-teams-list">
          {assignments.map(assignment => {
            const name = teamNameOf(assignment)
            const functionName = assignmentFunctionName(assignment)
            // Ships inside the assignment payload, so naming the person
            // answerable on the team costs no extra request.
            const person = assignmentAccountablePerson(assignment)
            const personUserId = assignmentAccountableUserId(assignment)
            return (
              <li key={assignment.id} className="owning-teams-row">
                <span className="owning-teams-name">{name}</span>
                {person && (
                  <span className="owning-teams-person">
                    {person}
                    <ContractorBadge
                      className="contractor-badge-inline"
                      memberType={memberTypeOf(personUserId)}
                      personName={person}
                    />
                  </span>
                )}
                {functionName && (
                  <span className="owning-teams-function">{functionName}</span>
                )}
                {canManage ? (
                  <label className="owning-teams-accountable-control">
                    <input
                      type="radio"
                      name={`accountable-team-${assignableId}`}
                      checked={assignment.is_accountable}
                      disabled={busy}
                      aria-label={`Make ${name} accountable for ${itemNoun(assignableType)}`}
                      onChange={() => void handleMakeAccountable(assignment)}
                    />
                    Accountable
                  </label>
                ) : (
                  assignment.is_accountable && (
                    <span className="owning-teams-accountable-pill">Accountable</span>
                  )
                )}
                {canManage && (
                  <button
                    type="button"
                    className="btn-team-remove"
                    disabled={busy}
                    onClick={() => void handleRemove(assignment)}
                    aria-label={`Remove ${name} from ${itemNoun(assignableType)}`}
                  >
                    Remove
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {canManage ? (
        <div className="owning-teams-add">
          <select
            aria-label={`Add an owning team to ${itemNoun(assignableType)}`}
            value={draftTeamId}
            disabled={busy || availableTeams.length === 0}
            onChange={e => setDraftTeamId(e.target.value)}
          >
            <option value="">Select a team…</option>
            {availableTeams.map(team => (
              <option key={team.id} value={team.id}>
                {team.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn-team-primary"
            disabled={busy || !draftTeamId}
            onClick={() => void handleAdd()}
          >
            Add team
          </button>
          {availableTeams.length === 0 && (
            <span className="team-add-hint">
              {teams.length === 0
                ? 'This organisation has no teams yet — create one under Users → Teams.'
                : 'Every team already owns this.'}
            </span>
          )}
        </div>
      ) : (
        <p className="owning-teams-readonly-note">
          Only organisation admins can change which teams own {itemNoun(assignableType)}.
        </p>
      )}
    </div>
  )
}
