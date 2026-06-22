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

  if (!guidance) {
    return null
  }

  const hasAnyGuidance = SIZES.some(s => guidance[s.key as keyof BusinessSizeGuidanceType])
  if (!hasAnyGuidance) {
    return null
  }

  const guidanceText = guidance[selectedSize as keyof BusinessSizeGuidanceType]

  return (
    <div className="detail-section-container">
      <div className="container-header">
        <span className="container-icon">🏢</span>
        <span className="container-title">Right-Sizing Guidance</span>
      </div>
      <div className="container-content">
        <div className="size-pills">
          {SIZES.map(size => {
            const hasGuidance = !!guidance[size.key as keyof BusinessSizeGuidanceType]
            return (
              <button
                key={size.key}
                className={`size-pill ${selectedSize === size.key ? 'active' : ''} ${!hasGuidance ? 'empty' : ''}`}
                onClick={() => setSelectedSize(size.key)}
                title={size.title}
              >
                {size.label}
              </button>
            )
          })}
        </div>
        {guidanceText ? (
          <div className="size-guidance-text">{guidanceText}</div>
        ) : (
          <div className="size-guidance-empty">No specific guidance available for this organization size</div>
        )}
      </div>
    </div>
  )
}
