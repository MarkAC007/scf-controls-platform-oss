/**
 * WindowReviewPanel — M4 (#574) per-window review UI.
 *
 * Sibling component to ``EvidenceFileList``. Loads the latest
 * ``EvidenceWindowAssessment`` for an evidence_id and exposes the per-window
 * review workflow: Approve / Reject / Request Revision / Reset, plus a notes
 * textarea capped at 2000 chars (matches backend Pydantic constraint).
 *
 * Renders only when the build-time flag ``VITE_ENABLE_PER_WINDOW_REVIEW=true``
 * is set on the bundle. The parent (``EvidenceReview``) gates the mount; this
 * component does not check the flag itself, so it is also the cleanest unit
 * to test in isolation.
 */

import { useState, useEffect, useCallback } from 'react'
import {
  listWindowAssessments,
  reviewWindowAssessment,
} from '../../data/apiClient'
import type { EvidenceWindowAssessment } from '../../types'

interface WindowReviewPanelProps {
  orgId: string
  evidenceId: string
  /**
   * Bumped by the parent to force a re-fetch (e.g. after an upload triggers a
   * fresh window assessment). Mirrors the ``refreshTrigger`` pattern used by
   * ``EvidenceFileList``.
   */
  refreshTrigger?: number
}

const REVIEW_STATUS_LABELS: Record<string, { label: string; className: string }> = {
  not_reviewed: { label: 'Not reviewed', className: 'review-badge-not-reviewed' },
  approved: { label: 'Approved', className: 'review-badge-approved' },
  rejected: { label: 'Rejected', className: 'review-badge-rejected' },
  needs_revision: { label: 'Needs revision', className: 'review-badge-needs-revision' },
}

const ASSESSMENT_STATUS_LABELS: Record<string, string> = {
  sufficient: 'Sufficient',
  partial: 'Partial',
  insufficient: 'Insufficient',
  insufficient_sample: 'Insufficient sample',
  error: 'Error',
  processing: 'Processing',
  pending: 'Pending',
}

const FINDING_SEVERITY_CLASSES: Record<string, string> = {
  high: 'window-review-finding-high',
  critical: 'window-review-finding-high',
  medium: 'window-review-finding-medium',
  low: 'window-review-finding-low',
  info: 'window-review-finding-low',
}

const NOTES_MAX_LENGTH = 2000

