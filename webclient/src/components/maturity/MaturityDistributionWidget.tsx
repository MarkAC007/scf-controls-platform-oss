import { useMemo } from 'react'
import type { EvidenceMaturityLevel } from './EvidenceMaturityTypes'
import {
  EVIDENCE_MATURITY_LEVELS,
  calculateMaturityScore,
  getMaturityGrade
} from './EvidenceMaturityTypes'

interface MaturityDistributionWidgetProps {
  distribution: Record<EvidenceMaturityLevel, number>
  title?: string
  showScore?: boolean
  showLegend?: boolean
  compact?: boolean
  className?: string
}

/**
 * MaturityDistributionWidget Component
 *
 * Dashboard widget showing organisation-wide evidence maturity distribution.
 * Displays a horizontal bar chart with counts at each level, overall score,
 * and maturity grade.
 *
 * Usage:
 *   <MaturityDistributionWidget
 *     distribution={{ L0: 5, L1: 10, L2: 25, L3: 30, L4: 20, L5: 10 }}
 *   />
 */
export function MaturityDistributionWidget({
  distribution,
  title = 'Evidence Collection Maturity',
  showScore = true,
  showLegend = true,
  compact = false,
  className = ''
}: MaturityDistributionWidgetProps) {
  const totalItems = useMemo(() => {
    return Object.values(distribution).reduce((sum, count) => sum + count, 0)
  }, [distribution])

  const maturityScore = useMemo(() => {
    return calculateMaturityScore(distribution)
  }, [distribution])

  const grade = useMemo(() => {
    return getMaturityGrade(maturityScore)
  }, [maturityScore])

  const levels: EvidenceMaturityLevel[] = ['L5', 'L4', 'L3', 'L2', 'L1', 'L0']

  // Calculate max value for bar scaling
  const maxCount = useMemo(() => {
    return Math.max(...Object.values(distribution), 1)
  }, [distribution])

  if (totalItems === 0) {
    return (
      <div className={`maturity-distribution-widget ${className}`}>
        <div className="maturity-distribution-header">
          <h3>{title}</h3>
        </div>
        <div className="maturity-distribution-empty">
          <span className="maturity-distribution-empty-icon">\uD83D\uDCCA</span>
          <p>No evidence items to analyse</p>
          <span className="maturity-distribution-empty-hint">
            Configure evidence tracking to see maturity distribution
          </span>
        </div>
      </div>
    )
  }

  return (
    <div className={`maturity-distribution-widget ${compact ? 'compact' : ''} ${className}`}>
      <div className="maturity-distribution-header">
        <h3>{title}</h3>
        {showScore && (
          <div className="maturity-distribution-score">
            <span className={`maturity-grade maturity-grade-${grade.grade.toLowerCase()}`}>
              {grade.grade}
            </span>
            <div className="maturity-score-details">
              <span className="maturity-score-value">{maturityScore.toFixed(1)}</span>
              <span className="maturity-score-label">{grade.label}</span>
            </div>
          </div>
        )}
      </div>

      <div className="maturity-distribution-summary">
        <span className="maturity-distribution-total">{totalItems}</span>
        <span className="maturity-distribution-total-label">evidence items assessed</span>
      </div>

      <div className="maturity-distribution-chart">
        {levels.map((level) => {
          const info = EVIDENCE_MATURITY_LEVELS[level]
          const count = distribution[level] || 0
          const percentage = totalItems > 0 ? (count / totalItems) * 100 : 0
          const barWidth = maxCount > 0 ? (count / maxCount) * 100 : 0

          return (
            <div key={level} className="maturity-distribution-row">
              <div className="maturity-distribution-label">
                <span
                  className="maturity-distribution-dot"
                  style={{ backgroundColor: info.colour }}
                />
                <span className="maturity-distribution-level">{level}</span>
                {!compact && (
                  <span className="maturity-distribution-name">{info.name}</span>
                )}
              </div>
              <div className="maturity-distribution-bar-container">
                <div className="maturity-distribution-bar-track">
                  <div
                    className="maturity-distribution-bar-fill"
                    style={{
                      width: `${barWidth}%`,
                      backgroundColor: info.colour
                    }}
                  />
                </div>
              </div>
              <div className="maturity-distribution-value">
                <span className="maturity-distribution-count">{count}</span>
                {!compact && (
                  <span className="maturity-distribution-percent">
                    ({percentage.toFixed(0)}%)
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {showLegend && !compact && (
        <div className="maturity-distribution-legend">
          <div className="maturity-distribution-legend-title">Maturity Levels</div>
          <div className="maturity-distribution-legend-grid">
            {levels.reverse().map((level) => {
              const info = EVIDENCE_MATURITY_LEVELS[level]
              return (
                <div key={level} className="maturity-distribution-legend-item">
                  <span
                    className="maturity-distribution-legend-dot"
                    style={{ backgroundColor: info.colour }}
                  />
                  <span className="maturity-distribution-legend-level">{level}</span>
                  <span className="maturity-distribution-legend-name">{info.name}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

export default MaturityDistributionWidget
