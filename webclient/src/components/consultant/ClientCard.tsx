import type { ClientSummary } from '../../types'

interface ClientCardProps {
  client: ClientSummary
  isCurrentOrg?: boolean
}

type ReadinessGrade = 'excellent' | 'good' | 'fair' | 'needs-work'

function getReadinessGrade(percent: number): ReadinessGrade {
  if (percent >= 90) return 'excellent'
  if (percent >= 70) return 'good'
  if (percent >= 50) return 'fair'
  return 'needs-work'
}

const GRADE_LABEL: Record<ReadinessGrade, string> = {
  excellent: 'Excellent',
  good: 'Good',
  fair: 'Fair',
  'needs-work': 'Needs Work',
}

function formatDate(dateString: string): string {
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

export default function ClientCard({ client, isCurrentOrg }: ClientCardProps) {
  const grade = getReadinessGrade(client.framework_readiness_percent)
  const implementationPercent = client.controls_total > 0
    ? Math.round((client.controls_implemented / client.controls_total) * 100)
    : 0

  return (
    <div className={`consultant-client-card${isCurrentOrg ? ' card-current' : ''}`}>
      {/* Header: org icon · name · badge */}
      <div className="consultant-card-header">
        <div className="consultant-card-org-icon">
          {client.organization_name.charAt(0).toUpperCase()}
        </div>
        <span className="consultant-card-org-name">{client.organization_name}</span>
        {isCurrentOrg && (
          <span className="consultant-card-current-badge">Current</span>
        )}
        {client.primary_framework && (
          <span className="consultant-card-framework-badge">{client.primary_framework}</span>
        )}
      </div>

      {/* Readiness */}
      <div className="consultant-card-readiness">
        <div className="consultant-card-readiness-header">
          <span className="consultant-card-readiness-label">Framework Readiness</span>
          <span className={`consultant-card-readiness-badge badge-${grade}`}>
            {GRADE_LABEL[grade]}
          </span>
        </div>
        <div className="consultant-card-readiness-bar">
          <div
            className={`consultant-card-readiness-fill fill-${grade}`}
            style={{ width: `${client.framework_readiness_percent}%` }}
          />
        </div>
        <span className="consultant-card-readiness-pct">{client.framework_readiness_percent}%</span>
      </div>

      {/* Controls stats */}
      <div className="consultant-card-stats">
        <div className="consultant-card-controls-row">
          <span className="consultant-card-controls-value">
            {client.controls_implemented}
            <span className="consultant-card-controls-denom">/{client.controls_total}</span>
          </span>
          <span className="consultant-card-controls-label">controls implemented</span>
        </div>
        <div className="consultant-card-impl-bar">
          <div
            className="consultant-card-impl-fill"
            style={{ width: `${implementationPercent}%` }}
          />
        </div>
        <div className="consultant-card-status-row">
          <span className="consultant-card-status-item">
            <span className="consultant-card-status-dot dot-in-progress" />
            {client.controls_in_progress} in progress
          </span>
          {client.controls_at_risk > 0 && (
            <span className="consultant-card-status-item item-at-risk">
              <span className="consultant-card-status-dot dot-at-risk" />
              {client.controls_at_risk} at risk
            </span>
          )}
        </div>
        <div className="consultant-card-evidence-row">
          <span className="consultant-card-evidence-value">{client.evidence_tracked}</span>
          /{client.evidence_total} evidence tracked
        </div>
      </div>

      {/* Footer */}
      <div className="consultant-card-footer">
        Last activity:{' '}
        <span className="consultant-card-activity-date">{formatDate(client.last_activity_date)}</span>
        {client.last_activity_by && (
          <> by {client.last_activity_by}</>
        )}
        {client.awaiting_admin && (
          <span className="consultant-card-awaiting-badge">Awaiting admin</span>
        )}
      </div>
    </div>
  )
}
