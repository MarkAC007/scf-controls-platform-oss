import { useState, useEffect, useMemo, useCallback } from 'react'
import type {
  EnrichedControl,
  ScopedControlsFile,
  EvidenceId,
  EvidenceWindowAssessmentSummary,
} from '../../types'
import {
  getEvidenceHealth,
  getUpcomingEvidence,
  getWindowAssessmentSummary,
  getAssessmentSummary,
  refreshStaleWindowAssessments,
  type EvidenceHealthResponse,
  type UpcomingEvidenceItem,
  type EvidenceAssessmentSummary,
} from '../../data/apiClient'
import { getScopedControl, getEvidenceTracking } from '../../data/scopingService'
import { evidenceOwnerLabel, evidenceOwnerUserId } from '../../data/userDisplay'
import { ContractorBadge } from '../ContractorBadge'
import { AssessmentReviewQueueCard } from './AssessmentReviewQueueCard'
import { useOrgMemberTypes } from '../../hooks/useOrgMemberTypes'
import { frequencyLabel } from '../../data/frequencyVocabulary'
import {
  basisLabel,
  basisTitle,
  coverageLabel,
  stalenessSortKey,
  uploadLabel,
} from '../../data/evidenceFreshness'
import { interactiveRowProps } from '../../data/interactiveRow'
import { FRESHNESS_RULE, FRESHNESS_LEGEND } from '../../data/freshnessRule'

// ---- Types ----

type StatusFilter = 'all' | 'green' | 'amber' | 'red' | 'unknown'

interface EvidenceDashboardTabProps {
  organizationId: string
  controls: EnrichedControl[]
  scopingData: ScopedControlsFile
  onNavigateToEvidence?: (evidenceId: string) => void
}

// ---- Sub-components ----

function HealthSummaryBar({ summary }: { summary: EvidenceHealthResponse['summary'] }) {
  return (
    <div className="ehd-summary-bar">
      <div className="ehd-summary-stat">
        <span className="ehd-summary-count">{summary.total_tracked}</span>
        <span className="ehd-summary-label">Tracked</span>
      </div>
      <div className="ehd-summary-stat ehd-stat-green">
        <span className="ehd-summary-count">{summary.green_count}</span>
        <span className="ehd-summary-label" title={FRESHNESS_RULE.green}>
          Fresh ({summary.green_pct}%)
        </span>
      </div>
      <div className="ehd-summary-stat ehd-stat-amber">
        <span className="ehd-summary-count">{summary.amber_count}</span>
        <span className="ehd-summary-label" title={FRESHNESS_RULE.amber}>
          Stale ({summary.amber_pct}%)
        </span>
      </div>
      <div className="ehd-summary-stat ehd-stat-red">
        <span className="ehd-summary-count">{summary.red_count}</span>
        <span className="ehd-summary-label" title={FRESHNESS_RULE.red}>
          Critical ({summary.red_pct}%)
        </span>
      </div>
      {summary.unknown_count > 0 && (
        <div className="ehd-summary-stat ehd-stat-unknown">
          <span className="ehd-summary-count">{summary.unknown_count}</span>
          <span className="ehd-summary-label" title={FRESHNESS_RULE.unknown}>No Data</span>
        </div>
      )}
      <p className="ehd-summary-legend">{FRESHNESS_LEGEND}</p>
    </div>
  )
}

function HealthProgressBar({ summary }: { summary: EvidenceHealthResponse['summary'] }) {
  const total = summary.total_tracked || 1
  return (
    <div className="ehd-progress-bar">
      <div
        className="ehd-progress-segment ehd-seg-green"
        style={{ width: `${(summary.green_count / total) * 100}%` }}
        title={`${summary.green_count} fresh`}
      />
      <div
        className="ehd-progress-segment ehd-seg-amber"
        style={{ width: `${(summary.amber_count / total) * 100}%` }}
        title={`${summary.amber_count} stale`}
      />
      <div
        className="ehd-progress-segment ehd-seg-red"
        style={{ width: `${(summary.red_count / total) * 100}%` }}
        title={`${summary.red_count} critical`}
      />
      <div
        className="ehd-progress-segment ehd-seg-unknown"
        style={{ width: `${(summary.unknown_count / total) * 100}%` }}
        title={`${summary.unknown_count} no data`}
      />
    </div>
  )
}

