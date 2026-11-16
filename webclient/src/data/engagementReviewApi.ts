/**
 * Engagement evidence review board and dashboard — API client.
 *
 * An engagement is an auditor reviewing evidence the organisation already
 * holds, seen from a mapped framework. The card is the evidence object (SCF
 * ERL id, e.g. E-IAC-07). Columns are review stages; collection state (files,
 * in-window counts, AI verdict) is on the card.
 *
 * Every route here is engagement-scoped, so the same calls work for an org
 * member and for an external auditor who is not a member of the organisation.
 * Shapes mirror backend/schemas_engagement_review.py and
 * backend/api/engagement_comments.py.
 */
import { apiFetch } from './apiClient'

// ---------------------------------------------------------------------------
// Shared vocabulary
// ---------------------------------------------------------------------------

export type ReviewStatus = 'open' | 'in_progress' | 'action_required' | 'submitted' | 'accepted'

/** Board order, left to right. */
export const REVIEW_STATUSES: ReviewStatus[] = [
  'open', 'in_progress', 'action_required', 'submitted', 'accepted',
]

export const REVIEW_STATUS_LABELS: Record<ReviewStatus, string> = {
  open: 'Open',
  in_progress: 'In progress',
  action_required: 'Action required',
  submitted: 'Submitted',
  accepted: 'Accepted',
}

/** One line under each column heading: what the stage means and whose move it is. */
export const REVIEW_STATUS_HINTS: Record<ReviewStatus, string> = {
  open: 'Not yet tested by the auditor',
  in_progress: 'Auditor is testing the evidence',
  action_required: 'Auditor needs more from the organisation',
  submitted: 'Organisation has responded, awaiting the auditor',
  accepted: 'Auditor accepts the evidence',
}

export type AiVerdict = 'sufficient' | 'partial' | 'insufficient' | 'not_assessed'

export const AI_VERDICT_LABELS: Record<AiVerdict, string> = {
  sufficient: 'AI: sufficient',
  partial: 'AI: partial',
  insufficient: 'AI: insufficient',
  not_assessed: 'AI: not assessed',
}

export interface PersonRef {
  user_id: string
  name: string
}

export type StatusCounts = Record<ReviewStatus, number>

// ---------------------------------------------------------------------------
// Board
// ---------------------------------------------------------------------------

export interface CardControl {
  scf_id: string
  title: string | null
  /** Clauses of the tab's framework that this control maps to. */
  clauses: string[]
}

export interface CardOwner {
  /** 'explicit' | 'accountable_team' | 'org_admin' */
  tier: string | null
  /** A person's name, a team name, or "Unowned → org admins". Never an email. */
  label: string
  /** The accountable team's id, when one is assigned. */
  team_id: string | null
  team_name: string | null
  is_tracked: boolean
  tracking_id: string | null
  assigned_user_id: string | null
  user_ids: string[]
}

export interface EvidenceCard {
  review_id: string
  /** SCF ERL id, e.g. E-IAC-07. */
  evidence_id: string
  title: string | null
  status: ReviewStatus
  controls: CardControl[]
  clauses: string[]
  file_count: number
  in_window_count: number
  ai_verdict: AiVerdict
  open_query_count: number
  reviewer: PersonRef | null
  owner: CardOwner
  /** Stages this caller may move the card to right now. */
  allowed_transitions: ReviewStatus[]
  /** Whether a note is required, keyed by each allowed transition. */
  note_required: Partial<Record<ReviewStatus, boolean>>
  updated_at: string | null
}

export interface CardArtifact {
  id: string
  filename: string
  uploaded_at: string | null
  effective_start: string | null
  effective_end: string | null
  in_window: boolean
  /** 'asserted_period' | 'upload_date' */
  window_basis: string
  /** Raw assessment status of this file, or null when it has none. */
  ai_status: string | null
}

export interface EvidenceCardDetail extends EvidenceCard {
  artifacts: CardArtifact[]
  decision_note: string | null
  last_decision_at: string | null
  last_decision_by: PersonRef | null
  /** Catalog description of the evidence object. */
  description: string | null
}

export interface NoEvidenceControl {
  scf_id: string
  title: string | null
  clauses: string[]
  open_query_count: number
}

