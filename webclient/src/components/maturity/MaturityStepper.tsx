import { useState } from 'react'
import type { EvidenceMaturityLevel } from './EvidenceMaturityTypes'
import { EVIDENCE_MATURITY_LEVELS, getNextMaturityLevel } from './EvidenceMaturityTypes'

interface MaturityStepperProps {
  value: EvidenceMaturityLevel | '' | undefined
  onChange: (level: EvidenceMaturityLevel) => void
  disabled?: boolean
}

const LEVELS: EvidenceMaturityLevel[] = ['L0', 'L1', 'L2', 'L3', 'L4', 'L5']

/**
 * MaturityStepper Component
 *
 * Renders the L0-L5 evidence collection maturity scale as a segmented
 * progression control. Replaces the bare <select> so the level a user picks
 * sits visibly on the ladder, with the selected level's meaning shown inline.
 *
 * Usage:
 *   <MaturityStepper value={tracking.maturity_level} onChange={setLevel} />
 */
export function MaturityStepper({ value, onChange, disabled = false }: MaturityStepperProps) {
  const [hovered, setHovered] = useState<EvidenceMaturityLevel | null>(null)
  const selected = value || null
  const selectedIndex = selected ? LEVELS.indexOf(selected) : -1
  // Detail card previews the hovered level; falls back to the selection
  const detailLevel = hovered || selected
  const detailInfo = detailLevel ? EVIDENCE_MATURITY_LEVELS[detailLevel] : null
  const nextLevel = selected ? getNextMaturityLevel(selected) : null

  return (
    <div className={`maturity-stepper ${disabled ? 'maturity-stepper-disabled' : ''}`}>
      <div className="maturity-stepper-track" role="radiogroup" aria-label="Collection maturity level">
        {LEVELS.map((level, index) => {
          const info = EVIDENCE_MATURITY_LEVELS[level]
          const isSelected = level === selected
          const isReached = selectedIndex >= 0 && index <= selectedIndex

          return (
            <button
              key={level}
              type="button"
              role="radio"
              aria-checked={isSelected}
              disabled={disabled}
              className={`maturity-step ${isSelected ? 'selected' : ''} ${isReached ? 'reached' : ''}`}
              style={isReached ? {
                borderColor: info.colour,
                background: info.colourBg,
              } : undefined}
              onClick={() => onChange(level)}
              onMouseEnter={() => setHovered(level)}
              onMouseLeave={() => setHovered(null)}
            >
              <span className="maturity-step-level" style={isReached ? { color: info.colour } : undefined}>
                {level}
              </span>
              <span className="maturity-step-name">{info.name}</span>
            </button>
          )
        })}
      </div>

      {detailInfo ? (
        <div className="maturity-stepper-detail" style={{ borderLeftColor: detailInfo.colour }}>
          <div className="maturity-stepper-detail-head">
            <strong>{detailInfo.level} — {detailInfo.name}</strong>
            <span className="maturity-stepper-detail-desc">{detailInfo.description}</span>
          </div>
          <ul className="maturity-stepper-characteristics">
            {detailInfo.characteristics.slice(0, 4).map(item => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          {!hovered && nextLevel && (
            <div className="maturity-stepper-next">
              Next: <strong>{nextLevel} — {EVIDENCE_MATURITY_LEVELS[nextLevel].name}</strong>
              {EVIDENCE_MATURITY_LEVELS[selected!].timeToUpgrade && (
                <span className="maturity-stepper-next-time">
                  ~{EVIDENCE_MATURITY_LEVELS[selected!].timeToUpgrade} · {EVIDENCE_MATURITY_LEVELS[selected!].roiIndicator}
                </span>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="maturity-stepper-detail maturity-stepper-empty">
          Select where your collection process for this evidence sits today — the collection guide adapts to the level you choose.
        </div>
      )}
    </div>
  )
}
