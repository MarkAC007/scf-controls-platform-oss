/**
 * RiskMatrix Component - Interactive 5x5 Risk Matrix Visualisation
 *
 * Displays risks on a likelihood vs impact grid with colour-coded cells.
 * Supports toggling between inherent and residual risk views.
 */
import { useMemo } from 'react'
import type {
  RiskMatrixCell,
  RiskAssessment,
  RiskLevel,
  RiskCodesFile,
  RiskThresholds
} from '../types'
import { getRiskLevel, DEFAULT_RISK_THRESHOLDS, LIKELIHOOD_LABELS, IMPACT_LABELS } from '../types'

interface RiskMatrixProps {
  assessments: RiskAssessment[]
  riskCodes: RiskCodesFile
  matrixType: 'inherent' | 'residual'
  onCellClick?: (likelihood: number, impact: number, riskCodes: string[]) => void
  selectedCell?: { likelihood: number; impact: number } | null
  thresholds?: RiskThresholds
}

// Risk level colour mapping
const LEVEL_COLORS: Record<RiskLevel, { bg: string; hover: string; text: string }> = {
  low: { bg: '#dcfce7', hover: '#bbf7d0', text: '#166534' },
  medium: { bg: '#fef9c3', hover: '#fef08a', text: '#854d0e' },
  high: { bg: '#fed7aa', hover: '#fdba74', text: '#9a3412' },
  critical: { bg: '#fecaca', hover: '#fca5a5', text: '#991b1b' }
}