export interface ReviewViewer {
  user_id: string | null
  /** Holds an active auditor grant on this engagement (external or internal). */
  is_auditor: boolean
  auditor_kind: 'external' | 'internal' | null
  /** Org role, or null for an external auditor. */
  org_role: 'viewer' | 'editor' | 'admin' | null
  can_assign_reviewer: boolean
  can_reassign_owner: boolean
}

export interface EngagementSummary {
  id: string
  name: string
  status: string
  start_date: string | null
  end_date: string | null
  frameworks: string[]
}

export interface EvidenceReviewBoard {
  engagement: EngagementSummary
  framework: string
  /** Unfiltered totals for this framework tab. */
  totals: { evidence: number; controls: number }
  /** Counts of the cards returned, after filters. */
  counts: StatusCounts
  cards: EvidenceCard[]
  no_evidence_controls: NoEvidenceControl[]
  filter_options: { clauses: string[]; scf_ids: string[]; reviewers: PersonRef[] }
  viewer: ReviewViewer
}

export interface ReviewBoardFilters {
  status?: ReviewStatus
  clause?: string
  scfId?: string
  aiVerdict?: AiVerdict
  hasOpenQueries?: boolean
  /** A user id, or 'none' for unassigned. */
  reviewer?: string
  search?: string
  /** Cards I review, or (org members) cards I own. */
  mine?: boolean
}

const base = (orgId: string, engagementId: string) =>
  `/organizations/${orgId}/engagements/${engagementId}`

const card = (orgId: string, engagementId: string, evidenceId: string) =>
  `${base(orgId, engagementId)}/evidence-reviews/${encodeURIComponent(evidenceId)}`

export function reviewBoardQuery(framework: string, filters: ReviewBoardFilters = {}): string {
  const params = new URLSearchParams()
  params.set('framework', framework)
  if (filters.status) params.set('status', filters.status)
  if (filters.clause) params.set('clause', filters.clause)
  if (filters.scfId) params.set('scf_id', filters.scfId)
  if (filters.aiVerdict) params.set('ai_verdict', filters.aiVerdict)
  if (filters.hasOpenQueries !== undefined) params.set('has_open_queries', String(filters.hasOpenQueries))
  if (filters.reviewer) params.set('reviewer', filters.reviewer)
  if (filters.search && filters.search.trim()) params.set('search', filters.search.trim())
  if (filters.mine) params.set('mine', 'true')
  return params.toString()
}

export function getReviewBoard(
  orgId: string, engagementId: string, framework: string, filters: ReviewBoardFilters = {},
): Promise<EvidenceReviewBoard> {
  return apiFetch<EvidenceReviewBoard>(
    `${base(orgId, engagementId)}/evidence-reviews?${reviewBoardQuery(framework, filters)}`,
  )
}

export function getReviewCard(
  orgId: string, engagementId: string, evidenceId: string, framework: string,
): Promise<EvidenceCardDetail> {
  return apiFetch<EvidenceCardDetail>(
    `${card(orgId, engagementId, evidenceId)}?framework=${encodeURIComponent(framework)}`,
  )
}

export interface ReviewStatusChange {
  to_status: ReviewStatus
  /**
   * The stage the caller saw the card in. When it no longer matches, the
   * server refuses the move with 409 instead of applying a stale one.
   */
  from_status?: ReviewStatus
  note?: string
  /** Organisation response only: existing evidence files of this evidence object. */
  artifact_file_ids?: string[]
  /** Organisation response only: why no artifact is linked. */
  no_artifact_reason?: string
}

/**
 * Move a card. The server decides whether the move is allowed:
 * 409 = not a valid move from the card's current stage (someone else moved it),
 * 403 = this caller may not make it, 422 = a required note or artifact is missing.
 * The thrown Error carries `.status`.
 */
export function changeReviewStatus(
  orgId: string, engagementId: string, evidenceId: string, framework: string, change: ReviewStatusChange,
): Promise<EvidenceCardDetail> {
  return apiFetch<EvidenceCardDetail>(
    `${card(orgId, engagementId, evidenceId)}/status?framework=${encodeURIComponent(framework)}`,
    { method: 'POST', body: JSON.stringify(change) },
  )
}

