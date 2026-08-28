import React, { useState } from 'react'
import type { CMMaturityGuidance } from '../types'

interface Props {
  maturity?: CMMaturityGuidance
  /**
   * The control's recorded maturity level ("L0".."L5"). The `full` variant marks
   * it as the target of the L0–L5 stepper; the `duo` variant treats it as where
   * the org is today and derives the next level from it. Unset falls back to L0.
   */
  level?: string | null
  /** `full` = six-chip L0–L5 stepper (library detail page). `duo` = current › next (scoping top bar). */
  variant?: 'full' | 'duo'
}

const LEVELS = [
  { key: 'level_0', label: 'L0', title: 'Initial' },
  { key: 'level_1', label: 'L1', title: 'Repeatable' },
  { key: 'level_2', label: 'L2', title: 'Defined' },
  { key: 'level_3', label: 'L3', title: 'Managed' },
  { key: 'level_4', label: 'L4', title: 'Measured' },
  { key: 'level_5', label: 'L5', title: 'Optimized' },
] as const

type Level = typeof LEVELS[number]

export default function MaturityRoadmap({ maturity, level, variant = 'full' }: Props) {
  const [hoveredLevel, setHoveredLevel] = useState<string | null>(null)

  if (!maturity) {
    return null
  }

  const hasAnyGuidance = LEVELS.some(l => maturity[l.key as keyof CMMaturityGuidance])
  if (!hasAnyGuidance) {
    return null
  }

  const levelIndex = level ? LEVELS.findIndex(l => l.label === level) : -1
  const hovered = LEVELS.find(l => l.key === hoveredLevel)
  const hoveredText = hovered ? maturity[hovered.key as keyof CMMaturityGuidance] : undefined

  const guidanceFor = (l: Level) => maturity[l.key as keyof CMMaturityGuidance]

  const popover = hovered && hoveredText
    ? (
      <div className="guidance-popover">
        <div className="guidance-popover-title">{hovered.label} — {hovered.title}</div>
        <div className="guidance-popover-text">{hoveredText}</div>
      </div>
    )
    : null

  if (variant === 'duo') {
    // Level unset falls back to L0; L5 has no successor, so it stands alone.
    const current = LEVELS[levelIndex >= 0 ? levelIndex : 0]
    const currentIndex = LEVELS.indexOf(current)
    const next = currentIndex < LEVELS.length - 1 ? LEVELS[currentIndex + 1] : null

    const chip = (l: Level, kind: 'current' | 'next') => (
      <span
        className={`roadmap-duo-chip ${kind}`}
        onMouseEnter={() => guidanceFor(l) && setHoveredLevel(l.key)}
        onMouseLeave={() => setHoveredLevel(null)}
        title={l.title}
      >
        <span className="roadmap-duo-lv">{l.label}</span>
        <span className="roadmap-duo-nm">{kind}</span>
      </span>
    )

    return (
      <div className="roadmap-duo">
        <span className="roadmap-duo-label">Maturity</span>
        <span className="roadmap-duo-chips">
          {chip(current, 'current')}
          {next && <span className="roadmap-duo-arrow" aria-hidden="true">›</span>}
          {next && chip(next, 'next')}
        </span>
        <span className="roadmap-duo-hint">hover for guidance</span>
        {popover}
      </div>
    )
  }

  return (
    <div className="roadmap-block">
      <div className="roadmap-block-head">
        <span className="detail-widget-group-label">Maturity Roadmap</span>
        <span className="roadmap-target-label">
          {levelIndex >= 0 ? `Target L${levelIndex}` : 'No Target'}
        </span>
      </div>

      <div className="roadmap-stepper">
        {LEVELS.map((l, i) => {
          const isActive = i <= levelIndex
          const isTarget = i === levelIndex

          return (
            <React.Fragment key={l.key}>
              {i > 0 && (
                <div className={`roadmap-connector ${isActive ? 'active' : 'inactive'}`} />
              )}
              <div
                className="roadmap-step"
                onMouseEnter={() => guidanceFor(l) && setHoveredLevel(l.key)}
                onMouseLeave={() => setHoveredLevel(null)}
              >
                <div
                  className={`roadmap-circle ${isTarget ? 'target' : isActive ? 'active' : 'inactive'}`}
                  title={l.title}
                >
                  {l.label}
                </div>
              </div>
            </React.Fragment>
          )
        })}
      </div>

      {popover}
    </div>
  )
}