export function StatusDot({ status }: { status: string }) {
  return <span className={`ehd-status-dot ehd-dot-${status}`} />
}

function HealthFilterBar({
  filter,
  onFilterChange,
  query,
  onQueryChange,
}: {
  filter: StatusFilter
  onFilterChange: (f: StatusFilter) => void
  query: string
  onQueryChange: (q: string) => void
}) {
  const filters: { value: StatusFilter; label: string; title: string }[] = [
    { value: 'all', label: 'All', title: 'Every tracked evidence item.' },
    { value: 'green', label: 'Fresh', title: FRESHNESS_RULE.green },
    { value: 'amber', label: 'Stale', title: FRESHNESS_RULE.amber },
    { value: 'red', label: 'Critical', title: FRESHNESS_RULE.red },
    { value: 'unknown', label: 'No Data', title: FRESHNESS_RULE.unknown },
  ]

  return (
    <div className="ehd-filter-bar">
      <div className="ehd-filter-tabs">
        {filters.map((f) => (
          <button
            key={f.value}
            className={`ehd-filter-tab ${filter === f.value ? 'active' : ''}`}
            onClick={() => onFilterChange(f.value)}
            title={f.title}
          >
            {f.value !== 'all' && <StatusDot status={f.value} />}
            {f.label}
          </button>
        ))}
      </div>
      <input
        type="text"
        className="ehd-search-input"
        placeholder="Search evidence..."
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
      />
    </div>
  )
}

export function HealthCard({ item, onNavigateToEvidence }: { item: EvidenceHealthResponse['items'][0]; onNavigateToEvidence?: (evidenceId: string) => void }) {
  // Coverage first, arrival second. The status colour follows coverage age, so
  // leading with the upload date produced red cards captioned "Today".
  const coverage = coverageLabel(item)
  const upload = uploadLabel(item)
  const basis = basisLabel(item)

  return (
    <div
      className={`ehd-card ehd-card-${item.status}${onNavigateToEvidence ? ' cursor-pointer' : ''}`}
      {...interactiveRowProps(
        onNavigateToEvidence && (() => onNavigateToEvidence(item.evidence_id)),
      )}
    >
      <div className="ehd-card-header">
        <StatusDot status={item.status} />
        <span className="ehd-card-id">{item.evidence_id}</span>
        {item.file_count > 0 && (
          <span className="ehd-card-files">{item.file_count} file{item.file_count !== 1 ? 's' : ''}</span>
        )}
      </div>
      {item.evidence_name && (
        <div className="ehd-card-name">{item.evidence_name}</div>
      )}
      <div className="ehd-card-meta">
        {item.collecting_system && (
          <span className="ehd-card-system">{item.collecting_system}</span>
        )}
        {item.frequency && (
          <span className="ehd-card-freq">{frequencyLabel(item.frequency)}</span>
        )}
      </div>
      <div className="ehd-card-footer">
        <span className="ehd-card-freshness" data-testid="ehd-coverage">
          Covers to: <strong>{coverage}</strong>
          {basis && (
            <span
              className={`ehd-card-basis ehd-basis-${item.staleness_basis}`}
              title={basisTitle(item) ?? undefined}
              data-testid="ehd-basis"
            >
              {basis}
            </span>
          )}
        </span>
        {item.staleness_threshold_days !== null && (
          <span className="ehd-card-threshold">
            Threshold: {item.staleness_threshold_days}d
          </span>
        )}
      </div>
      <div className="ehd-card-subfooter">
        <span className="ehd-card-upload" data-testid="ehd-upload">
          Last upload: {upload}
        </span>
      </div>
      {item.latest_validation_status && (
        <div className={`ehd-card-validation ehd-val-${item.latest_validation_status}`}>
          Validation: {item.latest_validation_status}
        </div>
      )}
      {item.latest_assessment_status && (
        <div className={`ehd-card-assessment ehd-assessment-${item.latest_assessment_status}`}>
          <span className="ehd-assessment-label">AI:</span>
          <span className="ehd-assessment-pill">{item.latest_assessment_status}</span>
          {item.latest_assessment_score !== null && (
            <span className="ehd-assessment-score">{Math.round(item.latest_assessment_score)}</span>
          )}
        </div>
      )}
    </div>
  )
}

