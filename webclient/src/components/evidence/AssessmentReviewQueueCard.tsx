import { useCallback, useEffect, useState } from 'react'
import {
  getAssessmentReviewQueue,
  type AssessmentReviewQueueItem,
} from '../../data/apiClient'
import { useIsOrgEditor } from '../../hooks/useHasOrgRole'
import { verdictPresentation } from './assessmentVerdict'

/**
 * The confirmation queue on the evidence dashboard (#881 WS3).
 *
 * Its job is to make unreviewed AI output *findable*. Before this, a
 * suggestion could only be discovered by opening the file that happened to
 * carry it, which meant the practical answer to "what has the AI said that
 * nobody has checked?" was "nobody knows".
 *
 * Ordered worst-first by the backend — most gaps, then most objectives the
 * assessor could not read, then least relevant, then oldest — so the top of
 * the list is where a reviewer's attention is worth most. The order is the
 * server's to decide and this component does not re-sort it; a queue whose
 * client quietly disagreed with its server about priority would be worse than
 * no queue.
 *
 * Viewer sees the list. Only an editor sees the prompt to act on it, matching
 * what the backend will actually accept.
 */

interface AssessmentReviewQueueCardProps {
  orgId: string
  /** Open an evidence item (and, where the caller supports it, a file). */
  onOpenEvidence?: (evidenceId: string, fileId: string) => void
  /** Bumped by the parent to force a refetch after something changes. */
  refreshTrigger?: number
}

const PAGE_SIZE = 10

export function AssessmentReviewQueueCard({
  orgId,
  onOpenEvidence,
  refreshTrigger = 0,
}: AssessmentReviewQueueCardProps) {
  const canReview = useIsOrgEditor(orgId)
  const [items, setItems] = useState<AssessmentReviewQueueItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await getAssessmentReviewQueue(orgId, {
        status: 'awaiting',
        limit: PAGE_SIZE,
      })
      setItems(result.items)
      setTotal(result.total)
    } catch (err) {
      // An empty queue and an unreachable queue look identical once drawn, so
      // they must not be drawn the same way.
      setError(err instanceof Error ? err.message : 'Could not load the review queue.')
    } finally {
      setLoading(false)
    }
  }, [orgId])

  useEffect(() => {
    void load()
  }, [load, refreshTrigger])

  return (
    <div className="ai-assessment-card" data-testid="assessment-review-queue">
      <div className="ai-assessment-card-header">
        <h3 className="ai-assessment-card-title">Awaiting confirmation</h3>
        <span className="ai-advisory-label">AI Advisory</span>
      </div>

      <p className="assessment-queue-framing">
        AI verdicts nobody has confirmed yet, most urgent first. Until one is
        confirmed it counts as a suggestion, and carries reduced weight in the
        evidence quality score.
      </p>

      {error ? (
        <div className="assessment-queue-error" role="alert">
          {error}
        </div>
      ) : loading ? (
        <div className="ai-assessment-empty">Loading queue...</div>
      ) : items.length === 0 ? (
        <div className="ai-assessment-empty">
          Every AI verdict has been reviewed.
        </div>
      ) : (
        <>
          <div className="assessment-queue-count">
            {total} awaiting{total > items.length ? ` · showing ${items.length}` : ''}
          </div>
          <ul className="assessment-queue-list">
            {items.map((item) => {
              const verdict = verdictPresentation(item.status, item.review_decision)
              const row = (
                <>
                  <span className="assessment-queue-filename">
                    {item.filename || item.evidence_id}
                  </span>
                  <span className={verdict.className}>{verdict.text}</span>
                  <span className="assessment-queue-counts">
                    {item.gap_count > 0 && (
                      <span className="assessment-queue-gap">
                        {item.gap_count} gap{item.gap_count === 1 ? '' : 's'}
                      </span>
                    )}
                    {item.cannot_assess_count > 0 && (
                      <span className="assessment-queue-cannot">
                        {item.cannot_assess_count} unreadable
                      </span>
                    )}
                  </span>
                </>
              )
              return (
                <li key={item.file_id} className="assessment-queue-item">
                  {onOpenEvidence ? (
                    <button
                      type="button"
                      className="assessment-queue-link"
                      onClick={() => onOpenEvidence(item.evidence_id, item.file_id)}
                    >
                      {row}
                    </button>
                  ) : (
                    <span className="assessment-queue-link assessment-queue-link-static">
                      {row}
                    </span>
                  )}
                </li>
              )
            })}
          </ul>
          {!canReview && (
            <div className="assessment-queue-readonly">
              Editor access is needed to confirm or correct these.
            </div>
          )}
        </>
      )}
    </div>
  )
}
