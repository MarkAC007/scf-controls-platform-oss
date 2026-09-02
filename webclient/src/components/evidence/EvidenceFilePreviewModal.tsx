import { useEffect, useState } from 'react'
import {
  type EvidenceFileResponse,
  type AssessmentFinding,
  type EvidenceAssessmentResponse,
} from '../../data/apiClient'
import { useAssessmentPolling } from '../../hooks/useAssessmentPolling'
import { formatFileSize, fileTypeIcon, relativeTime } from './EvidenceFileList'
import { PreparerAssertionPanel } from './PreparerAssertionPanel'
import { AssessmentReviewPanel } from './AssessmentReviewPanel'
import { verdictPresentation } from './assessmentVerdict'

// ---- Props ----

interface EvidenceFilePreviewModalProps {
  file: EvidenceFileResponse
  orgId: string
  evidenceId: string
  onClose: () => void
  onDownload: (fileId: string) => void
  onDelete: (fileId: string) => Promise<void>
  isDeleting: boolean
}

// ---- Component ----

// ---- AI Assessment Sub-components ----

const AI_STATUS_LABELS: Record<string, string> = {
  sufficient: 'Sufficient',
  partial: 'Partial',
  insufficient: 'Insufficient',
  pending: 'Pending',
  processing: 'Processing',
  error: 'Error',
  unassessable: 'Unassessable',
}

/**
 * What a failed *request* means, in the reader's terms (#881).
 *
 * Each line has one job: stop the reader concluding anything about their
 * evidence from an outage. "Insufficient" is a verdict; none of these are.
 */
const REQUEST_ERROR_COPY: Record<string, string> = {
  load: 'Could not load the AI assessment for this file.',
  poll: 'Lost contact while waiting for the assessment to finish. It may still be running.',
  trigger: 'Could not start the assessment.',
}

/**
 * Provenance for a stored verdict: which model, which prompt, and when.
 *
 * Rendered for every terminal assessment, including one missing the fields —
 * an assessment nobody can attribute to a model version is one nobody can
 * reproduce, and that is a fact an auditor needs on the face of the record
 * rather than discovered later.
 */
/**
 * The finding that carries the truncation disclosure, if there is one.
 *
 * Identified by its ``truncated`` marker, never by matching the message text —
 * the wording belongs to the backend and is free to change.
 */
function findTruncationFinding(
  assessment: EvidenceAssessmentResponse,
): AssessmentFinding | undefined {
  return assessment.findings.find(f => f.truncated)
}

/**
 * What to tell the reader about a truncated read.
 *
 * Prefers the backend's own sentence, which carries the real character count
 * from the extractor. The fallback exists for assessments stored before the
 * disclosure finding was added, and states the limitation without inventing a
 * figure it does not have.
 */
function truncationNotice(assessment: EvidenceAssessmentResponse): string {
  const finding = findTruncationFinding(assessment)
  if (finding?.message) return finding.message
  const chars = assessment.truncated_at_chars
  return chars
    ? `Only the first ${chars.toLocaleString()} characters of this document were assessed. `
      + 'Findings may not reflect later sections.'
    : 'This document was truncated before analysis. Findings may not reflect later sections.'
}

function AssessmentProvenance({ assessment }: { assessment: EvidenceAssessmentResponse }) {
  const assessedAt = assessment.assessed_at
    ? new Date(assessment.assessed_at).toLocaleString()
    : 'not recorded'
  return (
    <div className="ai-assessment-provenance" data-testid="ai-assessment-provenance">
      <span className="ai-assessment-provenance-item">
        <span className="ai-assessment-provenance-label">Model</span>
        <span className="ai-assessment-provenance-value">
          {assessment.model_id || 'not recorded'}
        </span>
      </span>
      <span className="ai-assessment-provenance-item">
        <span className="ai-assessment-provenance-label">Prompt</span>
        <span className="ai-assessment-provenance-value">
          {assessment.prompt_version || 'not recorded'}
        </span>
      </span>
      <span className="ai-assessment-provenance-item">
        <span className="ai-assessment-provenance-label">Assessed</span>
        <span className="ai-assessment-provenance-value">{assessedAt}</span>
      </span>
    </div>
  )
}

