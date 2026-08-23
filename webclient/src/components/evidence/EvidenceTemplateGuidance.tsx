import { useState, useCallback } from 'react'
import type { EvidenceId, EvidenceTemplatesFile, ERLFile, EvidenceTracking } from '../../types'
import { submitRecipeFeedback } from '../../data/apiClient'
import { resolveEvidenceGuidance, GUIDANCE_TIER_BADGE } from '../../data/evidenceGuidance'
import { interactiveRowProps } from '../../data/interactiveRow'

interface EvidenceTemplateGuidanceProps {
  evidenceId: EvidenceId
  evidenceTemplates: EvidenceTemplatesFile
  orgId?: string
  /**
   * The SCF evidence catalogue. Optional so existing callers keep working, but
   * without it every item with no hand-written template falls to the generic
   * text — which is the defect, not a degraded mode worth designing for.
   */
  erlData?: ERLFile
  /** The organisation's own tracking row, for cadence and collecting system. */
  tracking?: EvidenceTracking
}

/**
 * Guidance for one evidence item, at the best tier available (#789).
 *
 * The tier decision and every sentence live in `data/evidenceGuidance.ts`; this
 * component renders them and reports which tier it got. See that module's header
 * for why "Generic" was showing on roughly nine items in ten.
 */
export function EvidenceTemplateGuidance({
  evidenceId,
  evidenceTemplates,
  orgId,
  erlData,
  tracking,
}: EvidenceTemplateGuidanceProps) {
  const [collapsed, setCollapsed] = useState(true)
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false)

  const { tier, guidance } = resolveEvidenceGuidance(evidenceId, {
    templates: evidenceTemplates,
    erl: erlData,
    tracking,
  })
  const badge = GUIDANCE_TIER_BADGE[tier]

  const handleFeedback = useCallback(async (feedbackType: 'helpful' | 'not_matching') => {
    if (!orgId) return
    try {
      await submitRecipeFeedback(evidenceId, {
        system_type: 'evidence_template',
        vendor: undefined,
        feedback_type: feedbackType,
        maturity_level: 'L1',
      }, orgId)
      setFeedbackSubmitted(true)
    } catch (error) {
      console.error('Failed to submit template feedback:', error)
    }
  }, [orgId, evidenceId])

  return (
    <div className={`detail-section-container evidence-template-guidance ${collapsed ? 'collapsed' : ''}`}>
      {/*
        The header declared a button role and gave neither a tab stop nor a key
        handler, so it told a screen reader it was a control and then could not
        be reached or operated by one. The shared helper supplies all four props
        together, which is the point of it.

        (The role is not spelled out above on purpose: `interactiveRow.usage.test`
        asserts on this file's source and a literal in a comment would defeat it.)
      */}
      <div
        className="container-header"
        style={{ cursor: 'pointer' }}
        aria-expanded={!collapsed}
        {...interactiveRowProps(() => setCollapsed(!collapsed))}
      >
        <span className="container-icon">{'📋'}</span>
        <span className="container-title">Evidence Guidance</span>
        {badge && (
          <span className={`template-generic-badge template-tier-${tier}`}>{badge}</span>
        )}
        <span className="container-collapse-icon" style={{ marginLeft: 'auto' }}>
          {collapsed ? '▶' : '▼'}
        </span>
      </div>

      {!collapsed && (
        <div className="container-content">
          {/* Summary */}
          <p className="template-summary">{guidance.summary}</p>

          {/* Accepted Formats */}
          <div className="template-section">
            <div className="template-section-label">Accepted Formats</div>
            <div className="template-format-chips">
              {guidance.acceptable_formats.map(fmt => (
                <span key={fmt} className="template-format-chip">{fmt}</span>
              ))}
            </div>
          </div>

          {/* Good Examples */}
          <div className="template-section">
            <div className="template-section-label template-good-label">Good Evidence</div>
            <ul className="template-examples template-good-examples">
              {guidance.good_examples.map((ex, i) => (
                <li key={i}>{ex}</li>
              ))}
            </ul>
          </div>

          {/* Bad Examples */}
          <div className="template-section">
            <div className="template-section-label template-bad-label">Common Mistakes</div>
            <ul className="template-examples template-bad-examples">
              {guidance.bad_examples.map((ex, i) => (
                <li key={i}>{ex}</li>
              ))}
            </ul>
          </div>

          {/* Redaction Warnings */}
          {guidance.redaction_warnings.length > 0 && (
            <div className="template-section template-redaction">
              <div className="template-section-label template-redaction-label">Redact Before Uploading</div>
              <ul className="template-redaction-list">
                {guidance.redaction_warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Freshness */}
          <div className="template-section template-freshness">
            <span className="template-freshness-icon">{'⏰'}</span>
            <span className="template-freshness-text">
              <strong>Freshness:</strong> {guidance.freshness}
            </span>
          </div>

          {/* Auditor Tip */}
          <details className="template-auditor-tip">
            <summary className="template-auditor-tip-summary">
              <span>{'🔍'}</span> Auditor Tip
            </summary>
            <p className="template-auditor-tip-content">{guidance.auditor_tip}</p>
          </details>

          {/* Feedback */}
          <div className="recipe-feedback">
            {feedbackSubmitted ? (
              <div className="recipe-feedback-thanks">
                Thanks for your feedback!
              </div>
            ) : (
              <>
                <span className="recipe-feedback-label">Was this guidance helpful?</span>
                <button
                  className="recipe-feedback-btn recipe-feedback-yes"
                  onClick={() => handleFeedback('helpful')}
                >
                  Helpful
                </button>
                <button
                  className="recipe-feedback-btn recipe-feedback-no"
                  onClick={() => handleFeedback('not_matching')}
                >
                  Not relevant
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default EvidenceTemplateGuidance
