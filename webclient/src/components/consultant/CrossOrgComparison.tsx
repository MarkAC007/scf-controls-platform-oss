import { useState, useMemo } from 'react'
import type { ClientSummary } from '../../types'

interface CrossOrgComparisonProps {
  clients: ClientSummary[]
  currentOrgId?: string
}

type SortColumn = 'name' | 'readiness' | 'controls' | 'evidence' | 'activity'
type SortDirection = 'asc' | 'desc'

export default function CrossOrgComparison({ clients, currentOrgId }: CrossOrgComparisonProps) {
  const [sortColumn, setSortColumn] = useState<SortColumn>('readiness')
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc')

  const handleSort = (column: SortColumn) => {
    if (sortColumn === column) {
      setSortDirection(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortColumn(column)
      setSortDirection('desc')
    }
  }

  const sortedClients = useMemo(() => {
    const sorted = [...clients].sort((a, b) => {
      let comparison = 0

      switch (sortColumn) {
        case 'name':
          comparison = a.organization_name.localeCompare(b.organization_name)
          break
        case 'readiness':
          comparison = a.framework_readiness_percent - b.framework_readiness_percent
          break
        case 'controls':
          const aControlPercent = a.controls_total > 0 ? a.controls_implemented / a.controls_total : 0
          const bControlPercent = b.controls_total > 0 ? b.controls_implemented / b.controls_total : 0
          comparison = aControlPercent - bControlPercent
          break
        case 'evidence':
          const aEvidencePercent = a.evidence_total > 0 ? a.evidence_tracked / a.evidence_total : 0
          const bEvidencePercent = b.evidence_total > 0 ? b.evidence_tracked / b.evidence_total : 0
          comparison = aEvidencePercent - bEvidencePercent
          break
        case 'activity':
          comparison = new Date(a.last_activity_date).getTime() - new Date(b.last_activity_date).getTime()
          break
      }

      return sortDirection === 'asc' ? comparison : -comparison
    })

    return sorted
  }, [clients, sortColumn, sortDirection])

  // Calculate averages for comparison
  const averages = useMemo(() => {
    if (clients.length === 0) return null

    const avgReadiness = clients.reduce((sum, c) => sum + c.framework_readiness_percent, 0) / clients.length
    const avgControlPercent = clients.reduce((sum, c) => {
      return sum + (c.controls_total > 0 ? (c.controls_implemented / c.controls_total) * 100 : 0)
    }, 0) / clients.length
    const avgEvidencePercent = clients.reduce((sum, c) => {
      return sum + (c.evidence_total > 0 ? (c.evidence_tracked / c.evidence_total) * 100 : 0)
    }, 0) / clients.length

    return {
      readiness: Math.round(avgReadiness),
      controls: Math.round(avgControlPercent),
      evidence: Math.round(avgEvidencePercent)
    }
  }, [clients])

  const getReadinessClass = (percent: number): string => {
    if (percent >= 90) return 'readiness-excellent'
    if (percent >= 70) return 'readiness-good'
    if (percent >= 50) return 'readiness-fair'
    return 'readiness-needs-work'
  }

  const getComparisonClass = (value: number, average: number): string => {
    if (value >= average + 10) return 'comparison-above'
    if (value <= average - 10) return 'comparison-below'
    return 'comparison-average'
  }

  const formatDate = (dateString: string): string => {
    const date = new Date(dateString)
    const now = new Date()
    const diffMs = now.getTime() - date.getTime()
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))

    if (diffDays === 0) return 'Today'
    if (diffDays === 1) return 'Yesterday'
    if (diffDays < 7) return `${diffDays}d ago`
    return date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
  }

  const SortIcon = ({ column }: { column: SortColumn }) => (
    <span className={`sort-icon ${sortColumn === column ? 'active' : ''}`}>
      {sortColumn === column && sortDirection === 'asc' ? (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 5l7 7H5z" />
        </svg>
      ) : sortColumn === column && sortDirection === 'desc' ? (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 19l-7-7h14z" />
        </svg>
      ) : (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" opacity="0.3">
          <path d="M12 5l4 4H8zM12 19l-4-4h8z" />
        </svg>
      )}
    </span>
  )

  if (clients.length === 0) {
    return (
      <div className="comparison-empty">
        <p>No clients to compare</p>
      </div>
    )
  }

  return (
    <div className="cross-org-comparison">
      {/* Summary Bar */}
      {averages && (
        <div className="comparison-summary">
          <div className="comparison-summary-item">
            <span className="summary-label">Portfolio Avg. Readiness</span>
            <span className={`summary-value ${getReadinessClass(averages.readiness)}`}>
              {averages.readiness}%
            </span>
          </div>
          <div className="comparison-summary-item">
            <span className="summary-label">Avg. Control Implementation</span>
            <span className="summary-value">{averages.controls}%</span>
          </div>
          <div className="comparison-summary-item">
            <span className="summary-label">Avg. Evidence Tracking</span>
            <span className="summary-value">{averages.evidence}%</span>
          </div>
        </div>
      )}

      {/* Comparison Table */}
      <div className="comparison-table-container">
        <table className="comparison-table">
          <thead>
            <tr>
              <th
                className={`sortable ${sortColumn === 'name' ? 'sorted' : ''}`}
                onClick={() => handleSort('name')}
              >
                Organisation
                <SortIcon column="name" />
              </th>
              <th
                className={`sortable ${sortColumn === 'readiness' ? 'sorted' : ''}`}
                onClick={() => handleSort('readiness')}
              >
                Readiness
                <SortIcon column="readiness" />
              </th>
              <th
                className={`sortable ${sortColumn === 'controls' ? 'sorted' : ''}`}
                onClick={() => handleSort('controls')}
              >
                Controls
                <SortIcon column="controls" />
              </th>
              <th
                className={`sortable ${sortColumn === 'evidence' ? 'sorted' : ''}`}
                onClick={() => handleSort('evidence')}
              >
                Evidence
                <SortIcon column="evidence" />
              </th>
              <th>At Risk</th>
              <th
                className={`sortable ${sortColumn === 'activity' ? 'sorted' : ''}`}
                onClick={() => handleSort('activity')}
              >
                Last Activity
                <SortIcon column="activity" />
              </th>
            </tr>
          </thead>
          <tbody>
            {sortedClients.map(client => {
              const controlPercent = client.controls_total > 0
                ? Math.round((client.controls_implemented / client.controls_total) * 100)
                : 0
              const evidencePercent = client.evidence_total > 0
                ? Math.round((client.evidence_tracked / client.evidence_total) * 100)
                : 0

              return (
                <tr
                  key={client.organization_id}
                  className={client.organization_id === currentOrgId ? 'current-org-row' : ''}
                >
                  <td className="org-cell">
                    <div className="org-cell-content">
                      <div className="org-icon">
                        {client.organization_name.charAt(0).toUpperCase()}
                      </div>
                      <div className="org-info">
                        <span className="org-name">{client.organization_name}</span>
                        {client.primary_framework && (
                          <span className="org-framework">{client.primary_framework}</span>
                        )}
                      </div>
                      {client.organization_id === currentOrgId && (
                        <span className="current-badge">Current</span>
                      )}
                    </div>
                  </td>
                  <td className="readiness-cell">
                    <div className={`readiness-indicator ${getReadinessClass(client.framework_readiness_percent)}`}>
                      <div className="readiness-bar">
                        <div
                          className="readiness-fill"
                          style={{ width: `${client.framework_readiness_percent}%` }}
                        />
                      </div>
                      <span className="readiness-value">{client.framework_readiness_percent}%</span>
                    </div>
                  </td>
                  <td className="controls-cell">
                    <div className={`metric-cell ${averages ? getComparisonClass(controlPercent, averages.controls) : ''}`}>
                      <span className="metric-value">
                        {client.controls_implemented}/{client.controls_total}
                      </span>
                      <span className="metric-percent">{controlPercent}%</span>
                    </div>
                  </td>
                  <td className="evidence-cell">
                    <div className={`metric-cell ${averages ? getComparisonClass(evidencePercent, averages.evidence) : ''}`}>
                      <span className="metric-value">
                        {client.evidence_tracked}/{client.evidence_total}
                      </span>
                      <span className="metric-percent">{evidencePercent}%</span>
                    </div>
                  </td>
                  <td className="risk-cell">
                    {client.controls_at_risk > 0 ? (
                      <span className="risk-badge">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
                          <line x1="12" y1="9" x2="12" y2="13" />
                          <line x1="12" y1="17" x2="12.01" y2="17" />
                        </svg>
                        {client.controls_at_risk}
                      </span>
                    ) : (
                      <span className="no-risk">-</span>
                    )}
                  </td>
                  <td className="activity-cell">
                    <span className="activity-date">{formatDate(client.last_activity_date)}</span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Chart Section - Readiness Distribution */}
      <div className="comparison-charts">
        <div className="chart-section">
          <h3>Readiness Distribution</h3>
          <div className="readiness-distribution">
            {sortedClients.map(client => (
              <div key={client.organization_id} className="distribution-bar-wrapper">
                <span className="distribution-label" title={client.organization_name}>
                  {client.organization_name.length > 15
                    ? client.organization_name.substring(0, 15) + '...'
                    : client.organization_name}
                </span>
                <div className="distribution-bar-container">
                  <div
                    className={`distribution-bar ${getReadinessClass(client.framework_readiness_percent)}`}
                    style={{ width: `${client.framework_readiness_percent}%` }}
                  />
                </div>
                <span className="distribution-value">{client.framework_readiness_percent}%</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
