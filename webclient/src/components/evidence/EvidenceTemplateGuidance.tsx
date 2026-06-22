import { useState, useCallback } from 'react'
import type { EvidenceId, EvidenceTemplate, EvidenceTemplatesFile } from '../../types'
import { submitRecipeFeedback } from '../../data/apiClient'

interface EvidenceTemplateGuidanceProps {
  evidenceId: EvidenceId
  evidenceTemplates: EvidenceTemplatesFile
  orgId?: string
}

/** Generic fallback guidance shown when no specific template exists. */
const GENERIC_GUIDANCE = {
  summary: 'Upload documentation that demonstrates this control is implemented and operating effectively.',
  acceptable_formats: ['PDF', 'DOCX', 'XLSX', 'CSV', 'PNG', 'JPG'],
  good_examples: [
    'Signed, dated policy or procedure document with version control',
    'System-generated report or export with timestamps',
  ],
  bad_examples: [
    'Screenshot without date or context',
    'Draft document without approval signatures',
  ],
  redaction_warnings: [
    'Remove any personally identifiable information (PII) not relevant to the control',
  ],
  freshness: 'Within the current audit period',
  auditor_tip: 'Auditors look for evidence that is current, complete, and demonstrates consistent operation over the audit period.',
}

export function EvidenceTemplateGuidance({
  evidenceId,
  evidenceTemplates,
  orgId,
}: EvidenceTemplateGuidanceProps) {
  const [collapsed, setCollapsed] = useState(true)
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false)

  const template: EvidenceTemplate | undefined = evidenceTemplates[evidenceId]
  const guidance = template?.guidance || GENERIC_GUIDANCE
  const isGeneric = !template

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
      <div
        className="container-header"
        onClick={() => setCollapsed(!collapsed)}
        style={{ cursor: 'pointer' }}
        role="button"
        aria-expanded={!collapsed}
      >
        <span className="container-icon">{'\uD83D\uDCCB'}</span>
        <span className="container-title">Evidence Guidance</span>
        {isGeneric && (
          <span className="template-generic-badge">Generic</span>
        )}
        <span className="container-collapse-icon" style={{ marginLeft: 'auto' }}>
          {collapsed ? '\u25B6' : '\u25BC'}
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
            <span className="template-freshness-icon">{'\u23F0'}</span>
            <span className="template-freshness-text">
              <strong>Freshness:</strong> {guidance.freshness}
            </span>
          </div>

          {/* Auditor Tip */}
          <details className="template-auditor-tip">
            <summary className="template-auditor-tip-summary">
              <span>{'\uD83D\uDD0D'}</span> Auditor Tip
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
