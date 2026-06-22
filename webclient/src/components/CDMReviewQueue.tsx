import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'react-hot-toast'
import {
  listCdmMappings,
  acceptCdmMapping,
  dismissCdmMapping,
  bulkAcceptCdmMappings,
  bulkDismissCdmMappings,
  triggerCdmComputeMappings,
  getCdmComputeMappingsStatus,
  fetchScopedControlsPage,
  type CDMMapping,
  type CDMMappingBulkResponse,
  type CDMMappingStatus,
} from '../data/apiClient'

interface CDMReviewQueueProps {
  organizationId: string
}

const PAGE_SIZE = 25
const EXCERPT_PREVIEW_CHARS = 150
const EXCERPT_FULL_CHARS = 600

const STATUS_OPTIONS: { value: CDMMappingStatus; label: string }[] = [
  { value: 'proposed', label: 'Proposed' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'dismissed', label: 'Dismissed' },
  { value: 'stale', label: 'Stale' },
]

function formatScore(score: number): string {
  return score.toFixed(2)
}

function formatRange(start: number, end: number): string {
  const len = end - start
  return `${start.toLocaleString()}–${end.toLocaleString()} (${len.toLocaleString()} bytes)`
}

interface CatalogEntry {
  control_name: string
  control_description: string
}

