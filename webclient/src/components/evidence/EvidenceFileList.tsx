import { useState, useEffect, useRef, useCallback } from 'react'
import {
  listEvidenceFiles,
  deleteEvidenceFile,
  reviewEvidenceFile,
  getAssessment,
  type EvidenceFileResponse,
  type EvidenceAssessmentResponse,
} from '../../data/apiClient'
import { EvidenceFilePreviewModal } from './EvidenceFilePreviewModal'

// M4 (#574) — when the per-window review workflow is enabled, the per-file
// Approve/Reject buttons are hidden because reviews now happen at the window
// level (see ``WindowReviewPanel``). The row-level review badge stays visible
// for historical context. When the flag is unset (default), behaviour is
// unchanged — existing tests and existing per-file reviews keep working.
const PER_WINDOW_REVIEW_ENABLED =
  import.meta.env.VITE_ENABLE_PER_WINDOW_REVIEW === 'true'

// ---- Props ----

interface EvidenceFileListProps {
  orgId: string
  evidenceId: string
  refreshTrigger: number
  canDelete?: boolean
  canReview?: boolean
}

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

const AI_STATUS_CONFIG: Record<string, { label: string; className: string }> = {
  sufficient: { label: 'AI: Sufficient', className: 'ai-chip-sufficient' },
  partial: { label: 'AI: Partial', className: 'ai-chip-partial' },
  insufficient: { label: 'AI: Insufficient', className: 'ai-chip-insufficient' },
  pending: { label: 'AI: Assessing...', className: 'ai-chip-pending' },
  processing: { label: 'AI: Assessing...', className: 'ai-chip-pending' },
  error: { label: 'AI: Error', className: 'ai-chip-error' },
}

function AssessmentChip({ status }: { status: string | null }) {
  if (!status) return null
  const config = AI_STATUS_CONFIG[status]
  if (!config) return null
  return <span className={`ai-chip ${config.className}`}>{config.label}</span>
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
  assessmentStatus?: string | null
}

function FileRow({ file, onDelete, onReview, isDeleting, isReviewing, onView, isLoadingPreview, canDelete, canReview, assessmentStatus }: FileRowProps) {
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
          {file.review_status && (
            <>
              <span className="evidence-files-separator" aria-hidden="true">{'\u00B7'}</span>
              <ReviewStatusBadge status={file.review_status} />
            </>
          )}
          {assessmentStatus && (
            <>
              <span className="evidence-files-separator" aria-hidden="true">{'\u00B7'}</span>
              <AssessmentChip status={assessmentStatus} />
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
}: EvidenceFileListProps) {
  const [files, setFiles] = useState<EvidenceFileResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [deletingIds, setDeletingIds] = useState<Set<string>>(new Set())
  const [reviewingIds, setReviewingIds] = useState<Set<string>>(new Set())
  const [previewFile, setPreviewFile] = useState<EvidenceFileResponse | null>(null)
  const [loadingPreviewId, setLoadingPreviewId] = useState<string | null>(null)
  const [assessments, setAssessments] = useState<Record<string, string>>({}) // fileId -> status

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
          .then(r => ({ fileId: f.id, status: r?.status ?? null }))
          .catch(() => ({ fileId: f.id, status: null }))
      )
    ).then(results => {
      if (cancelled) return
      const map: Record<string, string> = {}
      for (const r of results) {
        if (r.status) map[r.fileId] = r.status
      }
      setAssessments(map)
    })
    return () => { cancelled = true }
  }, [files, orgId, evidenceId])

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
              assessmentStatus={assessments[file.id] ?? null}
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
