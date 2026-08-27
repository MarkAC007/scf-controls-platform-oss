import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'react-hot-toast'
import {
  listCdmControlProposals,
  acceptCdmControlProposal,
  dismissCdmControlProposal,
  type CDMControlProposal,
  type CDMControlProposalStatus,
  type CDMMapping,
} from '../data/apiClient'
import TabRow from './explorer/TabRow'
import type { TabRowItem } from './explorer/TabRow'

interface CDMReviewQueueProps {
  organizationId: string
  /**
   * Bumped by the parent when a mapping run settles — triggers a queue
   * reload. The run itself (trigger, poll, banner) lives in CDMWorkspace's
   * action bar so it is visible on both tabs.
   */
  runRefreshKey?: number
  /** Notifies the parent that proposal counts changed (accept/dismiss). */
  onQueueMutated?: () => void
}

const PAGE_SIZE = 25
const EXCERPT_PREVIEW_CHARS = 150
const EXCERPT_FULL_CHARS = 600

const STATUS_OPTIONS: { value: CDMControlProposalStatus; label: string }[] = [
  { value: 'proposed', label: 'Proposed' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'dismissed', label: 'Dismissed' },
  { value: 'stale', label: 'Stale' },
]

const STATUS_BADGES: Record<CDMControlProposalStatus, { label: string; className: string }> = {
  proposed: { label: 'Proposed', className: 'cdm-badge-progress' },
  accepted: { label: 'Accepted', className: 'cdm-badge-success' },
  dismissed: { label: 'Dismissed', className: 'cdm-badge-error' },
  stale: { label: 'Stale', className: 'cdm-badge-warning' },
}

function formatScore(score: number): string {
  return score.toFixed(2)
}

function formatRange(start: number, end: number): string {
  // Characters, not bytes. The column names still say byte_offset_* for
  // compatibility, but the values have always been character offsets into
  // the extracted text, and calling them bytes sends anyone verifying a
  // citation to the wrong position in any document containing non-ASCII.
  const len = end - start
  return `${start.toLocaleString()}–${end.toLocaleString()} (${len.toLocaleString()} chars)`
}

const MATCH_TYPE_LABELS: Record<string, { label: string; hint: string }> = {
  exact: {
    label: 'Exact',
    hint: 'The excerpt appears verbatim at these offsets in the source document.',
  },
  whitespace_flexible: {
    label: 'Whitespace-normalised',
    hint:
      'Matched after collapsing runs of whitespace. Offsets are mapped back to the original text, so the citation still resolves.',
  },
  fuzzy: {
    label: 'Partial',
    hint:
      'Only part of the passage matched. Read the excerpt against the source before accepting.',
  },
}

/**
 * Shows how a score was arrived at.
 *
 * Accepting a mapping is an audit assertion, so the reviewer is entitled to
 * see the evidence behind the number rather than being asked to trust it.
 * v1 could not offer this: its score was list position, and there was nothing
 * underneath to show.
 */
export function ScoreBreakdown({ mapping }: { mapping: CDMMapping }) {
  const weights = mapping.score_weights
  const components: Array<[string, number | null, number | undefined]> = [
    ['Text relevance', mapping.ts_rank_component, weights?.ts_rank],
    ['Objective coverage', mapping.objective_coverage_component, weights?.objective_coverage],
    ['Term overlap', mapping.term_overlap_component, weights?.term_overlap],
  ]

  if (components.every(([, value]) => value === null || value === undefined)) {
    return (
      <p className="cdm-review-card-notice">
        Scored before score components were recorded. Re-run mapping to see the
        breakdown.
      </p>
    )
  }

  return (
    <table className="cdm-score-breakdown">
      <tbody>
        {components.map(([label, value, weight]) =>
          value === null || value === undefined ? null : (
            <tr key={label}>
              <th scope="row">{label}</th>
              <td className="cdm-mono">{value.toFixed(3)}</td>
              <td className="cdm-row-meta">
                {weight === undefined ? '' : `× ${weight.toFixed(2)}`}
              </td>
            </tr>
          ),
        )}
        <tr className="cdm-score-breakdown-total">
          <th scope="row">Score</th>
          <td className="cdm-mono">{formatScore(mapping.relevance_score)}</td>
          <td />
        </tr>
      </tbody>
    </table>
  )
}