/** Assign the reviewing auditor, or pass null to clear. Auditor or org admin. */
export function assignReviewer(
  orgId: string, engagementId: string, evidenceId: string, framework: string, userId: string | null,
): Promise<EvidenceCardDetail> {
  return apiFetch<EvidenceCardDetail>(
    `${card(orgId, engagementId, evidenceId)}/reviewer?framework=${encodeURIComponent(framework)}`,
    { method: 'PUT', body: JSON.stringify({ user_id: userId }) },
  )
}

export interface ReviewEvent {
  id: string
  event_type: 'transition' | 'reviewer_assigned'
  from_status: ReviewStatus
  to_status: ReviewStatus
  actor: PersonRef | null
  actor_kind: 'auditor' | 'org'
  note: string | null
  created_at: string | null
}

/** The card's history, oldest first. */
export function listReviewEvents(
  orgId: string, engagementId: string, evidenceId: string,
): Promise<ReviewEvent[]> {
  return apiFetch<ReviewEvent[]>(`${card(orgId, engagementId, evidenceId)}/events`)
}

export interface ReviewPeople {
  auditors: { user_id: string; name: string; kind: 'external' | 'internal' }[]
  members: { user_id: string; name: string; role: string }[]
}

/** Active auditors and org members of this engagement. No email addresses. */
export function getReviewPeople(orgId: string, engagementId: string): Promise<ReviewPeople> {
  return apiFetch<ReviewPeople>(`${base(orgId, engagementId)}/review-people`)
}

/**
 * Signed link for one artifact. Engagement-scoped, so an external auditor can
 * open a file without the org-level evidence routes. `download_url` is relative
 * to the site origin and is null when the deployment cannot sign links.
 */
