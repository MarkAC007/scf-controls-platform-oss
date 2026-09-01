import { useState, useEffect, useRef, useCallback } from 'react'
import {
  listEvidenceFiles,
  deleteEvidenceFile,
  reviewEvidenceFile,
  getAssessment,
  bulkAssess,
  type EvidenceFileResponse,
  type EvidenceAssessmentResponse,
} from '../../data/apiClient'
import { EvidenceFilePreviewModal } from './EvidenceFilePreviewModal'
import { ASSESSMENT_STATUS_LABELS, verdictPresentation } from './assessmentVerdict'

// M4 (#574) — when the per-window review workflow is enabled, the per-file
// Approve/Reject buttons are hidden because reviews now happen at the window
// level (see ``WindowReviewPanel``). The row-level review badge stays visible
// for historical context. When the flag is unset (default), behaviour is
// unchanged — existing tests and existing per-file reviews keep working.
import { PER_WINDOW_REVIEW_ENABLED } from '../../data/featureFlags'

// ---- Props ----

interface EvidenceFileListProps {
  orgId: string
  evidenceId: string
  refreshTrigger: number
  canDelete?: boolean
  canReview?: boolean
  /** Whether this user may request AI assessments. Backend requires ``editor``. */
  canAssess?: boolean
}

/**
 * The server queues at most this many files per bulk-assess request and
 * silently drops the rest.
 *
 * The client slices to the same number and says it is doing so. Sending 200
 * and reporting "queued" for all of them would be the platform lying about
 * work it never scheduled.
 */
const BULK_ASSESS_MAX = 50

// ---- Helpers (exported for use by EvidenceFilePreviewModal) ----

