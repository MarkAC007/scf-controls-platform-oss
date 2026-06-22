import React, { useState } from 'react'
import type { CMMaturityGuidance } from '../types'

interface Props {
  maturity?: CMMaturityGuidance
  targetLevel?: string | null
}

const LEVELS = [
  { key: 'level_0', label: 'L0', stepLabel: 'INITIAL', title: 'Initial', color: 'var(--maturity-l0)' },
  { key: 'level_1', label: 'L1', stepLabel: 'REPEATABLE', title: 'Repeatable', color: 'var(--maturity-l1)' },
  { key: 'level_2', label: 'L2', stepLabel: 'DEFINED', title: 'Defined', color: 'var(--maturity-l2)' },
  { key: 'level_3', label: 'L3', stepLabel: 'MANAGED', title: 'Managed', color: 'var(--maturity-l3)' },
  { key: 'level_4', label: 'L4', stepLabel: 'MEASURED', title: 'Measured', color: 'var(--maturity-l4)' },
  { key: 'level_5', label: 'L5', stepLabel: 'OPTIMIZED', title: 'Optimized', color: 'var(--maturity-l5)' },
] as const

export default function MaturityRoadmap({ maturity, targetLevel }: Props) {
  const [hoveredLevel, setHoveredLevel] = useState<string | null>(null)

  if (!maturity) {
    return null
  }

  const hasAnyGuidance = LEVELS.some(l => maturity[l.key as keyof CMMaturityGuidance])
  if (!hasAnyGuidance) {
    return null
  }

  const targetIndex = targetLevel ? LEVELS.findIndex(l => l.label === targetLevel) : -1

  return (
    <div className="detail-section-container">
      <div className="container-header">
        <span className="container-icon">📊</span>
        <span className="container-title">Maturity Roadmap</span>
        <span className="maturity-target-label">{targetIndex >= 0 ? `Target: Level ${targetIndex}` : 'Target: Not Set'}</span>
      </div>
      <div className="container-content">
        <div className="maturity-stepper">
          {LEVELS.map((level, i) => {
            const isActive = i <= targetIndex
            const isTarget = i === targetIndex
            const guidanceText = maturity[level.key as keyof CMMaturityGuidance]

            return (
              <React.Fragment key={level.key}>
                {i > 0 && (
                  <div className={`maturity-connector ${i <= targetIndex ? 'active' : 'inactive'}`} />
                )}
                <div
                  className="maturity-step"
                  onMouseEnter={() => guidanceText && setHoveredLevel(level.key)}
                  onMouseLeave={() => setHoveredLevel(null)}
                >
                  <div className={`maturity-circle ${isTarget ? 'target' : isActive ? 'active' : 'inactive'}`}>
                    {level.label}
                  </div>
                  <div className={`maturity-step-label ${isTarget ? 'target' : isActive ? 'active' : ''}`}>
                    {level.stepLabel}
                  </div>
                  {hoveredLevel === level.key && guidanceText && (
                    <div className="maturity-popover">
                      <div className="maturity-popover-title">{level.label} — {level.title}</div>
                      <div className="maturity-popover-text">{guidanceText}</div>
                    </div>
                  )}
                </div>
              </React.Fragment>
            )
          })}
        </div>
      </div>
    </div>
  )
}