export function WindowReviewPanel({
  orgId,
  evidenceId,
  refreshTrigger,
}: WindowReviewPanelProps) {
  const [ewa, setEwa] = useState<EvidenceWindowAssessment | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notes, setNotes] = useState('')
  const [submitting, setSubmitting] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const fetchLatest = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const rows = await listWindowAssessments(orgId, evidenceId, { limit: 1 })
      const head = rows[0] ?? null
      setEwa(head)
      setNotes(head?.review_notes ?? '')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load window assessment')
      setEwa(null)
    } finally {
      setLoading(false)
    }
  }, [orgId, evidenceId])

  useEffect(() => {
    fetchLatest()
  }, [fetchLatest, refreshTrigger])

  const handleReview = useCallback(
    async (status: string) => {
      if (!ewa) return
      setSubmitting(status)
      setSubmitError(null)
      try {
        const updated = await reviewWindowAssessment(orgId, ewa.id, {
          review_status: status,
          review_notes: notes.trim() ? notes.trim() : undefined,
        })
        setEwa(updated)
        setNotes(updated.review_notes ?? '')
      } catch (e) {
        setSubmitError(e instanceof Error ? e.message : 'Review submission failed')
      } finally {
        setSubmitting(null)
      }
    },
    [ewa, orgId, notes],
  )

  // ---- Render branches ----

  if (loading) {
    return (
      <div
        className="detail-section-container surface-bench window-review-panel window-review-panel--loading"
        data-testid="window-review-panel-loading"
        aria-busy="true"
      >
        <div className="container-header bench-header">
          <span className="container-icon">🔍</span>
          <span className="container-title">Your Window Review</span>
        </div>
        <div className="container-content">
          <div className="window-review-skeleton" aria-hidden="true">
            Loading review state…
          </div>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div
        className="detail-section-container surface-bench window-review-panel window-review-panel--error"
        data-testid="window-review-panel-error"
        role="alert"
      >
        <div className="container-header bench-header">
          <span className="container-icon">🔍</span>
          <span className="container-title">Your Window Review</span>
        </div>
        <div className="container-content">
          <p className="window-review-error">Could not load window assessment.</p>
          <p className="window-review-error-detail">{error}</p>
          <button
            type="button"
            className="window-review-retry-btn"
            onClick={fetchLatest}
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  if (!ewa) {
    return (
      <div
        className="detail-section-container surface-bench window-review-panel window-review-panel--empty"
        data-testid="window-review-panel-empty"
      >
        <div className="container-header bench-header">
          <span className="container-icon">🔍</span>
          <span className="container-title">Your Window Review</span>
        </div>
        <div className="container-content">
          <p className="window-review-empty">
            No window assessment exists for this evidence yet. Trigger an
            assessment from the evidence-files panel to enable review.
          </p>
        </div>
      </div>
    )
  }

  const status = ewa.review_status ?? 'not_reviewed'
  const badge = REVIEW_STATUS_LABELS[status] ?? REVIEW_STATUS_LABELS.not_reviewed

  const assessmentStatus = ewa.status ?? null
  const assessmentLabel = assessmentStatus
    ? ASSESSMENT_STATUS_LABELS[assessmentStatus] ?? assessmentStatus
    : null
  const findings = ewa.findings ?? []
  const coverage = ewa.artifact_type_coverage ?? {}
  const expectedTypes = ewa.expected_artifact_types ?? []
  const mandatoryByType: Record<string, boolean> = {}
  for (const t of expectedTypes) {
    if (typeof t.type === 'string') {
      mandatoryByType[t.type] = Boolean(t.mandatory)
    }
  }
  const coverageEntries = Object.entries(coverage)
  const fileCount = ewa.file_ids?.length ?? 0
  const frequency = ewa.frequency_used ?? null

  return (
    <div
      className="detail-section-container surface-bench window-review-panel"
      data-testid="window-review-panel"
    >
      <div className="container-header bench-header">
        <span className="container-icon">🔍</span>
        <span className="container-title">Your Window Review</span>
        <span
          className={`review-badge ${badge.className}`}
          data-testid="window-review-status-badge"
        >
          {badge.label}
        </span>
      </div>
      <div className="container-content">
        <p className="window-review-meta">
          Window: {new Date(ewa.window_start).toLocaleDateString()} →{' '}
          {new Date(ewa.window_end).toLocaleDateString()}
          {ewa.relevance_score !== null && (
            <> &nbsp;·&nbsp; Relevance: {ewa.relevance_score.toFixed(1)}</>
          )}
        </p>
        {ewa.reviewed_at && (
          <p className="window-review-meta-muted">
            Last reviewed: {new Date(ewa.reviewed_at).toLocaleString()}
          </p>
        )}

        {(assessmentLabel || ewa.summary || findings.length > 0 || coverageEntries.length > 0) && (
          <div
            className="window-review-context"
            data-testid="window-review-context"
          >
            {assessmentLabel && (
              <div className="window-review-context-header">
                <span className="window-review-context-label">AI Assessment</span>
                <span
                  className={`assessment-status-badge assessment-status-badge-${assessmentStatus}`}
                  data-testid="window-review-status-assessment-badge"
                >
                  {assessmentLabel}
                </span>
                {frequency && (
                  <span className="window-review-files-caption">
                    {fileCount} {fileCount === 1 ? 'file' : 'files'} · frequency: {frequency}
                  </span>
                )}
              </div>
            )}

            {ewa.summary && (
              <p
                className="window-review-summary"
                data-testid="window-review-summary"
              >
                {ewa.summary}
              </p>
            )}

            {findings.length > 0 ? (
              <ul
                className="window-review-findings"
                data-testid="window-review-findings"
              >
                {findings.map((finding, idx) => {
                  const severity = typeof finding.severity === 'string'
                    ? finding.severity.toLowerCase()
                    : ''
                  const severityClass = FINDING_SEVERITY_CLASSES[severity] ?? ''
                  return (
                    <li
                      key={idx}
                      className={`window-review-finding ${severityClass}`}
                    >
                      <div>
                        {finding.severity && (
                          <span className="window-review-finding-severity">
                            {String(finding.severity)}
                          </span>
                        )}
                        {finding.control_id && (
                          <span className="window-review-finding-control">
                            {String(finding.control_id)}
                          </span>
                        )}
                        {finding.gap && <span>{String(finding.gap)}</span>}
                      </div>
                      {finding.suggestion && (
                        <div className="window-review-finding-suggestion">
                          {String(finding.suggestion)}
                        </div>
                      )}
                    </li>
                  )
                })}
              </ul>
            ) : (
              assessmentLabel && (
                <p
                  className="window-review-no-gaps"
                  data-testid="window-review-no-gaps"
                >
                  No gaps identified.
                </p>
              )
            )}

            {coverageEntries.length > 0 && (
              <ul
                className="window-review-coverage"
                data-testid="window-review-coverage"
              >
                {coverageEntries.map(([typeName, entry]) => {
                  const present = Boolean(entry?.present)
                  const mandatory = Boolean(mandatoryByType[typeName])
                  const itemClass = present
                    ? 'window-review-coverage-present'
                    : mandatory
                      ? 'window-review-coverage-missing-mandatory'
                      : 'window-review-coverage-missing'
                  return (
                    <li
                      key={typeName}
                      className={`window-review-coverage-item ${itemClass}`}
                    >
                      <span className="window-review-coverage-indicator">
                        {present ? '✓' : '✗'}
                      </span>
                      <span>{typeName}</span>
                      {!present && mandatory && (
                        <span className="window-review-coverage-mandatory-label">
                          Mandatory
                        </span>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        )}

        <div className="form-group">
          <label htmlFor={`window-review-notes-${ewa.id}`}>Review notes</label>
          <textarea
            id={`window-review-notes-${ewa.id}`}
            data-testid="window-review-notes-textarea"
            className="form-control window-review-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value.slice(0, NOTES_MAX_LENGTH))}
            placeholder="Optional reviewer notes (max 2000 chars)"
            maxLength={NOTES_MAX_LENGTH}
            rows={3}
            disabled={submitting !== null}
          />
          <div className="window-review-notes-counter">
            {notes.length} / {NOTES_MAX_LENGTH}
          </div>
        </div>

        {submitError && (
          <p
            className="window-review-error"
            role="alert"
            data-testid="window-review-submit-error"
          >
            {submitError}
          </p>
        )}

        <div className="window-review-actions">
          <button
            type="button"
            className="window-review-approve-btn"
            data-testid="window-review-approve-btn"
            onClick={() => handleReview('approved')}
            disabled={submitting !== null}
          >
            {submitting === 'approved' ? 'Approving…' : 'Approve'}
          </button>
          <button
            type="button"
            className="window-review-reject-btn"
            data-testid="window-review-reject-btn"
            onClick={() => handleReview('rejected')}
            disabled={submitting !== null}
          >
            {submitting === 'rejected' ? 'Rejecting…' : 'Reject'}
          </button>
          <button
            type="button"
            className="window-review-revision-btn"
            data-testid="window-review-revision-btn"
            onClick={() => handleReview('needs_revision')}
            disabled={submitting !== null}
          >
            {submitting === 'needs_revision' ? 'Submitting…' : 'Request revision'}
          </button>
          {status !== 'not_reviewed' && (
            <button
              type="button"
              className="window-review-reset-btn"
              data-testid="window-review-reset-btn"
              onClick={() => handleReview('not_reviewed')}
              disabled={submitting !== null}
            >
              {submitting === 'not_reviewed' ? 'Resetting…' : 'Reset'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

export default WindowReviewPanel
