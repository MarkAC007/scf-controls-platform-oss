import type { ConfirmOptions } from '../../contexts/ConfirmContext'
import type { ActionItemPriority, ActionItemStatus, Deficiency, UserSimple } from '../../types'

export { DEFICIENCY_STATUS_LABELS as STATUS_LABELS } from '../../data/domainLabels'

export const PRIORITY_LABELS: Record<ActionItemPriority, string> = {
  critical: 'Critical',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
}

export const PRIORITIES: ActionItemPriority[] = ['critical', 'high', 'medium', 'low']

/** Every status, in lifecycle order: the board's columns, left to right. */
export const STATUSES: ActionItemStatus[] = ['open', 'in_progress', 'completed', 'cancelled']

/** Where a deficiency came from, in words. */
export function sourceLabel(d: Pick<Deficiency, 'source' | 'source_ref'>): string {
  switch (d.source) {
    case 'control_at_risk':
      return 'Raised automatically: control at risk'
    case 'composite_insufficient':
      return 'Raised automatically: evidence insufficient'
    case 'evidence_rejected':
      return `Raised automatically: evidence ${d.source_ref ?? ''} rejected`.trimEnd()
    case 'capability_test':
      return 'Capability test finding'
    default:
      return 'Raised by hand'
  }
}

/**
 * The owner's name: the member, else the free-text owner, else nobody.
 * `users` is undefined while the members are unknown (loading or failed), when
 * an owner who is not found must not be called a former member.
 */
export function ownerLabel(d: Pick<Deficiency, 'owner_user_id' | 'owner_name'>, users: UserSimple[] | undefined): string {
  if (d.owner_user_id) {
    if (!users) return d.owner_name || 'A member'
    const user = users.find((u) => u.id === d.owner_user_id)
    if (user) return user.display_name || user.email
    if (d.owner_name) return d.owner_name
    return 'A former member'
  }
  return d.owner_name || 'Unassigned'
}

export const cancelMessage = (title: string) =>
  `Cancel "${title}"? It stops counting as an open deficiency. Deficiencies are never deleted; it can be reopened.`

export function reopenMessage(d: Pick<Deficiency, 'title' | 'status'>): string {
  return d.status === 'completed'
    ? `Reopen "${d.title}"? Its retest note and completion date are cleared, and completing it again needs a new retest.`
    : `Reopen "${d.title}"? It counts as an open deficiency again.`
}

/** The question asked before cancelling, by the list and the board alike. */
export const cancelConfirm = (d: Pick<Deficiency, 'title'>): ConfirmOptions => ({
  title: 'Cancel deficiency',
  message: cancelMessage(d.title),
  confirmLabel: 'Cancel deficiency',
  cancelLabel: 'Keep it',
})

/** The question asked before reopening a completed or cancelled deficiency. */
export const reopenConfirm = (d: Pick<Deficiency, 'title' | 'status'>): ConfirmOptions => ({
  title: 'Reopen deficiency',
  message: reopenMessage(d),
  confirmLabel: 'Reopen',
})
