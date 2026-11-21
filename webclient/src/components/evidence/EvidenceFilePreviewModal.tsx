// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  getEvidenceFilePreview,
  type EvidenceFileResponse,
  type AssessmentFinding,
  type EvidenceAssessmentResponse,
  type EvidenceFilePreviewMode,
  type EvidenceFileStructuredPreview,
} from '../../data/apiClient'
import { useAssessmentPolling } from '../../hooks/useAssessmentPolling'
import { useEvidenceFileTypes } from '../../hooks/useEvidenceFileTypes'
import { listedPreviewMode } from './evidenceFileTypes'
import { parseCsv } from './csvPreview'
import { formatFileSize, fileTypeIcon, relativeTime } from './EvidenceFileList'
import { PreparerAssertionPanel } from './PreparerAssertionPanel'
import { AssessmentReviewPanel } from './AssessmentReviewPanel'
import { ShadowVerdictPanel } from './ShadowVerdictPanel'
import { verdictPresentation } from './assessmentVerdict'
import { renderMarkdownDocument } from '../../data/markdownDocument'

// ---- Props ----

interface EvidenceFilePreviewModalProps {
  file: EvidenceFileResponse
  orgId: string
  evidenceId: string
  onClose: () => void
  onDownload: (fileId: string) => void
  onDelete: (fileId: string) => Promise<void>
  isDeleting: boolean
  /** Same meaning as on ``EvidenceFileList``: only ``false`` withholds Delete. */
  canDelete?: boolean
}

// ---- Text, CSV and structured previews (#1309) ----

/** Text-like files are fetched in full only up to this size. */
const TEXT_PREVIEW_MAX_BYTES = 512 * 1024
const CSV_MAX_ROWS = 500
const CSV_MAX_COLUMNS = 50

/** Previews fetched from the download URL and shown in the dialog. */
const TEXT_MODES: ReadonlySet<EvidenceFilePreviewMode> = new Set(['markdown', 'code', 'csv'])

/** Thousands separators that do not depend on the test runner's locale. */
function count(n: number): string {
  return n.toLocaleString('en-GB')
}

/**
 * A value from the structured preview, as display text. The reply is read out
 * of an uploaded file, so a field the contract says is a string is not trusted
 * to be one: anything else becomes text rather than crashing the dialog.
 */
function asText(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return ''
  return String(value)
}

function asList<T>(value: T[] | null | undefined): T[] {
  return Array.isArray(value) ? value : []
}

function padRow(row: string[], width: number): string[] {
  const cells = row.slice(0, width)
  while (cells.length < width) cells.push('')
  return cells
}

/**
 * CSV as a table: the first row as headers, at most 500 data rows and 50
 * columns, with a sentence saying so when there are more.
 */