export function relativeTime(dateStr: string): string {
  const now = Date.now()
  const then = new Date(dateStr).getTime()
  const diffMs = now - then
  const minutes = Math.floor(diffMs / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}d ago`
  return new Date(dateStr).toLocaleDateString()
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function fileTypeIcon(contentType: string): string {
  if (contentType.includes('pdf')) return '\uD83D\uDCC4'
  if (contentType.includes('spreadsheet') || contentType.includes('csv')) return '\uD83D\uDCCA'
  if (contentType.includes('word') || contentType.includes('document')) return '\uD83D\uDCDD'
  if (contentType.includes('image')) return '\uD83D\uDDBC\uFE0F'
  if (contentType.includes('json')) return '\uD83D\uDCCB'
  if (contentType.includes('yaml') || contentType.includes('yml')) return '\u2699\uFE0F'
  return '\uD83D\uDCCE'
}

// ---- Loading skeleton ----

function FileRowSkeleton() {
  return (
    <div className="evidence-files-row evidence-files-row--skeleton" aria-hidden="true">
      <div className="evidence-files-icon-col">
        <div className="evidence-files-skeleton-block evidence-files-skeleton-icon" />
      </div>
      <div className="evidence-files-meta-col">
        <div className="evidence-files-skeleton-block evidence-files-skeleton-name" />
        <div className="evidence-files-skeleton-block evidence-files-skeleton-sub" />
      </div>
      <div className="evidence-files-actions-col">
        <div className="evidence-files-skeleton-block evidence-files-skeleton-btn" />
        <div className="evidence-files-skeleton-block evidence-files-skeleton-btn" />
      </div>
    </div>
  )
}

// ---- Review status badge ----

const REVIEW_STATUS_CONFIG: Record<string, { label: string; className: string }> = {
  not_reviewed: { label: 'Not Reviewed', className: 'review-badge-not-reviewed' },
  approved: { label: 'Approved', className: 'review-badge-approved' },
  rejected: { label: 'Rejected', className: 'review-badge-rejected' },
  needs_revision: { label: 'Needs Revision', className: 'review-badge-needs-revision' },
}

const AI_STATUS_TITLES: Record<string, string> = {
  error: 'The assessment ran and failed. This is not a finding about the evidence.',
  unassessable:
    'No readable text could be extracted from this file, so it was not assessed. '
    + 'Open the file to see why.',
}

const AI_UNREVIEWED_TITLE =
  'A suggestion from the AI assessor. Nobody has confirmed it yet — open the file to review it.'

/** What the list knows about one file's AI verdict. */
export interface FileAssessmentSummary {
  status: string
  /** 'confirmed' | 'overridden' | null. Null means still a suggestion. */
  reviewDecision: string | null
}

/**
 * The verdict chip in a file row.
 *
 * Reads "AI suggests: Partial" until a person has confirmed it, and
 * "Confirmed: Partial" (or "Corrected: …") afterwards. The wording comes from
 * the shared vocabulary in ``assessmentVerdict`` rather than a local map,
 * because the list and the modal saying different things about the same
 * verdict is precisely the failure that vocabulary exists to prevent.
 */
function AssessmentChip({ assessment }: { assessment: FileAssessmentSummary | null }) {
  if (!assessment?.status) return null
  if (!ASSESSMENT_STATUS_LABELS[assessment.status]) return null
  const verdict = verdictPresentation(assessment.status, assessment.reviewDecision)
  const title =
    AI_STATUS_TITLES[assessment.status] ||
    (verdict.isReviewed ? undefined : AI_UNREVIEWED_TITLE)
  return (
    <span className={verdict.className} title={title}>
      {verdict.text}
    </span>
  )
}

// ---- Integrity badge (#57) ----
//
// The server hashes and scans every stored object out of band. Until that lands
// a file reads as "not yet scanned" — and stays downloadable and posture-bearing
// while it does, deliberately: the unscanned backlog is the platform's own debt,
// not the customer's, so it is disclosed rather than penalised. Only `infected`
// is withheld, and the backend enforces that independently of this label.

const INTEGRITY_BADGE_CONFIG: Record<string, { label: string; className: string; title: string }> = {
  infected: {
    label: 'Infected',
    className: 'integrity-badge-infected',
    title: 'The malware scanner flagged this file. It has been quarantined and cannot be downloaded.',
  },
  hash_mismatch: {
    label: 'Hash Mismatch',
    className: 'integrity-badge-mismatch',
    title:
      'The stored file does not match the checksum supplied at upload. It is still downloadable so the '
      + 'discrepancy can be investigated, but it does not count toward compliance posture.',
  },
  unreadable: {
    label: 'Unreadable',
    className: 'integrity-badge-unreadable',
    title: 'This record names a file that could not be read back from evidence storage.',
  },
  not_yet_scanned: {
    label: 'Not Yet Scanned',
    className: 'integrity-badge-pending',
    title:
      'This file has not been hashed or scanned by the server yet. It remains available and continues to '
      + 'count toward posture in the meantime.',
  },
}

function IntegrityBadge({ badge }: { badge: string | null }) {
  if (!badge) return null
  const config = INTEGRITY_BADGE_CONFIG[badge]
  if (!config) return null
  return (
    <span className={`integrity-badge ${config.className}`} title={config.title}>
      {config.label}
    </span>
  )
}

function ReviewStatusBadge({ status }: { status: string }) {
  if (status === 'not_reviewed') return null
  const config = REVIEW_STATUS_CONFIG[status]
  if (!config) return null
  return <span className={`review-badge ${config.className}`}>{config.label}</span>
}

// ---- File row ----

interface FileRowProps {
  file: EvidenceFileResponse
  onDelete: (fileId: string) => void
  onReview?: (fileId: string, status: string) => void
  isDeleting: boolean
  isReviewing: boolean
  onView: (fileId: string) => void
  isLoadingPreview: boolean
  canDelete?: boolean
  canReview?: boolean
  assessment?: FileAssessmentSummary | null
  selectable?: boolean
  selected?: boolean
  onToggleSelected?: (fileId: string) => void
}

/**
 * The asserted effective period, as a compact row chip (#786).
 *
 * Shown only when a period was actually asserted. The list is a scanning
 * surface, and a row of "not asserted" on every legacy file would drown the
 * rows that do carry a claim — the preview modal is where the absence is
 * spelled out. What the chip buys on the row is the comparison an auditor makes
 * at a glance: uploaded last week, covers 2023.
 */
/**
 * The chip owns its own separator, and therefore its own decision about whether
 * to appear at all. Guarding at the call site instead would put the same rule in
 * two places — and the version that survived a mutation sweep was the call site,
 * which meant the guard in here was unreachable and untested. One guard, here.
 */
function EffectivePeriodChip({ start, end }: { start: string | null; end: string | null }) {
  // Both ends or nothing. A half-asserted period is not a window, and
  // "covers 1 Apr 2026 – " reads as a claim the preparer never made.
  if (!start || !end) return null
  const short = (value: string) => {
    const parsed = new Date(value.length === 10 ? `${value}T00:00:00` : value)
    return Number.isNaN(parsed.getTime())
      ? value
      : parsed.toLocaleDateString('en-GB', { year: 'numeric', month: 'short', day: 'numeric' })
  }
  return (
    <>
      <span className="evidence-files-separator" aria-hidden="true">{'\u00B7'}</span>
      <span
        className="evidence-files-period"
        data-testid="evidence-file-effective-period"
        title={`Preparer asserts this evidence covers ${short(start)} to ${short(end)}`}
      >
        {`covers ${short(start)} – ${short(end)}`}
      </span>
    </>
  )
}

function FileRow({ file, onDelete, onReview, isDeleting, isReviewing, onView, isLoadingPreview, canDelete, canReview, assessment, selectable, selected, onToggleSelected }: FileRowProps) {
  const [confirmDelete, setConfirmDelete] = useState(false)

  function handleDeleteClick() {
    setConfirmDelete(true)
  }

  function handleConfirmDelete() {
    onDelete(file.id)
    setConfirmDelete(false)
  }

  function handleCancelDelete() {
    setConfirmDelete(false)
  }

  function handleDownload() {
    if (!file.download_url) return
    window.open(file.download_url + '&disposition=attachment', '_blank', 'noopener,noreferrer')
  }

  return (
    <div className={`evidence-files-row${isDeleting ? ' evidence-files-row--deleting' : ''}`}>
      {selectable && (
        <div className="evidence-files-select-col">
          <input
            type="checkbox"
            className="evidence-files-select"
            checked={selected ?? false}
            onChange={() => onToggleSelected?.(file.id)}
            aria-label={`Select ${file.filename} for AI assessment`}
          />
        </div>
      )}

      {/* File type icon */}
      <div className="evidence-files-icon-col" aria-hidden="true">
        <span className="evidence-files-type-icon">
          {fileTypeIcon(file.content_type)}
        </span>
      </div>

      {/* File metadata */}
      <div className="evidence-files-meta-col">
        <button
          type="button"
          className="evidence-files-filename evidence-files-filename--clickable"
          title={file.filename}
          onClick={() => onView(file.id)}
        >
          {file.filename}
        </button>
        <span className="evidence-files-submeta">
          <span className="evidence-files-size">{formatFileSize(file.file_size_bytes)}</span>
          {file.uploaded_by && (
            <>
              <span className="evidence-files-separator" aria-hidden="true">{'\u00B7'}</span>
              <span className="evidence-files-uploader">{file.uploaded_by.display_name}</span>
            </>
          )}
          <span className="evidence-files-separator" aria-hidden="true">{'\u00B7'}</span>
          <time
            className="evidence-files-timestamp"
            dateTime={file.uploaded_at}
            title={new Date(file.uploaded_at).toLocaleString()}
          >
            {relativeTime(file.uploaded_at)}
          </time>
          <EffectivePeriodChip
            start={file.effective_period_start}
            end={file.effective_period_end}
          />
          {file.review_status && (
            <>
              <span className="evidence-files-separator" aria-hidden="true">{'\u00B7'}</span>
              <ReviewStatusBadge status={file.review_status} />
            </>
          )}
          {file.integrity_badge && (
            <>
              <span className="evidence-files-separator" aria-hidden="true">{'\u00B7'}</span>
              <IntegrityBadge badge={file.integrity_badge} />
            </>
          )}
          {assessment && (
            <>
              <span className="evidence-files-separator" aria-hidden="true">{'\u00B7'}</span>
              <AssessmentChip assessment={assessment} />
            </>
          )}
        </span>
      </div>

      {/* Actions */}
      <div className="evidence-files-actions-col">
        {confirmDelete ? (
          <div className="evidence-files-confirm-delete">
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
              onClick={handleCancelDelete}
              disabled={isDeleting}
            >
              Cancel
            </button>
          </div>
        ) : (
          <>
            <button
              type="button"
              className="evidence-files-view-btn"
              onClick={() => onView(file.id)}
              disabled={isLoadingPreview}
            >
              {isLoadingPreview ? '…' : 'View'}
            </button>
            {file.download_url && (
              <button
                type="button"
                className="evidence-files-download-btn"
                onClick={handleDownload}
                title={`Download ${file.filename}`}
                aria-label={`Download ${file.filename}`}
              >
                Download
              </button>
            )}
            {canDelete !== false && (
              <button
                type="button"
                className="evidence-files-delete-btn"
                onClick={handleDeleteClick}
                title={`Delete ${file.filename}`}
                aria-label={`Delete ${file.filename}`}
                disabled={isDeleting}
              >
                Delete
              </button>
            )}
            {/* M4 (#574): when ``VITE_ENABLE_PER_WINDOW_REVIEW=true`` the
                per-file Approve/Reject buttons hide — review happens at the
                window level via ``WindowReviewPanel``. The review badge above
                stays visible for historical context. */}
            {!PER_WINDOW_REVIEW_ENABLED && canReview && onReview && file.review_status !== 'approved' && (
              <button
                type="button"
                className="evidence-files-approve-btn"
                onClick={() => onReview(file.id, 'approved')}
                disabled={isReviewing}
              >
                Approve
              </button>
            )}
            {!PER_WINDOW_REVIEW_ENABLED && canReview && onReview && file.review_status !== 'rejected' && (
              <button
                type="button"
                className="evidence-files-reject-btn"
                onClick={() => onReview(file.id, 'rejected')}
                disabled={isReviewing}
              >
                Reject
              </button>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ---- Main component ----

export function EvidenceFileList({
  orgId,
  evidenceId,
  refreshTrigger,
  canDelete,
  canReview,
  canAssess,
}: EvidenceFileListProps) {
  const [files, setFiles] = useState<EvidenceFileResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [deletingIds, setDeletingIds] = useState<Set<string>>(new Set())
  const [reviewingIds, setReviewingIds] = useState<Set<string>>(new Set())
  const [previewFile, setPreviewFile] = useState<EvidenceFileResponse | null>(null)
  const [loadingPreviewId, setLoadingPreviewId] = useState<string | null>(null)
  const [assessments, setAssessments] = useState<Record<string, FileAssessmentSummary>>({}) // fileId -> verdict
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set())
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkMessage, setBulkMessage] = useState<string | null>(null)

  // Track the last fetch key (trigger + evidenceId) to avoid re-fetch loops while
  // still re-fetching when the evidence item changes without a trigger increment.
  const lastFetchedKey = useRef<string | null>(null)

  const fetchFiles = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await listEvidenceFiles(evidenceId, orgId)
      setFiles(result.files)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load files'
      setError(message)
    } finally {
      setLoading(false)
    }
  }, [orgId, evidenceId])

  // Fetch on mount, when refreshTrigger increments, or when evidenceId changes.
  // Key combines both so switching evidence items always triggers a fresh fetch.
  useEffect(() => {
    const fetchKey = `${refreshTrigger}:${evidenceId}`
    if (lastFetchedKey.current === fetchKey) return
    lastFetchedKey.current = fetchKey
    fetchFiles()
  }, [refreshTrigger, evidenceId, fetchFiles])

  // Fetch AI assessment status for each file (non-blocking)
  useEffect(() => {
    if (files.length === 0) return
    let cancelled = false
    Promise.all(
      files.map(f =>
        getAssessment(orgId, evidenceId, f.id)
          .then(r => ({
            fileId: f.id,
            status: r?.status ?? null,
            // Carried alongside the status because the chip's wording turns on
            // it: without the decision, every verdict in the list would read as
            // settled regardless of whether anyone had looked at it.
            reviewDecision: r?.review_decision ?? null,
          }))
          .catch(() => ({ fileId: f.id, status: null, reviewDecision: null }))
      )
    ).then(results => {
      if (cancelled) return
      const map: Record<string, FileAssessmentSummary> = {}
      for (const r of results) {
        if (r.status) map[r.fileId] = { status: r.status, reviewDecision: r.reviewDecision }
      }
      setAssessments(map)
    })
    return () => { cancelled = true }
  }, [files, orgId, evidenceId])

  // Clear a stale selection when the evidence item changes — the ids would
  // refer to files that are no longer on screen.
  useEffect(() => {
    setSelectedIds(new Set())
    setBulkMessage(null)
  }, [evidenceId])

  const selectableIds = files.map(f => f.id)
  const allSelected = selectableIds.length > 0 && selectableIds.every(id => selectedIds.has(id))
  const selectedCount = selectedIds.size

  const toggleSelected = useCallback((fileId: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(fileId)) next.delete(fileId)
      else next.add(fileId)
      return next
    })
  }, [])

  const toggleSelectAll = useCallback(() => {
    setSelectedIds(prev => {
      const ids = files.map(f => f.id)
      if (ids.every(id => prev.has(id))) return new Set<string>()
      return new Set<string>(ids)
    })
  }, [files])

  const handleBulkAssess = useCallback(async () => {
    // Slice here, not server-side-and-hope. The server takes the first 50 and
    // drops the rest without complaint; the user is told which 50 they got.
    const ids = files.map(f => f.id).filter(id => selectedIds.has(id))
    const batch = ids.slice(0, BULK_ASSESS_MAX)
    const dropped = ids.length - batch.length

    setBulkBusy(true)
    setBulkMessage(null)
    try {
      const result = await bulkAssess(orgId, { evidence_id: evidenceId, file_ids: batch })
      const queued = `Queued ${result.queued} file${result.queued === 1 ? '' : 's'} for assessment.`
      setBulkMessage(
        dropped > 0
          ? `${queued} ${dropped} more were not sent — a maximum of ${BULK_ASSESS_MAX} files can be `
            + 'queued at once. Select the rest and run it again.'
          : queued
      )
      setSelectedIds(new Set())
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to queue assessments'
      setBulkMessage(`Could not queue assessments: ${message}`)
    } finally {
      setBulkBusy(false)
    }
  }, [orgId, evidenceId, files, selectedIds])

  const handleDelete = useCallback(async (fileId: string) => {
    setDeletingIds(prev => new Set(prev).add(fileId))
    try {
      await deleteEvidenceFile(evidenceId, fileId, orgId)
      setFiles(prev => prev.filter(f => f.id !== fileId))
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to delete file'
      setError(message)
    } finally {
      setDeletingIds(prev => {
        const next = new Set(prev)
        next.delete(fileId)
        return next
      })
    }
  }, [orgId, evidenceId])

  const handleReview = useCallback(async (fileId: string, reviewStatus: string) => {
    setReviewingIds(prev => new Set(prev).add(fileId))
    try {
      const updated = await reviewEvidenceFile(orgId, evidenceId, fileId, { review_status: reviewStatus })
      setFiles(prev => prev.map(f => f.id === fileId ? updated : f))
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to review file'
      setError(message)
    } finally {
      setReviewingIds(prev => {
        const next = new Set(prev)
        next.delete(fileId)
        return next
      })
    }
  }, [orgId, evidenceId])

  // Re-fetch list to get a fresh pre-signed URL, then open the preview modal
  const handleOpenPreview = useCallback(async (fileId: string) => {
    setLoadingPreviewId(fileId)
    try {
      const result = await listEvidenceFiles(evidenceId, orgId)
      setFiles(result.files)
      const freshFile = result.files.find(f => f.id === fileId)
      if (freshFile) setPreviewFile(freshFile)
    } finally {
      setLoadingPreviewId(null)
    }
  }, [evidenceId, orgId])

  // Download via signed URL
  const handleDownloadFromModal = useCallback((fileId: string) => {
    const file = files.find(f => f.id === fileId)
    if (file?.download_url) {
      window.open(file.download_url + '&disposition=attachment', '_blank', 'noopener,noreferrer')
    }
  }, [files])

  // Call existing delete handler, then close modal
  const handleDeleteFromModal = useCallback(async (fileId: string) => {
    await handleDelete(fileId)
    setPreviewFile(null)
  }, [handleDelete])

  const handleClosePreview = useCallback(() => {
    setPreviewFile(null)
  }, [])

  const [expanded, setExpanded] = useState(true)
  useEffect(() => { setExpanded(true) }, [evidenceId])
  const isCollapsible = !loading && !error && files.length > 0

  // ---- Render ----

  return (
    <div className="evidence-files-root">
      {isCollapsible ? (
        <button
          type="button"
          className="evidence-files-header evidence-files-header--collapsible"
          onClick={() => setExpanded(prev => !prev)}
          aria-expanded={expanded}
          data-testid="evidence-files-header-toggle"
        >
          <span className="evidence-files-header-title">Uploaded Files</span>
          <span className="evidence-files-count">{files.length}</span>
          <span className="evidence-files-collapse-indicator" aria-hidden="true">
            {expanded ? '▼' : '▶'}
          </span>
        </button>
      ) : (
        <div className="evidence-files-header">
          <span className="evidence-files-header-title">Uploaded Files</span>
        </div>
      )}

      <div
        className="evidence-files-list"
        hidden={isCollapsible && !expanded}
      >
        {/* Bulk assess bar — only for a user the backend would actually let
            queue a run, and only when there is something to select. */}
        {canAssess && !loading && !error && files.length > 0 && (
          <div className="evidence-files-bulk-bar">
            <label className="evidence-files-bulk-select-all">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={toggleSelectAll}
                disabled={bulkBusy}
                aria-label={allSelected ? 'Clear selection' : 'Select all files'}
              />
              <span>
                {allSelected
                  ? `All ${files.length} selected`
                  : `Select all ${files.length}`}
              </span>
            </label>
            {selectedCount > 0 && (
              <>
                <span className="evidence-files-bulk-count">
                  {selectedCount} selected
                </span>
                <button
                  type="button"
                  className="evidence-files-bulk-assess-btn"
                  onClick={handleBulkAssess}
                  disabled={bulkBusy}
                >
                  {bulkBusy ? 'Queueing…' : `Assess selected (${Math.min(selectedCount, BULK_ASSESS_MAX)})`}
                </button>
              </>
            )}
            {selectedCount > BULK_ASSESS_MAX && (
              <span className="evidence-files-bulk-cap-note">
                Only the first {BULK_ASSESS_MAX} will be queued.
              </span>
            )}
          </div>
        )}

        {bulkMessage && (
          <div className="evidence-files-bulk-message" role="status">
            {bulkMessage}
          </div>
        )}

        {/* Loading skeletons */}
        {loading && (
          <>
            <FileRowSkeleton />
            <FileRowSkeleton />
            <FileRowSkeleton />
          </>
        )}

        {/* Error state */}
        {!loading && error && (
          <div className="evidence-files-error">
            <span className="evidence-files-error-message">{error}</span>
            <button
              type="button"
              className="evidence-files-retry-btn"
              onClick={() => {
                // Force re-fetch by resetting our sentinel so the effect fires again
                lastFetchedKey.current = null
                fetchFiles()
              }}
            >
              Retry
            </button>
          </div>
        )}

        {/* Empty state */}
        {!loading && !error && files.length === 0 && (
          <div className="evidence-files-empty">
            <span className="evidence-files-empty-icon" aria-hidden="true">
              {'\uD83D\uDCC2'}
            </span>
            <span className="evidence-files-empty-label">No evidence files uploaded yet</span>
          </div>
        )}

        {/* File rows */}
        {!loading && !error && files.length > 0 && (
          files.map(file => (
            <FileRow
              key={file.id}
              file={file}
              onDelete={handleDelete}
              onReview={canReview ? handleReview : undefined}
              isDeleting={deletingIds.has(file.id)}
              isReviewing={reviewingIds.has(file.id)}
              onView={handleOpenPreview}
              isLoadingPreview={loadingPreviewId === file.id}
              canDelete={canDelete}
              canReview={canReview}
              assessment={assessments[file.id] ?? null}
              selectable={canAssess}
              selected={selectedIds.has(file.id)}
              onToggleSelected={toggleSelected}
            />
          ))
        )}
      </div>

      {/* Preview modal */}
      {previewFile && (
        <EvidenceFilePreviewModal
          file={previewFile}
          orgId={orgId}
          evidenceId={evidenceId}
          onClose={handleClosePreview}
          onDownload={handleDownloadFromModal}
          onDelete={handleDeleteFromModal}
          isDeleting={deletingIds.has(previewFile.id)}
        />
      )}
    </div>
  )
}

export default EvidenceFileList
