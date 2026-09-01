import { useCallback, useEffect, useState } from 'react'
import {
  reviewAssessment,
  listAssessmentVersions,
  type AOFinding,
  type AOOverrideRequestItem,
  type AssessmentVersion,
  type EvidenceAssessmentResponse,
} from '../../data/apiClient'
import { useIsOrgEditor } from '../../hooks/useHasOrgRole'
import {
  AO_DESIGNATIONS,
  designationClass,
  designationLabel,
  verdictPresentation,
  TERMINAL_STATUSES,
} from './assessmentVerdict'

/**
 * The human end of the AI assessment (#881 WS3).
 *
 * Everything above this panel in the modal is what the machine said. This is
 * where a person answers it — objective by objective, with a stated reason
 * whenever they disagree.
 *
 * Two decisions and no third: **confirm** means the verdict stands as the
 * model produced it; **override** replaces specific per-objective designations
 * with the reviewer's, and the file's recorded status is then re-derived from
 * the result server-side. There is deliberately no "reject" — rejecting an
 * assessment would leave the file with no verdict at all, which is not a
 * position anybody can act on. Disagreement is expressed by saying what the
 * right answer is.
 *
 * This is a different verb from the file review in ``EvidenceFileList``. That
 * one accepts or rejects the *document*; this one confirms or corrects the
 * *machine's reading of it*, and the labels say so.
 */

interface AssessmentReviewPanelProps {
  orgId: string
  evidenceId: string
  fileId: string
  assessment: EvidenceAssessmentResponse
  /** Called with the updated assessment once a decision lands. */
  onReviewed: (updated: EvidenceAssessmentResponse) => void
}

