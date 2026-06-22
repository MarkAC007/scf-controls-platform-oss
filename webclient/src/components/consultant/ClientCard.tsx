import type { ClientSummary } from '../../types'

interface ClientCardProps {
  client: ClientSummary
  isCurrentOrg?: boolean
}

export default function ClientCard({ client, isCurrentOrg }: ClientCardProps) {
  const getReadinessGrade = (percent: number): { label: string; className: string } => {
    if (percent >= 90) return { label: 'Excellent', className: 'readiness-excellent' }
    if (percent >= 70) return { label: 'Good', className: 'readiness-good' }
    if (percent >= 50) return { label: 'Fair', className: 'readiness-fair' }
    return { label: 'Needs Work', className: 'readiness-needs-work' }
  }

  const formatDate = (dateString: string): string => {
    const date = new Date(dateString)
    const now = new Date()
    const diffMs = now.getTime() - date.getTime()
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))

    if (diffDays === 0) return 'Today'
    if (diffDays === 1) return 'Yesterday'
    if (diffDays < 7) return `${diffDays} days ago`
    if (diffDays < 30) return `${Math.floor(diffDays / 7)} weeks ago`
    return date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
  }

  const readiness = getReadinessGrade(client.framework_readiness_percent)
  const implementationPercent = client.controls_total > 0
    ? Math.round((client.controls_implemented / client.controls_total) * 100)
    : 0

  return (
    <div className={`client-card ${isCurrentOrg ? 'client-card-current' : ''}`}>
      {isCurrentOrg && (
        <div className="client-card-current-badge">Current</div>
      )}

      <div className="client-card-header">
        <div className="client-card-org-icon">
          {client.organization_name.charAt(0).toUpperCase()}
        </div>
        <div className="client-card-title">
          <h3>{client.organization_name}</h3>
          {client.primary_framework && (
            <span className="client-card-framework">{client.primary_framework}</span>
          )}
        </div>
      </div>

      <div className="client-card-readiness">
        <div className="readiness-header">
          <span className="readiness-label">Framework Readiness</span>
          <span className={`readiness-badge ${readiness.className}`}>
            {readiness.label}
          </span>
        </div>
        <div className="readiness-bar-container">
          <div
            className={`readiness-bar-fill ${readiness.className}`}
            style={{ width: `${client.framework_readiness_percent}%` }}
          />
        </div>
        <div className="readiness-percent">{client.framework_readiness_percent}%</div>
      </div>

      <div className="client-card-stats">
        <div className="client-stat">
          <div className="client-stat-value">
            {client.controls_implemented}
            <span className="client-stat-total">/{client.controls_total}</span>
          </div>
          <div className="client-stat-label">Controls</div>
          <div className="client-stat-bar">
            <div
              className="client-stat-bar-fill implemented"
              style={{ width: `${implementationPercent}%` }}
            />
          </div>
        </div>

        <div className="client-stat-inline">
          <div className="client-stat-item">
            <span className="status-dot status-in_progress" />
            <span>{client.controls_in_progress} in progress</span>
          </div>
          {client.controls_at_risk > 0 && (
            <div className="client-stat-item at-risk">
              <span className="status-dot status-at_risk" />
              <span>{client.controls_at_risk} at risk</span>
            </div>
          )}
        </div>

        <div className="client-stat evidence">
          <div className="client-stat-value">
            {client.evidence_tracked}
            <span className="client-stat-total">/{client.evidence_total}</span>
          </div>
          <div className="client-stat-label">Evidence Tracked</div>
        </div>
      </div>

      <div className="client-card-footer">
        <div className="client-card-activity">
          <span className="activity-label">Last activity:</span>
          <span className="activity-date">{formatDate(client.last_activity_date)}</span>
          {client.last_activity_by && (
            <span className="activity-by">by {client.last_activity_by}</span>
          )}
        </div>

      </div>
    </div>
  )
}
