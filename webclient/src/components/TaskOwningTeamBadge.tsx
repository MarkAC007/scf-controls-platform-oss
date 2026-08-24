/**
 * TaskOwningTeamBadge — one task row's answer to "who owns this?" (#822 phase 4).
 *
 * Read-only. The list says who owns the work; the modal is where it changes.
 *
 * It always says something. A task with no assignee used to render nothing at
 * all here, which read as "nobody is on this" when in fact the evidence
 * item's accountable team was — and the daily scheduler was skipping it
 * outright. So there are exactly three things this can say, and none of them
 * is silence:
 *
 *  * inherited — the common case, named, with the pill that says it came from
 *    the evidence item rather than from a choice somebody made about the task
 *  * override — the task names its own team
 *  * neither — a warning, because that is ownership evaporating
 *
 * The distinction between inherited and override is worth a pill of its own:
 * without it a user cannot tell a deliberate setup/review split from a team
 * that simply followed the parent, and "why is GRC on this?" has no answer on
 * the screen.
 */
import type { MemberType, TaskTeamOwnership } from '../types'
import { taskOwnershipWarnings } from '../hooks/useTaskTeamOwnership'
import { ContractorBadge } from './ContractorBadge'
import { TeamWarningBadges } from './TeamWarningBadge'

interface TaskOwningTeamBadgeProps {
  ownership: TaskTeamOwnership
  /**
   * The owning person's ``member_type``, resolved by the caller.
   *
   * Passed in rather than looked up here because a list renders hundreds of
   * these and the lookup is one request for the whole organisation — a badge
   * that resolved its own would be the N+1 back again.
   */
  memberType?: MemberType | null
  /**
   * Suppress everything while ownership is still being read.
   *
   * A row that renders "No owning team" for the half second before the
   * assignment map lands has accused the org of something untrue, so an
   * unresolved row shows nothing rather than a guess.
   */
  resolved?: boolean
}

export function TaskOwningTeamBadge({
  ownership,
  memberType,
  resolved = true,
}: TaskOwningTeamBadgeProps) {
  if (!resolved) return null

  const warnings = taskOwnershipWarnings(ownership)
  const team = ownership.team

  return (
    <div className="task-owning-team-badge">
      <strong>Owning team:</strong>{' '}
      {team ? (
        <>
          <span className="task-owning-team-name">{team.name}</span>
          <span
            className={
              ownership.source === 'task'
                ? 'task-owning-team-pill task-owning-team-pill-override'
                : 'task-owning-team-pill task-owning-team-pill-inherited'
            }
            title={
              ownership.source === 'task'
                ? 'Set on this task, overriding its evidence item.'
                : 'Inherited from the accountable team on this task’s evidence item.'
            }
          >
            {ownership.source === 'task' ? 'Override' : 'Inherited'}
          </span>
          {team.person_name && (
            <span className="task-owning-team-person">
              {team.person_name}
              <ContractorBadge
                className="contractor-badge-inline"
                memberType={memberType}
                personName={team.person_name}
              />
            </span>
          )}
        </>
      ) : (
        <span className="task-owning-team-none">None</span>
      )}
      <TeamWarningBadges warnings={warnings} />
    </div>
  )
}

export default TaskOwningTeamBadge