// ---- AI Assessment Summary Card (windowed) ----

function AssessmentSummaryCard({ organizationId }: { organizationId: string }) {
  const [summary, setSummary] = useState<EvidenceWindowAssessmentSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshLoading, setRefreshLoading] = useState(false)
  const [refreshMessage, setRefreshMessage] = useState<string | null>(null)
  // Why items were skipped, grouped by reason (#788). The endpoint has always
  // returned `skipped_detail`; the UI used to read only the counts, so a
  // refresh that queued nothing said "Queued 0 of 1" and stopped there.
  const [skippedReasons, setSkippedReasons] = useState<SkipGroup[]>([])

  const loadSummary = useCallback(() => {
    getWindowAssessmentSummary(organizationId)
      .then(setSummary)
      .catch(() => setSummary(null))
  }, [organizationId])

  useEffect(() => {
    setLoading(true)
    getWindowAssessmentSummary(organizationId)
      .then(setSummary)
      .catch(() => setSummary(null))
      .finally(() => setLoading(false))
  }, [organizationId])

  const handleRefresh = async () => {
    setRefreshLoading(true)
    setRefreshMessage(null)
    setSkippedReasons([])
    try {
      const result = await refreshStaleWindowAssessments(organizationId)
      if (result.queued === 0 && result.candidates === 0) {
        setRefreshMessage('All windows up to date')
      } else {
        setRefreshMessage(`Queued ${result.queued} of ${result.candidates}`)
      }
      setSkippedReasons(groupSkipReasons(result.skipped_detail))
      // Re-poll summary briefly so the counts catch up with the worker.
      setTimeout(loadSummary, 5000)
    } catch {
      setRefreshMessage('Failed to queue refresh')
    } finally {
      setRefreshLoading(false)
    }
  }

  if (loading) return null
  if (!summary) return null

  const hasAny = summary.total_windows_assessed > 0

  return (
    <div className="ai-assessment-card">
      <div className="ai-assessment-card-header">
        <h3 className="ai-assessment-card-title">Evidence Coverage by Window</h3>
        <span className="ai-advisory-label">AI Advisory</span>
      </div>

      {hasAny ? (
        <>
          <div className="ai-assessment-stats">
            <div className="ai-assessment-stat">
              <span className="ai-assessment-stat-count">{summary.total_windows_assessed}</span>
              <span className="ai-assessment-stat-label">Windows</span>
            </div>
            <div className="ai-assessment-stat">
              <span className="ai-assessment-stat-count">{summary.sufficient_count}</span>
              <span className="ai-assessment-stat-label">Sufficient</span>
            </div>
            <div className="ai-assessment-stat">
              <span className="ai-assessment-stat-count">{summary.partial_count}</span>
              <span className="ai-assessment-stat-label">Partial</span>
            </div>
            <div className="ai-assessment-stat">
              <span className="ai-assessment-stat-count">{summary.insufficient_count}</span>
              <span className="ai-assessment-stat-label">Insufficient</span>
            </div>
            <div className="ai-assessment-stat">
              <span className="ai-assessment-stat-count">{summary.insufficient_sample_count}</span>
              <span className="ai-assessment-stat-label">Insufficient Sample</span>
            </div>
            {summary.pending_count > 0 && (
              <div className="ai-assessment-stat">
                <span className="ai-assessment-stat-count">{summary.pending_count}</span>
                <span className="ai-assessment-stat-label">Pending</span>
              </div>
            )}
            {summary.error_count > 0 && (
              <div className="ai-assessment-stat">
                <span className="ai-assessment-stat-count">{summary.error_count}</span>
                <span className="ai-assessment-stat-label">Error</span>
              </div>
            )}
          </div>
          {summary.average_relevance_score !== null && (
            <div className="ai-assessment-score">
              Avg Relevance: <strong>{Math.round(summary.average_relevance_score)}/100</strong>
            </div>
          )}
        </>
      ) : (
        <div className="ai-assessment-empty">
          No windowed assessments yet. The nightly job runs at 04:00 UTC.
        </div>
      )}

      <div style={{ marginTop: 8 }}>
        <button
          className="ai-assessment-bulk-btn"
          onClick={handleRefresh}
          disabled={refreshLoading}
        >
          {refreshLoading ? 'Queuing...' : 'Reassess Stale Windows'}
        </button>
        {refreshMessage && (
          <span className="edt-refresh-message">{refreshMessage}</span>
        )}
        {skippedReasons.length > 0 && (
          <ul className="edt-skip-reasons">
            {skippedReasons.map(group => (
              <li key={group.reason}>
                <span className="edt-skip-reason">{group.reason}</span>
                {' — '}
                <span className="edt-skip-ids">{formatSkipIds(group.ids)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

// ---- AI Assessment Summary Card (per file) ----

/**
 * The per-file counterpart to the windowed card above (#881).
 *
 * The two answer different questions and both matter: the windowed card says
 * whether each collection period is covered, this one says how far the file-by
 * -file sweep has actually got. ``unassessed_count`` is the reason it earns
 * its space — a dashboard showing only what was assessed lets a large untouched
 * backlog read as silence.
 *
 * ``total_cost_cents`` is in the API response and is deliberately not rendered:
 * internal inference spend is not a customer-facing number.
 */
function FileAssessmentSummaryCard({ organizationId }: { organizationId: string }) {
  const [summary, setSummary] = useState<EvidenceAssessmentSummary | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    getAssessmentSummary(organizationId)
      .then(setSummary)
      .catch(() => setSummary(null))
      .finally(() => setLoading(false))
  }, [organizationId])

  if (loading) return null
  if (!summary) return null

  const hasAny = summary.total_assessed > 0 || summary.unassessed_count > 0

  return (
    <div className="ai-assessment-card">
      <div className="ai-assessment-card-header">
        <h3 className="ai-assessment-card-title">Evidence Files Assessed</h3>
        <span className="ai-advisory-label">AI Advisory</span>
      </div>

      {hasAny ? (
        <>
          <div className="ai-assessment-stats">
            <div className="ai-assessment-stat">
              <span className="ai-assessment-stat-count">{summary.total_assessed}</span>
              <span className="ai-assessment-stat-label">Assessed</span>
            </div>
            <div className="ai-assessment-stat">
              <span className="ai-assessment-stat-count">{summary.sufficient_count}</span>
              <span className="ai-assessment-stat-label">Sufficient</span>
            </div>
            <div className="ai-assessment-stat">
              <span className="ai-assessment-stat-count">{summary.partial_count}</span>
              <span className="ai-assessment-stat-label">Partial</span>
            </div>
            <div className="ai-assessment-stat">
              <span className="ai-assessment-stat-count">{summary.insufficient_count}</span>
              <span className="ai-assessment-stat-label">Insufficient</span>
            </div>
            {summary.pending_count > 0 && (
              <div className="ai-assessment-stat">
                <span className="ai-assessment-stat-count">{summary.pending_count}</span>
                <span className="ai-assessment-stat-label">Pending</span>
              </div>
            )}
            {summary.error_count > 0 && (
              <div className="ai-assessment-stat">
                <span className="ai-assessment-stat-count">{summary.error_count}</span>
                <span className="ai-assessment-stat-label">Error</span>
              </div>
            )}
            {/* Nothing could be read out of these. Shown beside the verdict
                buckets rather than hidden, because otherwise the four buckets
                above visibly fail to sum to the total and the gap reads as an
                arithmetic bug. */}
            {summary.unassessable_count > 0 && (
              <div className="ai-assessment-stat">
                <span className="ai-assessment-stat-count">{summary.unassessable_count}</span>
                <span className="ai-assessment-stat-label">Unassessable</span>
              </div>
            )}
            {/* Always shown, including at zero. The size of the untouched
                backlog is the number a reader is most likely to be misled by
                if it is only rendered when inconvenient. */}
            <div className="ai-assessment-stat">
              <span className="ai-assessment-stat-count">{summary.unassessed_count}</span>
              <span className="ai-assessment-stat-label">Not Assessed</span>
            </div>
            {/* Always shown, including at zero: "everything has been reviewed"
                is a real and reassuring answer, and hiding the stat when it is
                zero would leave a reader unable to tell that from "this
                platform does not track review at all". */}
            <div className="ai-assessment-stat">
              <span className="ai-assessment-stat-count">{summary.awaiting_review_count}</span>
              <span className="ai-assessment-stat-label">Awaiting Confirmation</span>
            </div>
          </div>
          {summary.average_relevance_score !== null && (
            <div className="ai-assessment-score">
              Avg Relevance: <strong>{Math.round(summary.average_relevance_score)}/100</strong>
            </div>
          )}
        </>
      ) : (
        <div className="ai-assessment-empty">
          No evidence files have been assessed yet.
        </div>
      )}
    </div>
  )
}

/** One skip reason plus every evidence id it applied to (#788). */
interface SkipGroup {
  reason: string
  ids: string[]
}

/** Ids listed per reason, capped — the signal is the reason, not the roll call. */
const SKIP_IDS_SHOWN = 5

/**
 * Group `skipped_detail` by reason.
 *
 * The endpoint returns one row per skipped evidence id, and a capped refresh
 * can skip a hundred of them for the same reason. Listing that verbatim buries
 * the one thing the user needs — WHY — under a wall of identical strings.
 */
export function groupSkipReasons(
  detail: Array<{ evidence_id: string; reason: string }> | undefined | null,
): SkipGroup[] {
  const byReason = new Map<string, string[]>()
  for (const row of detail ?? []) {
    const reason = row.reason || 'skipped'
    const ids = byReason.get(reason)
    if (ids) ids.push(row.evidence_id)
    else byReason.set(reason, [row.evidence_id])
  }
  return [...byReason.entries()]
    .map(([reason, ids]) => ({ reason, ids }))
    .sort((a, b) => b.ids.length - a.ids.length)
}

/** "E-AST-01, E-AST-02 and 3 more" — never an unbounded list. */
export function formatSkipIds(ids: string[]): string {
  const shown = ids.slice(0, SKIP_IDS_SHOWN).join(', ')
  const rest = ids.length - SKIP_IDS_SHOWN
  return rest > 0 ? `${shown} and ${rest} more` : shown
}

// ---- Owner Workload Section ----
//
// Grouped on the resolved owner/assignee user since #781. It used to group on
// the free-text "Owner Team" box, which nothing else in the product read — so
// the workload shown here was whatever people had typed, not who was actually
// on the hook. The `edt-team-*` class names are kept: they are style hooks in
// evidence-dashboard CSS, not part of the data model.

interface OwnerWorkload {
  owner: string
  /**
   * The user behind ``owner``, or null when the card covers evidence with more
   * than one owner of that name or nobody at all (#822 phase 2). Cards key off
   * the display label, so the id is only trustworthy when it is unanimous.
   */
  ownerUserId: string | null
  total: number
  tracked: number
  notTracked: number
}

function OwnerWorkloadSection({
  organizationId,
  controls,
  scopingData,
}: {
  organizationId: string
  controls: EnrichedControl[]
  scopingData: ScopedControlsFile
}) {
  const { memberTypeOf } = useOrgMemberTypes(organizationId)
  const ownerData = useMemo(() => {
    const selectedControls = controls.filter(c => {
      const scoped = getScopedControl(scopingData, c.scf_id)
      return scoped?.selected
    })

    const evidenceMap = new Map<
      EvidenceId,
      { owner: string; ownerUserId: string | null; isTracked: boolean }
    >()
    selectedControls.forEach(control => {
      control.artifactsResolved.forEach(artifact => {
        if (!evidenceMap.has(artifact.id)) {
          const tracking = getEvidenceTracking(scopingData, artifact.id)
          evidenceMap.set(artifact.id, {
            owner: evidenceOwnerLabel(tracking),
            ownerUserId: evidenceOwnerUserId(tracking),
            isTracked: tracking?.is_tracked || false,
          })
        }
      })
    })

    const byOwner: Record<string, OwnerWorkload> = {}
    // A card whose evidence resolves to two different users of the same name
    // drops its id rather than picking one: a wrong contractor label on a
    // named person is worse than no label.
    const conflicted = new Set<string>()
    evidenceMap.forEach(({ owner, ownerUserId, isTracked }) => {
      if (!byOwner[owner]) {
        byOwner[owner] = { owner, ownerUserId, total: 0, tracked: 0, notTracked: 0 }
      } else if (byOwner[owner].ownerUserId !== ownerUserId) {
        conflicted.add(owner)
      }
      byOwner[owner].total++
      if (isTracked) byOwner[owner].tracked++
      else byOwner[owner].notTracked++
    })
    conflicted.forEach(owner => {
      byOwner[owner].ownerUserId = null
    })

    return Object.values(byOwner).sort((a, b) => b.total - a.total)
  }, [controls, scopingData])

  if (ownerData.length === 0) return null

  return (
    <div className="edt-team-section">
      <h3 className="edt-section-title">Owner Workload</h3>
      <div className="edt-team-grid">
        {ownerData.map(owner => {
          const pct = owner.total > 0 ? Math.round((owner.tracked / owner.total) * 100) : 0
          return (
            <div key={owner.owner} className="edt-team-card">
              <div className="edt-team-name">
                {owner.owner}
                <ContractorBadge
                  className="contractor-badge-inline"
                  memberType={memberTypeOf(owner.ownerUserId)}
                  personName={owner.owner}
                />
              </div>
              <div className="edt-team-stats">
                <span className="edt-team-tracked">{owner.tracked} tracked</span>
                <span className="edt-team-sep">/</span>
                <span className="edt-team-total">{owner.total} total</span>
              </div>
              <div className="progress-bar progress-bar-small">
                <div
                  className="progress-fill progress-fill-info"
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---- Main Component ----

export default function EvidenceDashboardTab({
  organizationId,
  controls,
  scopingData,
  onNavigateToEvidence,
}: EvidenceDashboardTabProps) {
  const [data, setData] = useState<EvidenceHealthResponse | null>(null)
  const [upcomingItems, setUpcomingItems] = useState<UpcomingEvidenceItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [searchQuery, setSearchQuery] = useState('')

  const loadHealth = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [healthResult, upcomingResult] = await Promise.all([
        getEvidenceHealth(organizationId),
        getUpcomingEvidence(organizationId, 14),
      ])
      setData(healthResult)
      setUpcomingItems(upcomingResult.items)
    } catch (err: any) {
      setError(err.message || 'Failed to load evidence health data')
    } finally {
      setLoading(false)
    }
  }, [organizationId])

  useEffect(() => {
    loadHealth()
  }, [loadHealth])

  const readinessScore = useMemo(() => {
    if (!data || data.summary.total_tracked === 0) return 0
    return Math.round((data.summary.green_count / data.summary.total_tracked) * 100)
  }, [data])

  const staleAlerts = useMemo(() => {
    if (!data) return []
    return data.items
      .filter(i => i.status === 'amber' || i.status === 'red')
      .sort((a, b) => stalenessSortKey(b) - stalenessSortKey(a))
  }, [data])

  const filteredItems = useMemo(() => {
    if (!data) return []
    let items = data.items

    if (statusFilter !== 'all') {
      items = items.filter((i) => i.status === statusFilter)
    }

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      items = items.filter(
        (i) =>
          i.evidence_id.toLowerCase().includes(q) ||
          (i.evidence_name && i.evidence_name.toLowerCase().includes(q)) ||
          (i.collecting_system && i.collecting_system.toLowerCase().includes(q))
      )
    }

    return items
  }, [data, statusFilter, searchQuery])

  if (loading) {
    return (
      <div className="ehd-loading">
        <div className="loading-spinner" />
        <p>Loading evidence health data...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="ehd-error">
        <p>Error: {error}</p>
        <button onClick={loadHealth} className="btn-primary">
          Retry
        </button>
      </div>
    )
  }

  if (!data) return null

  return (
    <div className="ehd-container">
      <div className="ehd-header">
        <div>
          <h2>Evidence Dashboard</h2>
          <p className="ehd-subtitle">
            Monitor evidence freshness and owner workload across your organisation
          </p>
        </div>
        <button onClick={loadHealth} className="btn-secondary ehd-refresh-btn" title="Refresh">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="23 4 23 10 17 10" />
            <polyline points="1 20 1 14 7 14" />
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
          </svg>
        </button>
      </div>

      <div className="edt-stats-row">
        <div className="edt-stats-section">
          <HealthSummaryBar summary={data.summary} />
          <HealthProgressBar summary={data.summary} />
        </div>
        <div className="edt-stats-section edt-readiness-section">
          <div className="edt-readiness-card">
            <div className="edt-readiness-score">{readinessScore}%</div>
            <div className="edt-readiness-label">Readiness Score</div>
            <div className="edt-readiness-desc">Evidence items with fresh status</div>
          </div>
        </div>
        <div className="edt-stats-section">
          <AssessmentSummaryCard organizationId={organizationId} />
          <FileAssessmentSummaryCard organizationId={organizationId} />
          <AssessmentReviewQueueCard orgId={organizationId} />
        </div>
      </div>

      <OwnerWorkloadSection
        organizationId={organizationId}
        controls={controls}
        scopingData={scopingData}
      />

      {/* Due Soon */}
      {upcomingItems.length > 0 && (
        <div className="edt-due-soon-section">
          <h3 className="edt-section-title">Due Soon</h3>
          <div className="edt-due-soon-table-wrapper">
            <table className="edt-due-soon-table">
              <thead>
                <tr>
                  <th>Evidence ID</th>
                  <th>Frequency</th>
                  <th>System</th>
                  <th>Last Upload</th>
                  <th>Due</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {upcomingItems.map(item => (
                  <tr
                    key={item.evidence_id}
                    className={`${item.is_overdue ? 'edt-row-overdue' : ''}${onNavigateToEvidence ? ' cursor-pointer' : ''}`}
                    onClick={() => onNavigateToEvidence?.(item.evidence_id)}
                  >
                    <td className="cell-id">{item.evidence_id}</td>
                    <td>{frequencyLabel(item.frequency) || '-'}</td>
                    <td>{item.collecting_system || '-'}</td>
                    <td>{item.last_uploaded_at ? new Date(item.last_uploaded_at).toLocaleDateString() : 'Never'}</td>
                    <td>{item.next_due ? new Date(item.next_due).toLocaleDateString() : '-'}</td>
                    <td>
                      {item.days_until_due === null ? (
                        // Never collected: there is no due date to be late
                        // against, so there is no day count to show (#788).
                        // Matches "Never uploaded" in the stale list below —
                        // one component, one way of saying "no such number".
                        <span className="edt-badge-overdue">Never collected</span>
                      ) : item.is_overdue ? (
                        <span className="edt-badge-overdue">Overdue ({Math.abs(item.days_until_due)}d)</span>
                      ) : (
                        <span className="edt-badge-upcoming">In {item.days_until_due}d</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Stale Alerts */}
      {staleAlerts.length > 0 && (
        <div className="edt-stale-section">
          <h3 className="edt-section-title">Stale Evidence Alerts ({staleAlerts.length})</h3>
          <div className="edt-stale-list">
            {staleAlerts.slice(0, 10).map(item => (
              <div
                key={item.evidence_id}
                className={`edt-stale-item edt-stale-${item.status}${onNavigateToEvidence ? ' cursor-pointer' : ''}`}
                {...interactiveRowProps(
                  onNavigateToEvidence && (() => onNavigateToEvidence(item.evidence_id)),
                )}
              >
                <StatusDot status={item.status} />
                <span className="edt-stale-id">{item.evidence_id}</span>
                {item.evidence_name && <span className="edt-stale-name">{item.evidence_name}</span>}
                {/*
                  This used to read "{days_since_upload}d overdue", which was
                  wrong twice over: the number was days since the file arrived,
                  not days past due, and it was the wrong measure now that
                  status follows coverage. Say what the number is.
                */}
                <span className="edt-stale-age" data-testid="edt-stale-age">
                  {item.days_since_coverage !== null
                    ? `Covers to ${coverageLabel(item)}`
                    : 'No coverage'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <HealthFilterBar
        filter={statusFilter}
        onFilterChange={setStatusFilter}
        query={searchQuery}
        onQueryChange={setSearchQuery}
      />

      {filteredItems.length === 0 ? (
        <div className="ehd-empty">
          <p>No evidence items match the current filter.</p>
        </div>
      ) : (
        <div className="ehd-grid">
          {filteredItems.map((item) => (
            <HealthCard key={item.evidence_id} item={item} onNavigateToEvidence={onNavigateToEvidence} />
          ))}
        </div>
      )}
    </div>
  )
}