export default function CDMReviewQueue({ organizationId }: CDMReviewQueueProps) {
  const [statusFilter, setStatusFilter] = useState<CDMMappingStatus>('proposed')
  const [offset, setOffset] = useState(0)
  const [mappings, setMappings] = useState<CDMMapping[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [dismissingId, setDismissingId] = useState<string | null>(null)
  const [dismissReason, setDismissReason] = useState('')
  const [computeTaskId, setComputeTaskId] = useState<string | null>(null)
  const [computeState, setComputeState] = useState<string | null>(null)
  const [computeBusy, setComputeBusy] = useState(false)
  const [catalog, setCatalog] = useState<Record<string, CatalogEntry>>({})
  const [expandedExcerptIds, setExpandedExcerptIds] = useState<Record<string, boolean>>({})
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set())
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkDismissReason, setBulkDismissReason] = useState('')
  const refreshRef = useRef<() => Promise<void>>()

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const response = await listCdmMappings(organizationId, {
        status: statusFilter,
        limit: PAGE_SIZE,
        offset,
      })
      setMappings(response.mappings)
      setTotal(response.total)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load mappings'
      toast.error(message)
    } finally {
      setLoading(false)
    }
  }, [organizationId, statusFilter, offset])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    refreshRef.current = refresh
  }, [refresh])

  // Reset to first page when the filter changes.
  useEffect(() => {
    setOffset(0)
  }, [statusFilter])

  // Clear bulk selection whenever the visible page changes (filter, paging,
  // or successful single-action refresh re-fetches the list).
  useEffect(() => {
    setSelectedIds(new Set())
  }, [statusFilter, offset, organizationId])

  // Catalog map keyed by scf_id, populated once per page of mappings so each
  // review card can show SCF control language alongside the document excerpt.
  // Misses (mapping.scf_id not in catalog) render the bare SCF ID — no fatal.
  useEffect(() => {
    const wanted = new Set<string>()
    for (const m of mappings) {
      if (m.scf_id && !catalog[m.scf_id]) wanted.add(m.scf_id)
    }
    if (wanted.size === 0) return

    let cancelled = false
    const loadCatalog = async () => {
      try {
        const resp = await fetchScopedControlsPage(
          { limit: 500, scope_status: 'all' },
          organizationId,
        )
        if (cancelled) return
        setCatalog((prev) => {
          const next = { ...prev }
          for (const c of resp.controls) {
            if (!next[c.scf_id]) {
              next[c.scf_id] = {
                control_name: c.control_name,
                control_description: c.control_description,
              }
            }
          }
          return next
        })
      } catch {
        /* swallow — cards fall back to SCF ID only */
      }
    }
    void loadCatalog()
    return () => {
      cancelled = true
    }
  }, [mappings, organizationId, catalog])

  const handleRunMapping = useCallback(async () => {
    setComputeBusy(true)
    try {
      const resp = await triggerCdmComputeMappings(organizationId)
      setComputeTaskId(resp.task_id)
      setComputeState('PENDING')
      toast.success(
        resp.idempotent_existing
          ? 'A mapping run is already in progress — tracking it now.'
          : 'Mapping run started.',
      )
    } catch (err) {
      const raw = err instanceof Error ? err.message : 'Failed to start mapping run'
      if (raw.includes('proposed-mappings cap reached')) {
        toast.error(
          `${raw}. Accept or dismiss enough mappings in the queue to drop below the cap, then try again.`,
          { duration: 8000 },
        )
      } else if (raw.includes('compute lock contention')) {
        toast.error(
          'A previous mapping run is still finishing. Wait a minute or two and try again.',
          { duration: 6000 },
        )
      } else {
        toast.error(raw)
      }
    } finally {
      setComputeBusy(false)
    }
  }, [organizationId])

  // Poll the mapping task while it is in flight. Terminal states (SUCCESS,
  // FAILURE, REVOKED) stop the poll loop and trigger a queue refresh.
  useEffect(() => {
    if (!computeTaskId) return
    if (computeState === 'SUCCESS' || computeState === 'FAILURE' || computeState === 'REVOKED') {
      return
    }

    let cancelled = false
    const tick = async () => {
      try {
        const resp = await getCdmComputeMappingsStatus(organizationId, computeTaskId)
        if (cancelled) return
        setComputeState(resp.state)
        if (resp.ready) {
          if (resp.successful) {
            toast.success('Mapping run complete — reloading queue.')
            await refreshRef.current?.()
          } else {
            toast.error('Mapping run failed — see worker logs.')
          }
        }
      } catch {
        /* swallow — next tick retries */
      }
    }
    void tick()
    const handle = window.setInterval(tick, 3000)
    return () => {
      cancelled = true
      window.clearInterval(handle)
    }
  }, [computeTaskId, computeState, organizationId])

  const handleAccept = useCallback(
    async (mapping: CDMMapping) => {
      setBusyId(mapping.id)
      try {
        await acceptCdmMapping(organizationId, mapping.id)
        toast.success(`Accepted mapping for ${mapping.scf_id ?? 'control'}`)
        await refresh()
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Accept failed'
        toast.error(message)
      } finally {
        setBusyId(null)
      }
    },
    [organizationId, refresh],
  )

  const handleDismissSubmit = useCallback(
    async (mapping: CDMMapping) => {
      setBusyId(mapping.id)
      try {
        await dismissCdmMapping(organizationId, mapping.id, dismissReason || null)
        toast.success(`Dismissed mapping for ${mapping.scf_id ?? 'control'}`)
        setDismissingId(null)
        setDismissReason('')
        await refresh()
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Dismiss failed'
        toast.error(message)
      } finally {
        setBusyId(null)
      }
    },
    [organizationId, dismissReason, refresh],
  )

  const visibleProposedIds = useMemo(
    () => mappings.filter((m) => m.status === 'proposed').map((m) => m.id),
    [mappings],
  )
  const allVisibleSelected =
    visibleProposedIds.length > 0 &&
    visibleProposedIds.every((id) => selectedIds.has(id))

  const toggleSelected = useCallback((mappingId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(mappingId)) next.delete(mappingId)
      else next.add(mappingId)
      return next
    })
  }, [])

  const toggleSelectAllVisible = useCallback(() => {
    setSelectedIds((prev) => {
      if (visibleProposedIds.every((id) => prev.has(id))) {
        const next = new Set(prev)
        visibleProposedIds.forEach((id) => next.delete(id))
        return next
      }
      const next = new Set(prev)
      visibleProposedIds.forEach((id) => next.add(id))
      return next
    })
  }, [visibleProposedIds])

  const summariseBulkResult = useCallback(
    (verb: 'Accepted' | 'Dismissed', resp: CDMMappingBulkResponse) => {
      const ok = verb === 'Accepted' ? resp.accepted.length : resp.dismissed.length
      const skipped = resp.skipped.length
      const missing = resp.not_found.length
      const tail = []
      if (skipped) tail.push(`${skipped} skipped (not 'proposed')`)
      if (missing) tail.push(`${missing} not found`)
      const suffix = tail.length ? ` — ${tail.join(', ')}` : ''
      if (ok > 0) toast.success(`${verb} ${ok} mapping${ok === 1 ? '' : 's'}${suffix}`)
      else toast.error(`No mappings ${verb.toLowerCase()}${suffix || ''}`)
    },
    [],
  )

  const handleBulkAccept = useCallback(async () => {
    const ids = Array.from(selectedIds)
    if (ids.length === 0) return
    setBulkBusy(true)
    try {
      const resp = await bulkAcceptCdmMappings(organizationId, ids)
      summariseBulkResult('Accepted', resp)
      setSelectedIds(new Set())
      await refresh()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Bulk accept failed')
    } finally {
      setBulkBusy(false)
    }
  }, [organizationId, selectedIds, summariseBulkResult, refresh])

  const handleBulkDismiss = useCallback(async () => {
    const ids = Array.from(selectedIds)
    if (ids.length === 0) return
    setBulkBusy(true)
    try {
      const resp = await bulkDismissCdmMappings(
        organizationId,
        ids,
        bulkDismissReason || null,
      )
      summariseBulkResult('Dismissed', resp)
      setSelectedIds(new Set())
      setBulkDismissReason('')
      await refresh()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Bulk dismiss failed')
    } finally {
      setBulkBusy(false)
    }
  }, [organizationId, selectedIds, bulkDismissReason, summariseBulkResult, refresh])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1
  const canPrev = offset > 0
  const canNext = offset + PAGE_SIZE < total

  const ActionsCell = useMemo(
    () =>
      function ActionsCell({ m }: { m: CDMMapping }) {
        if (statusFilter !== 'proposed') {
          return <span className="cdm-row-meta">No actions</span>
        }
        if (dismissingId === m.id) {
          return (
            <div className="cdm-dismiss-inline">
              <input
                type="text"
                placeholder="Reason (optional)"
                value={dismissReason}
                onChange={(e) => setDismissReason(e.target.value)}
                className="cdm-dismiss-input"
                disabled={busyId === m.id}
              />
              <button
                type="button"
                className="btn-secondary"
                disabled={busyId === m.id}
                onClick={() => void handleDismissSubmit(m)}
              >
                {busyId === m.id ? '…' : 'Confirm'}
              </button>
              <button
                type="button"
                className="btn-text"
                disabled={busyId === m.id}
                onClick={() => {
                  setDismissingId(null)
                  setDismissReason('')
                }}
              >
                Cancel
              </button>
            </div>
          )
        }
        return (
          <div className="cdm-row-actions">
            <button
              type="button"
              className="btn-primary"
              disabled={busyId === m.id}
              onClick={() => void handleAccept(m)}
            >
              {busyId === m.id ? '…' : 'Accept'}
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={busyId === m.id}
              onClick={() => {
                setDismissingId(m.id)
                setDismissReason('')
              }}
            >
              Dismiss
            </button>
          </div>
        )
      },
    [statusFilter, dismissingId, dismissReason, busyId, handleAccept, handleDismissSubmit],
  )

  return (
    <section className="cdm-review-section">
      <div className="cdm-review-header">
        <div>
          <h2>Review queue</h2>
          <p className="cdm-review-sub">
            Mappings the platform proposed against your scoped controls.
            Accept the ones that genuinely cover the control; dismiss the
            false positives.
          </p>
        </div>
        <div className="cdm-review-filters">
          <label className="cdm-filter-label">
            Status
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as CDMMappingStatus)}
              className="cdm-filter-select"
            >
              {STATUS_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="btn-primary"
            disabled={computeBusy || (computeTaskId !== null && computeState !== 'SUCCESS' && computeState !== 'FAILURE' && computeState !== 'REVOKED')}
            onClick={() => void handleRunMapping()}
            title="Re-run the mapper across your indexed documents and scoped controls"
          >
            {computeBusy
              ? 'Starting…'
              : computeTaskId && computeState && computeState !== 'SUCCESS' && computeState !== 'FAILURE' && computeState !== 'REVOKED'
                ? `Running… (${computeState})`
                : 'Run mapping'}
          </button>
        </div>
      </div>
      {computeTaskId ? (
        <div className="cdm-review-task-banner">
          <span className="cdm-mono">Task {computeTaskId.slice(0, 8)}…</span>
          <span> · state: <strong>{computeState ?? '—'}</strong></span>
          {computeState === 'SUCCESS' ? <span> · queue refreshed.</span> : null}
          {computeState === 'FAILURE' ? <span> · check worker logs.</span> : null}
        </div>
      ) : null}

      {statusFilter === 'proposed' && visibleProposedIds.length > 0 ? (
        <div className="cdm-bulk-bar">
          <label className="cdm-bulk-select-all">
            <input
              type="checkbox"
              checked={allVisibleSelected}
              onChange={toggleSelectAllVisible}
              disabled={bulkBusy}
            />
            <span>
              {allVisibleSelected
                ? `All ${visibleProposedIds.length} on this page selected`
                : `Select all ${visibleProposedIds.length} on this page`}
            </span>
          </label>
          {selectedIds.size > 0 ? (
            <div className="cdm-bulk-actions">
              <span className="cdm-row-meta">{selectedIds.size} selected</span>
              <input
                type="text"
                placeholder="Dismiss reason (optional, applied to all)"
                value={bulkDismissReason}
                onChange={(e) => setBulkDismissReason(e.target.value)}
                className="cdm-dismiss-input"
                disabled={bulkBusy}
              />
              <button
                type="button"
                className="btn-primary"
                disabled={bulkBusy}
                onClick={() => void handleBulkAccept()}
              >
                {bulkBusy ? '…' : `Accept ${selectedIds.size}`}
              </button>
              <button
                type="button"
                className="btn-secondary"
                disabled={bulkBusy}
                onClick={() => void handleBulkDismiss()}
              >
                {bulkBusy ? '…' : `Dismiss ${selectedIds.size}`}
              </button>
              <button
                type="button"
                className="btn-text"
                disabled={bulkBusy}
                onClick={() => setSelectedIds(new Set())}
              >
                Clear
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      {loading ? (
        <div className="cdm-loading">Loading mappings…</div>
      ) : mappings.length === 0 ? (
        <div className="cdm-empty">
          <p>No mappings to review.</p>
          <p className="cdm-empty-hint">
            Upload more documents or compute new mappings — the queue will
            populate as the worker proposes matches.
          </p>
        </div>
      ) : (
        <ul className="cdm-review-card-list">
          {mappings.map((m) => {
            const catalogEntry = m.scf_id ? catalog[m.scf_id] : undefined
            const excerpt = m.excerpt ?? ''
            const expanded = !!expandedExcerptIds[m.id]
            const showExpandToggle = excerpt.length > EXCERPT_PREVIEW_CHARS
            let visibleExcerpt: string
            if (!excerpt) {
              visibleExcerpt = ''
            } else if (expanded) {
              visibleExcerpt =
                excerpt.length > EXCERPT_FULL_CHARS
                  ? `${excerpt.slice(0, EXCERPT_FULL_CHARS)}…`
                  : excerpt
            } else {
              visibleExcerpt =
                excerpt.length > EXCERPT_PREVIEW_CHARS
                  ? `${excerpt.slice(0, EXCERPT_PREVIEW_CHARS)}…`
                  : excerpt
            }

            return (
              <li key={m.id} className="cdm-review-card">
                <div className="cdm-review-card-header">
                  <div className="cdm-review-card-control">
                    {m.status === 'proposed' && statusFilter === 'proposed' ? (
                      <input
                        type="checkbox"
                        className="cdm-review-card-checkbox"
                        checked={selectedIds.has(m.id)}
                        onChange={() => toggleSelected(m.id)}
                        disabled={bulkBusy}
                        aria-label={`Select mapping for ${m.scf_id ?? 'control'}`}
                      />
                    ) : null}
                    <span className="cdm-review-card-scf-id">{m.scf_id ?? '—'}</span>
                    {catalogEntry ? (
                      <span className="cdm-review-card-control-name">
                        {catalogEntry.control_name}
                      </span>
                    ) : null}
                  </div>
                  <span className="cdm-row-meta cdm-review-card-score">
                    score {formatScore(m.relevance_score)}
                  </span>
                </div>

                <div className="cdm-review-card-meta">
                  <span className="cdm-filename">{m.original_filename ?? '—'}</span>
                  <span className="cdm-review-card-sep">·</span>
                  <span className="cdm-mapping-section">{m.section ?? '—'}</span>
                  <span className="cdm-review-card-sep">·</span>
                  <span className="cdm-mono">{formatRange(m.byte_offset_start, m.byte_offset_end)}</span>
                </div>

                <div className="cdm-review-card-body">
                  <div className="cdm-review-card-excerpt">
                    <h4 className="cdm-review-card-block-title">Document excerpt</h4>
                    {excerpt ? (
                      <>
                        <pre className="cdm-excerpt">{visibleExcerpt}</pre>
                        {showExpandToggle ? (
                          <button
                            type="button"
                            className="cdm-link-button"
                            onClick={() =>
                              setExpandedExcerptIds((prev) => ({
                                ...prev,
                                [m.id]: !prev[m.id],
                              }))
                            }
                          >
                            {expanded ? 'Show less' : 'Show more'}
                          </button>
                        ) : null}
                      </>
                    ) : (
                      <p className="cdm-review-card-notice">
                        Excerpt unavailable — re-run mapping to populate.
                      </p>
                    )}
                  </div>

                  <div className="cdm-review-card-scf">
                    <h4 className="cdm-review-card-block-title">SCF control</h4>
                    {catalogEntry ? (
                      <>
                        <div className="cdm-scf-name">{catalogEntry.control_name}</div>
                        <p className="cdm-scf-description">
                          {catalogEntry.control_description}
                        </p>
                      </>
                    ) : (
                      <p className="cdm-row-meta">
                        Catalog text unavailable for {m.scf_id ?? 'this control'}.
                      </p>
                    )}
                  </div>
                </div>

                <div className="cdm-review-card-actions">
                  <ActionsCell m={m} />
                </div>
              </li>
            )
          })}
        </ul>
      )}

      {total > 0 ? (
        <div className="cdm-pagination">
          <span className="cdm-pagination-meta">
            Page {currentPage} of {totalPages} — {total.toLocaleString()} total
          </span>
          <div className="cdm-pagination-controls">
            <button
              type="button"
              className="btn-secondary"
              disabled={!canPrev || loading}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              Previous
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={!canNext || loading}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
            </button>
          </div>
        </div>
      ) : null}
    </section>
  )
}
