import type { EvidenceMaturityLevel } from './EvidenceMaturityTypes'
import { getEvidenceMaturityInfo, getNextMaturityLevel } from './EvidenceMaturityTypes'
import { MaturityBadge } from './MaturityBadge'
import type { CollectionRecipe } from '../../types'

interface MaturityAdvisoryCardProps {
  currentLevel: EvidenceMaturityLevel
  evidenceId?: string
  evidenceTitle?: string
  onUpgradeClick?: () => void
  className?: string
  /** When provided, shows the next-level recipe preview instead of generic upgrade actions */
  nextLevelRecipe?: CollectionRecipe
  /** System name for system-specific context in the advisory */
  systemName?: string
}

/**
 * MaturityAdvisoryCard Component
 *
 * Displays the current maturity level and recommendations for improvement.
 * Shows what actions to take to reach the next level, estimated time,
 * and potential ROI.
 *
 * When nextLevelRecipe is provided, shows a recipe-aware preview
 * of what the next maturity level enables.
 *
 * Usage:
 *   <MaturityAdvisoryCard currentLevel="L2" evidenceTitle="Access Logs" />
 *   <MaturityAdvisoryCard currentLevel="L1" nextLevelRecipe={recipe} systemName="Okta" />
 */
export function MaturityAdvisoryCard({
  currentLevel,
  evidenceId,
  evidenceTitle,
  onUpgradeClick,
  className = '',
  nextLevelRecipe,
  systemName,
}: MaturityAdvisoryCardProps) {
  const currentInfo = getEvidenceMaturityInfo(currentLevel)
  const nextLevel = getNextMaturityLevel(currentLevel)
  const nextInfo = nextLevel ? getEvidenceMaturityInfo(nextLevel) : null

  const isAtMax = currentLevel === 'L5'

  return (
    <div className={`maturity-advisory-card ${className}`}>
      <div className="maturity-advisory-header">
        <div className="maturity-advisory-title">
          <span className="maturity-advisory-icon">
            {isAtMax ? '\u2713' : '\u2191'}
          </span>
          <span>Evidence Maturity Advisory</span>
        </div>
        {evidenceTitle && (
          <span className="maturity-advisory-evidence">{evidenceTitle}</span>
        )}
      </div>

      <div className="maturity-advisory-current">
        <div className="maturity-advisory-current-header">
          <span className="maturity-advisory-label">Current Level</span>
          <MaturityBadge level={currentLevel} size="large" showTooltip={false} />
        </div>
        <p className="maturity-advisory-current-description">
          {currentInfo.description}
        </p>
      </div>

      {isAtMax ? (
        <div className="maturity-advisory-success">
          <div className="maturity-advisory-success-icon">{'\u2728'}</div>
          <div className="maturity-advisory-success-content">
            <span className="maturity-advisory-success-title">Excellent!</span>
            <p>This evidence collection is at the highest maturity level. Continue to:</p>
            <ul>
              {currentInfo.upgradeActions.map((action, index) => (
                <li key={index}>{action}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : nextInfo && (
        <div className="maturity-advisory-upgrade">
          <div className="maturity-advisory-upgrade-header">
            <span className="maturity-advisory-label">Upgrade Path</span>
            <div className="maturity-advisory-upgrade-path">
              <MaturityBadge level={currentLevel} size="small" showLabel={false} showTooltip={false} />
              <span className="maturity-advisory-arrow">{'\u2192'}</span>
              <MaturityBadge level={nextLevel!} size="small" showLabel={false} showTooltip={false} />
            </div>
          </div>

          <div className="maturity-advisory-target">
            <div className="maturity-advisory-target-header">
              <span className="maturity-advisory-target-level">{nextLevel!}</span>
              <span className="maturity-advisory-target-name">{nextInfo.name}</span>
            </div>
            <p className="maturity-advisory-target-description">{nextInfo.description}</p>
          </div>

          {/* Recipe-aware upgrade preview — shows actual steps for the next maturity level */}
          {nextLevelRecipe ? (
            <div className="maturity-advisory-recipe-preview">
              <span className="maturity-advisory-actions-title">
                {systemName ? `How to reach ${nextLevel} with ${systemName}` : `How to reach ${nextLevel}`}
              </span>
              <div className="maturity-advisory-recipe-summary">
                <p className="maturity-advisory-recipe-title">{nextLevelRecipe.title}</p>
                <div className="maturity-advisory-recipe-meta">
                  {nextLevelRecipe.estimated_time && (
                    <span className="recipe-meta-item">
                      <span className="recipe-meta-icon">{'\u23F1'}</span>
                      {nextLevelRecipe.estimated_time}
                    </span>
                  )}
                  {nextLevelRecipe.frequency && (
                    <span className="recipe-meta-item">
                      <span className="recipe-meta-icon">{'\u21BB'}</span>
                      {nextLevelRecipe.frequency}
                    </span>
                  )}
                </div>
              </div>
              <ol className="maturity-advisory-recipe-steps">
                {nextLevelRecipe.steps.map((step, index) => (
                  <li key={index} className="maturity-advisory-recipe-step">
                    <span className="maturity-advisory-step-action">{step.action}</span>
                    {step.permissions_required && (
                      <span className="maturity-advisory-step-permission">
                        Requires: {step.permissions_required}
                      </span>
                    )}
                  </li>
                ))}
              </ol>
            </div>
          ) : (
            <div className="maturity-advisory-actions">
              <span className="maturity-advisory-actions-title">Recommended Actions</span>
              <ul className="maturity-advisory-actions-list">
                {currentInfo.upgradeActions.map((action, index) => (
                  <li key={index}>
                    <span className="maturity-advisory-action-bullet">{'\u25CF'}</span>
                    {action}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="maturity-advisory-metrics">
            {currentInfo.timeToUpgrade && (
              <div className="maturity-advisory-metric">
                <span className="maturity-advisory-metric-icon">{'\u23F1'}</span>
                <div className="maturity-advisory-metric-content">
                  <span className="maturity-advisory-metric-label">Est. Time</span>
                  <span className="maturity-advisory-metric-value">{currentInfo.timeToUpgrade}</span>
                </div>
              </div>
            )}
            {currentInfo.roiIndicator && (
              <div className="maturity-advisory-metric">
                <span className="maturity-advisory-metric-icon">{'\u2197'}</span>
                <div className="maturity-advisory-metric-content">
                  <span className="maturity-advisory-metric-label">Expected ROI</span>
                  <span className="maturity-advisory-metric-value">{currentInfo.roiIndicator}</span>
                </div>
              </div>
            )}
          </div>

          {onUpgradeClick && (
            <button
              className="maturity-advisory-cta"
              onClick={onUpgradeClick}
              style={{ borderColor: nextInfo.colour, color: nextInfo.colour }}
            >
              Start Upgrade Journey
            </button>
          )}
        </div>
      )}
    </div>
  )
}

export default MaturityAdvisoryCard
