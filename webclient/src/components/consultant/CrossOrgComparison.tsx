import { useState, useMemo } from 'react'
import type { ClientSummary } from '../../types'

interface CrossOrgComparisonProps {
  clients: ClientSummary[]
  currentOrgId?: string
}

type SortColumn = 'name' | 'readiness' | 'controls' | 'evidence' | 'activity'
type SortDirection = 'asc' | 'desc'

type ReadinessGrade = 'excellent' | 'good' | 'fair' | 'needs-work'

function getReadinessGrade(percent: number): ReadinessGrade {
  if (percent >= 90) return 'excellent'
  if (percent >= 70) return 'good'
  if (percent >= 50) return 'fair'
  return 'needs-work'
}

function formatDate(dateString: string): string {
  const date = new Date(dateString)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))

  if (diffDays === 0) return 'Today'
  if (diffDays === 1) return 'Yesterday'
  if (diffDays < 7) return `${diffDays}d ago`
  return date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

function deltaClass(value: number, average: number): string {
  if (value >= average + 10) return 'consultant-comparison-delta-above'
  if (value <= average - 10) return 'consultant-comparison-delta-below'
  return 'consultant-comparison-delta-avg'
}

function deltaLabel(value: number, average: number): string {
  const diff = Math.round(value - average)
  if (Math.abs(diff) < 1) return '— avg'
  return diff > 0 ? `▲ +${diff}` : `▼ ${diff}`
}

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
        case 'controls': {
          const aControlPercent = a.controls_total > 0 ? a.controls_implemented / a.controls_total : 0
          const bControlPercent = b.controls_total > 0 ? b.controls_implemented / b.controls_total : 0
          comparison = aControlPercent - bControlPercent
          break
        }
        case 'evidence': {
          const aEvidencePercent = a.evidence_total > 0 ? a.evidence_tracked / a.evidence_total : 0
          const bEvidencePercent = b.evidence_total > 0 ? b.evidence_tracked / b.evidence_total : 0
          comparison = aEvidencePercent - bEvidencePercent
          break
        }
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

  if (clients.length === 0) {
    return (
      <div className="comparison-empty">
        <p>No clients to compare</p>
      </div>
    )
  }

  const sortIndicator = (col: SortColumn) => {
    if (sortColumn !== col) return null
    return sortDirection === 'asc' ? ' ▲' : ' ▾'
  }

  return (
    <div className="consultant-comparison">
      <div className="consultant-comparison-heading">Cross-Org Comparison — vs Portfolio Average</div>

      <table className="consultant-comparison-table">
        <thead>
          <tr>
            <th className="col-org" onClick={() => handleSort('name')}>
              Organisation{sortIndicator('name')}
            </th>
            <th className="col-metric" onClick={() => handleSort('readiness')}>
              Readiness{sortIndicator('readiness')}
            </th>
            <th className="col-metric" onClick={() => handleSort('controls')}>
              Controls{sortIndicator('controls')}
            </th>
            <th className="col-metric" onClick={() => handleSort('evidence')}>
              Evidence{sortIndicator('evidence')}
            </th>
            <th onClick={() => handleSort('activity')}>
              Last Activity{sortIndicator('activity')}
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
            const isCurrent = client.organization_id === currentOrgId

            return (
              <tr key={client.organization_id} className={isCurrent ? 'row-current' : ''}>
                <td>
                  <span className="consultant-comparison-org-name">
                    {client.organization_name}
                  </span>
                  {isCurrent && (
                    <span className="consultant-comparison-current-tag">· current</span>
                  )}
                </td>
                <td>
                  {client.framework_readiness_percent}%{' '}
                  {averages && (
                    <span className={deltaClass(client.framework_readiness_percent, averages.readiness)}>
                      {deltaLabel(client.framework_readiness_percent, averages.readiness)}
                    </span>
                  )}
                </td>
                <td>
                  {controlPercent}%{' '}
                  {averages && (
                    <span className={deltaClass(controlPercent, averages.controls)}>
                      {deltaLabel(controlPercent, averages.controls)}
                    </span>
                  )}
                </td>
                <td>
                  {evidencePercent}%{' '}
                  {averages && (
                    <span className={deltaClass(evidencePercent, averages.evidence)}>
                      {deltaLabel(evidencePercent, averages.evidence)}
                    </span>
                  )}
                </td>
                <td className="consultant-comparison-activity">
                  {formatDate(client.last_activity_date)}
                </td>
              </tr>
            )
          })}
        </tbody>
        {/* Portfolio average footer row */}
        {averages && (
          <tfoot>
            <tr className="consultant-comparison-avg-row">
              <td>Portfolio Avg</td>
              <td>{averages.readiness}%</td>
              <td>{averages.controls}%</td>
              <td>{averages.evidence}%</td>
              <td></td>
            </tr>
          </tfoot>
        )}
      </table>

      {/* Readiness distribution chart */}
      <div className="consultant-distribution">
        {sortedClients.map(client => {
          const grade = client.framework_readiness_percent >= 90 ? 'excellent'
            : client.framework_readiness_percent >= 70 ? 'good'
            : client.framework_readiness_percent >= 50 ? 'fair'
            : 'needs-work'

          return (
            <div key={client.organization_id} className="consultant-distribution-bar-wrapper">
              <span
                className="consultant-distribution-label"
                title={client.organization_name}
              >
                {client.organization_name.length > 15
                  ? client.organization_name.substring(0, 15) + '…'
                  : client.organization_name}
              </span>
              <div className="consultant-distribution-bar-track">
                <div
                  className={`consultant-distribution-bar-fill dist-${grade}`}
                  style={{ width: `${client.framework_readiness_percent}%` }}
                />
              </div>
              <span className="consultant-distribution-value">
                {client.framework_readiness_percent}%
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
