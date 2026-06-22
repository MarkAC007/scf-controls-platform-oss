import { useEffect, useState } from 'react'
import { type EvidenceFileResponse, type AssessmentFinding } from '../../data/apiClient'
import { useAssessmentPolling } from '../../hooks/useAssessmentPolling'
import { formatFileSize, fileTypeIcon, relativeTime } from './EvidenceFileList'

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
  const { assessment, loading: assessLoading, triggering, trigger } = useAssessmentPolling(orgId, evidenceId, file.id)
  // ESC key handler
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

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

        {/* Body */}
        {renderBody()}

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
              {assessLoading ? (
                <div className="ai-assessment-empty">Loading assessment...</div>
              ) : assessment && assessment.status !== 'pending' && assessment.status !== 'processing' ? (
                <>
                  <div className="ai-assessment-panel-status">
                    <span className={`ai-chip ai-chip-${assessment.status}`}>
                      {AI_STATUS_LABELS[assessment.status] || assessment.status}
                    </span>
                    {assessment.relevance_score !== null && (
                      <span className="ai-assessment-panel-score">
                        {Math.round(assessment.relevance_score)}/100
                      </span>
                    )}
                  </div>
                  {assessment.summary && (
                    <div className="ai-assessment-panel-summary">{assessment.summary}</div>
                  )}
                  {assessment.findings.length > 0 && (
                    <div className="ai-findings-list">
                      {assessment.findings.map((f, i) => (
                        <FindingRow key={i} finding={f} />
                      ))}
                    </div>
                  )}
                  <div style={{ marginTop: 8 }}>
                    <button
                      className="ai-assess-btn"
                      onClick={trigger}
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
              ) : (
                <div className="ai-assessment-empty">
                  <span>No AI assessment yet</span>
                  <button
                    className="ai-assess-btn"
                    onClick={trigger}
                    disabled={triggering}
                  >
                    {triggering ? 'Starting...' : 'Assess with AI'}
                  </button>
                </div>
              )}
            </>
          )}
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