export function getReviewArtifactLink(
  orgId: string, engagementId: string, evidenceId: string, fileId: string,
): Promise<{ download_url: string | null; filename: string }> {
  return apiFetch(`${card(orgId, engagementId, evidenceId)}/files/${fileId}`)
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

export interface StatusBlock {
  total: number
  by_status: StatusCounts
}

export interface ClauseRollupItem {
  clause: string
  /** Leading segment of the clause id; the catalog holds no theme names. */
  group: string
  status: ReviewStatus
  control_count: number
  evidence_count: number
}

export interface ClauseGroup {
  key: string
  label: string
  clauses_total: number
  clauses_accepted: number
}

export interface ActionRequiredItem {
  evidence_id: string
  title: string | null
  held_controls: string[]
  held_clauses: string[]
  open_query_count: number
}

export interface TimelineItem {
  /** window_start | testing_started | all_evidence_tested | all_clauses_accepted | window_end */
  key: string
  label: string
  /** ISO date or datetime, or null when not known / not reached. */
  date: string | null
  done: boolean
}

export interface ReviewDashboard {
  engagement: EngagementSummary & { created_at: string | null }
  framework: string
  evidence: StatusBlock
  /** Rolled up from evidence: a control is Accepted only when all its evidence objects are. */
  controls: StatusBlock
  /** Rolled up from controls by the same rule. */
  clauses: StatusBlock
  clause_rollup: ClauseRollupItem[]
  groups: ClauseGroup[]
  action_required: ActionRequiredItem[]
  no_evidence_controls: NoEvidenceControl[]
  open_queries: { total: number; evidence: number; control: number }
  timeline: TimelineItem[]
}

export function getReviewDashboard(
  orgId: string, engagementId: string, framework: string,
): Promise<ReviewDashboard> {
  return apiFetch<ReviewDashboard>(
    `${base(orgId, engagementId)}/review-dashboard?framework=${encodeURIComponent(framework)}`,
  )
}

// ---------------------------------------------------------------------------
// Discussion thread (shared / internal)
// ---------------------------------------------------------------------------

/**
 * `internal` comments are org-only notes. The server never returns them to
 * anyone holding an auditor grant: for those callers the internal list is a
 * 404 and the counts have no `internal` key. The UI shows the Internal tab
 * only when `counts.internal` is present.
 */
export type CommentVisibility = 'shared' | 'internal'

export interface ReviewComment {
  id: string
  parent_comment_id: string | null
  author: { user_id: string | null; name: string | null }
  /** Blank for a soft-deleted comment, which keeps its place in the thread. */
  content: string
  visibility: CommentVisibility
  mentions: { user_id: string | null; name: string | null }[]
  is_edited: boolean
  edited_at: string | null
  is_deleted: boolean
  created_at: string | null
  can_edit: boolean
  can_delete: boolean
}

export interface ReviewCommentCounts {
  shared: number
  /** Absent (not zero) for anyone who may not see internal notes. */
  internal?: number
}

const comments = (orgId: string, engagementId: string, evidenceId: string) =>
  `${card(orgId, engagementId, evidenceId)}/comments`

export function listReviewComments(
  orgId: string, engagementId: string, evidenceId: string, visibility: CommentVisibility = 'shared',
): Promise<ReviewComment[]> {
  return apiFetch<ReviewComment[]>(
    `${comments(orgId, engagementId, evidenceId)}?visibility=${visibility}`,
  )
}

export function getReviewCommentCounts(
  orgId: string, engagementId: string, evidenceId: string,
): Promise<ReviewCommentCounts> {
  return apiFetch<ReviewCommentCounts>(`${comments(orgId, engagementId, evidenceId)}/counts`)
}

export function createReviewComment(
  orgId: string, engagementId: string, evidenceId: string,
  data: { content: string; visibility?: CommentVisibility; parent_comment_id?: string; mentions?: string[] },
): Promise<ReviewComment> {
  return apiFetch<ReviewComment>(comments(orgId, engagementId, evidenceId), {
    method: 'POST', body: JSON.stringify(data),
  })
}

export function updateReviewComment(
  orgId: string, engagementId: string, evidenceId: string, commentId: string, content: string,
): Promise<ReviewComment> {
  return apiFetch<ReviewComment>(`${comments(orgId, engagementId, evidenceId)}/${commentId}`, {
    method: 'PATCH', body: JSON.stringify({ content }),
  })
}

/** Soft delete: the author, or an org admin who is not acting as an auditor. */
export function deleteReviewComment(
  orgId: string, engagementId: string, evidenceId: string, commentId: string,
): Promise<void> {
  return apiFetch<void>(`${comments(orgId, engagementId, evidenceId)}/${commentId}`, {
    method: 'DELETE',
  })
}

export interface ReviewCommentHistoryItem {
  id: string
  old_content: string
  edited_at: string | null
  edited_by: { user_id: string | null; name: string | null }
}

export function getReviewCommentHistory(
  orgId: string, engagementId: string, evidenceId: string, commentId: string,
): Promise<ReviewCommentHistoryItem[]> {
  return apiFetch<ReviewCommentHistoryItem[]>(
    `${comments(orgId, engagementId, evidenceId)}/${commentId}/history`,
  )
}

// ---------------------------------------------------------------------------
// react-query keys (one place, so a move on the board refreshes the dashboard)
// ---------------------------------------------------------------------------

export const reviewKeys = {
  all: (engagementId: string) => ['engagement-review', engagementId] as const,
  board: (engagementId: string, framework: string, filters: ReviewBoardFilters) =>
    ['engagement-review', engagementId, 'board', framework, filters] as const,
  card: (engagementId: string, evidenceId: string, framework: string) =>
    ['engagement-review', engagementId, 'card', evidenceId, framework] as const,
  events: (engagementId: string, evidenceId: string) =>
    ['engagement-review', engagementId, 'events', evidenceId] as const,
  people: (engagementId: string) => ['engagement-review', engagementId, 'people'] as const,
  dashboard: (engagementId: string, framework: string) =>
    ['engagement-review', engagementId, 'dashboard', framework] as const,
  comments: (engagementId: string, evidenceId: string, visibility: CommentVisibility) =>
    ['engagement-review', engagementId, 'comments', evidenceId, visibility] as const,
  commentCounts: (engagementId: string, evidenceId: string) =>
    ['engagement-review', engagementId, 'comment-counts', evidenceId] as const,
  queries: (engagementId: string, target: string) =>
    ['engagement-review', engagementId, 'queries', target] as const,
}