function CsvTable({ rows }: { rows: string[][] }) {
  const totalColumns = rows.reduce((max, r) => Math.max(max, r.length), 0)
  const columns = Math.min(totalColumns, CSV_MAX_COLUMNS)
  const [header, ...data] = rows
  const shown = data.slice(0, CSV_MAX_ROWS)
  return (
    <>
      {data.length > CSV_MAX_ROWS && (
        <p className="evidence-preview-note">
          {`Showing the first ${count(CSV_MAX_ROWS)} of ${count(data.length)} rows.`}
        </p>
      )}
      {totalColumns > CSV_MAX_COLUMNS && (
        <p className="evidence-preview-note">
          {`Showing the first ${count(CSV_MAX_COLUMNS)} of ${count(totalColumns)} columns.`}
        </p>
      )}
      <table className="evidence-preview-table">
        <thead>
          <tr>
            {padRow(header, columns).map((cell, i) => (
              <th key={i} scope="col">{cell}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, r) => (
            <tr key={r}>
              {padRow(row, columns).map((cell, c) => (
                <td key={c}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

/** Rows with no header row: a spreadsheet's first row is not known to be one. */
function PlainTable({ rows }: { rows: string[][] }) {
  const width = rows.reduce((max, r) => Math.max(max, asList(r).length), 0)
  return (
    <table className="evidence-preview-table">
      <tbody>
        {rows.map((row, r) => (
          <tr key={r}>
            {padRow(asList(row).map(asText), width).map((cell, c) => (
              <td key={c}>{cell}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/**
 * The server's reading of an Office file, archive or email, drawn by kind.
 * Returns null for a kind this client does not know, so the caller can show
 * the not-available card rather than an empty pane.
 */
function StructuredContent({ preview }: { preview: EvidenceFileStructuredPreview }): ReactNode {
  switch (preview.kind) {
    case 'document':
      return (
        <div className="evidence-preview-document">
          {asList(preview.paragraphs).map((p, i) => <p key={i}>{asText(p)}</p>)}
        </div>
      )
    case 'spreadsheet':
      return (
        <div className="evidence-preview-document">
          {asList(preview.sheets).map((sheet, i) => (
            <section key={i} className="evidence-preview-sheet">
              <h3>{asText(sheet.name)}</h3>
              {sheet.truncated && (
                <p className="evidence-preview-note">Only the first rows of this sheet are shown.</p>
              )}
              <PlainTable rows={asList(sheet.rows)} />
            </section>
          ))}
        </div>
      )
    case 'slides':
      return (
        <div className="evidence-preview-document">
          {asList(preview.slides).map((slide, i) => (
            <section key={i} className="evidence-preview-slide">
              <h3>{`Slide ${asText(slide.number)}`}</h3>
              {asList(slide.paragraphs).map((p, j) => <p key={j}>{asText(p)}</p>)}
            </section>
          ))}
        </div>
      )
    case 'archive':
      return (
        <div className="evidence-preview-document">
          <ul className="evidence-preview-archive">
            {asList(preview.members).map((m, i) => (
              <li key={i}>
                <span className="evidence-preview-member-name">{asText(m.name)}</span>
                <span className="evidence-preview-member-meta">
                  {formatFileSize(Number(m.size_bytes) || 0)}
                  {' \u00B7 '}
                  {m.label ? asText(m.label) : 'not read'}
                </span>
                {m.note && <span className="evidence-preview-member-note">{asText(m.note)}</span>}
              </li>
            ))}
          </ul>
        </div>
      )
    case 'email': {
      const h = preview.headers ?? {}
      const headerRows: Array<[string, unknown]> = [
        ['From', h.from], ['To', h.to], ['Cc', h.cc], ['Subject', h.subject], ['Date', h.date],
      ]
      const attachments = asList(preview.attachments)
      return (
        <div className="evidence-preview-document">
          <dl className="evidence-preview-email-headers">
            {headerRows
              .filter(([, value]) => asText(value).trim() !== '')
              .map(([label, value]) => (
                <div key={label} className="evidence-preview-email-header">
                  <dt>{label}</dt>
                  <dd>{asText(value)}</dd>
                </div>
              ))}
          </dl>
          <div className="evidence-preview-email-body">{asText(preview.body)}</div>
          {attachments.length > 0 && (
            <>
              <h3>Attachments</h3>
              <p className="evidence-preview-note">Attachments are listed, not opened.</p>
              <ul className="evidence-preview-archive">
                {attachments.map((a, i) => (
                  <li key={i}>
                    <span className="evidence-preview-member-name">{asText(a.name)}</span>
                    <span className="evidence-preview-member-meta">
                      {asText(a.content_type)}
                      {' \u00B7 '}
                      {formatFileSize(Number(a.size_bytes) || 0)}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )
    }
    default:
      return null
  }
}

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
  canDelete,
}: EvidenceFilePreviewModalProps) {
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [codeContent, setCodeContent] = useState<string | null>(null)
  const [codeLoading, setCodeLoading] = useState(false)
  const [codeError, setCodeError] = useState<string | null>(null)
  const [codeTooLarge, setCodeTooLarge] = useState(false)
  const [structuredPreview, setStructuredPreview] = useState<EvidenceFileStructuredPreview | null>(null)
  const [structuredLoading, setStructuredLoading] = useState(false)
  const [structuredError, setStructuredError] = useState<string | null>(null)
  const { fileTypes, loading: fileTypesLoading } = useEvidenceFileTypes()
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
  const isMarkdown =
    contentType === 'text/markdown' ||
    contentType === 'text/x-markdown' ||
    file.filename.toLowerCase().endsWith('.md') ||
    file.filename.toLowerCase().endsWith('.markdown')

  // Which viewer: the server's list decides (#1309). The flags above are what
  // this dialog knew before that list existed, and are kept as the fallback so
  // a slow or failed list request never makes a file previewable yesterday
  // unpreviewable today.
  const fallbackMode: EvidenceFilePreviewMode =
    isMarkdown ? 'markdown' : isCodeFile ? 'code' : isImage ? 'image' : isPdf ? 'pdf' : 'none'
  const mode: EvidenceFilePreviewMode =
    (fileTypes && listedPreviewMode(fileTypes.types, file)) || fallbackMode
  // Saying "not available" for a file the list is about to place would be a
  // claim the dialog has to retract a moment later.
  const awaitingFileTypes = !fileTypes && fileTypesLoading && fallbackMode === 'none'
  // Fetched as text and shown inline; only the presentation differs.
  const isTextPreview = TEXT_MODES.has(mode)
  const isStructured = mode === 'structured'

  // Reset state when switching files
  useEffect(() => {
    setCodeContent(null)
    setCodeLoading(false)
    setCodeError(null)
    setCodeTooLarge(false)
    setStructuredPreview(null)
    setStructuredLoading(false)
    setStructuredError(null)
    setConfirmDelete(false)
  }, [file.id])

  // Office files, archives and email: the server reads them, the dialog draws
  // what it read. Independent of `download_url` — the bytes never come here.
  useEffect(() => {
    if (!isStructured) return
    let cancelled = false
    setStructuredLoading(true)
    setStructuredError(null)
    setStructuredPreview(null)
    getEvidenceFilePreview(orgId, evidenceId, file.id)
      .then(preview => {
        if (!cancelled) setStructuredPreview(preview)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setStructuredError(
          err instanceof Error && err.message ? err.message : 'The preview could not be loaded.',
        )
      })
      .finally(() => {
        if (!cancelled) setStructuredLoading(false)
      })
    return () => { cancelled = true }
  }, [orgId, evidenceId, file.id, isStructured])

  // Fetch text content for text-like files (code, CSV, Markdown)
  useEffect(() => {
    if (!isTextPreview || !file.download_url) return
    if (file.file_size_bytes > TEXT_PREVIEW_MAX_BYTES) return

    let cancelled = false
    setCodeLoading(true)
    setCodeError(null)
    setCodeContent(null)
    setCodeTooLarge(false)

    fetch(file.download_url)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.text()
      })
      .then(text => {
        if (cancelled) return
        // The size on the record is zero until the server has measured the
        // file, so what arrived is checked as well before any of it is drawn.
        if (text.length > TEXT_PREVIEW_MAX_BYTES) {
          setCodeTooLarge(true)
          return
        }
        if (isJson && mode === 'code') {
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
  }, [file.id, file.download_url, file.file_size_bytes, isTextPreview, isJson, mode])

  const csvRows = useMemo(
    () => (mode === 'csv' && codeContent !== null ? parseCsv(codeContent) : null),
    [mode, codeContent],
  )

  function handleDialogClick(e: { stopPropagation(): void }) {
    e.stopPropagation()
  }

  async function handleConfirmDelete() {
    await onDelete(file.id)
  }

  // ---- Body branches ----

  function renderLoading(): ReactNode {
    return (
      <div className="evidence-preview-body evidence-preview-body--structured">
        <span className="evidence-preview-code-loading">Loading…</span>
      </div>
    )
  }

  function renderStructured(): ReactNode {
    if (structuredError) return renderUnavailable(structuredError)
    if (structuredLoading || !structuredPreview) return renderLoading()
    const content = StructuredContent({ preview: structuredPreview })
    if (content === null) return renderUnavailable()
    return (
      <div
        className="evidence-preview-body evidence-preview-body--structured"
        data-testid="evidence-preview-structured"
      >
        {structuredPreview.note && (
          <p className="evidence-preview-note">{asText(structuredPreview.note)}</p>
        )}
        {structuredPreview.truncated && (
          <p className="evidence-preview-note">
            Only the first part of this file is shown. Download it to see the rest.
          </p>
        )}
        {content}
      </div>
    )
  }

  function renderBody() {
    if (awaitingFileTypes) return renderLoading()

    // Before the URL guard: a structured preview is read by the server and
    // does not need a download URL in this dialog.
    if (isStructured) return renderStructured()

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

    if (isTextPreview) {
      if (file.file_size_bytes > TEXT_PREVIEW_MAX_BYTES || codeTooLarge) {
        return (
          <div className="evidence-preview-body evidence-preview-body--unsupported">
            <span className="evidence-preview-unsupported-icon" aria-hidden="true">📦</span>
            <span className="evidence-preview-unsupported-label">
              File too large to preview inline (&gt;512 KB) — use Download
            </span>
          </div>
        )
      }
      if (mode === 'markdown') {
        return (
          <div className="evidence-preview-body evidence-preview-body--markdown">
            {codeLoading && (
              <span className="evidence-preview-code-loading">Loading…</span>
            )}
            {codeError && (
              <span className="evidence-preview-unsupported-label">{codeError}</span>
            )}
            {codeContent !== null && (
              <div
                className="evidence-preview-markdown"
                data-testid="evidence-preview-markdown"
                // Uploaded evidence is untrusted: the renderer escapes the
                // source before parsing and sanitises the result.
                dangerouslySetInnerHTML={{ __html: renderMarkdownDocument(codeContent) }}
              />
            )}
          </div>
        )
      }
      if (csvRows && csvRows.length > 0) {
        return (
          <div className="evidence-preview-body evidence-preview-body--table">
            <CsvTable rows={csvRows} />
          </div>
        )
      }
      // Code, and CSV with nothing to tabulate. The file body verbatim, as a
      // text node: XML in particular is never parsed or inserted as markup.
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

    if (mode === 'image') {
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

    if (mode === 'pdf') {
      return (
        <div className="evidence-preview-body evidence-preview-body--pdf">
          {/*
            Deliberately NOT sandboxed. Chrome refuses to load its PDF viewer
            plugin inside any sandboxed iframe (there is no sandbox token that
            re-enables it), so a sandbox here renders the grey "plugin blocked"
            placeholder instead of the document. The old value
            (allow-same-origin + allow-scripts) gave no isolation anyway: a
            same-origin frame with scripts can strip its own sandbox. The file
            is served same-origin by our own backend with an inline disposition.
          */}
          <iframe
            src={file.download_url}
            title={file.filename}
            className="evidence-preview-iframe"
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

    return renderUnavailable()
  }

  /**
   * The metadata card. With `detail`, the server refused a preview for a
   * reason of its own (too large, unreadable, held by the malware scan), and
   * that sentence is shown instead of blaming the file type.
   */
  function renderUnavailable(detail?: string): ReactNode {
    return (
      <div className="evidence-preview-body evidence-preview-body--unsupported">
        <span className="evidence-preview-unsupported-icon" aria-hidden="true">
          {fileTypeIcon(contentType)}
        </span>
        <span className="evidence-preview-unsupported-label">
          {detail ? 'Preview not available' : 'Preview not available for this file type'}
        </span>
        {detail && <span className="evidence-preview-unsupported-detail">{detail}</span>}
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

                    <ShadowVerdictPanel
                      orgId={orgId}
                      evidenceId={evidenceId}
                      fileId={file.id}
                      assessmentVersion={assessment.version_number}
                      assessmentVersionId={assessment.current_version_id}
                    />

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
            {canDelete === false ? null : confirmDelete ? (
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
