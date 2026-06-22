import type { CapabilityThemeResponse, CapabilityThemeEvidencePosture } from '../../types'
import AxisDot from './AxisDot'
import { bandToClass, formatAxisPercent } from './axisHelpers'

/** Map icon field from capability_themes.json to inline SVGs */
const themeIcons: Record<string, JSX.Element> = {
  'shield-check': (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  ),
  'git-branch': (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="6" y1="3" x2="6" y2="15" />
      <circle cx="18" cy="6" r="3" />
      <circle cx="6" cy="18" r="3" />
      <path d="M18 9a9 9 0 0 1-9 9" />
    </svg>
  ),
  'cloud': (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z" />
    </svg>
  ),
  'graduation-cap': (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 10v6M2 10l10-5 10 5-10 5z" />
      <path d="M6 12v5c0 2 2 3 6 3s6-1 6-3v-5" />
    </svg>
  ),
  'key': (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />
    </svg>
  ),
  'alert-triangle': (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  ),
  'eye': (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  ),
  'clipboard-list': (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
      <rect x="8" y="2" width="8" height="4" rx="1" ry="1" />
      <path d="M9 14h.01M13 14h2M9 18h.01M13 18h2" />
    </svg>
  ),
  'refresh-cw': (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="23 4 23 10 17 10" />
      <polyline points="1 20 1 14 7 14" />
      <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
    </svg>
  ),
  'settings': (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  ),
  'link': (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
      <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
    </svg>
  ),
}

const defaultIcon = (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polygon points="12 2 2 7 12 12 22 7 12 2" />
    <polyline points="2 17 12 22 22 17" />
    <polyline points="2 12 12 17 22 12" />
  </svg>
)

interface PostureBarSegment {
  key: string
  value: number
  color: string
  label: string
}

function getPostureSegments(posture: CapabilityThemeResponse['posture'], total: number): PostureBarSegment[] {
  if (total === 0) return []
  return [
    { key: 'monitored', value: posture.monitored, color: '#8b5cf6', label: 'Monitored' },
    { key: 'implemented', value: posture.implemented, color: '#22c55e', label: 'Implemented' },
    { key: 'ready_for_review', value: posture.ready_for_review, color: '#06b6d4', label: 'Ready for Review' },
    { key: 'in_progress', value: posture.in_progress, color: '#3b82f6', label: 'In Progress' },
    { key: 'not_started', value: posture.not_started, color: '#94a3b8', label: 'Not Started' },
    { key: 'at_risk', value: posture.at_risk, color: '#ef4444', label: 'At Risk' },
    { key: 'not_applicable', value: posture.not_applicable, color: '#d1d5db', label: 'N/A' },
    { key: 'deferred', value: posture.deferred, color: '#f59e0b', label: 'Deferred' },
  ].filter(s => s.value > 0)
}

const CONFIDENCE_CONFIG: Record<string, { color: string; label: string }> = {
  strong: { color: 'var(--success)', label: 'Strong Evidence' },
  moderate: { color: 'var(--warning)', label: 'Moderate Evidence' },
  weak: { color: 'var(--destructive)', label: 'Weak Evidence' },
  none: { color: 'var(--muted-foreground)', label: 'No Evidence' },
}

interface ThemeCardProps {
  theme: CapabilityThemeResponse
  evidencePosture?: CapabilityThemeEvidencePosture
  onClick: (themeCode: string) => void
}

export default function ThemeCard({ theme, evidencePosture, onClick }: ThemeCardProps) {
  const icon = theme.icon ? (themeIcons[theme.icon] || defaultIcon) : defaultIcon
  const segments = getPostureSegments(theme.posture, theme.scoped_controls)

  return (
    <button
      className="cp-theme-card"
      onClick={() => onClick(theme.theme_code)}
    >
      <div className="cp-theme-card-header">
        <span className="cp-theme-icon">{icon}</span>
        <div className="cp-theme-title-group">
          <span className="cp-theme-name">{theme.name}</span>
          {theme.ksi_reference && (
            <span className="cp-ksi-badge">{theme.ksi_reference}</span>
          )}
        </div>
      </div>

      <div className="cp-theme-card-body">
        <div className="cp-composite-row">
          <span className={`cp-composite-value ${bandToClass(theme.composite_band)}`}>
            {formatAxisPercent(theme.composite_score)}
          </span>
          <div className="cp-composite-labels">
            <span className="cp-composite-caption">Composite KPS</span>
            {theme.composite_band && (
              <span className={`cp-composite-band ${bandToClass(theme.composite_band)}`}>
                {theme.composite_band}
              </span>
            )}
          </div>
        </div>

        <div className="cp-axis-row" aria-label="Axis scores">
          <AxisDot
            axis="IC"
            value={theme.implementation_coverage}
            band={theme.implementation_band}
          />
          <AxisDot
            axis="M"
            value={theme.maturity_score !== null ? theme.maturity_score / 5 : null}
            band={theme.maturity_band}
            maturityScore={theme.maturity_score}
          />
          <AxisDot
            axis="EC"
            value={theme.evidence_coverage}
            band={theme.evidence_coverage_band}
          />
          <AxisDot
            axis="EQ"
            value={theme.evidence_quality}
            band={theme.evidence_quality_band}
            warning={theme.evidence_quality_warning}
          />
        </div>

        <div className="cp-posture-bar" title="Status distribution">
          {segments.map(seg => (
            <div
              key={seg.key}
              className="cp-posture-bar-segment"
              style={{
                width: `${(seg.value / theme.scoped_controls) * 100}%`,
                backgroundColor: seg.color,
              }}
              title={`${seg.label}: ${seg.value}`}
            />
          ))}
          {segments.length === 0 && (
            <div className="cp-posture-bar-empty" />
          )}
        </div>

        <div className="cp-theme-card-footer">
          <span className="cp-control-count">
            {theme.scoped_controls} of {theme.total_controls} scoped
          </span>
          {theme.posture.at_risk > 0 && (
            <span className="cp-at-risk-badge">
              {theme.posture.at_risk} at risk
            </span>
          )}
          {evidencePosture && (
            <span
              className={`cp-evidence-badge cp-evidence-${evidencePosture.evidence_confidence}`}
              style={{ color: CONFIDENCE_CONFIG[evidencePosture.evidence_confidence]?.color }}
              title={`${evidencePosture.controls_with_evidence}/${theme.scoped_controls} controls have evidence, ${evidencePosture.sufficient_count} sufficient, ${evidencePosture.partial_count} partial, ${evidencePosture.insufficient_count} insufficient`}
            >
              {CONFIDENCE_CONFIG[evidencePosture.evidence_confidence]?.label || 'No Evidence'}
            </span>
          )}
        </div>
      </div>
    </button>
  )
}