/** HTTP status carried on errors thrown by apiFetch, when there is one. */
function httpStatus(err: unknown): number | undefined {
  return (err as { status?: number } | null)?.status
}

function truncateExcerpt(excerpt: string, expanded: boolean): string {
  const limit = expanded ? EXCERPT_FULL_CHARS : EXCERPT_PREVIEW_CHARS
  return excerpt.length > limit ? `${excerpt.slice(0, limit)}…` : excerpt
}

/**
 * One citation under a proposal — the passage that put the control and the
 * document together, with the score components behind it.
 *
 * This is provenance, not a decision. The reviewer accepts or dismisses the
 * proposal; opening a citation is how they satisfy themselves before doing so.
 */
function CitationDetail({
  citation,
  expanded,
  onToggleExcerpt,
}: {
  citation: CDMMapping
  expanded: boolean
  onToggleExcerpt: () => void
}) {
  const excerpt = citation.excerpt ?? ''
  const matchTypeMeta = citation.match_type
    ? MATCH_TYPE_LABELS[citation.match_type]
    : undefined

  return (
    <li className="cdm-citation">
      <div className="cdm-citation-header">
        <span className="cdm-mapping-section">{citation.section ?? '—'}</span>
        <span className="cdm-row-meta cdm-review-card-score">
          {matchTypeMeta ? (
            <span
              className={`cdm-match-badge cdm-match-badge-${citation.match_type}`}
              title={matchTypeMeta.hint}
            >
              {matchTypeMeta.label}
            </span>
          ) : null}
          score {formatScore(citation.relevance_score)}
        </span>
      </div>

      <div className="cdm-review-card-meta">
        <span className="cdm-mono">
          {formatRange(citation.byte_offset_start, citation.byte_offset_end)}
        </span>
        {citation.retrieval_tier ? (
          <>
            <span className="cdm-review-card-sep">·</span>
            <span className="cdm-row-meta">via {citation.retrieval_tier}</span>
          </>
        ) : null}
      </div>

      {citation.matched_objective_text ? (
        <p className="cdm-matched-objective">
          <span className="cdm-matched-objective-label">Answers objective:</span>{' '}
          {citation.matched_objective_text}
        </p>
      ) : null}

      {excerpt ? (
        <>
          <pre className="cdm-excerpt">{truncateExcerpt(excerpt, expanded)}</pre>
          {excerpt.length > EXCERPT_PREVIEW_CHARS ? (
            <button type="button" className="cdm-link-button" onClick={onToggleExcerpt}>
              {expanded ? 'Show less' : 'Show more'}
            </button>
          ) : null}
        </>
      ) : (
        <p className="cdm-review-card-notice">
          Excerpt unavailable — re-run mapping to populate.
        </p>
      )}

      <ScoreBreakdown mapping={citation} />
    </li>
  )
}

