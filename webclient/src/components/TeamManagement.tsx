/**
 * TeamManagement — describe an organisation's teams and who sits on them.
 *
 * Renders beside UserManagement on the Users screen because a team is a
 * statement about people, not about the platform's settings. Teams are
 * grouped under its primary business function; each team may serve several.
 *
 * Teams grant no permissions. Nothing here gates a capability on membership
 * and nothing here should ever start to: authorisation is entirely
 * ``organization_members.role``, which UserManagement above owns. The roles
 * on this screen — primary, delegate, member — say who is answerable for the
 * team's work, and that is all they say.
 *
 * Health signals are warnings, never blocks. A team with no members at all is
 * legal — it is the state every team is in the moment it is created — so the
 * UI flags it and carries on rather than refusing to render it.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { toast } from 'react-hot-toast'
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
} from '../data/apiClient'
import { TeamWarningBadges } from './TeamWarningBadge'
import { ContractorBadge, withContractorSuffix } from './ContractorBadge'
import { useOrgMemberTypes } from '../hooks/useOrgMemberTypes'
import type { TeamWarning } from './TeamWarningBadge'
import type {
  OrgFunction,
  TeamDetail,
  TeamMember,
  TeamMembershipRole,
  UserSimple,
} from '../types'

interface TeamManagementProps {
  organizationId: string
}

const MEMBERSHIP_ROLE_OPTIONS: { value: TeamMembershipRole; label: string }[] = [
  { value: 'primary', label: 'Primary' },
  { value: 'delegate', label: 'Delegate' },
  { value: 'member', label: 'Member' },
]

/**
 * A role change held back for confirmation. Only ``primary`` and ``delegate``
 * ever land here, and only when somebody already holds the slot: those two
 * roles are capped at one person per team, so filling an occupied one demotes
 * the incumbent. The backend does that swap atomically inside the single
 * PATCH this confirmation releases — there is no second call to demote by
 * hand, and adding one would be a bug.
 */
interface PendingRoleChange {
  teamId: string
  userId: string
  memberName: string
  nextRole: TeamMembershipRole
  incumbentName: string
}

/** Draft state for the add-member control, keyed by the team it belongs to. */
interface AddMemberDraft {
  userId: string
  membershipRole: TeamMembershipRole
}

/**
 * Business functions held for a team while the admin edits them, before
 * anything is sent. ``teamId`` is carried so that expanding a different team
 * cannot silently apply one team's draft to another.
 */
interface FunctionDraft {
  teamId: string
  ids: string[]
}

/**
 * Business functions as add/remove chips.
 *
 * Replaces the native ``<select multiple>`` both team forms used to carry.
 * That control is hostile on a form that means something: a plain click
 * inside it collapses the entire selection down to the one option under the
 * cursor, and nothing on screen distinguishes "I picked one" from "I just
 * discarded four". Chips make every add and every remove a separate, named,
 * reversible act, and the current selection is readable without opening
 * anything.
 *
 * ``is_active`` behaviour is carried over from the select unchanged. An
 * inactive function cannot be added — it is listed, disabled, and suffixed
 * "(inactive)", exactly as the old ``<option disabled>`` was. One that is
 * already assigned still renders as a chip and is still removable, because a
 * team left holding a retired function needs a way out of it.
 *
 * The last chip cannot be removed: a team must serve at least one function,
 * so an empty selection is not a state the form should be able to reach. To
 * swap the only function, add the replacement first.
 */