export default function RiskMatrix({
  assessments,
  riskCodes,
  matrixType,
  onCellClick,
  selectedCell,
  thresholds
}: RiskMatrixProps) {
  const t = thresholds ?? DEFAULT_RISK_THRESHOLDS
  // Build the matrix data
  const matrixData = useMemo(() => {
    const cells: RiskMatrixCell[][] = []

    // Build 5x5 grid (impact as rows, likelihood as columns)
    for (let impact = 5; impact >= 1; impact--) {
      const row: RiskMatrixCell[] = []
      for (let likelihood = 1; likelihood <= 5; likelihood++) {
        const score = likelihood * impact
        const level = getRiskLevel(score, t)

        // Find risks in this cell
        const risksInCell = assessments.filter(a => {
          if (matrixType === 'inherent') {
            return a.likelihood === likelihood && a.impact === impact
          } else {
            return a.residual_likelihood === likelihood && a.residual_impact === impact
          }
        })

        row.push({
          likelihood,
          impact,
          score,
          level,
          risk_codes: risksInCell.map(r => r.risk_code),
          count: risksInCell.length
        })
      }
      cells.push(row)
    }

    return cells
  }, [assessments, matrixType, t])

  // Summary statistics
  const summary = useMemo(() => {
    const stats: Record<RiskLevel, number> = { low: 0, medium: 0, high: 0, critical: 0 }
    let assessed = 0
    let unassessed = 0

    assessments.forEach(a => {
      const hasScore = matrixType === 'inherent'
        ? (a.likelihood != null && a.impact != null)
        : (a.residual_likelihood != null && a.residual_impact != null)

      if (hasScore) {
        assessed++
        const level = matrixType === 'inherent' ? a.inherent_risk_level : a.residual_risk_level
        if (level) stats[level]++
      } else {
        unassessed++
      }
    })

    return { ...stats, assessed, unassessed }
  }, [assessments, matrixType])

  return (
    <div className="risk-matrix-container">
      {/* Summary stats bar */}
      <div className="risk-matrix-stats">
        <div className="stat-item stat-low">
          <span className="stat-count">{summary.low}</span>
          <span className="stat-label">Low</span>
        </div>
        <div className="stat-item stat-medium">
          <span className="stat-count">{summary.medium}</span>
          <span className="stat-label">Medium</span>
        </div>
        <div className="stat-item stat-high">
          <span className="stat-count">{summary.high}</span>
          <span className="stat-label">High</span>
        </div>
        <div className="stat-item stat-critical">
          <span className="stat-count">{summary.critical}</span>
          <span className="stat-label">Critical</span>
        </div>
        <div className="stat-divider" />
        <div className="stat-item stat-assessed">
          <span className="stat-count">{summary.assessed}</span>
          <span className="stat-label">Assessed</span>
        </div>
        <div className="stat-item stat-unassessed">
          <span className="stat-count">{summary.unassessed}</span>
          <span className="stat-label">Not Assessed</span>
        </div>
      </div>

      {/* Matrix grid */}
      <div className="risk-matrix-grid">
        {/* Y-axis label */}
        <div className="risk-matrix-y-label">
          <span>Impact</span>
        </div>

        {/* Empty corner cell */}
        <div className="risk-matrix-corner" />

        {/* Likelihood header row */}
        {[1, 2, 3, 4, 5].map(l => (
          <div key={`header-${l}`} className="risk-matrix-header risk-matrix-header-col" style={{ gridColumn: l + 2 }}>
            <span className="header-value">{l}</span>
            <span className="header-label">{LIKELIHOOD_LABELS[l]}</span>
          </div>
        ))}

        {/* Matrix rows */}
        {matrixData.map((row, rowIndex) => {
          const impact = 5 - rowIndex
          return (
            <div key={`row-${impact}`} className="risk-matrix-row" style={{ display: 'contents' }}>
              {/* Impact row header */}
              <div className="risk-matrix-header risk-matrix-header-row">
                <span className="header-value">{impact}</span>
                <span className="header-label">{IMPACT_LABELS[impact]}</span>
              </div>

              {/* Cells */}
              {row.map(cell => {
                const colors = LEVEL_COLORS[cell.level]
                const isSelected = selectedCell?.likelihood === cell.likelihood &&
                                   selectedCell?.impact === cell.impact

                return (
                  <button
                    key={`cell-${cell.likelihood}-${cell.impact}`}
                    className={`risk-matrix-cell risk-level-${cell.level} ${isSelected ? 'selected' : ''}`}
                    style={{
                      backgroundColor: isSelected ? colors.hover : colors.bg,
                      color: colors.text
                    }}
                    onClick={() => onCellClick?.(cell.likelihood, cell.impact, cell.risk_codes)}
                    title={`Likelihood ${cell.likelihood}, Impact ${cell.impact}\nScore: ${cell.score} (${cell.level})\n${cell.count} risk(s)`}
                  >
                    {cell.count > 0 && (
                      <div className="cell-content">
                        <span className="cell-count">{cell.count}</span>
                        {cell.count <= 3 && (
                          <div className="cell-codes">
                            {cell.risk_codes.map(code => (
                              <span key={code} className="cell-code" title={riskCodes.codes[code]?.title}>
                                {code.replace('R-', '')}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                    <span className="cell-score">{cell.score}</span>
                  </button>
                )
              })}
            </div>
          )
        })}

        {/* X-axis label */}
        <div className="risk-matrix-x-label-spacer" />
        <div className="risk-matrix-x-label">
          <span>Likelihood</span>
        </div>
      </div>

      {/* Legend */}
      <div className="risk-matrix-legend">
        <span className="legend-title">Risk Levels:</span>
        <div className="legend-item">
          <span className="legend-swatch" style={{ backgroundColor: LEVEL_COLORS.low.bg }} />
          <span>Low (1-{t.lowMax})</span>
        </div>
        <div className="legend-item">
          <span className="legend-swatch" style={{ backgroundColor: LEVEL_COLORS.medium.bg }} />
          <span>Medium ({t.lowMax + 1}-{t.mediumMax})</span>
        </div>
        <div className="legend-item">
          <span className="legend-swatch" style={{ backgroundColor: LEVEL_COLORS.high.bg }} />
          <span>High ({t.mediumMax + 1}-{t.highMax})</span>
        </div>
        <div className="legend-item">
          <span className="legend-swatch" style={{ backgroundColor: LEVEL_COLORS.critical.bg }} />
          <span>Critical ({t.highMax + 1}-25)</span>
        </div>
      </div>
    </div>
  )
}
