import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'react-hot-toast'
import {
  listCdmDocuments,
  uploadCdmDocument,
  getCdmJobStatus,
  deleteCdmDocument,
  reingestCdmDocuments,
  listCdmControlProposals,
  type CDMDocument,
  type CDMIngestStatus,
} from '../data/apiClient'
import { useCdmComputeRun } from '../hooks/useCdmComputeRun'
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

// Statuses that mean a worker is (or should be) actively on the row.
const IN_FLIGHT_STATUSES: ReadonlySet<CDMIngestStatus> = new Set([
  'pending',
  'parsing',
  'indexing',
])

// 'parsed' is the terminal state whenever knowledge-base indexing is
// disabled (the self-hosted default), and a moments-long transient when it
// is enabled. The chip therefore answers "can I map this yet?" — readiness —
// and machine activity is a subordinate line, so the chip is truthful under
// both configurations. 'indexing_failed' is deliberately READY: parsing
// completed before indexing was attempted, so the document is fully
// mappable — only knowledge-base search is degraded.
const READY_STATUSES: ReadonlySet<CDMIngestStatus> = new Set([
  'parsed',
  'indexing',
  'indexed',
  'indexing_failed',
])

// After a poll sees 'parsed', keep watching a few ticks in case indexing is
// enabled and the row is about to transition; then rest at Ready to map.
const PARSED_GRACE_TICKS = 5
// Hard ceiling per poll run — never spin forever. On expiry the UI says
// "status unknown" and offers a manual re-check instead of silently lying.
const MAX_POLL_TICKS = 40
const SLOW_AFTER_TICKS = 10
const POLL_INTERVAL_MS = 3000
const SLOW_POLL_INTERVAL_MS = 10000

function statusLabel(status: CDMIngestStatus): string {
  if (READY_STATUSES.has(status)) return 'Ready to map'
  switch (status) {
    case 'pending':
      return 'Queued'
    case 'parsing':
      return 'Not ready'
    case 'failed':
      return 'Extraction failed'
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
      return 'Text extracted — included in the next mapping run. Knowledge-base indexing runs only when LightRAG is enabled.'
    case 'indexing':
      return 'Ready to map. Knowledge-base indexing is still running.'
    case 'indexed':
      return 'Text extracted and indexed into the knowledge base.'
    case 'failed':
      return 'Nothing was extracted. Retry, or fix the source file and re-upload.'
    case 'indexing_failed':
      return 'Mapping works — the text was extracted. Only knowledge-base search is affected.'
    default:
      return status
  }
}

// Machine activity / advisory microcopy rendered under the readiness chip.
function statusActivity(doc: CDMDocument): { text: string; warning: boolean } | null {
  if (doc.is_stale) {
    return { text: 'Status unknown — processing may have stalled', warning: true }
  }
  switch (doc.ingest_status) {
    case 'parsing':
      return { text: 'Extracting text…', warning: false }
    case 'indexing':
      return { text: 'Indexing…', warning: false }
    case 'indexing_failed':
      return { text: 'Indexing failed — search may be incomplete', warning: true }
    default:
      return null
  }
}