function FunctionChips({
  selectId,
  addLabel,
  functions,
  selectedIds,
  onChange,
}: {
  selectId: string
  addLabel: string
  functions: OrgFunction[]
  selectedIds: string[]
  onChange: (next: string[]) => void
}) {
  const unselected = functions.filter(fn => !selectedIds.includes(fn.id))
  return (
    <div className="team-function-chips">
      <ul className="team-function-chip-list">
        {selectedIds.map(id => {
          // A selected id the catalogue did not return still gets a chip
          // rather than vanishing. Dropping it from the display would drop it
          // from the next save too, which is data loss by omission.
          const fn = functions.find(f => f.id === id)
          const name = fn?.name ?? 'Unrecognised function'
          const inactive = fn ? !fn.is_active : false
          return (
            <li
              key={id}
              className={`team-function-chip${inactive ? ' team-function-chip--inactive' : ''}`}
            >
              <span className="team-function-chip-label">
                {name}{inactive ? ' (inactive)' : ''}
              </span>
              <button
                type="button"
                className="team-function-chip-remove"
                aria-label={`Remove ${name}`}
                disabled={selectedIds.length === 1}
                title={
                  selectedIds.length === 1
                    ? 'A team must serve at least one business function.'
                    : `Remove ${name}`
                }
                onClick={() => onChange(selectedIds.filter(sel => sel !== id))}
              >
                ×
              </button>
            </li>
          )
        })}
      </ul>
      <select
        id={selectId}
        className="team-function-chip-add"
        aria-label={addLabel}
        value=""
        onChange={event => {
          const id = event.currentTarget.value
          if (id) onChange([...selectedIds, id])
        }}
      >
        <option value="">Add a business function…</option>
        {unselected.map(fn => (
          <option key={fn.id} value={fn.id} disabled={!fn.is_active}>
            {fn.name}{fn.is_active ? '' : ' (inactive)'}
          </option>
        ))}
      </select>
    </div>
  )
}

function displayName(user: UserSimple | null | undefined, fallback: string): string {
  return user?.display_name || user?.email || fallback
}

function memberWithRole(
  team: TeamDetail,
  role: TeamMembershipRole
): TeamMember | undefined {
  return team.members.find(m => m.membership_role === role)
}

