/**
 * ShadowVerdictPanel — Jev's verdict on this file, beside Claude's.
 *
 * Only rendered when a shadow verdict exists for the assessment version on
 * screen. In `jev_shadow` mode it shows how far the two engines agree,
 * objective by objective, with Claude's designation next to Jev's wherever
 * they differ. (In `jev` mode Jev's verdict is the assessment itself and no
 * shadow row is written, so nothing renders here.)
 *
 * The shadow row lands seconds after the Claude verdict it belongs to — Jev
 * is called after the primary commit, with its own retries. A row from an
 * earlier version beside a new Claude verdict would read as if it compared
 * that verdict, so a row whose `version_id` is not the one on screen is held
 * back and re-read on a short timer until the right one lands or the wait
 * runs out.
 *
 * A failed shadow run is written as a row by the backend so the miss is
 * visible; this panel shows it as a failure rather than hiding it. A failure
 * to *read* the shadow, on the other hand, renders nothing: the panel is a
 * secondary view, and the Claude verdict above it must not be crowded out by
 * an error about the one beside it.
 */
import { useEffect, useState } from 'react'

import { getShadowVerdict } from '../../data/assessmentEngineApi'
import type { ShadowVerdict } from '../../data/assessmentEngineApi'
import { designationClass, designationLabel, verdictPresentation } from './assessmentVerdict'

interface ShadowVerdictPanelProps {
  orgId: string
  evidenceId: string
  fileId: string
  /** The Claude assessment version on screen; a new one re-reads the shadow. */
  assessmentVersion?: number | null
  /**
   * The id of that version. When given, a shadow row for a different version
   * is treated as not-yet-arrived rather than shown against this verdict.
   */
  assessmentVersionId?: string | null
}

/** How often to re-read while the row for this version is still on its way. */
export const SHADOW_POLL_MS = 5000
/** How many re-reads before giving up: three minutes covers a Jev call with its 429 backoff. */
export const SHADOW_POLL_LIMIT = 36

function percent(rate: number | null): string {
  return rate === null ? '—' : `${Math.round(rate * 100)}%`
}

export function ShadowVerdictPanel({
  orgId,
  evidenceId,
  fileId,
  assessmentVersion,
  assessmentVersionId,
}: ShadowVerdictPanelProps) {
  const [shadow, setShadow] = useState<ShadowVerdict | null>(null)

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let attempts = 0

    const load = () => {
      getShadowVerdict(orgId, evidenceId, fileId)
        .then(result => {
          if (cancelled) return
          const stale =
            result !== null && assessmentVersionId != null && result.version_id !== assessmentVersionId
          setShadow(stale ? null : result)
          // A stale row proves a shadow pipeline is active for this file, so
          // the one for this version is worth waiting for. No row at all is
          // not polled: most files, on most engines, never have a shadow.
          if (stale && attempts < SHADOW_POLL_LIMIT) {
            attempts += 1
            timer = setTimeout(load, SHADOW_POLL_MS)
          }
        })
        .catch(err => {
          console.error('Failed to load the Jev shadow verdict:', err)
          if (!cancelled) setShadow(null)
        })
    }

    load()
    return () => {
      cancelled = true
      if (timer !== undefined) clearTimeout(timer)
    }
  }, [orgId, evidenceId, fileId, assessmentVersion, assessmentVersionId])

  if (!shadow) return null

  const comparison = shadow.comparison
  const claudeSays = new Map((comparison?.disagreements ?? []).map(d => [d.ao_id, d.llm]))
  // Jev's verdict is never a human's, so it always reads as a suggestion.
  const chip = verdictPresentation(shadow.error ? 'error' : shadow.status, null)
  const cutoff = shadow.confidence_cutoff

  let summary: string
  if (shadow.error) {
    summary = `Shadow assessment failed: ${shadow.error}`
  } else if (comparison) {
    summary =
      `Agrees with Claude on ${comparison.agreed} of ${comparison.compared} objectives ` +
      `(${percent(comparison.agreement_rate)}; confident subset ` +
      `${comparison.confident_agreed}/${comparison.confident_total})`
  } else {
    summary = 'No comparison was recorded for this shadow run'
  }

  return (
    <div className="shadow-verdict" data-testid="shadow-verdict-panel">
      <div className="shadow-verdict-header">
        <h5 className="shadow-verdict-title">Jev shadow verdict</h5>
        <span className={chip.className}>{chip.text}</span>
      </div>
      <p className={`shadow-verdict-summary${shadow.error ? ' failed' : ''}`}>{summary}</p>

      {shadow.ao_findings.length > 0 && (
        <ul className="shadow-verdict-list">
          {shadow.ao_findings.map(finding => {
            const claude = claudeSays.get(finding.ao_id)
            // An unreported confidence is not a confident one.
            const low =
              finding.confidence === null || (cutoff !== null && finding.confidence < cutoff)
            return (
              <li key={finding.ao_id} className="shadow-verdict-row" data-testid="shadow-verdict-row">
                <span className="shadow-verdict-ao">{finding.ao_id}</span>
                <span className={designationClass(finding.suggested_designation)}>
                  {designationLabel(finding.suggested_designation)}
                </span>
                <span
                  className={`shadow-verdict-confidence${low ? ' low' : ''}`}
                  title={
                    low
                      ? finding.confidence === null
                        ? 'No confidence figure was reported'
                        : `Below the ${percent(cutoff)} confidence cutoff`
                      : undefined
                  }
                >
                  {percent(finding.confidence)}
                </span>
                {claude && (
                  <span className="shadow-verdict-claude">Claude: {designationLabel(claude)}</span>
                )}
              </li>
            )
          })}
        </ul>
      )}

      <div className="ai-assessment-provenance">
        {shadow.model_id !== null && (
          <span className="ai-assessment-provenance-item">
            <span className="ai-assessment-provenance-label">Model</span>
            <span className="ai-assessment-provenance-value">{shadow.model_id}</span>
          </span>
        )}
        {shadow.processing_time_ms !== null && (
          <span className="ai-assessment-provenance-item">
            <span className="ai-assessment-provenance-label">Latency</span>
            <span className="ai-assessment-provenance-value">{shadow.processing_time_ms} ms</span>
          </span>
        )}
      </div>
    </div>
  )
}
