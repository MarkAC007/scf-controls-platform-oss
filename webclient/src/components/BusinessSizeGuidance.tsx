import { useState } from 'react'
import type { BusinessSizeGuidance as BusinessSizeGuidanceType } from '../types'

interface Props {
  guidance?: BusinessSizeGuidanceType
}

const SIZES = [
  { key: 'micro_small', label: 'Micro', title: 'Micro/Small (<10 employees)' },
  { key: 'small', label: 'Small', title: 'Small (10-49 employees)' },
  { key: 'medium', label: 'Medium', title: 'Medium (50-249 employees)' },
  { key: 'large', label: 'Large', title: 'Large (250-999 employees)' },
  { key: 'enterprise', label: 'Enterprise', title: 'Enterprise (1000+ employees)' },
] as const

type SizeKey = typeof SIZES[number]['key']

export default function BusinessSizeGuidance({ guidance }: Props) {
  const [selectedSize, setSelectedSize] = useState<SizeKey>('medium')
  const [hoveredSize, setHoveredSize] = useState<SizeKey | null>(null)

  if (!guidance) {
    return null
  }

  const hasAnyGuidance = SIZES.some(s => guidance[s.key as keyof BusinessSizeGuidanceType])
  if (!hasAnyGuidance) {
    return null
  }

  const hovered = SIZES.find(s => s.key === hoveredSize)
  const hoveredText = hovered ? guidance[hovered.key as keyof BusinessSizeGuidanceType] : undefined

  return (
    <div className="sizing-block">
      <div className="detail-widget-group-label">Right-Sizing Guidance</div>

      <div className="size-pills">
        {SIZES.map(size => {
          const hasGuidance = !!guidance[size.key as keyof BusinessSizeGuidanceType]
          return (
            <div
              key={size.key}
              className="size-pill-wrap"
              onMouseEnter={() => hasGuidance && setHoveredSize(size.key)}
              onMouseLeave={() => setHoveredSize(null)}
            >
              <button
                className={`size-pill ${selectedSize === size.key ? 'active' : ''} ${!hasGuidance ? 'empty' : ''}`}
                onClick={() => setSelectedSize(size.key)}
                title={size.title}
              >
                {size.label}
              </button>
            </div>
          )
        })}
      </div>

      {hovered && hoveredText && (
        <div className="guidance-popover">
          <div className="guidance-popover-title">{hovered.title}</div>
          <div className="guidance-popover-text">{hoveredText}</div>
        </div>
      )}
    </div>
  )
}
