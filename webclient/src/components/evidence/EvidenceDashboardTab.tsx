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
  refreshStaleWindowAssessments,
  type EvidenceHealthResponse,
  type UpcomingEvidenceItem,
} from '../../data/apiClient'
import { getScopedControl, getEvidenceTracking } from '../../data/scopingService'

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
        <span className="ehd-summary-label">Fresh ({summary.green_pct}%)</span>
      </div>
      <div className="ehd-summary-stat ehd-stat-amber">
        <span className="ehd-summary-count">{summary.amber_count}</span>
        <span className="ehd-summary-label">Stale ({summary.amber_pct}%)</span>
      </div>
      <div className="ehd-summary-stat ehd-stat-red">
        <span className="ehd-summary-count">{summary.red_count}</span>
        <span className="ehd-summary-label">Critical ({summary.red_pct}%)</span>
      </div>
      {summary.unknown_count > 0 && (
        <div className="ehd-summary-stat ehd-stat-unknown">
          <span className="ehd-summary-count">{summary.unknown_count}</span>
          <span className="ehd-summary-label">No Data</span>
        </div>
      )}
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
  const filters: { value: StatusFilter; label: string }[] = [
    { value: 'all', label: 'All' },
    { value: 'green', label: 'Fresh' },
    { value: 'amber', label: 'Stale' },
    { value: 'red', label: 'Critical' },
    { value: 'unknown', label: 'No Data' },
  ]

  return (
    <div className="ehd-filter-bar">
      <div className="ehd-filter-tabs">
        {filters.map((f) => (
          <button
            key={f.value}
            className={`ehd-filter-tab ${filter === f.value ? 'active' : ''}`}
            onClick={() => onFilterChange(f.value)}
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
  const freshnessLabel =
    item.days_since_upload !== null
      ? item.days_since_upload === 0
        ? 'Today'
        : `${item.days_since_upload}d ago`
      : 'Never'

  return (
    <div
      className={`ehd-card ehd-card-${item.status}${onNavigateToEvidence ? ' cursor-pointer' : ''}`}
      onClick={() => onNavigateToEvidence?.(item.evidence_id)}
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
          <span className="ehd-card-freq">{item.frequency}</span>
        )}
      </div>
      <div className="ehd-card-footer">
        <span className="ehd-card-freshness">
          Last upload: <strong>{freshnessLabel}</strong>
        </span>
        {item.staleness_threshold_days !== null && (
          <span className="ehd-card-threshold">
            Threshold: {item.staleness_threshold_days}d
          </span>
        )}
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
    try {
      const result = await refreshStaleWindowAssessments(organizationId)
      if (result.queued === 0 && result.candidates === 0) {
        setRefreshMessage('All windows up to date')
      } else {
        setRefreshMessage(`Queued ${result.queued} of ${result.candidates}`)
      }
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
          <span style={{ marginLeft: 10, fontSize: '0.78rem', color: '#6366f1' }}>
            {refreshMessage}
          </span>
        )}
      </div>
    </div>
  )
}

// ---- Team Workload Section ----

interface TeamWorkload {
  team: string
  total: number
  tracked: number
  notTracked: number
}

function TeamWorkloadSection({
  controls,
  scopingData,
}: {
  controls: EnrichedControl[]
  scopingData: ScopedControlsFile
}) {
  const teamData = useMemo(() => {
    const selectedControls = controls.filter(c => {
      const scoped = getScopedControl(scopingData, c.scf_id)
      return scoped?.selected
    })

    const evidenceMap = new Map<EvidenceId, { owner: string; isTracked: boolean }>()
    selectedControls.forEach(control => {
      control.artifactsResolved.forEach(artifact => {
        if (!evidenceMap.has(artifact.id)) {
          const tracking = getEvidenceTracking(scopingData, artifact.id)
          evidenceMap.set(artifact.id, {
            owner: tracking?.owner || 'Unassigned',
            isTracked: tracking?.is_tracked || false,
          })
        }
      })
    })

    const byTeam: Record<string, TeamWorkload> = {}
    evidenceMap.forEach(({ owner, isTracked }) => {
      if (!byTeam[owner]) {
        byTeam[owner] = { team: owner, total: 0, tracked: 0, notTracked: 0 }
      }
      byTeam[owner].total++
      if (isTracked) byTeam[owner].tracked++
      else byTeam[owner].notTracked++
    })

    return Object.values(byTeam).sort((a, b) => b.total - a.total)
  }, [controls, scopingData])

  if (teamData.length === 0) return null

  return (
    <div className="edt-team-section">
      <h3 className="edt-section-title">Team Workload</h3>
      <div className="edt-team-grid">
        {teamData.map(team => {
          const pct = team.total > 0 ? Math.round((team.tracked / team.total) * 100) : 0
          return (
            <div key={team.team} className="edt-team-card">
              <div className="edt-team-name">{team.team}</div>
              <div className="edt-team-stats">
                <span className="edt-team-tracked">{team.tracked} tracked</span>
                <span className="edt-team-sep">/</span>
                <span className="edt-team-total">{team.total} total</span>
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
      .sort((a, b) => (b.days_since_upload ?? 999) - (a.days_since_upload ?? 999))
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
            Monitor evidence freshness and team workload across your organisation
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
        </div>
      </div>

      <TeamWorkloadSection controls={controls} scopingData={scopingData} />

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
                    <td>{item.frequency || '-'}</td>
                    <td>{item.collecting_system || '-'}</td>
                    <td>{item.last_uploaded_at ? new Date(item.last_uploaded_at).toLocaleDateString() : 'Never'}</td>
                    <td>{item.next_due ? new Date(item.next_due).toLocaleDateString() : '-'}</td>
                    <td>
                      {item.is_overdue ? (
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
                onClick={() => onNavigateToEvidence?.(item.evidence_id)}
              >
                <StatusDot status={item.status} />
                <span className="edt-stale-id">{item.evidence_id}</span>
                {item.evidence_name && <span className="edt-stale-name">{item.evidence_name}</span>}
                <span className="edt-stale-age">
                  {item.days_since_upload !== null ? `${item.days_since_upload}d overdue` : 'Never uploaded'}
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
