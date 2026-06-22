/**
 * FrequencyHealthTile — M4 (#574) dashboard tile.
 *
 * Loads the frequency-health report for the active organization and renders:
 *   - a count of misaligned evidence items (declared vs observed cadence)
 *   - a low-confidence count for awareness
 *   - a drill-down per-evidence list with a one-click "Apply fix" button
 *     that updates the tracking row's ``frequency`` to match the suggested
 *     cadence detected by ``frequency_health_service``.
 *
 * Renders only when the build-time flag ``VITE_ENABLE_PER_WINDOW_REVIEW=true``
 * is set on the bundle. The parent (``Dashboard``) gates the mount.
 */

import { useState, useEffect, useCallback } from 'react'
import {
  getFrequencyHealth,
  createOrUpdateEvidenceTracking,
} from '../../data/apiClient'
import type {
  FrequencyHealthResponse,
  FrequencyHealthItem,
} from '../../types'

interface FrequencyHealthTileProps {
  orgId: string
}

const CONFIDENCE_LABELS: Record<string, string> = {
  high: 'High',
  medium: 'Medium',
  low: 'Low',
}

export function FrequencyHealthTile({ orgId }: FrequencyHealthTileProps) {
  const [report, setReport] = useState<FrequencyHealthResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [applyingId, setApplyingId] = useState<string | null>(null)
  const [applyError, setApplyError] = useState<string | null>(null)

  const fetchReport = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await getFrequencyHealth(orgId)
      setReport(r)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load frequency health')
      setReport(null)
    } finally {
      setLoading(false)
    }
  }, [orgId])

  useEffect(() => {
    fetchReport()
  }, [fetchReport])

  const applyFix = useCallback(
    async (item: FrequencyHealthItem) => {
      if (!item.suggested_frequency) return
      setApplyingId(item.evidence_id)
      setApplyError(null)
      try {
        await createOrUpdateEvidenceTracking(
          {
            evidence_id: item.evidence_id,
            frequency: item.suggested_frequency,
          },
          orgId,
        )
        // Refresh the report so the count decrements and the row drops.
        await fetchReport()
      } catch (e) {
        setApplyError(
          e instanceof Error
            ? `${item.evidence_id}: ${e.message}`
            : `${item.evidence_id}: failed to apply suggested frequency`,
        )
      } finally {
        setApplyingId(null)
      }
    },
    [orgId, fetchReport],
  )

  // ---- Render branches ----

  if (loading) {
    return (
      <div
        className="frequency-health-tile frequency-health-tile--loading"
        data-testid="frequency-health-tile-loading"
        aria-busy="true"
      >
        <div className="container-header">
          <span className="container-icon">📈</span>
          <span className="container-title">Frequency Health</span>
        </div>
        <div className="container-content">
          <div className="frequency-health-skeleton">Loading…</div>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div
        className="frequency-health-tile frequency-health-tile--error"
        data-testid="frequency-health-tile-error"
        role="alert"
      >
        <div className="container-header">
          <span className="container-icon">📈</span>
          <span className="container-title">Frequency Health</span>
        </div>
        <div className="container-content">
          <p className="frequency-health-error">Could not load frequency health.</p>
          <p className="frequency-health-error-detail">{error}</p>
          <button
            type="button"
            className="frequency-health-retry-btn"
            onClick={fetchReport}
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  if (!report) {
    return null
  }

  const { misaligned_count, low_confidence_count, items } = report

  if (misaligned_count === 0) {
    return (
      <div
        className="frequency-health-tile frequency-health-tile--empty"
        data-testid="frequency-health-tile-empty"
      >
        <div className="container-header">
          <span className="container-icon">📈</span>
          <span className="container-title">Frequency Health</span>
        </div>
        <div className="container-content">
          <p className="frequency-health-empty">
            No misaligned cadences detected. Tracking frequencies match observed
            upload cadences across {report.total_evidence_ids_evaluated} evidence
            items.
          </p>
          {low_confidence_count > 0 && (
            <p className="frequency-health-low-confidence">
              {low_confidence_count} evidence item{low_confidence_count === 1 ? '' : 's'}{' '}
              had insufficient data to confidently classify cadence.
            </p>
          )}
        </div>
      </div>
    )
  }

  return (
    <div
      className="frequency-health-tile"
      data-testid="frequency-health-tile"
    >
      <div className="container-header">
        <span className="container-icon">📈</span>
        <span className="container-title">Frequency Health</span>
        <span
          className="frequency-health-count"
          data-testid="frequency-health-count"
        >
          {misaligned_count} misaligned
        </span>
      </div>
      <div className="container-content">
        <p className="frequency-health-summary">
          {misaligned_count} of {report.total_evidence_ids_evaluated} tracked
          evidence item{report.total_evidence_ids_evaluated === 1 ? '' : 's'} have
          a declared frequency that does not match observed upload cadence.
          {low_confidence_count > 0 && (
            <> &nbsp;·&nbsp; {low_confidence_count} low-confidence.</>
          )}
        </p>

        <button
          type="button"
          className="frequency-health-toggle"
          data-testid="frequency-health-toggle"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
        >
          {expanded ? 'Hide details' : 'Show details'}
        </button>

        {applyError && (
          <p
            className="frequency-health-error"
            role="alert"
            data-testid="frequency-health-apply-error"
          >
            {applyError}
          </p>
        )}

        {expanded && (
          <ul
            className="frequency-health-list"
            data-testid="frequency-health-list"
          >
            {items.map((item) => (
              <li
                key={item.evidence_id}
                className="frequency-health-row"
                data-testid={`frequency-health-row-${item.evidence_id}`}
              >
                <div className="frequency-health-row-header">
                  <span className="frequency-health-evidence-id">
                    {item.evidence_id}
                  </span>
                  <span className="frequency-health-confidence">
                    {CONFIDENCE_LABELS[item.confidence] ?? item.confidence}
                  </span>
                </div>
                <div className="frequency-health-row-detail">
                  Declared: <strong>{item.declared_frequency ?? '—'}</strong>{' '}
                  &nbsp;→&nbsp; Suggested:{' '}
                  <strong>{item.suggested_frequency ?? '—'}</strong>
                  {item.observed_cadence_days !== null && (
                    <>
                      {' '}
                      (observed every ~{item.observed_cadence_days.toFixed(1)} days,{' '}
                      {item.file_count} files)
                    </>
                  )}
                </div>
                <p className="frequency-health-reason">{item.reason}</p>
                <div className="frequency-health-row-actions">
                  <button
                    type="button"
                    className="frequency-health-apply-btn"
                    data-testid={`frequency-health-apply-${item.evidence_id}`}
                    onClick={() => applyFix(item)}
                    disabled={
                      !item.suggested_frequency ||
                      applyingId === item.evidence_id
                    }
                  >
                    {applyingId === item.evidence_id
                      ? 'Applying…'
                      : 'Apply fix'}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

export default FrequencyHealthTile
