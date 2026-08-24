import type { MemberType, UserSimple } from '../../types'
import { userLabel } from '../../data/userDisplay'
import { ContractorBadge, withContractorSuffix } from '../ContractorBadge'

/**
 * Assignee picker for an evidence tracking row (#781).
 *
 * Bound to `evidence_tracking.assigned_user_id` — the column the task generator,
 * the due-date notifier and the work queue all read. Until this existed, the only
 * assignment control the evidence panel offered was the free-text "Owner Team"
 * box, which none of those three ever look at, so every auto-generated collection
 * task was created unassigned: no reminder was sent for it and it could not
 * appear in anybody's "assigned to me" queue.
 *
 * Deliberately a plain <select> and not the polymorphic `AssignmentPicker`.
 * That component manages a multi-user COLLABORATOR list in the `assignments`
 * table, which is a different thing from "the one person who has to collect
 * this" and is not what task generation reads. Both are shown in the panel; the
 * labels say which is which.
 */

export interface EvidenceAssigneeSelectProps {
  /** Current `assigned_user_id`, or empty/undefined when unassigned. */
  value?: string | null
  /** Members of the owning organisation, as returned by `getOrgMembers`. */
  members: UserSimple[]
  /** Receives a user id, or '' to unassign. */
  onChange: (userId: string) => void
  label?: string
  /**
   * Server-resolved user for the current value. Used only to keep a stale or
   * since-removed assignee visible rather than silently rendering as
   * "Unassigned" — an assignment that has quietly vanished is worse than one
   * that is visibly odd.
   */
  resolved?: UserSimple | null
  id?: string
  /**
   * Per-organisation internal/contractor lookup (#822 phase 2), supplied by
   * the screen rather than resolved here: this component is rendered more than
   * once per screen and each instance fetching the same membership list would
   * be one request per assignee picker for an answer that never differs.
   *
   * Optional, so a caller that has not wired it renders exactly as before.
   */
  memberTypeOf?: (userId: string | null | undefined) => MemberType | undefined
}

export function EvidenceAssigneeSelect({
  value,
  members,
  onChange,
  label = 'Assignee',
  resolved,
  id,
  memberTypeOf,
}: EvidenceAssigneeSelectProps) {
  const current = value || ''
  const typeOf = (userId: string | null | undefined) => memberTypeOf?.(userId)
  const knownIds = new Set(members.map(m => m.id))

  // The stored assignee may not be in `members`: they can have left the org, be
  // a consultant (whom GET /members does not list), or simply not have loaded
  // yet. Always append them, so the value stays visible and the select stays
  // controlled — an assignment that quietly renders as "Unassigned" is the one
  // failure mode this component exists to prevent.
  const orphan =
    current && !knownIds.has(current) ? resolved ?? { id: current, email: current } : null

  // ...but only CALL them a non-member once we have a member list to say so
  // against. While the fetch is in flight — and permanently, if it fails —
  // `members` is empty, so an ungated suffix would brand every assignee on the
  // page "(not a current member)". Flag the failure instead of mislabelling the
  // data.
  const membersLoaded = members.length > 0
  const orphanLabel = orphan
    ? membersLoaded
      ? `${userLabel(orphan)} (not a current member)`
      : userLabel(orphan)
    : ''

  // An <option> can hold only text, so the badge cannot live against each
  // name in the list — it goes beside the field label, describing whoever is
  // currently assigned, and the options carry the same word as a suffix.
  const currentLabel = current
    ? userLabel(members.find(m => m.id === current) ?? orphan ?? { id: current, email: current })
    : null

  return (
    <div className="form-group">
      <label htmlFor={id}>
        {label}
        <ContractorBadge
          className="contractor-badge-inline"
          memberType={typeOf(current)}
          personName={currentLabel}
        />
      </label>
      <select
        id={id}
        value={current}
        onChange={e => onChange(e.target.value)}
        className="form-control"
      >
        <option value="">Unassigned</option>
        {members.map(member => (
          <option key={member.id} value={member.id}>
            {withContractorSuffix(userLabel(member), typeOf(member.id))}
          </option>
        ))}
        {orphan && (
          <option key={orphan.id} value={orphan.id}>
            {withContractorSuffix(orphanLabel, typeOf(orphan.id))}
          </option>
        )}
      </select>
      {!membersLoaded && (
        <small className="form-text text-muted">
          Member list unavailable — only the current assignee can be kept.
        </small>
      )}
    </div>
  )
}