export function AssessmentReviewPanel({
  orgId,
  evidenceId,
  fileId,
  assessment,
  onReviewed,
}: AssessmentReviewPanelProps) {
  // Editor, not admin. The backend accepts `require_org_role("editor")`, and a
  // stricter gate here would hide a control from people the platform has
  // already decided may use it — a UI that disagrees with the API about who
  // may act is a bug in the UI.
  const canReview = useIsOrgEditor(orgId)

  const [mode, setMode] = useState<'idle' | 'overriding'>('idle')
  const [reason, setReason] = useState('')
  const [overrides, setOverrides] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [versions, setVersions] = useState<AssessmentVersion[] | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyError, setHistoryError] = useState<string | null>(null)

  const aoFindings: AOFinding[] = assessment.ao_findings ?? []
  const reviewed = Boolean(assessment.review_decision)
  const isTerminal = TERMINAL_STATUSES.includes(assessment.status)
  const verdict = verdictPresentation(assessment.status, assessment.review_decision)

  const loadVersions = useCallback(async () => {
    try {
      setHistoryError(null)
      setVersions(await listAssessmentVersions(orgId, evidenceId, fileId))
    } catch (err) {
      // A history we could not fetch is not an empty history, and must not be
      // drawn as one.
      setHistoryError(err instanceof Error ? err.message : 'Could not load the assessment history.')
    }
  }, [orgId, evidenceId, fileId])

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
      const updated = await reviewAssessment(orgId, evidenceId, fileId, body)
      onReviewed(updated)
      setMode('idle')
      setReason('')
      setOverrides({})
      // The decision produced a new state of the record; anything cached about
      // its history is now stale.
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
    <div className="assessment-review" data-testid="assessment-review">
      <div className="assessment-review-header">
        <h5 className="assessment-review-title">Assessment objectives</h5>
        <span className={verdict.className} data-testid="assessment-review-verdict">
          {verdict.text}
        </span>
      </div>

      {/* Said in words as well as in the chip. The chip is a label; this is the
          sentence that tells a reader what they are looking at and what is
          being asked of them. */}
      <p className="assessment-review-framing">
        {reviewed
          ? assessment.review_decision === 'overridden'
            ? 'A reviewer corrected this assessment. The designations below are theirs where they disagreed with the AI.'
            : 'A reviewer confirmed this assessment. The designations below stand as the AI proposed them.'
          : 'These are the AI’s suggestions. They are advisory until someone confirms or corrects them.'}
      </p>

      {aoFindings.length === 0 ? (
        <div className="assessment-review-empty">
          The controls mapped to this evidence publish no assessment objectives, so
          there is nothing to review objective by objective.
        </div>
      ) : (
        <ul className="ao-finding-list">
          {aoFindings.map((finding) => (
            <AOFindingRow
              key={finding.ao_id}
              finding={finding}
              editable={mode === 'overriding'}
              selected={overrides[finding.ao_id]}
              onSelect={(designation) =>
                setOverrides((current) => {
                  const next = { ...current }
                  // Choosing the AI's own answer is not a disagreement, so it
                  // drops out of the payload rather than recording a no-op
                  // override in the audit trail.
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
        <div className="assessment-review-decided" data-testid="assessment-review-decided">
          {assessment.review_decision === 'overridden' ? 'Corrected' : 'Confirmed'}
          {assessment.reviewed_at && ` · ${new Date(assessment.reviewed_at).toLocaleString()}`}
        </div>
      )}

      {error && (
        <div className="assessment-review-error" role="alert">
          {error}
        </div>
      )}

      {!reviewed && canReview && mode === 'idle' && (
        <div className="assessment-review-actions">
          <button
            type="button"
            className="assessment-review-confirm-btn"
            onClick={() => submit('confirmed')}
            disabled={submitting}
          >
            {submitting ? 'Recording...' : 'Confirm AI assessment'}
          </button>
          <button
            type="button"
            className="assessment-review-override-btn"
            onClick={() => setMode('overriding')}
            disabled={submitting || aoFindings.length === 0}
          >
            Correct designations
          </button>
        </div>
      )}

      {!reviewed && canReview && mode === 'overriding' && (
        <div className="assessment-review-override-form">
          <label className="assessment-review-reason-label" htmlFor="assessment-override-reason">
            Why are you changing this? (required)
          </label>
          <textarea
            id="assessment-override-reason"
            className="assessment-review-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            placeholder="What the AI missed, or where the evidence actually shows it."
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

      {historyOpen && (
        <AssessmentHistory versions={versions} error={historyError} />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------

function AOFindingRow({
  finding,
  editable,
  selected,
  onSelect,
}: {
  finding: AOFinding
  editable: boolean
  selected?: string
  onSelect: (designation: string) => void
}) {
  // What the reviewer has picked in this session takes precedence, but the AI's
  // answer is still shown struck through beside it: a correction that hid what
  // it corrected would leave no way to see that a disagreement happened.
  const effective = selected ?? finding.suggested_designation
  const humanDesignation = selected ?? (finding.overridden_by_human ? finding.suggested_designation : undefined)
  const disagreement = Boolean(selected) || Boolean(finding.overridden_by_human)

  return (
    <li className="ao-finding" data-testid={`ao-finding-${finding.ao_id}`}>
      <div className="ao-finding-head">
        <span className="ao-finding-id">{finding.ao_id}</span>
        {disagreement && humanDesignation ? (
          <span className="ao-finding-disagreement">
            <s className="ao-designation ao-designation-superseded">
              {designationLabel(finding.suggested_designation)}
            </s>
            <span aria-hidden="true"> → </span>
            <span className={designationClass(humanDesignation)}>
              {designationLabel(humanDesignation)}
            </span>
            <span className="ao-finding-disagreement-note">(reviewer)</span>
          </span>
        ) : (
          <span className={designationClass(effective)}>
            {designationLabel(effective)}
          </span>
        )}
      </div>

      {finding.rationale && <p className="ao-finding-rationale">{finding.rationale}</p>}
      {finding.suggestion && (
        <p className="ao-finding-suggestion">Suggestion: {finding.suggestion}</p>
      )}
      {finding.override_note && (
        <p className="ao-finding-override-note">Reviewer note: {finding.override_note}</p>
      )}

      {editable && (
        <div className="ao-finding-picker" role="group" aria-label={`Designation for ${finding.ao_id}`}>
          {AO_DESIGNATIONS.map((designation) => (
            <button
              key={designation}
              type="button"
              className={
                'ao-designation-option' +
                (effective === designation ? ' ao-designation-option-selected' : '')
              }
              aria-pressed={effective === designation}
              onClick={() => onSelect(designation)}
            >
              {designationLabel(designation)}
            </button>
          ))}
        </div>
      )}
    </li>
  )
}

function AssessmentHistory({
  versions,
  error,
}: {
  versions: AssessmentVersion[] | null
  error: string | null
}) {
  if (error) {
    return (
      <div className="assessment-history-error" role="alert">
        {error}
      </div>
    )
  }
  if (versions === null) {
    return <div className="assessment-history-loading">Loading history...</div>
  }
  if (versions.length === 0) {
    return <div className="assessment-history-empty">No earlier verdicts recorded.</div>
  }

  return (
    <ol className="assessment-history" data-testid="assessment-history">
      {versions.map((version) => {
        const presentation = verdictPresentation(version.status, version.review_decision)
        return (
          <li key={version.id} className="assessment-history-entry">
            <div className="assessment-history-head">
              <span className="assessment-history-version">v{version.version_number}</span>
              <span className={presentation.className}>{presentation.text}</span>
              {version.assessed_at && (
                <span className="assessment-history-date">
                  {new Date(version.assessed_at).toLocaleDateString()}
                </span>
              )}
            </div>
            {version.model_id && (
              <div className="assessment-history-provenance">
                {version.model_id}
                {version.prompt_version ? ` · prompt ${version.prompt_version}` : ''}
                {version.schema_version === 1 ? ' · pre-objective verdict' : ''}
              </div>
            )}
            {version.review_reason && (
              <div className="assessment-history-reason">Reason: {version.review_reason}</div>
            )}
            {version.ao_overrides && version.ao_overrides.length > 0 && (
              <ul className="assessment-history-overrides">
                {version.ao_overrides.map((override) => (
                  <li key={override.ao_id} className="assessment-history-override">
                    <span className="ao-finding-id">{override.ao_id}</span>{' '}
                    {override.ai_designation && (
                      <s className="ao-designation ao-designation-superseded">
                        {designationLabel(override.ai_designation)}
                      </s>
                    )}
                    <span aria-hidden="true"> → </span>
                    <span className={designationClass(override.human_designation)}>
                      {designationLabel(override.human_designation)}
                    </span>
                    {override.note && (
                      <span className="assessment-history-override-note"> {override.note}</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </li>
        )
      })}
    </ol>
  )
}
