import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'react-hot-toast'
import {
  listCdmDocuments,
  uploadCdmDocument,
  getCdmJobStatus,
  deleteCdmDocument,
  type CDMDocument,
  type CDMIngestStatus,
} from '../data/apiClient'
import CDMReviewQueue from './CDMReviewQueue'

interface CDMWorkspaceProps {
  organizationId: string
}

type CDMTab = 'documents' | 'review'

const ACCEPTED_MIME_TYPES = new Set<string>([
  'text/plain',
  'text/markdown',
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
])

const ACCEPTED_EXTENSIONS = '.txt,.md,.pdf,.docx'

const TERMINAL_STATUSES: ReadonlySet<CDMIngestStatus> = new Set([
  'parsed',
  'indexed',
  'failed',
  'indexing_failed',
])

function isInFlight(status: CDMIngestStatus): boolean {
  return !TERMINAL_STATUSES.has(status)
}

function statusLabel(status: CDMIngestStatus): string {
  switch (status) {
    case 'pending':
      return 'Pending'
    case 'parsing':
      return 'Parsing'
    case 'parsed':
      return 'Indexing'
    case 'indexing':
      return 'Indexing'
    case 'indexed':
      return 'Indexed'
    case 'failed':
      return 'Failed'
    case 'indexing_failed':
      return 'Indexing failed'
    default:
      return status
  }
}

function statusTitle(status: CDMIngestStatus): string {
  switch (status) {
    case 'pending':
      return 'Queued — waiting for a worker to pick it up.'
    case 'parsing':
      return 'Extracting text from the uploaded file.'
    case 'parsed':
      return 'Text extracted — indexing into the knowledge base.'
    case 'indexing':
      return 'Indexing into the knowledge base.'
    case 'indexed':
      return 'Ready — the document is part of your knowledge base and eligible for mapping.'
    case 'failed':
      return 'Text extraction failed. Delete and re-upload after fixing the source file.'
    case 'indexing_failed':
      return 'Knowledge-base indexing failed. Delete and re-upload to retry.'
    default:
      return status
  }
}

