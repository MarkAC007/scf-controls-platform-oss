/**
 * TaskOwningTeamField — the team that owns one task, and whether it was
 * inherited or chosen (#822 phase 4).
 *
 * The tri-state is the point of the column, so it is the point of this field:
 *
 *  * **Not set — inherit.** The common case, and the one that should cost the
 *    user nothing. It is rendered as what it actually resolves to, never as
 *    an empty box: "Inherits from evidence item: Security Operations" is the
 *    difference between a user who can see that the task is owned and a user
 *    who thinks nobody has it.
 *  * **Set — override.** The case the column exists for. ``setup``,
 *    ``collection`` and ``review`` on one evidence item are routinely
 *    different functions: engineering wires the export, the platform collects
 *    it, GRC signs it off.
 *
 * There is no "unowned" option and there must never be one. Clearing the
 * field returns the task to inheriting; it does not detach it. A task whose
 * evidence item has no accountable team is still inheriting — inheriting
 * nothing — and that is a warning about the evidence item, which is where the
 * fix belongs.
 *
 * Teams grant no permissions. Naming a team here changes nothing about what
 * its members may do; ``organization_members.role`` remains the only input to
 * access control, exactly as in phases 1–3.
 */
import { useMemo } from 'react'

import { useOrgMemberTypes } from '../hooks/useOrgMemberTypes'
import {
  taskOwnershipWarnings,
  useTaskTeamOwnership,
} from '../hooks/useTaskTeamOwnership'
import type { TaskTeamOwnership } from '../types'
import { ContractorBadge } from './ContractorBadge'
import { TeamWarningBadges } from './TeamWarningBadge'

interface TaskOwningTeamFieldProps {
  organizationId: string
  /**
   * The parent evidence item's tracking row id — the thing a null override
   * inherits from. Without it the field cannot say what it would inherit, so
   * callers that do not have one must not render this.
   */
  evidenceTrackingId: string
  /** The task's ``owning_team_id``: null to inherit, a team id to override. */
  value: string | null
  onChange: (teamId: string | null) => void
  disabled?: boolean
  /** Distinguishes the radio/select ids when two of these share a page. */
  idPrefix?: string
}

/** The sentence under the picker. One node, so it reads as one claim. */
function resolvedSentence(ownership: TaskTeamOwnership): string {
  if (ownership.source === 'task') {
    return ownership.team
      ? `Overrides the evidence item: ${ownership.team.name}`
      : 'Overrides the evidence item: unknown team'
  }
  if (ownership.source === 'evidence' && ownership.team) {
    return `Inherits from evidence item: ${ownership.team.name}`
  }
  return 'Inherits from evidence item: no accountable team'
}

export default function TaskOwningTeamField({
  organizationId,
  evidenceTrackingId,
  value,
  onChange,
  disabled = false,
  idPrefix = 'task',
}: TaskOwningTeamFieldProps) {
  /*
   * Two pending tasks, one hook. The first is what the user is about to save;
   * the second is the same task with the override cleared, which is what the
   * picker's inherit option and the hint below it describe. Resolving both
   * through one hook rather than two keeps it at one assignment read and one
   * team read — two hook instances would double both for a display detail.
   */
  const drafts = useMemo(
    () => [
      {
        id: `${idPrefix}-draft`,
        evidence_tracking_id: evidenceTrackingId,
        owning_team_id: value,
      },
      {
        id: `${idPrefix}-inherit`,
        evidence_tracking_id: evidenceTrackingId,
        owning_team_id: null,
      },
    ],
    [idPrefix, evidenceTrackingId, value]
  )

  const { ownershipFor, teams, loading } = useTaskTeamOwnership(organizationId, drafts)

  const { memberTypeOf } = useOrgMemberTypes(organizationId)

  const ownership = ownershipFor(drafts[0])
  const inherited = ownershipFor(drafts[1])
  const warnings = taskOwnershipWarnings(ownership)

  const selectable = useMemo(
    () => teams.filter(t => t.is_active).sort((a, b) => a.name.localeCompare(b.name)),
    [teams]
  )

  const inheritLabel = inherited.team
    ? `Inherit from evidence item (${inherited.team.name})`
    : 'Inherit from evidence item (no accountable team)'

  return (
    <div className="task-owning-team">
      <div className="task-owning-team-header">
        <label className="task-modal-label" htmlFor={`${idPrefix}-owning-team`}>
          Owning team
        </label>
        <TeamWarningBadges warnings={warnings} />
      </div>

      <select
        id={`${idPrefix}-owning-team`}
        className="task-modal-select"
        aria-label="Owning team for this task"
        // An empty string, not a missing value: the option that means
        // "inherit" has to be selectable, and a select cannot hold null.
        value={value ?? ''}
        disabled={disabled || loading}
        onChange={e => onChange(e.target.value === '' ? null : e.target.value)}
      >
        <option value="">{inheritLabel}</option>
        {selectable.map(team => (
          <option key={team.id} value={team.id}>
            {team.name}
          </option>
        ))}
      </select>

      <p className="task-owning-team-resolved">
        <span className="task-owning-team-source">{resolvedSentence(ownership)}</span>
        {ownership.team?.person_name && (
          <span className="task-owning-team-person">
            {` — ${ownership.team.person_name}`}
            <ContractorBadge
              className="contractor-badge-inline"
              memberType={memberTypeOf(ownership.team.person_user_id)}
              personName={ownership.team.person_name}
            />
          </span>
        )}
      </p>

      {ownership.source === 'task' && (
        <p className="task-owning-team-hint">
          {inherited.team
            ? `Without this override the task would follow its evidence item to ${inherited.team.name}.`
            : 'Its evidence item has no accountable team, so without this override nobody would own the task.'}
        </p>
      )}

      {ownership.source !== 'task' && (
        <p className="task-owning-team-hint">
          Leave this inheriting unless the work belongs to a different function
          from the evidence item — setup, collection and review often do.
        </p>
      )}
    </div>
  )
}
