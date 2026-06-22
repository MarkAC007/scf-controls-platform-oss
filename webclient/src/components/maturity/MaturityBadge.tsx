import { useState, useRef, useEffect } from 'react'
import type { EvidenceMaturityLevel } from './EvidenceMaturityTypes'
import { getEvidenceMaturityInfo } from './EvidenceMaturityTypes'

interface MaturityBadgeProps {
  level: EvidenceMaturityLevel
  size?: 'small' | 'medium' | 'large'
  showLabel?: boolean
  showTooltip?: boolean
  className?: string
}

/**
 * MaturityBadge Component
 *
 * Displays an evidence collection maturity level (L0-L5) as a colour-coded badge.
 * Includes an optional tooltip with level details.
 *
 * Usage:
 *   <MaturityBadge level="L3" />
 *   <MaturityBadge level="L2" size="small" showLabel={false} />
 */
export function MaturityBadge({
  level,
  size = 'medium',
  showLabel = true,
  showTooltip = true,
  className = ''
}: MaturityBadgeProps) {
  const [tooltipVisible, setTooltipVisible] = useState(false)
  const [tooltipPosition, setTooltipPosition] = useState<'top' | 'bottom'>('top')
  const badgeRef = useRef<HTMLDivElement>(null)
  const maturityInfo = getEvidenceMaturityInfo(level)

  // Calculate tooltip position based on available space
  useEffect(() => {
    if (tooltipVisible && badgeRef.current) {
      const rect = badgeRef.current.getBoundingClientRect()
      const spaceAbove = rect.top
      const tooltipHeight = 200 // Approximate tooltip height

      setTooltipPosition(spaceAbove > tooltipHeight ? 'top' : 'bottom')
    }
  }, [tooltipVisible])

  const sizeClasses = {
    small: 'maturity-badge-small',
    medium: 'maturity-badge-medium',
    large: 'maturity-badge-large'
  }

  return (
    <div
      ref={badgeRef}
      className={`maturity-badge-wrapper ${className}`}
      onMouseEnter={() => showTooltip && setTooltipVisible(true)}
      onMouseLeave={() => setTooltipVisible(false)}
    >
      <div
        className={`maturity-badge-ev ${sizeClasses[size]}`}
        style={{
          backgroundColor: maturityInfo.colourBg,
          borderColor: maturityInfo.colour,
          color: maturityInfo.colour
        }}
      >
        <span className="maturity-badge-level">{level}</span>
        {showLabel && <span className="maturity-badge-name">{maturityInfo.name}</span>}
      </div>

      {showTooltip && tooltipVisible && (
        <div className={`maturity-tooltip maturity-tooltip-${tooltipPosition}`}>
          <div className="maturity-tooltip-header">
            <span
              className="maturity-tooltip-dot"
              style={{ backgroundColor: maturityInfo.colour }}
            />
            <span className="maturity-tooltip-level">{level}</span>
            <span className="maturity-tooltip-name">{maturityInfo.name}</span>
          </div>
          <p className="maturity-tooltip-description">{maturityInfo.description}</p>
          <div className="maturity-tooltip-characteristics">
            <span className="maturity-tooltip-section-title">Characteristics</span>
            <ul>
              {maturityInfo.characteristics.slice(0, 3).map((char, index) => (
                <li key={index}>{char}</li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  )
}

export default MaturityBadge
