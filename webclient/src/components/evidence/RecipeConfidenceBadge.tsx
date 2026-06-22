import type { RecipeConfidence } from '../../types'

interface RecipeConfidenceBadgeProps {
  confidence: RecipeConfidence
  className?: string
}

const CONFIDENCE_INFO: Record<RecipeConfidence, { label: string; icon: string; className: string }> = {
  system_specific: { label: 'System-specific', icon: '\u2713', className: 'confidence-high' },
  vendor_generic: { label: 'Vendor guide', icon: '\u2248', className: 'confidence-medium' },
  type_generic: { label: 'Generic guide', icon: '\u2139', className: 'confidence-low' },
}

export function RecipeConfidenceBadge({ confidence, className = '' }: RecipeConfidenceBadgeProps) {
  const info = CONFIDENCE_INFO[confidence]
  return (
    <span className={`recipe-confidence-badge ${info.className} ${className}`}>
      <span className="recipe-confidence-icon">{info.icon}</span>
      <span className="recipe-confidence-label">{info.label}</span>
    </span>
  )
}

export default RecipeConfidenceBadge