export default function TeamManagement({ organizationId }: TeamManagementProps) {
  const [functions, setFunctions] = useState<OrgFunction[]>([])
  const [teams, setTeams] = useState<TeamDetail[]>([])
  const [orgUsers, setOrgUsers] = useState<UserSimple[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showArchived, setShowArchived] = useState(false)
  const [expandedTeamId, setExpandedTeamId] = useState<string | null>(null)
  const [pendingRoleChange, setPendingRoleChange] = useState<PendingRoleChange | null>(null)
  const [addDraft, setAddDraft] = useState<AddMemberDraft>({ userId: '', membershipRole: 'member' })
  const [functionDraft, setFunctionDraft] = useState<FunctionDraft | null>(null)
  const [isSavingFunctions, setIsSavingFunctions] = useState(false)

  // Internal / contractor, per organisation. One request for the screen; each
  // member row looks itself up rather than fetching. Display only — it never
  // decides who may be added to a team or what a team role may be set to.
  const { memberTypeOf } = useOrgMemberTypes(organizationId)

  // Create-team form
  const [showCreateForm, setShowCreateForm] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDescription, setNewDescription] = useState('')
  const [newFunctionIds, setNewFunctionIds] = useState<string[]>([])
  const [isCreating, setIsCreating] = useState(false)

  const loadAll = useCallback(async () => {
    try {
      setLoading(true)
      setError(null)
      const [fns, teamList, users] = await Promise.all([
        listFunctions(),
        listTeams(organizationId, { includeInactive: showArchived }),
        getOrgMembers(organizationId),
      ])
      // The list endpoint returns the team rows alone, and every row on this
      // screen reports its membership — count, primary, delegate — so each one
      // needs its detail. Fetched together rather than in sequence; an
      // organisation has teams in the tens, not the thousands. If that stops
      // being true, the fix is a member summary on the list endpoint, not
      // pagination here.
      const details = await Promise.all(
        teamList.map(team => getTeam(organizationId, team.id))
      )
      setFunctions(fns)
      setTeams(details)
      setOrgUsers(users)
    } catch (err: any) {
      console.error('Failed to load teams:', err)
      setError(err?.message || 'Failed to load teams')
    } finally {
      setLoading(false)
    }
  }, [organizationId, showArchived])

  useEffect(() => {
    void loadAll()
  }, [loadAll])

  /** Re-read one team after a membership change, leaving the rest untouched. */
  const refreshTeam = useCallback(async (teamId: string) => {
    const detail = await getTeam(organizationId, teamId)
    setTeams(prev => prev.map(t => (t.id === teamId ? detail : t)))
  }, [organizationId])

  const functionsById = useMemo(
    () => new Map(functions.map(fn => [fn.id, fn])),
    [functions]
  )

  /**
   * Teams grouped under their function, in the function's ``display_order``.
   * Only functions that actually have a team appear — the full fourteen live
   * in the create form's picker, where they are a choice rather than noise.
   * A team pointing at a function the catalogue did not return still gets a
   * group, at the end, rather than vanishing from the list.
   */
  const groups = useMemo(() => {
    const byFunction = new Map<string, TeamDetail[]>()
    for (const team of teams) {
      const bucket = byFunction.get(team.function_id)
      if (bucket) bucket.push(team)
      else byFunction.set(team.function_id, [team])
    }
    return Array.from(byFunction.entries())
      .map(([functionId, groupTeams]) => ({
        functionId,
        fn: functionsById.get(functionId) ?? null,
        teams: [...groupTeams].sort((a, b) => a.name.localeCompare(b.name)),
      }))
      .sort((a, b) => {
        const orderA = a.fn?.display_order ?? Number.MAX_SAFE_INTEGER
        const orderB = b.fn?.display_order ?? Number.MAX_SAFE_INTEGER
        if (orderA !== orderB) return orderA - orderB
        return (a.fn?.name ?? '').localeCompare(b.fn?.name ?? '')
      })
  }, [teams, functionsById])

  const sortedFunctions = useMemo(
    () => [...functions].sort((a, b) => {
      const orderA = a.display_order ?? Number.MAX_SAFE_INTEGER
      const orderB = b.display_order ?? Number.MAX_SAFE_INTEGER
      if (orderA !== orderB) return orderA - orderB
      return a.name.localeCompare(b.name)
    }),
    [functions]
  )

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault()
    if (!newName.trim() || newFunctionIds.length === 0) return
    try {
      setIsCreating(true)
      const created = await createTeam(organizationId, {
        name: newName.trim(),
        description: newDescription.trim(),
        function_id: newFunctionIds[0],
        function_ids: newFunctionIds,
      })
      toast.success(`Team "${created.name}" created`)
      setNewName('')
      setNewDescription('')
      setNewFunctionIds([])
      setShowCreateForm(false)
      await loadAll()
      setExpandedTeamId(created.id)
    } catch (err: any) {
      console.error('Failed to create team:', err)
      toast.error(err?.message || 'Failed to create team')
    } finally {
      setIsCreating(false)
    }
  }

  const handleArchive = async (team: TeamDetail) => {
    if (!confirm(
      `Archive "${team.name}"?\n\n` +
      'The team is hidden from this list but nothing is deleted — its ' +
      'members and history are kept, and you can restore it from "Show ' +
      'archived teams".'
    )) return
    try {
      await archiveTeam(organizationId, team.id)
      toast.success(`Team "${team.name}" archived`)
      if (expandedTeamId === team.id) setExpandedTeamId(null)
      await loadAll()
    } catch (err: any) {
      console.error('Failed to archive team:', err)
      toast.error(err?.message || 'Failed to archive team')
    }
  }

  const handleRestore = async (team: TeamDetail) => {
    try {
      await updateTeam(organizationId, team.id, { is_active: true })
      toast.success(`Team "${team.name}" restored`)
      await loadAll()
    } catch (err: any) {
      console.error('Failed to restore team:', err)
      toast.error(err?.message || 'Failed to restore team')
    }
  }

  const applyRoleChange = async (
    teamId: string,
    userId: string,
    nextRole: TeamMembershipRole
  ) => {
    try {
      // One request. The backend demotes any incumbent in the same
      // transaction; a second PATCH here would race it.
      await updateTeamMemberRole(organizationId, teamId, userId, nextRole)
      await refreshTeam(teamId)
      toast.success('Team role updated')
    } catch (err: any) {
      console.error('Failed to update team role:', err)
      toast.error(err?.message || 'Failed to update team role')
    } finally {
      setPendingRoleChange(null)
    }
  }

  const handleRoleSelect = (
    team: TeamDetail,
    member: TeamMember,
    nextRole: TeamMembershipRole
  ) => {
    if (nextRole === member.membership_role) return
    if (nextRole === 'primary' || nextRole === 'delegate') {
      const incumbent = team.members.find(
        m => m.membership_role === nextRole && m.user_id !== member.user_id
      )
      if (incumbent) {
        setPendingRoleChange({
          teamId: team.id,
          userId: member.user_id,
          memberName: displayName(member.user, 'this person'),
          nextRole,
          incumbentName: displayName(incumbent.user, 'the current holder'),
        })
        return
      }
    }
    void applyRoleChange(team.id, member.user_id, nextRole)
  }

  const handleAddMember = async (team: TeamDetail) => {
    if (!addDraft.userId) return
    try {
      await addTeamMember(organizationId, team.id, addDraft.userId, addDraft.membershipRole)
      setAddDraft({ userId: '', membershipRole: 'member' })
      await refreshTeam(team.id)
      toast.success('Member added to team')
    } catch (err: any) {
      console.error('Failed to add team member:', err)
      toast.error(err?.message || 'Failed to add member')
    }
  }

  const handleRemoveMember = async (team: TeamDetail, member: TeamMember) => {
    const name = displayName(member.user, 'this person')
    if (!confirm(`Remove ${name} from "${team.name}"?`)) return
    try {
      await removeTeamMember(organizationId, team.id, member.user_id)
      await refreshTeam(team.id)
      toast.success(`${name} removed from ${team.name}`)
    } catch (err: any) {
      console.error('Failed to remove team member:', err)
      toast.error(err?.message || 'Failed to remove member')
    }
  }

  const toggleExpanded = (teamId: string) => {
    setExpandedTeamId(prev => (prev === teamId ? null : teamId))
    setPendingRoleChange(null)
    setAddDraft({ userId: '', membershipRole: 'member' })
    // Unsaved function edits die with the panel they were made in. Carrying
    // them to the next team is the only way this draft could ever be applied
    // to the wrong one.
    setFunctionDraft(null)
  }

  /**
   * Send the staged business functions for one team. Nothing reaches the API
   * until this runs — see the note on the edit form's chip picker for why.
   */
  const saveFunctions = async (team: TeamDetail, ids: string[]) => {
    if (ids.length === 0) return
    try {
      setIsSavingFunctions(true)
      await updateTeam(organizationId, team.id, {
        // The existing primary stays primary if it survived the edit;
        // otherwise the first remaining function takes the slot.
        function_id: ids.includes(team.function_id) ? team.function_id : ids[0],
        function_ids: ids,
      })
      setFunctionDraft(null)
      await refreshTeam(team.id)
      toast.success('Business functions updated')
    } catch (err: any) {
      console.error('Failed to update business functions:', err)
      toast.error(err?.message || 'Failed to update business functions')
    } finally {
      setIsSavingFunctions(false)
    }
  }

  const renderWarnings = (team: TeamDetail) => {
    const servedFunctions = (team.function_ids ?? [team.function_id])
      .map(functionId => functionsById.get(functionId))
      .filter((fn): fn is OrgFunction => Boolean(fn))
    const hasPrimary = Boolean(memberWithRole(team, 'primary'))
    const badges: TeamWarning[] = []
    if (team.members.length === 0) {
      badges.push({
        key: 'no-members',
        label: 'No members',
        title: 'Nobody is on this team yet. That is allowed — add members when you are ready.',
      })
    }
    if (!hasPrimary) {
      badges.push({
        key: 'no-primary',
        label: 'No primary',
        title: 'No one is named primary for this team, so nobody is answerable for its work.',
      })
    }
    if (servedFunctions.some(fn => !fn.is_active)) {
      badges.push({
        key: 'function-inactive',
        label: 'Function inactive',
        title: 'The business function this team is aligned to is no longer active.',
      })
    }
    return <TeamWarningBadges warnings={badges} />
  }

  const renderTeamDetail = (team: TeamDetail) => {
    const assignable = orgUsers.filter(
      user => !team.members.some(m => m.user_id === user.id)
    )
    const savedFunctionIds = team.function_ids ?? [team.function_id]
    const draftFunctionIds =
      functionDraft?.teamId === team.id ? functionDraft.ids : savedFunctionIds
    const functionsDirty =
      draftFunctionIds.length !== savedFunctionIds.length ||
      draftFunctionIds.some((id, idx) => id !== savedFunctionIds[idx])
    return (
      <div className="team-detail">
        {team.description && <p className="team-detail-description">{team.description}</p>}

        <div className="team-create-field">
          <label htmlFor={`team-functions-${team.id}`}>Business functions</label>
          {/*
            Staged, not live. This picker used to write straight through: every
            change to the old multi-select fired an immediate ``updateTeam``,
            so one stray click inside it collapsed a team's functions to a
            single entry and persisted that before the admin could react. The
            edit is now held until Save, and Cancel puts it back — the same
            contract the create form has always had, which is also why both
            forms now use the same control.
          */}
          <FunctionChips
            selectId={`team-functions-${team.id}`}
            addLabel={`Business functions for ${team.name}`}
            functions={sortedFunctions}
            selectedIds={draftFunctionIds}
            onChange={ids => setFunctionDraft({ teamId: team.id, ids })}
          />
          {functionsDirty ? (
            <div className="team-function-chip-actions">
              <button
                type="button"
                className="btn-team-primary"
                disabled={isSavingFunctions}
                onClick={() => void saveFunctions(team, draftFunctionIds)}
              >
                {isSavingFunctions ? 'Saving…' : 'Save functions'}
              </button>
              <button
                type="button"
                className="btn-team-secondary"
                disabled={isSavingFunctions}
                onClick={() => setFunctionDraft(null)}
              >
                Cancel
              </button>
            </div>
          ) : (
            <span className="team-add-hint">
              Add or remove functions, then save. The current primary remains
              primary unless you remove it.
            </span>
          )}
        </div>

        {(team.health?.warnings?.length ?? 0) > 0 && (
          <ul className="team-health-warnings">
            {team.health.warnings.map((warning: string, idx: number) => (
              <li key={idx}>{warning}</li>
            ))}
          </ul>
        )}

        {pendingRoleChange && pendingRoleChange.teamId === team.id && (
          <div className="team-promotion-confirm" role="alert">
            <div className="team-promotion-text">
              <strong>
                Make {pendingRoleChange.memberName} the {pendingRoleChange.nextRole} of{' '}
                {team.name}?
              </strong>
              <span>
                A team has one {pendingRoleChange.nextRole}, so{' '}
                {pendingRoleChange.incumbentName} will be demoted to member. They stay
                on the team.
              </span>
            </div>
            <div className="team-promotion-actions">
              <button
                type="button"
                className="btn-team-secondary"
                onClick={() => setPendingRoleChange(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn-team-primary"
                onClick={() => void applyRoleChange(
                  pendingRoleChange.teamId,
                  pendingRoleChange.userId,
                  pendingRoleChange.nextRole
                )}
              >
                Confirm
              </button>
            </div>
          </div>
        )}

        <table className="team-members-table">
          <thead>
            <tr>
              <th>Member</th>
              <th>Team role</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {team.members.length === 0 ? (
              <tr>
                <td colSpan={3} className="empty-state">
                  No members yet — add someone below.
                </td>
              </tr>
            ) : (
              team.members.map(member => (
                <tr key={member.id}>
                  <td>
                    <div className="team-member-name">
                      {displayName(member.user, 'Unknown user')}
                      <ContractorBadge
                        className="contractor-badge-inline"
                        memberType={memberTypeOf(member.user_id)}
                        personName={displayName(member.user, 'This member')}
                      />
                    </div>
                    {member.user?.email && (
                      <div className="team-member-email">{member.user.email}</div>
                    )}
                  </td>
                  <td>
                    <select
                      className={`team-role-select team-role-${member.membership_role}`}
                      value={member.membership_role}
                      aria-label={`Team role for ${displayName(member.user, 'member')}`}
                      onChange={e => handleRoleSelect(
                        team,
                        member,
                        e.target.value as TeamMembershipRole
                      )}
                    >
                      {MEMBERSHIP_ROLE_OPTIONS.map(option => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn-team-remove"
                      onClick={() => void handleRemoveMember(team, member)}
                      title="Remove from team"
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>

        <div className="team-add-member">
          <select
            aria-label={`Add a member to ${team.name}`}
            value={addDraft.userId}
            onChange={e => setAddDraft(prev => ({ ...prev, userId: e.target.value }))}
          >
            <option value="">Select a person…</option>
            {/*
              * An <option> can hold only text, so the badge becomes a suffix
              * here. Everyone stays selectable: knowing somebody is a
              * contractor is not a reason to keep them off a team.
              */}
            {assignable.map(user => (
              <option key={user.id} value={user.id}>
                {withContractorSuffix(
                  user.display_name || user.email,
                  memberTypeOf(user.id)
                )}
              </option>
            ))}
          </select>
          <select
            aria-label={`Team role for the new member of ${team.name}`}
            value={addDraft.membershipRole}
            onChange={e => setAddDraft(prev => ({
              ...prev,
              membershipRole: e.target.value as TeamMembershipRole,
            }))}
          >
            {MEMBERSHIP_ROLE_OPTIONS.map(option => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn-team-primary"
            disabled={!addDraft.userId}
            onClick={() => void handleAddMember(team)}
          >
            Add member
          </button>
          {assignable.length === 0 && (
            <span className="team-add-hint">
              Everyone in the organisation is already on this team.
            </span>
          )}
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="team-management">
        <div className="loading-state">Loading teams…</div>
      </div>
    )
  }

  return (
    <div className="team-management">
      <div className="team-management-header">
        <div className="header-left">
          <h1>Teams</h1>
          <p className="subtitle">
            {teams.length} team{teams.length !== 1 ? 's' : ''} across{' '}
            {groups.length} function{groups.length !== 1 ? 's' : ''}
          </p>
        </div>
        <div className="header-right">
          <label className="team-archive-toggle">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={e => setShowArchived(e.target.checked)}
            />
            Show archived teams
          </label>
          <button
            type="button"
            className="btn-team-primary"
            onClick={() => setShowCreateForm(prev => !prev)}
          >
            {showCreateForm ? 'Cancel' : 'New team'}
          </button>
        </div>
      </div>

      <div className="team-permissions-note">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
          <circle cx="12" cy="12" r="10" />
          <path d="M12 16v-4" />
          <path d="M12 8h.01" />
        </svg>
        <span>
          Teams describe who is answerable for what. They grant no access:
          permissions come from organisation roles, set under User Management.
        </span>
      </div>

      {error && (
        <div className="error-banner">
          <span>{error}</span>
          <button type="button" onClick={() => void loadAll()}>Retry</button>
        </div>
      )}

      {showCreateForm && (
        <form className="team-create-form" onSubmit={handleCreate}>
          <div className="team-create-field">
            <label htmlFor="team-name">Team name</label>
            <input
              id="team-name"
              type="text"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              placeholder="e.g. Security Operations"
              required
            />
          </div>
          <div className="team-create-field">
            <label htmlFor="team-function">Business functions</label>
            <FunctionChips
              selectId="team-function"
              addLabel="Business functions"
              functions={sortedFunctions}
              selectedIds={newFunctionIds}
              onChange={setNewFunctionIds}
            />
            <span className="team-add-hint">
              Add one or more functions. The first one added is the team's primary function.
            </span>
          </div>
          <div className="team-create-field team-create-field-wide">
            <label htmlFor="team-description">Description</label>
            <input
              id="team-description"
              type="text"
              value={newDescription}
              onChange={e => setNewDescription(e.target.value)}
              placeholder="What this team is responsible for"
            />
          </div>
          <div className="team-create-actions">
            <button
              type="submit"
              className="btn-team-primary"
              disabled={isCreating || !newName.trim() || newFunctionIds.length === 0}
            >
              {isCreating ? 'Creating…' : 'Create team'}
            </button>
          </div>
        </form>
      )}

      {groups.length === 0 ? (
        <div className="team-empty-state">
          <p>No teams yet.</p>
          <p className="subtitle">
            Create one to describe how your organisation is structured.
          </p>
        </div>
      ) : (
        groups.map(group => (
          <section key={group.functionId} className="team-function-group">
            <div className="team-function-header">
              <h2>{group.fn?.name ?? 'Unrecognised function'}</h2>
              <span className="team-function-count">
                {group.teams.length} team{group.teams.length !== 1 ? 's' : ''}
              </span>
            </div>
            {group.fn?.description && (
              <p className="team-function-description">{group.fn.description}</p>
            )}
            <div className="team-rows">
              {group.teams.map(team => {
                const primary = memberWithRole(team, 'primary')
                const delegate = memberWithRole(team, 'delegate')
                const expanded = expandedTeamId === team.id
                return (
                  <div
                    key={team.id}
                    className={`team-row${team.is_active ? '' : ' team-row-archived'}`}
                  >
                    <div className="team-row-main">
                      <button
                        type="button"
                        className="team-row-toggle"
                        aria-expanded={expanded}
                        onClick={() => toggleExpanded(team.id)}
                      >
                        <svg
                          className={`team-row-chevron${expanded ? ' open' : ''}`}
                          width="16" height="16" viewBox="0 0 24 24"
                          fill="none" stroke="currentColor" strokeWidth="2"
                          aria-hidden="true"
                        >
                          <path d="M9 18l6-6-6-6" />
                        </svg>
                        <span className="team-row-name">{team.name}</span>
                      </button>
                      {!team.is_active && (
                        <span className="team-archived-badge">Archived</span>
                      )}
                      <span className="team-row-count">
                        {team.members.length} member{team.members.length !== 1 ? 's' : ''}
                      </span>
                      <span className="team-row-role">
                        <span className="team-row-role-label">Primary</span>
                        {primary
                          ? displayName(primary.user, 'Unknown user')
                          : <span className="team-row-role-empty">Not assigned</span>}
                      </span>
                      <span className="team-row-role">
                        <span className="team-row-role-label">Delegate</span>
                        {delegate
                          ? displayName(delegate.user, 'Unknown user')
                          : <span className="team-row-role-empty">Not assigned</span>}
                      </span>
                      {renderWarnings(team)}
                      <span className="team-row-actions">
                        {team.is_active ? (
                          <button
                            type="button"
                            className="btn-team-archive"
                            onClick={() => void handleArchive(team)}
                            title="Archive this team — nothing is deleted"
                          >
                            Archive
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="btn-team-secondary"
                            onClick={() => void handleRestore(team)}
                          >
                            Restore
                          </button>
                        )}
                      </span>
                    </div>
                    {expanded && renderTeamDetail(team)}
                  </div>
                )
              })}
            </div>
          </section>
        ))
      )}
    </div>
  )
}