function statusBadgeClass(status: CDMIngestStatus): string {
  if (status === 'indexed') return 'cdm-badge cdm-badge-success'
  if (status === 'failed' || status === 'indexing_failed') return 'cdm-badge cdm-badge-error'
  return 'cdm-badge cdm-badge-progress'
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

export default function CDMWorkspace({ organizationId }: CDMWorkspaceProps) {
  const [activeTab, setActiveTab] = useState<CDMTab>('documents')
  const [documents, setDocuments] = useState<CDMDocument[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<{ done: number; total: number } | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const refresh = useCallback(async () => {
    try {
      const response = await listCdmDocuments(organizationId)
      setDocuments(response.documents)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load documents'
      toast.error(message)
    } finally {
      setLoading(false)
    }
  }, [organizationId])

  useEffect(() => {
    setLoading(true)
    void refresh()
  }, [refresh])

  const inFlightIds = useMemo(
    () => documents.filter((d) => isInFlight(d.ingest_status)).map((d) => d.id),
    [documents],
  )

  // Poll only in-flight rows. Stops automatically when nothing is in flight
  // (D-2). Each tick fans out one GET per pending document and merges
  // results back into the table.
  useEffect(() => {
    if (inFlightIds.length === 0) return

    let cancelled = false
    const tick = async () => {
      try {
        const updates = await Promise.all(
          inFlightIds.map(async (id) => {
            try {
              return await getCdmJobStatus(organizationId, id)
            } catch {
              return null
            }
          }),
        )
        if (cancelled) return
        setDocuments((prev) =>
          prev.map((doc) => {
            const u = updates.find((x) => x && x.document_id === doc.id)
            if (!u) return doc
            return {
              ...doc,
              ingest_status: u.ingest_status,
              ingest_error: u.ingest_error,
              word_count: u.word_count,
            }
          }),
        )
      } catch {
        /* swallow — toast on next manual refresh */
      }
    }

    const handle = window.setInterval(tick, 3000)
    return () => {
      cancelled = true
      window.clearInterval(handle)
    }
  }, [inFlightIds, organizationId])

  const uploadOne = useCallback(
    async (file: File): Promise<boolean> => {
      if (file.size === 0) {
        toast.error(`Skipped ${file.name}: empty file`)
        return false
      }
      const mime = file.type || ''
      const extOk = ACCEPTED_EXTENSIONS.split(',').some((e) =>
        file.name.toLowerCase().endsWith(e),
      )
      if (mime && !ACCEPTED_MIME_TYPES.has(mime) && !extOk) {
        toast.error(`Skipped ${file.name}: unsupported type (${mime || 'unknown'})`)
        return false
      }

      try {
        const result = await uploadCdmDocument(organizationId, file)
        setDocuments((prev) => [
          {
            id: result.document_id,
            organization_id: organizationId,
            original_filename: file.name,
            mime_type: mime || 'application/octet-stream',
            size_bytes: file.size,
            sha256: '',
            ingest_status: result.ingest_status,
            ingest_error: null,
            word_count: null,
            upload_user_id: null,
            kb_revision_at_ingest: null,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
          ...prev,
        ])
        return true
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Upload failed'
        toast.error(`${file.name}: ${message}`)
        return false
      }
    },
    [organizationId],
  )

  const handleFiles = useCallback(
    async (files: File[]) => {
      const list = files.filter(Boolean)
      if (list.length === 0) return

      setUploading(true)
      setUploadProgress({ done: 0, total: list.length })
      let okCount = 0
      try {
        for (let i = 0; i < list.length; i += 1) {
          const file = list[i]
          const ok = await uploadOne(file)
          if (ok) okCount += 1
          setUploadProgress({ done: i + 1, total: list.length })
        }
        if (okCount > 0) {
          toast.success(
            list.length === 1
              ? `Uploaded ${list[0].name}`
              : `Uploaded ${okCount} of ${list.length} files`,
          )
        }
        void refresh()
      } finally {
        setUploading(false)
        setUploadProgress(null)
        if (fileInputRef.current) fileInputRef.current.value = ''
      }
    },
    [uploadOne, refresh],
  )

  const onDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault()
      setDragOver(false)
      const files = Array.from(e.dataTransfer.files ?? [])
      if (files.length > 0) void handleFiles(files)
    },
    [handleFiles],
  )

  const onFilePicker = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files ?? [])
      if (files.length > 0) void handleFiles(files)
    },
    [handleFiles],
  )

  const handleDelete = useCallback(
    async (doc: CDMDocument) => {
      const confirmed = window.confirm(
        `Delete "${doc.original_filename}"?\n\n` +
          'This will remove the document from your knowledge base and ' +
          'cascade-remove any proposed or accepted mappings that reference ' +
          'it. The action is logged in the audit trail and cannot be undone.',
      )
      if (!confirmed) return

      // Optimistic removal — restore on failure so the row reappears.
      const previous = documents
      setDeletingId(doc.id)
      setDocuments((prev) => prev.filter((d) => d.id !== doc.id))
      try {
        await deleteCdmDocument(organizationId, doc.id)
        toast.success(`Deleted ${doc.original_filename}`)
        void refresh()
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Delete failed'
        toast.error(message)
        setDocuments(previous)
      } finally {
        setDeletingId(null)
      }
    },
    [documents, organizationId, refresh],
  )

  return (
    <div className="cdm-workspace">
      <header className="cdm-workspace-header">
        <h1>Control Documents</h1>
        <p className="cdm-workspace-sub">
          Upload policy and procedure documents. We index them into your
          knowledge base so the platform can propose mappings against your
          scoped controls.
        </p>
      </header>

      <div className="cdm-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'documents'}
          className={`cdm-tab ${activeTab === 'documents' ? 'is-active' : ''}`}
          onClick={() => setActiveTab('documents')}
        >
          Documents
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'review'}
          className={`cdm-tab ${activeTab === 'review' ? 'is-active' : ''}`}
          onClick={() => setActiveTab('review')}
        >
          Review queue
        </button>
      </div>

      {activeTab === 'review' ? (
        <CDMReviewQueue organizationId={organizationId} />
      ) : (
        <>
      <section
        className={`cdm-upload-zone ${dragOver ? 'is-drag-over' : ''} ${uploading ? 'is-uploading' : ''}`}
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
      >
        <div className="cdm-upload-zone-inner">
          <div className="cdm-upload-icon" aria-hidden="true">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
          </div>
          <p className="cdm-upload-headline">
            {uploading
              ? uploadProgress && uploadProgress.total > 1
                ? `Uploading ${uploadProgress.done} of ${uploadProgress.total}…`
                : 'Uploading…'
              : 'Drag files here, or'}
          </p>
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPTED_EXTENSIONS}
            multiple
            onChange={onFilePicker}
            disabled={uploading}
            style={{ display: 'none' }}
          />
          <button
            type="button"
            className="btn-primary"
            disabled={uploading}
            onClick={() => fileInputRef.current?.click()}
          >
            {uploading ? 'Uploading…' : 'Choose files'}
          </button>
          <p className="cdm-upload-hint">Accepted: .txt, .md, .pdf, .docx — multiple files supported</p>
        </div>
      </section>

      <section className="cdm-documents-section">
        <div className="cdm-documents-header">
          <h2>Uploaded documents</h2>
          <button type="button" className="btn-secondary" onClick={() => void refresh()}>
            Refresh
          </button>
        </div>

        {loading ? (
          <div className="cdm-loading">Loading documents…</div>
        ) : documents.length === 0 ? (
          <div className="cdm-empty">
            <p>No documents yet.</p>
            <p className="cdm-empty-hint">
              Upload your first control document above — once it is indexed, the
              platform will propose mappings against your scoped controls.
            </p>
          </div>
        ) : (
          <div className="cdm-documents-table-wrap">
            <table className="cdm-documents-table">
              <thead>
                <tr>
                  <th>Filename</th>
                  <th>Status</th>
                  <th>Words</th>
                  <th>Size</th>
                  <th>Uploaded</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {documents.map((doc) => (
                  <tr key={doc.id}>
                    <td>
                      <div className="cdm-filename">{doc.original_filename}</div>
                      {doc.ingest_error ? (
                        <div className="cdm-row-error">{doc.ingest_error}</div>
                      ) : null}
                    </td>
                    <td>
                      <span
                        className={statusBadgeClass(doc.ingest_status)}
                        title={statusTitle(doc.ingest_status)}
                      >
                        {statusLabel(doc.ingest_status)}
                      </span>
                    </td>
                    <td>{doc.word_count?.toLocaleString() ?? '—'}</td>
                    <td>{formatBytes(doc.size_bytes)}</td>
                    <td>{formatDate(doc.created_at)}</td>
                    <td>
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={deletingId === doc.id}
                        onClick={() => void handleDelete(doc)}
                        title="Remove this document and all of its mappings"
                      >
                        {deletingId === doc.id ? 'Deleting…' : 'Delete'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
        </>
      )}
    </div>
  )
}
