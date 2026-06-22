import { useState, useMemo } from 'react'
import { useCapabilityThemes, useCapabilityThemeEvidencePosture } from '../hooks/useCapabilityThemes'
import ThemeCard from './capability-posture/ThemeCard'
import ThemeDetail from './capability-posture/ThemeDetail'
import { formatAxisPercent } from './capability-posture/axisHelpers'
import type { CapabilityThemeResponse, CapabilityThemeEvidencePosture } from '../types'

interface CapabilityPostureProps {
  organizationId: string
}

export default function CapabilityPosture({ organizationId }: CapabilityPostureProps) {
  const { data, isLoading, error } = useCapabilityThemes(organizationId)
  const { data: evidenceData } = useCapabilityThemeEvidencePosture(organizationId)
  const [selectedTheme, setSelectedTheme] = useState<string | null>(null)

  const themes = data?.themes ?? []

  // Index evidence posture by theme_code for O(1) lookup
  const evidenceByTheme = useMemo(() => {
    const map: Record<string, CapabilityThemeEvidencePosture> = {}
    for (const ep of evidenceData?.themes ?? []) {
      map[ep.theme_code] = ep
    }
    return map
  }, [evidenceData])

  const aggregateStats = useMemo(() => {
    if (themes.length === 0) return null
    let totalScoped = 0
    let totalControls = 0
    let totalAtRisk = 0
    let compositeSum = 0
    let compositeCount = 0

    for (const t of themes) {
      totalScoped += t.scoped_controls
      totalControls += t.total_controls
      totalAtRisk += t.posture.at_risk
      if (t.composite_score !== null) {
        compositeSum += t.composite_score
        compositeCount += 1
      }
    }

    return {
      overallKps: compositeCount > 0 ? compositeSum / compositeCount : null,
      totalScoped,
      totalControls,
      totalAtRisk,
    }
  }, [themes])

  const selectedThemeData: CapabilityThemeResponse | undefined = useMemo(
    () => themes.find((t) => t.theme_code === selectedTheme),
    [themes, selectedTheme]
  )

  if (selectedThemeData) {
    return (
      <ThemeDetail
        theme={selectedThemeData}
        evidencePosture={evidenceByTheme[selectedThemeData.theme_code]}
        organizationId={organizationId}
        onBack={() => setSelectedTheme(null)}
      />
    )
  }

  return (
    <div className="cp-container">
      <div className="cp-header">
        <nav className="page-breadcrumb">
          <span>Governance</span>
          <span className="breadcrumb-separator">&rsaquo;</span>
          <span className="breadcrumb-active">Capabilities</span>
        </nav>
        <h1 className="page-title">Capability Posture</h1>
        <p className="dashboard-subtitle">Monitor capability themes across control domains and organizational maturity.</p>
        {aggregateStats && (
          <div className="cp-aggregate-stats">
            <div className="kpi-card">
              <div className="kpi-card-header">
                <span className="kpi-label">OVERALL KPS</span>
              </div>
              <div className="kpi-value">{formatAxisPercent(aggregateStats.overallKps)}</div>
            </div>
            <div className="kpi-card">
              <div className="kpi-card-header">
                <span className="kpi-label">SCOPED CONTROLS</span>
              </div>
              <div className="kpi-value">{aggregateStats.totalScoped}</div>
            </div>
            {aggregateStats.totalAtRisk > 0 && (
              <div className="kpi-card">
                <div className="kpi-card-header">
                  <span className="kpi-label">AT RISK</span>
                </div>
                <div className="kpi-value" style={{ color: 'var(--destructive)' }}>{aggregateStats.totalAtRisk}</div>
              </div>
            )}
          </div>
        )}
      </div>

      {isLoading ? (
        <div className="cp-grid">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="cp-theme-card cp-skeleton">
              <div className="cp-skeleton-line cp-skeleton-title" />
              <div className="cp-skeleton-line cp-skeleton-bar" />
              <div className="cp-skeleton-line cp-skeleton-footer" />
            </div>
          ))}
        </div>
      ) : error ? (
        <div className="cp-error">
          <p>Failed to load capability themes: {(error as Error).message}</p>
        </div>
      ) : themes.length === 0 ? (
        <div className="cp-empty">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="cp-empty-icon">
            <polygon points="12 2 2 7 12 12 22 7 12 2" />
            <polyline points="2 17 12 22 22 17" />
            <polyline points="2 12 12 17 22 12" />
          </svg>
          <h3>No capability posture data yet</h3>
          <p>Start by scoping controls in the Control Scoping view to see capability theme posture.</p>
        </div>
      ) : (
        <div className="cp-grid">
          {themes.map((theme) => (
            <ThemeCard
              key={theme.theme_code}
              theme={theme}
              evidencePosture={evidenceByTheme[theme.theme_code]}
              onClick={setSelectedTheme}
            />
          ))}
        </div>
      )}
    </div>
  )
}