export default function CDMReviewQueue({
  organizationId,
  runRefreshKey = 0,
  onQueueMutated,
}: CDMReviewQueueProps) {
  const [statusFilter, setStatusFilter] = useState<CDMControlProposalStatus>('proposed')
  const [offset, setOffset] = useState(0)
  const [proposals, setProposals] = useState<CDMControlProposal[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [dismissingId, setDismissingId] = useState<string | null>(null)
  const [dismissReason, setDismissReason] = useState('')
  const [expandedProposalIds, setExpandedProposalIds] = useState<Record<string, boolean>>({})
  const [expandedExcerptIds, setExpandedExcerptIds] = useState<Record<string, boolean>>({})
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set())
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkDismissReason, setBulkDismissReason] = useState('')
  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const response = await listCdmControlProposals(organizationId, {
        status: statusFilter,
        limit: PAGE_SIZE,
        offset,
      })
      setProposals(response.proposals)
      setTotal(response.total)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load proposals'
      toast.error(message)
    } finally {
      setLoading(false)
    }
  }, [organizationId, statusFilter, offset])

  useEffect(() => {
    void refresh()
  }, [refresh, runRefreshKey])

  // Reviewer actions change the proposal counts the parent surfaces (the
  // tab badge and action bar), so each one both reloads this list and
  // notifies upward.
  const refreshAndNotify = useCallback(async () => {
    await refresh()
    onQueueMutated?.()
  }, [refresh, onQueueMutated])

  // Reset to first page when the filter changes.
  useEffect(() => {
    setOffset(0)
  }, [statusFilter])

  // Clear bulk selection whenever the visible page changes (filter, paging,
  // or successful single-action refresh re-fetches the list).
  useEffect(() => {
    setSelectedIds(new Set())
  }, [statusFilter, offset, organizationId])

  const handleAccept = useCallback(
    async (proposal: CDMControlProposal) => {
      setBusyId(proposal.id)
      try {
        const resp = await acceptCdmControlProposal(organizationId, proposal.id)
        toast.success(
          `Accepted ${proposal.scf_id ?? 'control'} — ${resp.citations_accepted} citation${
            resp.citations_accepted === 1 ? '' : 's'
          }`,
        )
        await refreshAndNotify()
      } catch (err) {
        // A 409 means someone (or a mapping run) already moved this proposal
        // out of 'proposed'. The reviewer's click was fine; their page is old.
        if (httpStatus(err) === 409) {
          toast('That proposal had already been actioned — queue reloaded.')
          await refreshAndNotify()
          return
        }
        toast.error(err instanceof Error ? err.message : 'Accept failed')
      } finally {
        setBusyId(null)
      }
    },
    [organizationId, refresh],
  )

  const handleDismissSubmit = useCallback(
    async (proposal: CDMControlProposal) => {
      setBusyId(proposal.id)
      try {
        await dismissCdmControlProposal(organizationId, proposal.id, dismissReason || null)
        toast.success(`Dismissed ${proposal.scf_id ?? 'control'}`)
        setDismissingId(null)
        setDismissReason('')
        await refreshAndNotify()
      } catch (err) {
        if (httpStatus(err) === 409) {
          toast('That proposal had already been actioned — queue reloaded.')
          setDismissingId(null)
          setDismissReason('')
          await refreshAndNotify()
          return
        }
        toast.error(err instanceof Error ? err.message : 'Dismiss failed')
      } finally {
        setBusyId(null)
      }
    },
    [organizationId, dismissReason, refresh],
  )

  const visibleProposedIds = useMemo(
    () => proposals.filter((p) => p.status === 'proposed').map((p) => p.id),
    [proposals],
  )
  const allVisibleSelected =
    visibleProposedIds.length > 0 &&
    visibleProposedIds.every((id) => selectedIds.has(id))

  const toggleSelected = useCallback((proposalId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(proposalId)) next.delete(proposalId)
      else next.add(proposalId)
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

  /**
   * Apply a decision to every selected proposal, one request each.
   *
   * There is no bulk proposal endpoint and at a page size of 25 there does not
   * need to be. A 409 inside the loop is an expected outcome rather than a
   * failure — it means that proposal moved on — so it is counted and the loop
   * continues; abandoning the rest of the selection would be the worse answer.
   */
  const runBulk = useCallback(
    async (verb: 'Accepted' | 'Dismissed', apply: (id: string) => Promise<unknown>) => {
      const ids = Array.from(selectedIds)
      if (ids.length === 0) return
      setBulkBusy(true)
      let ok = 0
      let conflicts = 0
      let failed = 0
      try {
        for (const id of ids) {
          try {
            await apply(id)
            ok += 1
          } catch (err) {
            if (httpStatus(err) === 409) conflicts += 1
            else failed += 1
          }
        }

        const tail: string[] = []
        if (conflicts) tail.push(`${conflicts} already actioned`)
        if (failed) tail.push(`${failed} failed`)
        const suffix = tail.length ? ` — ${tail.join(', ')}` : ''
        if (ok > 0) {
          toast.success(`${verb} ${ok} proposal${ok === 1 ? '' : 's'}${suffix}`)
        } else {
          toast.error(`No proposals ${verb.toLowerCase()}${suffix}`)
        }

        setSelectedIds(new Set())
        await refreshAndNotify()
      } finally {
        setBulkBusy(false)
      }
    },
    [selectedIds, refresh],
  )

  const handleBulkAccept = useCallback(
    () => runBulk('Accepted', (id) => acceptCdmControlProposal(organizationId, id)),
    [runBulk, organizationId],
  )

  const handleBulkDismiss = useCallback(
    async () => {
      const reason = bulkDismissReason || null
      await runBulk('Dismissed', (id) =>
        dismissCdmControlProposal(organizationId, id, reason),
      )
      setBulkDismissReason('')
    },
    [runBulk, organizationId, bulkDismissReason],
  )

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1
  const canPrev = offset > 0
  const canNext = offset + PAGE_SIZE < total

  return (
    <section className="cdm-review-section">
      <div className="cdm-review-header">
        <div>
          <h2>Review queue</h2>
          <p className="cdm-review-sub">
            One decision per control and document: the platform proposes a
            control it believes a document covers, and shows you the passages it
            is relying on. Accept the proposals that genuinely cover the
            control; dismiss the false positives.
          </p>
        </div>
      </div>

      <TabRow
        tabs={STATUS_OPTIONS.map(
          (opt): TabRowItem => ({ id: opt.value, label: opt.label }),
        )}
        activeId={statusFilter}
        onSelect={(id) => setStatusFilter(id as CDMControlProposalStatus)}
        aria-label="Proposal status"
      />

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
                placeholder="Dismiss reason (optional)"
                title="Applied to every dismissed proposal in this bulk action"
                value={bulkDismissReason}
                onChange={(e) => setBulkDismissReason(e.target.value)}
                className="cdm-dismiss-input"
                disabled={bulkBusy}
              />
              <span className="cdm-bulk-buttons">
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
              </span>
            </div>
          ) : null}
        </div>
      ) : null}

      {loading ? (
        <div className="cdm-loading">Loading proposals…</div>
      ) : proposals.length === 0 ? (
        <div className="cdm-empty">
          <p>No proposals to review.</p>
          {statusFilter === 'proposed' ? (
            <p className="cdm-empty-hint">
              Mappings may exist that have not been consolidated into proposals
              yet. Run Mapping to (re)build the review queue.
            </p>
          ) : null}
        </div>
      ) : (
        <ul className="cdm-review-card-list">
          {proposals.map((p) => {
            const badge = STATUS_BADGES[p.status]
            const citationsExpanded = !!expandedProposalIds[p.id]
            const recomputed = p.recompute_provider !== null

            return (
              <li key={p.id} className="cdm-review-card cdm-hairline-card">
                <div className="cdm-review-card-header">
                  <div className="cdm-review-card-control">
                    {p.status === 'proposed' && statusFilter === 'proposed' ? (
                      <input
                        type="checkbox"
                        className="cdm-review-card-checkbox"
                        checked={selectedIds.has(p.id)}
                        onChange={() => toggleSelected(p.id)}
                        disabled={bulkBusy}
                        aria-label={`Select proposal for ${p.scf_id ?? 'control'}`}
                      />
                    ) : null}
                    <span className="cdm-review-card-scf-id">{p.scf_id ?? '—'}</span>
                    {p.control_name ? (
                      <span className="cdm-review-card-control-name">
                        {p.control_name}
                      </span>
                    ) : null}
                  </div>
                  <span className="cdm-row-meta cdm-review-card-score">
                    <span className={`cdm-badge ${badge.className}`}>{badge.label}</span>
                    score {formatScore(p.consolidated_score)}
                  </span>
                </div>

                <div className="cdm-review-card-meta">
                  <span className="cdm-filename">{p.original_filename ?? '—'}</span>
                  <span className="cdm-review-card-sep">·</span>
                  {recomputed ? (
                    <span className="cdm-row-meta">
                      recomputed
                      {p.recompute_model_id
                        ? ` · ${p.recompute_provider}/${p.recompute_model_id}`
                        : ` · ${p.recompute_provider}`}
                    </span>
                  ) : (
                    <span
                      className="cdm-row-meta"
                      title="Highest citation score. No model has consolidated this proposal yet."
                    >
                      heuristic score
                    </span>
                  )}
                </div>

                {p.status === 'proposed' && p.dismiss_reason ? (
                  <p className="cdm-review-card-resurrected">
                    Previously dismissed: {p.dismiss_reason}
                  </p>
                ) : null}

                {recomputed && p.rationale ? (
                  <div className="cdm-proposal-rationale">
                    <h4 className="cdm-review-card-block-title">
                      Consolidated judgment
                    </h4>
                    <p className="cdm-proposal-rationale-text">{p.rationale}</p>
                  </div>
                ) : null}

                <div className="cdm-proposal-citations">
                  <button
                    type="button"
                    className="cdm-link-button"
                    aria-expanded={citationsExpanded}
                    onClick={() =>
                      setExpandedProposalIds((prev) => ({
                        ...prev,
                        [p.id]: !prev[p.id],
                      }))
                    }
                  >
                    {citationsExpanded ? 'Hide' : 'Show'} {p.citation_count} citation
                    {p.citation_count === 1 ? '' : 's'}
                  </button>

                  {citationsExpanded ? (
                    p.citations.length > 0 ? (
                      <ul className="cdm-citation-list">
                        {p.citations.map((c) => (
                          <CitationDetail
                            key={c.id}
                            citation={c}
                            expanded={!!expandedExcerptIds[c.id]}
                            onToggleExcerpt={() =>
                              setExpandedExcerptIds((prev) => ({
                                ...prev,
                                [c.id]: !prev[c.id],
                              }))
                            }
                          />
                        ))}
                      </ul>
                    ) : (
                      <p className="cdm-review-card-notice">
                        Citations unavailable — re-run mapping to populate.
                      </p>
                    )
                  ) : null}
                </div>

                {p.status === 'proposed' ? (
                  <div className="cdm-review-card-actions">
                    {dismissingId === p.id ? (
                      <div className="cdm-dismiss-inline">
                        <input
                          type="text"
                          placeholder="Reason (optional)"
                          value={dismissReason}
                          onChange={(e) => setDismissReason(e.target.value)}
                          className="cdm-dismiss-input"
                          disabled={busyId === p.id}
                        />
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={busyId === p.id}
                          onClick={() => void handleDismissSubmit(p)}
                        >
                          {busyId === p.id ? '…' : 'Confirm'}
                        </button>
                        <button
                          type="button"
                          className="btn-text"
                          disabled={busyId === p.id}
                          onClick={() => {
                            setDismissingId(null)
                            setDismissReason('')
                          }}
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <div className="cdm-row-actions">
                        <button
                          type="button"
                          className="btn-primary"
                          disabled={busyId === p.id}
                          onClick={() => void handleAccept(p)}
                        >
                          {busyId === p.id ? '…' : 'Accept'}
                        </button>
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={busyId === p.id}
                          onClick={() => {
                            setDismissingId(p.id)
                            setDismissReason('')
                          }}
                        >
                          Dismiss
                        </button>
                      </div>
                    )}
                  </div>
                ) : null}
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