function FindingRow({ finding }: { finding: AssessmentFinding }) {
  const levelConfig: Record<string, { label: string; cls: string }> = {
    sufficient: { label: 'Pass', cls: 'ai-chip-sufficient' },
    partial: { label: 'Partial', cls: 'ai-chip-partial' },
    insufficient: { label: 'Fail', cls: 'ai-chip-insufficient' },
    info: { label: 'Info', cls: 'ai-chip-pending' },
  }
  const config = levelConfig[finding.level] || levelConfig.info

  return (
    <div className="ai-finding-row">
      <span className={`ai-chip ai-finding-level ${config.cls}`}>{config.label}</span>
      <div className="ai-finding-content">
        <div className="ai-finding-message">{finding.message}</div>
        {finding.control_id && (
          <div className="ai-finding-control">{finding.control_id}</div>
        )}
        {finding.suggestion && (
          <div className="ai-finding-suggestion">{finding.suggestion}</div>
        )}
      </div>
    </div>
  )
}

// ---- Main Component ----

export function EvidenceFilePreviewModal({
  file,
  orgId,
  evidenceId,
  onClose,
  onDownload,
  onDelete,
  isDeleting,
}: EvidenceFilePreviewModalProps) {
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [codeContent, setCodeContent] = useState<string | null>(null)
  const [codeLoading, setCodeLoading] = useState(false)
  const [codeError, setCodeError] = useState<string | null>(null)
  const [panelExpanded, setPanelExpanded] = useState(true)
  const {
    assessment: polledAssessment,
    loading: assessLoading,
    triggering,
    trigger,
    requestError,
    retry,
  } = useAssessmentPolling(orgId, evidenceId, file.id)

  // A recorded review returns the updated row, and it is fresher than anything
  // the poller is holding. Keyed to the *version*, not just the row id: the
  // assessment row is rewritten in place on every re-assessment, so an id match
  // alone would let a decision about the previous verdict keep masking a new
  // one that is genuinely awaiting review.
  const [reviewed, setReviewed] = useState<EvidenceAssessmentResponse | null>(null)
  const assessment =
    reviewed &&
    polledAssessment &&
    reviewed.id === polledAssessment.id &&
    reviewed.version_number === polledAssessment.version_number
      ? reviewed
      : polledAssessment

  // The truncation disclosure is shown once, at the top, as a caveat over the
  // whole verdict — so it is lifted out of the findings list rather than
  // repeated inside it.
  const truncationFinding = assessment ? findTruncationFinding(assessment) : undefined
  const visibleFindings = assessment
    ? assessment.findings.filter(f => !f.truncated)
    : []
  // ESC key handler
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  // The dialog scrolls its own content, so the wheel must not scroll the page
  // behind it. The previous value is restored rather than cleared: clearing
  // would release a lock this modal never took.
  useEffect(() => {
    const previousBodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = previousBodyOverflow }
  }, [])

  const contentType = file.content_type ?? ''
  const isImage = contentType.startsWith('image/')
  const isPdf = contentType === 'application/pdf'

  const isJson =
    contentType === 'application/json' ||
    file.filename.toLowerCase().endsWith('.json')
  const isYaml =
    contentType === 'text/yaml' ||
    contentType === 'application/yaml' ||
    contentType === 'application/x-yaml' ||
    file.filename.toLowerCase().endsWith('.yml') ||
    file.filename.toLowerCase().endsWith('.yaml')
  const isCodeFile = isJson || isYaml

  // Reset state when switching files
  useEffect(() => {
    setCodeContent(null)
    setCodeLoading(false)
    setCodeError(null)
    setConfirmDelete(false)
  }, [file.id])

  // Fetch code content for JSON/YAML files
  useEffect(() => {
    if (!isCodeFile || !file.download_url) return
    if (file.file_size_bytes > 512 * 1024) return

    let cancelled = false
    setCodeLoading(true)
    setCodeError(null)
    setCodeContent(null)

    fetch(file.download_url)
      .then(r => r.text())
      .then(text => {
        if (cancelled) return
        if (isJson) {
          try {
            setCodeContent(JSON.stringify(JSON.parse(text), null, 2))
          } catch {
            setCodeContent(text)
          }
        } else {
          setCodeContent(text)
        }
      })
      .catch(() => {
        if (!cancelled) setCodeError('Failed to load file content')
      })
      .finally(() => {
        if (!cancelled) setCodeLoading(false)
      })

    return () => { cancelled = true }
  }, [file.id, file.download_url, file.file_size_bytes, isCodeFile, isJson])

  function handleDialogClick(e: { stopPropagation(): void }) {
    e.stopPropagation()
  }

  async function handleConfirmDelete() {
    await onDelete(file.id)
  }

  // ---- Body branches ----

  function renderBody() {
    // Null URL guard — show error state instead of broken image / blank iframe
    if (!file.download_url) {
      return (
        <div className="evidence-preview-body evidence-preview-body--unsupported">
          <span className="evidence-preview-unsupported-icon" aria-hidden="true">⚠️</span>
          <span className="evidence-preview-unsupported-label">
            File URL unavailable — try closing and reopening
          </span>
        </div>
      )
    }

    if (isCodeFile) {
      if (file.file_size_bytes > 512 * 1024) {
        return (
          <div className="evidence-preview-body evidence-preview-body--unsupported">
            <span className="evidence-preview-unsupported-icon" aria-hidden="true">📦</span>
            <span className="evidence-preview-unsupported-label">
              File too large to preview inline (&gt;512 KB) — use Download
            </span>
          </div>
        )
      }
      return (
        <div className="evidence-preview-body evidence-preview-body--code">
          {codeLoading && (
            <span className="evidence-preview-code-loading">Loading…</span>
          )}
          {codeError && (
            <span className="evidence-preview-unsupported-label">{codeError}</span>
          )}
          {codeContent && (
            <pre className="evidence-preview-code"><code>{codeContent}</code></pre>
          )}
        </div>
      )
    }

    if (isImage) {
      return (
        <div className="evidence-preview-body evidence-preview-body--image">
          <img
            src={file.download_url}
            alt={file.filename}
            className="evidence-preview-image"
          />
        </div>
      )
    }

    if (isPdf) {
      return (
        <div className="evidence-preview-body evidence-preview-body--pdf">
          <iframe
            src={file.download_url}
            title={file.filename}
            className="evidence-preview-iframe"
            sandbox="allow-same-origin allow-scripts allow-popups"
          />
          {/* iOS Safari fallback — WebKit doesn't embed PDFs in iframes */}
          <a
            href={file.download_url}
            target="_blank"
            rel="noopener noreferrer"
            className="evidence-preview-pdf-fallback"
          >
            Open PDF ↗
          </a>
        </div>
      )
    }

    // Unsupported file type — metadata card
    return (
      <div className="evidence-preview-body evidence-preview-body--unsupported">
        <span className="evidence-preview-unsupported-icon" aria-hidden="true">
          {fileTypeIcon(contentType)}
        </span>
        <span className="evidence-preview-unsupported-label">
          Preview not available for this file type
        </span>
        <div className="evidence-preview-meta-card">
          <div className="evidence-preview-meta-row">
            <span className="evidence-preview-meta-label">Filename</span>
            <span className="evidence-preview-meta-value" title={file.filename}>
              {file.filename}
            </span>
          </div>
          <div className="evidence-preview-meta-row">
            <span className="evidence-preview-meta-label">Type</span>
            <span className="evidence-preview-meta-value">{file.content_type}</span>
          </div>
          <div className="evidence-preview-meta-row">
            <span className="evidence-preview-meta-label">Size</span>
            <span className="evidence-preview-meta-value">
              {formatFileSize(file.file_size_bytes)}
            </span>
          </div>
          {file.uploaded_by && (
            <div className="evidence-preview-meta-row">
              <span className="evidence-preview-meta-label">Uploaded by</span>
              <span className="evidence-preview-meta-value">
                {file.uploaded_by.display_name}
              </span>
            </div>
          )}
          <div className="evidence-preview-meta-row">
            <span className="evidence-preview-meta-label">Uploaded</span>
            <span className="evidence-preview-meta-value">
              {relativeTime(file.uploaded_at)}
            </span>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="evidence-preview-overlay" onClick={onClose}>
      <div
        className="evidence-preview-dialog"
        onClick={handleDialogClick}
        role="dialog"
        aria-modal="true"
        aria-label={`Preview: ${file.filename}`}
      >
        {/* Header */}
        <div className="evidence-preview-header">
          <span className="evidence-preview-title" title={file.filename}>
            {fileTypeIcon(file.content_type)} {file.filename}
          </span>
          <button
            type="button"
            className="evidence-preview-close-btn"
            onClick={onClose}
            aria-label="Close preview"
          >
            ×
          </button>
        </div>

        <div className="evidence-preview-scroll">
          {/* Body */}
          {renderBody()}

          {/* What the preparer asserted (#786, #802). Rendered for every file,
              including one with nothing asserted — "no claim was made about this
              evidence's period, population or provenance" is exactly the finding
              an auditor needs, and hiding the panel would hide it. Note the meta
              card inside renderBody() only appears for unsupported file types, so
              this is the only always-visible metadata region in the modal. */}
          <PreparerAssertionPanel file={file} />

          {/* AI Assessment Panel */}
          <div className="ai-assessment-panel">
            <div
              className="ai-assessment-panel-header"
              onClick={() => setPanelExpanded(!panelExpanded)}
            >
              <h4 className="ai-assessment-panel-title">AI Assessment</h4>
              <span className="ai-advisory-label">AI Advisory</span>
              {assessment?.assessed_at && (
                <span className="ai-assessment-panel-timestamp">
                  {relativeTime(assessment.assessed_at)}
                </span>
              )}
              <span className="ai-assessment-panel-toggle">
                {panelExpanded ? '\u25B2' : '\u25BC'}
              </span>
            </div>

            {panelExpanded && (
              <>
                {/* A failed request, stated as a failed request. It sits above
                    the verdict rather than replacing it: the last assessment we
                    did fetch was true when we fetched it, and hiding it would
                    lose information. What must not happen is the reader taking
                    an outage for a judgement on their evidence. */}
                {requestError && (
                  <div className="ai-assessment-request-error" role="alert">
                    <div className="ai-assessment-request-error-message">
                      {REQUEST_ERROR_COPY[requestError.kind] || 'The assessment request failed.'}
                    </div>
                    <div className="ai-assessment-request-error-detail">
                      {requestError.message}
                    </div>
                    <div className="ai-assessment-request-error-note">
                      This is a problem reaching the assessment service — not a finding about this file.
                    </div>
                    {/* Retry the thing that actually failed. A trigger that
                        never started needs starting again; a fetch that failed
                        needs re-fetching, and must not silently re-run a model
                        the user did not ask for a second time. */}
                    <button
                      type="button"
                      className="ai-assess-btn ai-assess-retry-btn"
                      onClick={() => (requestError.kind === 'trigger' ? trigger() : retry())}
                      disabled={triggering}
                    >
                      {requestError.kind === 'trigger' ? 'Try again' : 'Retry'}
                    </button>
                  </div>
                )}

                {assessLoading ? (
                  <div className="ai-assessment-empty">Loading assessment...</div>
                ) : assessment && assessment.status !== 'pending' && assessment.status !== 'processing' ? (
                  <>
                    <div className="ai-assessment-panel-status">
                      {/* "AI suggests: Partial" until a person confirms it, then
                          "Confirmed: Partial". The wording is the one place a
                          reader learns whether this verdict has been stood
                          behind, so it comes from the shared vocabulary rather
                          than from a label map local to this file. */}
                      <span
                        className={verdictPresentation(assessment.status, assessment.review_decision).className}
                        data-testid="ai-assessment-verdict-chip"
                      >
                        {verdictPresentation(assessment.status, assessment.review_decision).text}
                      </span>
                      {assessment.relevance_score !== null && (
                        <span className="ai-assessment-panel-score">
                          {Math.round(assessment.relevance_score)}/100
                        </span>
                      )}
                    </div>

                    {/* Nothing was read, so nothing was judged. Said plainly,
                        because "Unassessable" next to a score of nothing invites
                        the reader to assume the file failed on its merits. */}
                    {assessment.status === 'unassessable' && (
                      <div className="ai-assessment-unassessable-note">
                        No readable text could be extracted from this file, so it has not been
                        assessed. This is not a judgement on the evidence — a scanned image or a
                        binary format can be perfectly good evidence that this check cannot read.
                        {/* The specific extraction error is not a separate field:
                            the backend writes it into `summary` and the first
                            finding, both rendered below. Repeating it here would
                            just say the same thing twice. */}
                      </div>
                    )}

                    {/* The model saw part of the document. Anything it did not
                        read, it cannot have found a gap in — so an unqualified
                        "Sufficient" over a truncated read would overclaim.
                        Promoted out of the findings list to the top, because a
                        caveat that changes how every finding below it should be
                        read does not belong buried among them. */}
                    {assessment.truncated && (
                      <div className="ai-assessment-truncation" data-testid="ai-assessment-truncation">
                        <div>{truncationNotice(assessment)}</div>
                        {truncationFinding?.suggestion && (
                          <div className="ai-assessment-truncation-suggestion">
                            {truncationFinding.suggestion}
                          </div>
                        )}
                      </div>
                    )}

                    {assessment.summary && (
                      <div className="ai-assessment-panel-summary">{assessment.summary}</div>
                    )}
                    {visibleFindings.length > 0 && (
                      <div className="ai-findings-list">
                        {visibleFindings.map((f, i) => (
                          <FindingRow key={i} finding={f} />
                        ))}
                      </div>
                    )}

                    {/* Where the AI's suggestions get answered. Below the
                        file-level findings because a reviewer decides objective
                        by objective, and above Re-assess because correcting a
                        verdict is the more common next step than replacing it. */}
                    <AssessmentReviewPanel
                      orgId={orgId}
                      evidenceId={evidenceId}
                      fileId={file.id}
                      assessment={assessment}
                      onReviewed={setReviewed}
                    />

                    <AssessmentProvenance assessment={assessment} />

                    <div style={{ marginTop: 8 }}>
                      {/* Re-assess forces a fresh run. Without ``force`` the
                          backend may serve the cached verdict, and the button
                          would look broken to the one user who most wants a
                          second opinion. */}
                      <button
                        className="ai-assess-btn"
                        onClick={() => trigger({ force: true })}
                        disabled={triggering}
                      >
                        {triggering ? 'Re-assessing...' : 'Re-assess'}
                      </button>
                    </div>
                  </>
                ) : assessment && (assessment.status === 'pending' || assessment.status === 'processing') ? (
                  <div className="ai-assessment-empty">
                    <span className="ai-chip ai-chip-pending">Assessing...</span>
                    <span>AI assessment in progress</span>
                  </div>
                ) : requestError ? (
                  // The fetch failed, so we do not know whether an assessment
                  // exists. Saying "No AI assessment yet" here would be a claim
                  // we cannot support; the error block above is the whole story.
                  null
                ) : (
                  <div className="ai-assessment-empty">
                    <span>No AI assessment yet</span>
                    <button
                      className="ai-assess-btn"
                      onClick={() => trigger()}
                      disabled={triggering}
                    >
                      {triggering ? 'Starting...' : 'Assess with AI'}
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="evidence-preview-footer">
          <div className="evidence-preview-footer-left">
            {confirmDelete ? (
              <>
                <span className="evidence-files-confirm-label">Delete?</span>
                <button
                  type="button"
                  className="evidence-files-confirm-yes-btn"
                  onClick={handleConfirmDelete}
                  disabled={isDeleting}
                >
                  Yes, delete
                </button>
                <button
                  type="button"
                  className="evidence-files-confirm-cancel-btn"
                  onClick={() => setConfirmDelete(false)}
                  disabled={isDeleting}
                >
                  Cancel
                </button>
              </>
            ) : (
              <button
                type="button"
                className="evidence-files-delete-btn"
                onClick={() => setConfirmDelete(true)}
                disabled={isDeleting}
              >
                Delete
              </button>
            )}
          </div>
          {file.download_url && (
            <button
              type="button"
              className="evidence-files-download-btn"
              onClick={() => onDownload(file.id)}
            >
              Download
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
