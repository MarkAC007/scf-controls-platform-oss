import { useCallback, useEffect, useState } from 'react'
import {
  reviewWindowVerdict,
  listWindowAssessmentVersions,
  type AOFinding,
  type AOOverrideRequestItem,
  type WindowAssessmentVersion,
} from '../../data/apiClient'
import type { EvidenceWindowAssessment } from '../../types'
import { useIsOrgEditor } from '../../hooks/useHasOrgRole'
import { AOFindingRow, AssessmentHistory } from './AssessmentReviewPanel'
import { verdictPresentation, TERMINAL_STATUSES } from './assessmentVerdict'

/**
 * The human end of the *window* AI assessment (window parity, PR3).
 *
 * The per-file ``AssessmentReviewPanel`` answers the machine one file at a
 * time. This answers it one collection period at a time: the AI has read
 * every file in the window as a portfolio and given each assessment
 * objective a designation, naming the files it relied on; a person confirms
 * that reading or corrects it, objective by objective, with a stated reason
 * whenever they disagree.
 *
 * Two decisions and no third, for the same reason as the per-file panel:
 * rejecting a verdict would leave the window with nothing to act on.
 * Disagreement is expressed by saying what the right answer is.
 *
 * This is a different verb from the acceptance review that sits above it in
 * ``WindowReviewPanel`` (Approve / Reject / Request revision). That one is
 * the organisation's decision about the *evidence*; this one is a person
 * standing behind the machine's *reading* of it. Until someone does, the
 * verdict is a suggestion and carries reduced weight in the evidence
 * quality score.
 */

interface WindowVerdictReviewPanelProps {
  orgId: string
  assessment: EvidenceWindowAssessment
  /** Called with the updated window assessment once a decision lands. */
  onReviewed: (updated: EvidenceWindowAssessment) => void
}

