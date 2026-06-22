import type { SCRMFocus } from '../types'

interface Props {
  focus?: SCRMFocus
}

const TIERS = [
  {
    key: 'tier1_strategic',
    label: 'T1: Strategic',
    description: 'Strategic supplier relationships and governance',
    color: 'var(--scrm-strategic)'
  },
  {
    key: 'tier2_operational',
    label: 'T2: Operational',
    description: 'Day-to-day supplier operations and management',
    color: 'var(--scrm-operational)'
  },
  {
    key: 'tier3_tactical',
    label: 'T3: Tactical',
    description: 'Tactical procurement and vendor selection',
    color: 'var(--scrm-tactical)'
  },
] as const

export default function SCRMFocusBadges({ focus }: Props) {
  if (!focus) {
    return null
  }

  const hasAnyTier = TIERS.some(t => focus[t.key as keyof SCRMFocus])
  if (!hasAnyTier) {
    return null
  }

  return (
    <div className="detail-section-container">
      <div className="container-header">
        <span className="container-icon">🔗</span>
        <span className="container-title">Supply Chain Focus</span>
      </div>
      <div className="container-content">
        <div className="scrm-tier-cards">
          {TIERS.map(tier => {
            const isActive = !!focus[tier.key as keyof SCRMFocus]
            return (
              <div
                key={tier.key}
                className={`scrm-tier-card ${isActive ? 'active' : 'inactive'}`}
                title={tier.description}
              >
                <span className="scrm-tier-check">{isActive ? '✓' : ''}</span>
                <span className="scrm-tier-id">{tier.key === 'tier1_strategic' ? 'T1' : tier.key === 'tier2_operational' ? 'T2' : 'T3'}</span>
                <span className="scrm-tier-label">PROVIDER</span>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
