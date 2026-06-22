import { useState, useEffect, useMemo, useCallback } from 'react'
import { getEvidenceHealth, type EvidenceHealthResponse } from '../../data/apiClient'

// ---- Types ----

type StatusFilter = 'all' | 'green' | 'amber' | 'red' | 'unknown'

interface EvidenceHealthDashboardProps {
  organizationId: string
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

function StatusDot({ status }: { status: string }) {
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

function HealthCard({ item, onNavigateToEvidence }: { item: EvidenceHealthResponse['items'][0]; onNavigateToEvidence?: (evidenceId: string) => void }) {
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
    </div>
  )
}

// ---- Main Component ----

export default function EvidenceHealthDashboard({ organizationId, onNavigateToEvidence }: EvidenceHealthDashboardProps) {
  const [data, setData] = useState<EvidenceHealthResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [searchQuery, setSearchQuery] = useState('')

  const loadHealth = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await getEvidenceHealth(organizationId)
      setData(result)
    } catch (err: any) {
      setError(err.message || 'Failed to load evidence health data')
    } finally {
      setLoading(false)
    }
  }, [organizationId])

  useEffect(() => {
    loadHealth()
  }, [loadHealth])

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
          <h2>Evidence Health</h2>
          <p className="ehd-subtitle">
            Monitor evidence freshness across your organisation
          </p>
        </div>
        <button onClick={loadHealth} className="btn-secondary ehd-refresh-btn">
          Refresh
        </button>
      </div>

      <HealthSummaryBar summary={data.summary} />
      <HealthProgressBar summary={data.summary} />

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