export function WindowVerdictReviewPanel({
  orgId,
  assessment,
  onReviewed,
}: WindowVerdictReviewPanelProps) {
  // Editor, matching the backend's require_org_role("editor").
  const canReview = useIsOrgEditor(orgId)

  const [mode, setMode] = useState<'idle' | 'overriding'>('idle')
  const [reason, setReason] = useState('')
  const [overrides, setOverrides] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [versions, setVersions] = useState<WindowAssessmentVersion[] | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyError, setHistoryError] = useState<string | null>(null)

  const status = assessment.status ?? assessment.assessment_status
  const aoFindings: AOFinding[] = assessment.ao_findings ?? []
  const reviewed = Boolean(assessment.review_decision)
  const isTerminal = TERMINAL_STATUSES.includes(status)
  const hasVersion = Boolean(assessment.current_version_id)
  const verdict = verdictPresentation(status, assessment.review_decision)
  const fileIds = assessment.file_ids ?? []

  // Files are named by their position in the window, which is what the
  // model was shown: "file 2 of 3". The window row carries IDs, not
  // filenames, and a UUID tells a reviewer nothing.
  const fileLabel = useCallback(
    (fileId: string) => {
      const index = fileIds.indexOf(fileId)
      return index === -1 ? `file ${fileId.slice(0, 8)}…` : `file ${index + 1} of ${fileIds.length}`
    },
    [fileIds],
  )

  const loadVersions = useCallback(async () => {
    try {
      setHistoryError(null)
      setVersions(await listWindowAssessmentVersions(orgId, assessment.id))
    } catch (err) {
      // A history we could not fetch is not an empty history.
      setHistoryError(err instanceof Error ? err.message : 'Could not load the assessment history.')
    }
  }, [orgId, assessment.id])

  useEffect(() => {
    if (historyOpen && versions === null && historyError === null) {
      void loadVersions()
    }
  }, [historyOpen, versions, historyError, loadVersions])

  const submit = async (decision: 'confirmed' | 'overridden') => {
    setSubmitting(true)
    setError(null)
    try {
      const body =
        decision === 'confirmed'
          ? { decision: 'confirmed' as const }
          : {
              decision: 'overridden' as const,
              reason,
              ao_overrides: Object.entries(overrides).map(
                ([ao_id, human_designation]): AOOverrideRequestItem => ({
                  ao_id,
                  human_designation,
                }),
              ),
            }
      const updated = await reviewWindowVerdict(orgId, assessment.id, body)
      onReviewed(updated)
      setMode('idle')
      setReason('')
      setOverrides({})
      setVersions(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not record the decision.')
    } finally {
      setSubmitting(false)
    }
  }

  const changedCount = Object.keys(overrides).length
  const canSubmitOverride = changedCount > 0 && reason.trim().length > 0

  if (!isTerminal) return null

  return (
    <div className="assessment-review window-verdict-review" data-testid="window-verdict-review">
      <div className="assessment-review-header">
        <h5 className="assessment-review-title">Assessment objectives for this window</h5>
        <span className={verdict.className} data-testid="window-verdict-review-verdict">
          {verdict.text}
        </span>
      </div>

      <p className="assessment-review-framing">
        {reviewed
          ? assessment.review_decision === 'overridden'
            ? 'A reviewer corrected this assessment. The designations below are theirs where they disagreed with the AI.'
            : 'A reviewer confirmed this assessment. The designations below stand as the AI proposed them.'
          : 'These are the AI’s suggestions for the whole collection period. They are advisory until someone confirms or corrects them.'}
      </p>

      {status === 'insufficient_sample' && (
        <p className="assessment-review-framing window-verdict-sample-note" data-testid="window-verdict-sample-note">
          Too few files reached this window to judge the cadence. Confirming records
          that a person has seen that; correcting an objective does not change it.
        </p>
      )}

      {status === 'unassessable' && assessment.unassessable_reason && (
        <p className="assessment-review-framing" data-testid="window-verdict-unassessable-reason">
          {assessment.unassessable_reason}
        </p>
      )}

      {aoFindings.length === 0 ? (
        <div className="assessment-review-empty">
          {assessment.schema_version === 1 || assessment.schema_version === undefined
            ? 'This verdict predates objective-grounded window assessment. Re-run the assessment to review it objective by objective.'
            : 'The controls mapped to this evidence publish no assessment objectives, so there is nothing to review objective by objective.'}
        </div>
      ) : (
        <ul className="ao-finding-list">
          {aoFindings.map((finding) => (
            <AOFindingRow
              key={finding.ao_id}
              finding={finding}
              editable={mode === 'overriding'}
              selected={overrides[finding.ao_id]}
              fileLabel={fileLabel}
              onSelect={(designation) =>
                setOverrides((current) => {
                  const next = { ...current }
                  if (designation === finding.suggested_designation) delete next[finding.ao_id]
                  else next[finding.ao_id] = designation
                  return next
                })
              }
            />
          ))}
        </ul>
      )}

      {reviewed && (
        <div className="assessment-review-decided" data-testid="window-verdict-review-decided">
          {assessment.review_decision === 'overridden' ? 'Corrected' : 'Confirmed'}
          {assessment.verdict_reviewed_at && ` · ${new Date(assessment.verdict_reviewed_at).toLocaleString()}`}
          {assessment.review_reason && ` · ${assessment.review_reason}`}
        </div>
      )}

      {error && (
        <div className="assessment-review-error" role="alert">
          {error}
        </div>
      )}

      {!reviewed && canReview && !hasVersion && (
        <div className="assessment-review-readonly" data-testid="window-verdict-no-version">
          This verdict has no recorded version to confirm. Re-run the assessment to produce one.
        </div>
      )}

      {!reviewed && canReview && hasVersion && mode === 'idle' && (
        <div className="assessment-review-actions">
          <button
            type="button"
            className="assessment-review-confirm-btn"
            data-testid="window-verdict-confirm-btn"
            onClick={() => submit('confirmed')}
            disabled={submitting}
          >
            {submitting ? 'Recording...' : 'Confirm AI assessment'}
          </button>
          <button
            type="button"
            className="assessment-review-override-btn"
            data-testid="window-verdict-override-btn"
            onClick={() => setMode('overriding')}
            disabled={submitting || aoFindings.length === 0}
          >
            Correct designations
          </button>
        </div>
      )}

      {!reviewed && canReview && hasVersion && mode === 'overriding' && (
        <div className="assessment-review-override-form">
          <label className="assessment-review-reason-label" htmlFor={`window-override-reason-${assessment.id}`}>
            Why are you changing this? (required)
          </label>
          <textarea
            id={`window-override-reason-${assessment.id}`}
            className="assessment-review-reason"
            data-testid="window-verdict-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            placeholder="What the AI missed across the period, or where the files actually show it."
          />
          <div className="assessment-review-override-summary">
            {changedCount === 0
              ? 'Change at least one objective above, or confirm the assessment instead.'
              : `${changedCount} objective${changedCount === 1 ? '' : 's'} changed.`}
          </div>
          <div className="assessment-review-actions">
            <button
              type="button"
              className="assessment-review-confirm-btn"
              data-testid="window-verdict-save-btn"
              onClick={() => submit('overridden')}
              disabled={submitting || !canSubmitOverride}
            >
              {submitting ? 'Recording...' : 'Save correction'}
            </button>
            <button
              type="button"
              className="assessment-review-cancel-btn"
              onClick={() => {
                setMode('idle')
                setOverrides({})
                setReason('')
                setError(null)
              }}
              disabled={submitting}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {!reviewed && !canReview && (
        <div className="assessment-review-readonly">
          These suggestions are awaiting confirmation. Editor access is needed to
          confirm or correct them.
        </div>
      )}

      <button
        type="button"
        className="assessment-review-history-toggle"
        onClick={() => setHistoryOpen((open) => !open)}
        aria-expanded={historyOpen}
      >
        {historyOpen ? 'Hide' : 'Show'} assessment history
        {assessment.version_number ? ` (${assessment.version_number})` : ''}
      </button>

      {historyOpen && <AssessmentHistory versions={versions} error={historyError} />}
    </div>
  )
}

export default WindowVerdictReviewPanel