function statusBadgeClass(status: CDMIngestStatus): string {
  if (READY_STATUSES.has(status)) return 'cdm-badge cdm-badge-success'
  if (status === 'failed') return 'cdm-badge cdm-badge-error'
  if (status === 'pending') return 'cdm-badge cdm-badge-neutral'
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

function formatElapsed(from: Date, to: Date): string {
  const seconds = Math.max(0, Math.floor((to.getTime() - from.getTime()) / 1000))
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}

export default function CDMWorkspace({ organizationId }: CDMWorkspaceProps) {
  const [activeTab, setActiveTab] = useState<CDMTab>('documents')
  const [documents, setDocuments] = useState<CDMDocument[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<{ done: number; total: number } | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [retryingId, setRetryingId] = useState<string | null>(null)
  const [pollExhausted, setPollExhausted] = useState<Date | null>(null)
  // Bumped by "Re-check" (and by retry) to re-arm an exhausted poll run.
  const [pollEpoch, setPollEpoch] = useState(0)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  // Grace-tick bookkeeping for rows recently seen at 'parsed' — lives in a
  // ref so it survives poll-effect re-runs when the in-flight set changes.
  const graceRef = useRef<Map<string, number>>(new Map())

  // ── Mapping run + proposal count: workspace-level, so the action bar and
  // tab badge are visible from either tab. The journey is upload → ready →
  // run → review, and the run must be startable where readiness is shown.
  const [proposedTotal, setProposedTotal] = useState<number | null>(null)
  const [runRefreshKey, setRunRefreshKey] = useState(0)
  const [lastRunOutcome, setLastRunOutcome] = useState<'success' | 'failure' | null>(null)

  const fetchProposedCount = useCallback(async () => {
    try {
      const response = await listCdmControlProposals(organizationId, {
        status: 'proposed',
        limit: 1,
        offset: 0,
      })
      setProposedTotal(response.total)
    } catch {
      /* badge is best-effort — leave the previous value */
    }
  }, [organizationId])

  useEffect(() => {
    void fetchProposedCount()
  }, [fetchProposedCount])

  const computeRun = useCdmComputeRun(organizationId, (successful) => {
    setLastRunOutcome(successful ? 'success' : 'failure')
    setRunRefreshKey((k) => k + 1)
    void fetchProposedCount()
  })

  // Ticker so the "running for Xs" line moves while a run is active.
  const [nowTick, setNowTick] = useState(() => new Date())
  useEffect(() => {
    if (!computeRun.running) return
    const handle = window.setInterval(() => setNowTick(new Date()), 1000)
    return () => window.clearInterval(handle)
  }, [computeRun.running])

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

  // Current documents, readable from inside the poll loop without making the
  // effect depend on (and restart on) every table update.
  const documentsRef = useRef(documents)
  useEffect(() => {
    documentsRef.current = documents
  }, [documents])

  // Key the poll effect on the SET of in-flight rows, not the array identity:
  // status updates that keep the same set must not tear down the timer.
  const pollSetKey = useMemo(
    () =>
      documents
        .filter(
          (d) =>
            IN_FLIGHT_STATUSES.has(d.ingest_status) ||
            (d.ingest_status === 'parsed' && (graceRef.current.get(d.id) ?? 0) > 0),
        )
        .map((d) => d.id)
        .sort()
        .join(','),
    [documents],
  )

  // Poll in-flight rows with a self-scheduling timeout chain: immediate
  // first tick, 3s cadence backing off to 10s, and a hard ceiling that ends
  // in an explicit "status unknown — re-check" state rather than an
  // unbounded spinner. Rows seen at 'parsed' get a few grace ticks in case
  // knowledge-base indexing is enabled and about to take over.
  useEffect(() => {
    if (pollSetKey === '') return

    let cancelled = false
    let timer: number | undefined
    let ticks = 0

    const currentPollIds = (): string[] => {
      const ids: string[] = []
      for (const d of documentsRef.current) {
        if (IN_FLIGHT_STATUSES.has(d.ingest_status)) ids.push(d.id)
        else if (d.ingest_status === 'parsed' && (graceRef.current.get(d.id) ?? 0) > 0) {
          ids.push(d.id)
        }
      }
      return ids
    }

    const tick = async () => {
      ticks += 1
      const ids = currentPollIds()
      if (ids.length === 0) return

      const updates = await Promise.all(
        ids.map(async (id) => {
          try {
            return await getCdmJobStatus(organizationId, id)
          } catch {
            return null
          }
        }),
      )
      if (cancelled) return

      for (const u of updates) {
        if (!u) continue
        if (u.ingest_status === 'parsed') {
          const g = graceRef.current.get(u.document_id)
          graceRef.current.set(
            u.document_id,
            g === undefined ? PARSED_GRACE_TICKS : Math.max(0, g - 1),
          )
        } else {
          graceRef.current.delete(u.document_id)
        }
      }

      setDocuments((prev) => {
        let changed = false
        const next = prev.map((doc) => {
          const u = updates.find((x) => x && x.document_id === doc.id)
          if (!u) return doc
          if (
            u.ingest_status === doc.ingest_status &&
            u.ingest_error === doc.ingest_error &&
            u.word_count === doc.word_count
          ) {
            return doc
          }
          changed = true
          return {
            ...doc,
            ingest_status: u.ingest_status,
            ingest_error: u.ingest_error,
            word_count: u.word_count,
          }
        })
        return changed ? next : prev
      })
      schedule()
    }

    const schedule = () => {
      if (cancelled) return
      if (currentPollIds().length === 0) return
      if (ticks >= MAX_POLL_TICKS) {
        setPollExhausted(new Date())
        return
      }
      const delay =
        ticks === 0 ? 0 : ticks >= SLOW_AFTER_TICKS ? SLOW_POLL_INTERVAL_MS : POLL_INTERVAL_MS
      timer = window.setTimeout(() => void tick(), delay)
    }

    setPollExhausted(null)
    schedule()
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [pollSetKey, pollEpoch, organizationId])

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
            ingest_started_at: null,
            is_stale: false,
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

  const handleRetry = useCallback(
    async (doc: CDMDocument) => {
      setRetryingId(doc.id)
      try {
        await reingestCdmDocuments(organizationId, [doc.id])
        toast.success(`Re-processing ${doc.original_filename}`)
        graceRef.current.delete(doc.id)
        setDocuments((prev) =>
          prev.map((d) =>
            d.id === doc.id
              ? { ...d, ingest_status: 'pending', ingest_error: null, is_stale: false }
              : d,
          ),
        )
        // Re-arm the poll with a fresh tick budget for the retried run.
        setPollEpoch((e) => e + 1)
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Retry failed'
        toast.error(message)
      } finally {
        setRetryingId(null)
      }
    },
    [organizationId],
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

  const readyCount = documents.filter((d) => READY_STATUSES.has(d.ingest_status)).length

  // One banner state at a time, in priority order: a live run beats a stale
  // outcome, an unknown run beats a remembered success.
  const runBarVariant = computeRun.running
    ? 'is-info'
    : computeRun.state === 'UNKNOWN'
      ? 'is-warning'
      : lastRunOutcome === 'failure'
        ? 'is-error'
        : lastRunOutcome === 'success'
          ? 'is-success'
          : ''

  return (
    <div className="cdm-workspace">
      <header className="cdm-workspace-header">
        <h1>Control Documents</h1>
        <p className="cdm-workspace-sub">
          Upload policy and procedure documents. We extract their text so the
          platform can propose mappings against your scoped controls.
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
          {proposedTotal ? (
            <span className="cdm-tab-count">{proposedTotal}</span>
          ) : null}
        </button>
      </div>

      {/* The mapping run is the bridge between the two tabs: upload here,
          review there. The bar lives outside the tab panels so a running
          run stays visible wherever the user is. */}
      <div className={`cdm-action-bar ${runBarVariant}`}>
        <div className="cdm-action-bar-text">
          {computeRun.running ? (
            <>
              <strong>Mapping run in progress</strong>
              <span>
                {computeRun.startedAt
                  ? `Started ${formatElapsed(computeRun.startedAt, nowTick)} ago. `
                  : ''}
                New proposals will appear in the Review queue when it finishes.
              </span>
            </>
          ) : computeRun.state === 'UNKNOWN' ? (
            <>
              <strong>Lost track of the last run</strong>
              <span>
                It may still be finishing in the background. Running again is
                safe — existing proposals are kept.
              </span>
            </>
          ) : lastRunOutcome === 'failure' ? (
            <>
              <strong>The last mapping run failed</strong>
              <span>
                No proposals were produced. Try again, or check the worker
                logs if it keeps failing.
              </span>
            </>
          ) : lastRunOutcome === 'success' ? (
            <>
              <strong>Mapping run complete</strong>
              <span>
                {proposedTotal
                  ? `${proposedTotal} proposal${proposedTotal === 1 ? '' : 's'} waiting for review.`
                  : 'No new proposals — your documents may already be fully mapped.'}
              </span>
            </>
          ) : readyCount > 0 ? (
            <>
              <strong>
                {readyCount} document{readyCount === 1 ? '' : 's'} ready to map
              </strong>
              <span>
                Run mapping to propose links between your documents and
                scoped controls
                {proposedTotal
                  ? `, or review the ${proposedTotal} pending proposal${proposedTotal === 1 ? '' : 's'}.`
                  : '.'}
              </span>
            </>
          ) : (
            <>
              <strong>No documents ready yet</strong>
              <span>Upload a document above to get started.</span>
            </>
          )}
        </div>
        <div className="cdm-action-bar-actions">
          {activeTab === 'documents' && proposedTotal && !computeRun.running ? (
            <button
              type="button"
              className="btn btn-outline"
              onClick={() => setActiveTab('review')}
            >
              Review proposals →
            </button>
          ) : null}
          <button
            type="button"
            className="btn btn-primary cdm-run-mapping"
            onClick={() => void computeRun.start()}
            disabled={computeRun.busy || computeRun.running || readyCount === 0}
          >
            {computeRun.busy
              ? 'Starting…'
              : computeRun.running
                ? 'Running…'
                : lastRunOutcome || computeRun.state === 'UNKNOWN'
                  ? 'Run mapping again'
                  : 'Run mapping'}
          </button>
        </div>
      </div>

      {activeTab === 'review' ? (
        <CDMReviewQueue
          organizationId={organizationId}
          runRefreshKey={runRefreshKey}
          onQueueMutated={() => void fetchProposedCount()}
        />
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

        {pollExhausted ? (
          <div className="cdm-poll-exhausted" role="status">
            Status unknown — some documents are still processing. Last checked{' '}
            {pollExhausted.toLocaleTimeString()}.{' '}
            <button
              type="button"
              className="btn-text"
              onClick={() => {
                setPollExhausted(null)
                setPollEpoch((e) => e + 1)
              }}
            >
              Re-check
            </button>
          </div>
        ) : null}

        {loading ? (
          <div className="cdm-loading">Loading documents…</div>
        ) : documents.length === 0 ? (
          <div className="cdm-empty">
            <p>No documents yet.</p>
            <p className="cdm-empty-hint">
              Upload your first control document above — once its text is
              extracted, you can run a mapping pass against your scoped
              controls from the Review queue.
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
                      {(() => {
                        const activity = statusActivity(doc)
                        if (!activity) return null
                        return (
                          <div
                            className={`cdm-status-activity ${activity.warning ? 'cdm-status-activity-warning' : ''}`}
                          >
                            {activity.text}
                          </div>
                        )
                      })()}
                    </td>
                    <td>{doc.word_count?.toLocaleString() ?? '—'}</td>
                    <td>{formatBytes(doc.size_bytes)}</td>
                    <td>{formatDate(doc.created_at)}</td>
                    <td>
                      {doc.ingest_status === 'failed' || doc.is_stale ? (
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={retryingId === doc.id}
                          onClick={() => void handleRetry(doc)}
                          title="Run ingest again against the already-uploaded file"
                        >
                          {retryingId === doc.id ? 'Retrying…' : 'Retry'}
                        </button>
                      ) : null}{' '}
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
