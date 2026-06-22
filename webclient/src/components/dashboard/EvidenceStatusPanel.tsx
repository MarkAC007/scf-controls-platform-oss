import { useState } from 'react'
import { MaturityDistributionWidget } from '../maturity'

interface EvidenceGap {
  evidence_id: string
  evidence_title?: string
  required_by_controls: string[]
  capable_systems: string[]
  recommended_action?: string
}

interface EvidenceGaps {
  total_gaps: number
  total_tracked: number
  total_evidence: number
  coverage_percentage: number
  gaps: EvidenceGap[]
}

interface EvidenceStatusPanelProps {
  trackedEvidence: number
  totalEvidence: number
  evidencePercentage: number
  evidenceMaturityDistribution: Record<string, number>
  evidenceGaps: EvidenceGaps | null
  loadingGaps: boolean
  evidenceByTeamCounts: Record<string, { total: number; tracked: number }>
}

function EvidenceStatusPanel({
  trackedEvidence,
  totalEvidence,
  evidencePercentage,
  evidenceMaturityDistribution,
  evidenceGaps,
  loadingGaps,
  evidenceByTeamCounts
}: EvidenceStatusPanelProps) {
  const [teamBreakdownExpanded, setTeamBreakdownExpanded] = useState(false)

  const clampedPercentage = Math.min(Math.max(evidencePercentage, 0), 100)
  const teamEntries = Object.entries(evidenceByTeamCounts)

  return (
    <div className="evidence-status-panel">
      {/* Header */}
      <div className="esp-header">
        <h3>Evidence Status</h3>
        <span className="esp-badge">{clampedPercentage}%</span>
      </div>

      {/* Progress */}
      <div className="esp-progress">
        <div className="esp-progress-text">
          {trackedEvidence}/{totalEvidence} tracked
        </div>
        <div className="esp-progress-bar">
          <div
            className="esp-progress-fill"
            style={{ width: `${clampedPercentage}%` }}
          />
        </div>
        <div className="esp-progress-text">{clampedPercentage}%</div>
      </div>

      {/* Maturity Distribution */}
      <MaturityDistributionWidget
        distribution={evidenceMaturityDistribution as Record<string, number> & Record<'L0' | 'L1' | 'L2' | 'L3' | 'L4' | 'L5', number>}
        compact={true}
        showScore={true}
        showLegend={false}
      />

      {/* Top 5 Evidence Gaps */}
      <div className="esp-gaps">
        {loadingGaps ? (
          <p>Loading...</p>
        ) : evidenceGaps && evidenceGaps.gaps.length > 0 ? (
          <ul>
            {evidenceGaps.gaps.slice(0, 5).map((gap) => (
              <li key={gap.evidence_id} className="esp-gap-item">
                <span className="esp-gap-id">{gap.evidence_id}</span>
                <span className="esp-gap-count">
                  {gap.required_by_controls.length} control{gap.required_by_controls.length !== 1 ? 's' : ''}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p>
            <span className="esp-check">&#10003;</span> All evidence tracked
          </p>
        )}
      </div>

      {/* Team Burden Breakdown */}
      {teamEntries.length > 0 && (
        <div className="esp-teams">
          <button
            type="button"
            className="esp-expand-toggle"
            onClick={() => setTeamBreakdownExpanded((prev) => !prev)}
            aria-expanded={teamBreakdownExpanded}
          >
            {teamBreakdownExpanded ? 'Hide' : 'Show'} Team Breakdown
          </button>

          {teamBreakdownExpanded && (
            <div className="esp-teams-list">
              {teamEntries.map(([team, counts]) => {
                const teamPercentage =
                  counts.total > 0
                    ? Math.round((counts.tracked / counts.total) * 100)
                    : 0

                return (
                  <div key={team} className="esp-team-item">
                    <div className="esp-team-label">
                      <span className="esp-team-name">{team}</span>
                      <span className="esp-team-count">
                        {counts.tracked}/{counts.total}
                      </span>
                    </div>
                    <div className="esp-progress-bar">
                      <div
                        className="esp-progress-fill"
                        style={{ width: `${teamPercentage}%` }}
                      />
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default EvidenceStatusPanel
