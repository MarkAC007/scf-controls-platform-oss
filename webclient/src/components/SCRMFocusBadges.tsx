import type { SCRMFocus } from '../types'

interface Props {
  focus?: SCRMFocus
  /** `card` = full-width tier section (scoping detail). `bar` = compact chips for a header badge row (library detail). */
  variant?: 'card' | 'bar'
}

const TIERS = [
  {
    key: 'tier1_strategic',
    short: 'T1',
    label: 'T1: Strategic',
    description: 'Strategic supplier relationships and governance',
    color: 'var(--scrm-strategic)'
  },
  {
    key: 'tier2_operational',
    short: 'T2',
    label: 'T2: Operational',
    description: 'Day-to-day supplier operations and management',
    color: 'var(--scrm-operational)'
  },
  {
    key: 'tier3_tactical',
    short: 'T3',
    label: 'T3: Tactical',
    description: 'Tactical procurement and vendor selection',
    color: 'var(--scrm-tactical)'
  },
] as const

export default function SCRMFocusBadges({ focus, variant = 'card' }: Props) {
  if (!focus) {
    return null
  }

  const hasAnyTier = TIERS.some(t => focus[t.key as keyof SCRMFocus])
  if (!hasAnyTier) {
    return null
  }

  const isActive = (key: string) => !!focus[key as keyof SCRMFocus]

  if (variant === 'bar') {
    return (
      <div className="scrm-bar">
        <span className="scrm-bar-label">Supply Chain</span>
        <span className="scrm-bar-tiles">
          {TIERS.map(tier => (
            <span
              key={tier.key}
              className={`scrm-bar-tile ${isActive(tier.key) ? 'active' : 'off'}`}
              title={tier.description}
            >
              {isActive(tier.key) && <span className="scrm-bar-check" aria-hidden="true">✓</span>}
              <span className="scrm-bar-id">{tier.short}</span>
              <span className="scrm-bar-sub">provider</span>
            </span>
          ))}
        </span>
      </div>
    )
  }

  return (
    <div className="detail-section-container">
      <div className="container-header">
        <span className="container-icon">🔗</span>
        <span className="container-title">Supply Chain Focus</span>
      </div>
      <div className="container-content">
        <div className="scrm-tier-cards">
          {TIERS.map(tier => (
            <div
              key={tier.key}
              className={`scrm-tier-card ${isActive(tier.key) ? 'active' : 'inactive'}`}
              title={tier.description}
            >
              <span className="scrm-tier-check">{isActive(tier.key) ? '✓' : ''}</span>
              <span className="scrm-tier-id">{tier.short}</span>
              <span className="scrm-tier-label">PROVIDER</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
