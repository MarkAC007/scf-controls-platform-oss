import type { EvidenceTracking, UserSimple } from '../types'

/**
 * How a user is named in the UI.
 *
 * Mirrors `user_label()` in `backend/user_display.py`, which produces the same
 * string for the API payloads that don't serialise a whole user object. Keep
 * the two in step: a name that changes shape depending on which endpoint drew
 * it reads as two different people.
 */
export function userLabel(user: UserSimple): string {
  return user.display_name?.trim() || user.email
}

/**
 * Who is answerable for an evidence row, as a display/grouping label (#781).
 *
 * Until #781 the reporting and dashboard breakdowns grouped on
 * `evidence_tracking.owner` — a free-text "Owner Team" box that no task, no
 * reminder and no queue ever read. The box is gone, so the answer now comes
 * from the columns that actually drive work: the accountable owner, falling
 * back to the assignee when nobody has been made accountable yet.
 *
 * Returns 'Unassigned' rather than '' so it can be used directly as a group
 * key — an empty bucket heading is indistinguishable from a rendering bug.
 */
export function evidenceOwnerLabel(tracking?: EvidenceTracking | null): string {
  const user = tracking?.owner_user || tracking?.assigned_user
  return user ? userLabel(user) : 'Unassigned'
}

/**
 * The user id behind ``evidenceOwnerLabel`` (#822 phase 2).
 *
 * The reporting and dashboard breakdowns group on the LABEL, which is right —
 * it is what the heading has to read. But a label cannot be turned back into a
 * person: two people can share a display name and 'Unassigned' is not anybody.
 * So the id is carried alongside, and a group is only labelled a contractor's
 * when every row in it resolved to the same person.
 *
 * Null when nobody is answerable, which is the ordinary case for untracked
 * evidence and must render as no badge rather than as a missing lookup.
 */
export function evidenceOwnerUserId(tracking?: EvidenceTracking | null): string | null {
  const user = tracking?.owner_user || tracking?.assigned_user
  return user?.id ? String(user.id) : null
}
