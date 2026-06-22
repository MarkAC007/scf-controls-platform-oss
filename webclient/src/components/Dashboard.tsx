import { useState, useEffect, useCallback, useMemo } from 'react'
import type { EnrichedControl, ScopedControlsFile, EvidenceGapsResponse, FrameworkReadinessResponse, FrameworkReadinessRequest, FrameworkReadinessItem } from '../types'
import { getEvidenceGaps, getFrameworkReadiness } from '../data/apiClient'
import { useDashboardStats } from '../hooks/useDashboardStats'
import { MaturityDistributionWidget } from './maturity'
import FrameworkGapDetail from './FrameworkGapDetail'
import CollapsibleSection from './CollapsibleSection'
import { FRAMEWORK_GROUPS, OTHER_GROUP, getFrameworkGroup } from '../data/frameworkGroups'
import { FrameworkLogo } from './FrameworkLogo'
import { FrequencyHealthTile } from './dashboard/FrequencyHealthTile'

// M4 (#574) — gate the Frequency Health tile mount on the build-time flag.
const PER_WINDOW_REVIEW_ENABLED =
  import.meta.env.VITE_ENABLE_PER_WINDOW_REVIEW === 'true'

type DashboardTab = 'implementation' | 'maturity' | 'evidence' | 'frameworks'

interface DashboardProps {
  controls: EnrichedControl[]
  scopingData: ScopedControlsFile
  onScopingDataChange: (data: ScopedControlsFile) => void
  onNavigateToScoping?: (framework?: string) => void
}

export default function Dashboard({ controls, scopingData, onScopingDataChange, onNavigateToScoping }: DashboardProps) {
  const stats = useDashboardStats(controls, scopingData)

  const [activeTab, setActiveTab] = useState<DashboardTab>('implementation')

  // Expanded framework gap panels
  const [expandedFrameworkGaps, setExpandedFrameworkGaps] = useState<Set<string>>(new Set())

  const toggleFrameworkGap = useCallback((frameworkKey: string) => {
    setExpandedFrameworkGaps(prev => {
      const next = new Set(prev)
      if (next.has(frameworkKey)) {
        next.delete(frameworkKey)
      } else {
        next.add(frameworkKey)
      }
      return next
    })
  }, [])

  // Evidence gaps
  const [evidenceGaps, setEvidenceGaps] = useState<EvidenceGapsResponse | null>(null)
  const [loadingGaps, setLoadingGaps] = useState(false)

  useEffect(() => {
    const fetchGaps = async () => {
      setLoadingGaps(true)
      try {
        const gaps = await getEvidenceGaps()
        setEvidenceGaps(gaps)
      } catch (error) {
        console.error('Failed to load evidence gaps:', error)
      } finally {
        setLoadingGaps(false)
      }
    }
    fetchGaps()
  }, [])

  // Framework readiness
  const [frameworkReadiness, setFrameworkReadiness] = useState<FrameworkReadinessResponse | null>(null)
  const [loadingReadiness, setLoadingReadiness] = useState(false)

  const frameworkMappingRequest = useMemo((): FrameworkReadinessRequest | null => {
    if (!controls.length) return null

    const frameworks: FrameworkReadinessRequest['frameworks'] = {}

    controls.forEach(control => {
      Object.keys(control.frameworksResolved).forEach(frameworkName => {
        if (!frameworks[frameworkName]) {
          frameworks[frameworkName] = { controls: [], evidence: [] }
        }
        if (!frameworks[frameworkName].controls.includes(control.scf_id)) {
          frameworks[frameworkName].controls.push(control.scf_id)
        }
        control.artifactsResolved.forEach(artifact => {
          if (!frameworks[frameworkName].evidence.includes(artifact.id)) {
            frameworks[frameworkName].evidence.push(artifact.id)
          }
        })
      })
    })

    return { frameworks }
  }, [controls])

  useEffect(() => {
    const fetchReadiness = async () => {
      if (!frameworkMappingRequest) return
      setLoadingReadiness(true)
      try {
        const readiness = await getFrameworkReadiness(frameworkMappingRequest)
        setFrameworkReadiness(readiness)
      } catch (error) {
        console.error('Failed to load framework readiness:', error)
      } finally {
        setLoadingReadiness(false)
      }
    }
    fetchReadiness()
  }, [frameworkMappingRequest])

  const readinessMap = useMemo(() => {
    if (!frameworkReadiness) return new Map<string, FrameworkReadinessItem>()
    return new Map(frameworkReadiness.frameworks.map(f => [f.framework_name, f]))
  }, [frameworkReadiness])

  // Scoped-only toggle for frameworks tab
  const [scopedOnly, setScopedOnly] = useState(() => {
    return localStorage.getItem('scf-dashboard-scoped-only') === 'true'
  })

  useEffect(() => {
    localStorage.setItem('scf-dashboard-scoped-only', String(scopedOnly))
  }, [scopedOnly])

  // Group frameworks by geographic/organizational prefix
  const groupedFrameworks = useMemo(() => {
    const filtered = scopedOnly
      ? stats.frameworkStats.filter(fw => fw.totalControls > 0 && fw.selectedControls === fw.totalControls)
      : stats.frameworkStats

    const groupMap = new Map<string, typeof filtered>()

    for (const fw of filtered) {
      const groupId = getFrameworkGroup(fw.frameworkKey)
      if (!groupMap.has(groupId)) {
        groupMap.set(groupId, [])
      }
      groupMap.get(groupId)!.push(fw)
    }

    // Return ordered array following FRAMEWORK_GROUPS order, Other last
    const allGroups = [...FRAMEWORK_GROUPS, OTHER_GROUP]
    return allGroups
      .filter(g => groupMap.has(g.id) && groupMap.get(g.id)!.length > 0)
      .map(g => ({
        ...g,
        frameworks: groupMap.get(g.id)!
      }))
  }, [stats.frameworkStats, scopedOnly])

  const hasData = stats.selectedCount > 0

  if (!hasData) {
    return (
      <div className="dashboard-empty">
        <div className="empty-state">
          <div className="empty-icon">--</div>
          <h2>Welcome to Your GRC Dashboard</h2>
          <p>Your SCF catalogue is loaded. Head to the Control Scoping tab to choose the frameworks and controls that apply to your organisation — your posture and metrics will appear here once controls are scoped.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="dashboard">
      <div className="dashboard-header">
        <h1 className="page-title">GRC Dashboard</h1>
        <p className="page-subtitle">Real-time governance oversight and risk posture analysis.</p>
      </div>

      {/* KPI Summary Row */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-card-header">
            <span className="kpi-label">Controls in Scope</span>
            <span className="kpi-icon">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
            </span>
          </div>
          <div className="kpi-value">{stats.selectedCount}</div>
          <div className="kpi-secondary">{stats.totalEvidence > 0 ? `${stats.selectedCount} scoped` : 'Scope controls to begin'}</div>
          <div className="kpi-glow"></div>
        </div>
        <div className="kpi-card">
          <div className="kpi-card-header">
            <span className="kpi-label">Implemented</span>
            <span className="kpi-icon">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
            </span>
          </div>
          <div className="kpi-value">{stats.implementedPercentage}%</div>
          <div className="kpi-secondary">{stats.statusCounts.implemented} of {stats.selectedCount} controls completed</div>
          <div className="kpi-glow"></div>
        </div>
        <div className="kpi-card">
          <div className="kpi-card-header">
            <span className="kpi-label">At Risk</span>
            <span className="kpi-icon">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
            </span>
          </div>
          <div className="kpi-value">{stats.statusCounts.at_risk}</div>
          <div className="kpi-secondary">Immediate action required</div>
          <div className="kpi-glow"></div>
        </div>
        <div className="kpi-card">
          <div className="kpi-card-header">
            <span className="kpi-label">Evidence Tracked</span>
            <span className="kpi-icon">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/></svg>
            </span>
          </div>
          <div className="kpi-value">{stats.evidencePercentage}%</div>
          <div className="kpi-secondary">{stats.trackedEvidence} of {stats.totalEvidence} evidence items</div>
          <div className="kpi-glow"></div>
        </div>
      </div>

      {/* Tab Navigation */}
      <div className="dashboard-tabs">
        <button
          className={`dashboard-tab${activeTab === 'implementation' ? ' dashboard-tab-active' : ''}`}
          onClick={() => setActiveTab('implementation')}
        >
          Implementation
        </button>
        <button
          className={`dashboard-tab${activeTab === 'maturity' ? ' dashboard-tab-active' : ''}`}
          onClick={() => setActiveTab('maturity')}
        >
          Maturity
        </button>
        <button
          className={`dashboard-tab${activeTab === 'evidence' ? ' dashboard-tab-active' : ''}`}
          onClick={() => setActiveTab('evidence')}
        >
          Evidence
        </button>
        <button
          className={`dashboard-tab${activeTab === 'frameworks' ? ' dashboard-tab-active' : ''}`}
          onClick={() => setActiveTab('frameworks')}
        >
          Frameworks
        </button>
      </div>

      {/* Tab Content */}
      <div className="dashboard-tab-content">

        {/* === IMPLEMENTATION TAB === */}
        {activeTab === 'implementation' && (
          <div className="dashboard-two-col">
            <div className="dashboard-col-left">
              <div className="stat-card-feature stat-card-success">
                <div className="feature-header">
                  <h3>Implementation Progress</h3>
                </div>
                <div className="feature-main">
                  <div className="feature-value">{stats.statusCounts.implemented}</div>
                  <div className="feature-label">Implemented</div>
                  <div className="feature-progress">
                    <div className="feature-progress-bar">
                      <div className="feature-progress-fill" style={{ width: `${stats.implementedPercentage}%` }}></div>
                    </div>
                    <div className="feature-progress-text">{stats.implementedPercentage}% of {stats.selectedCount} controls</div>
                  </div>
                </div>
                <div className="feature-breakdown">
                  <div className="feature-breakdown-item">
                    <span className="status-dot status-in_progress"></span>
                    <span>In Progress</span>
                    <strong>{stats.statusCounts.in_progress}</strong>
                  </div>
                  <div className="feature-breakdown-item">
                    <span className="status-dot status-at_risk"></span>
                    <span>At Risk</span>
                    <strong>{stats.statusCounts.at_risk}</strong>
                  </div>
                  <div className="feature-breakdown-item">
                    <span className="status-dot status-not_started"></span>
                    <span>Not Started</span>
                    <strong>{stats.statusCounts.not_started}</strong>
                  </div>
                </div>
              </div>
            </div>

            <div className="dashboard-col-right">
              <div className="stat-card">
                <h3>Controls by Owner Team</h3>
                <div className="stat-list">
                  {Object.entries(stats.controlsByTeam)
                    .sort(([,a], [,b]) => b - a)
                    .map(([team, count]) => (
                      <div key={team} className="stat-list-item">
                        <span className="stat-list-label">{team}</span>
                        <span className="stat-list-value">{count}</span>
                        <div className="stat-list-bar">
                          <div className="stat-list-bar-fill" style={{ width: `${stats.selectedCount > 0 ? (count / stats.selectedCount) * 100 : 0}%` }}></div>
                        </div>
                      </div>
                    ))}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* === MATURITY TAB === */}
        {activeTab === 'maturity' && (
          <div className="stat-card-accent">
            <div className="accent-header">
              <h3>Control Maturity</h3>
              <div className="accent-badge">
                {stats.averageMaturity >= 4 ? 'Excellent' :
                 stats.averageMaturity >= 3 ? 'Good' :
                 stats.averageMaturity >= 2 ? 'Developing' :
                 stats.averageMaturity > 0 ? 'Initial' : 'N/A'}
              </div>
            </div>
            <div className="accent-main">
              <div className="accent-value">{stats.averageMaturity > 0 ? stats.averageMaturity.toFixed(1) : 'N/A'}</div>
              <div className="accent-label">Average Maturity Level</div>
            </div>
            {(() => {
              const bars = [
                { key: 'L0', label: 'L0', count: stats.maturityCounts.L0 },
                { key: 'L1', label: 'L1', count: stats.maturityCounts.L1 },
                { key: 'L2', label: 'L2', count: stats.maturityCounts.L2 },
                { key: 'L3', label: 'L3', count: stats.maturityCounts.L3 },
                { key: 'L4', label: 'L4', count: stats.maturityCounts.L4 },
                { key: 'L5', label: 'L5', count: stats.maturityCounts.L5 },
                ...(stats.maturityCounts.unset > 0
                  ? [{ key: 'unset', label: '—', count: stats.maturityCounts.unset }]
                  : []),
              ]
              const LEVEL_COLORS: Record<string, string> = {
                L0: '#ef4444', L1: '#f97316', L2: '#f59e0b',
                L3: '#22c55e', L4: '#16a34a', L5: '#15803d', unset: '#94a3b8',
              }
              const max = Math.max(1, ...bars.map(b => b.count))
              return (
                <div className="cp-histogram" role="img" aria-label="Maturity level distribution histogram">
                  <div className="cp-histogram-bars">
                    {bars.map(bar => {
                      const heightPct = (bar.count / max) * 100
                      return (
                        <div key={bar.key} className="cp-histogram-col">
                          <div className="cp-histogram-bar-track">
                            <div
                              className="cp-histogram-bar-fill"
                              style={{
                                height: `${heightPct}%`,
                                backgroundColor: LEVEL_COLORS[bar.key] || LEVEL_COLORS.unset,
                              }}
                              title={`${bar.label}: ${bar.count} control${bar.count === 1 ? '' : 's'}`}
                            />
                          </div>
                          <span className="cp-histogram-count">{bar.count}</span>
                          <span className="cp-histogram-label">{bar.label}</span>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )
            })()}
          </div>
        )}

        {/* === EVIDENCE TAB === */}
        {activeTab === 'evidence' && (
          <>
            {/* M4 (#574) — Frequency Health tile, mounts only when the
                ENABLE_PER_WINDOW_REVIEW flag is on. ``scopingData.organizationId``
                follows the same pattern as the rest of the dashboard. */}
            {PER_WINDOW_REVIEW_ENABLED && scopingData.organizationId && (
              <div className="evidence-section">
                <FrequencyHealthTile orgId={scopingData.organizationId} />
              </div>
            )}
            {/* Evidence Tracking */}
            <div className="evidence-section">
              <h2>Evidence Tracking & Team Burden</h2>
              <div className="evidence-grid">
                <div className="evidence-summary">
                  <div className="evidence-summary-header">
                    <div>
                      <div className="evidence-value">{stats.trackedEvidence}<span className="evidence-total">/{stats.totalEvidence}</span></div>
                      <div className="evidence-label">Evidence Items Tracked</div>
                    </div>
                  </div>
                  <div className="evidence-progress">
                    <div className="evidence-progress-bar">
                      <div className="evidence-progress-fill" style={{ width: `${stats.evidencePercentage}%` }}></div>
                    </div>
                    <div className="evidence-progress-text">{stats.evidencePercentage}% Complete</div>
                  </div>
                </div>

                <div className="evidence-teams">
                  {Object.entries(stats.evidenceByTeamCounts)
                    .sort(([,a], [,b]) => b.total - a.total)
                    .map(([team, data]) => {
                      const percentage = data.total > 0 ? Math.round((data.tracked / data.total) * 100) : 0
                      return (
                        <div key={team} className="evidence-team-card">
                          <div className="evidence-team-header">
                            <span className="evidence-team-name">{team}</span>
                            <span className="evidence-team-percentage">{percentage}%</span>
                          </div>
                          <div className="evidence-team-stats">
                            <span className="evidence-team-tracked">{data.tracked} tracked</span>
                            <span className="evidence-team-divider">&bull;</span>
                            <span className="evidence-team-total">{data.total} total</span>
                          </div>
                          <div className="evidence-team-bar">
                            <div className="evidence-team-bar-fill" style={{ width: `${percentage}%` }}></div>
                          </div>
                        </div>
                      )
                    })}
                </div>
              </div>
            </div>

            {/* Evidence Collection Maturity Distribution */}
            {Object.values(stats.evidenceMaturityDistribution).some(count => count > 0) && (
              <div className="evidence-maturity-section">
                <h2>Evidence Collection Maturity</h2>
                <div className="evidence-maturity-grid">
                  <MaturityDistributionWidget
                    distribution={stats.evidenceMaturityDistribution}
                    title="Collection Process Maturity"
                    showScore={true}
                    showLegend={true}
                  />
                </div>
              </div>
            )}

            {/* Evidence Collection Gaps */}
            <div className="evidence-gaps-section">
              <h2>Evidence Collection Gaps</h2>
              <div className="evidence-gaps-content">
                {loadingGaps ? (
                  <div className="gaps-loading">Loading gap analysis...</div>
                ) : !evidenceGaps ? (
                  <div className="gaps-error">Unable to load evidence gaps. Ensure Systems Registry is configured.</div>
                ) : evidenceGaps.total_gaps === 0 ? (
                  <div className="gaps-success">
                    <div className="gaps-success-icon">&#10003;</div>
                    <div className="gaps-success-text">
                      <strong>Excellent!</strong> All evidence items with capable systems are being tracked.
                    </div>
                  </div>
                ) : (
                  <div className="gaps-grid">
                    <div className="stat-card-accent gaps-summary-card">
                      <div className="accent-header">
                        <h3>Collection Coverage</h3>
                        <div className={`accent-badge ${
                          evidenceGaps.coverage_percentage >= 90 ? 'badge-success' :
                          evidenceGaps.coverage_percentage >= 70 ? 'badge-good' :
                          evidenceGaps.coverage_percentage >= 50 ? 'badge-warning' :
                          'badge-danger'
                        }`}>
                          {evidenceGaps.coverage_percentage >= 90 ? 'Excellent' :
                           evidenceGaps.coverage_percentage >= 70 ? 'Good' :
                           evidenceGaps.coverage_percentage >= 50 ? 'Needs Work' :
                           'Critical'}
                        </div>
                      </div>
                      <div className="gaps-stats-row">
                        <div className="gaps-stat">
                          <div className="gaps-stat-value gaps-value-danger">{evidenceGaps.total_gaps}</div>
                          <div className="gaps-stat-label">Gaps</div>
                        </div>
                        <div className="gaps-stat">
                          <div className="gaps-stat-value gaps-value-success">{evidenceGaps.total_tracked}</div>
                          <div className="gaps-stat-label">Tracked</div>
                        </div>
                        <div className="gaps-stat">
                          <div className="gaps-stat-value">{evidenceGaps.total_evidence}</div>
                          <div className="gaps-stat-label">Total</div>
                        </div>
                      </div>
                      <div className="gaps-progress">
                        <div className="gaps-progress-bar">
                          <div className="gaps-progress-fill" style={{ width: `${evidenceGaps.coverage_percentage}%` }}></div>
                        </div>
                        <div className="gaps-progress-text">{evidenceGaps.coverage_percentage.toFixed(1)}% Coverage</div>
                      </div>
                    </div>

                    <div className="gaps-list-card">
                      <div className="gaps-list-header">
                        <h4>Top Gaps Needing Attention</h4>
                        <span className="gaps-list-count">{evidenceGaps.gaps.length} total</span>
                      </div>
                      <div className="gaps-list">
                        {evidenceGaps.gaps.slice(0, 5).map((gap) => (
                          <div key={gap.evidence_id} className="gap-item">
                            <div className="gap-item-main">
                              <div className="gap-item-id">{gap.evidence_id}</div>
                              {gap.evidence_title && (
                                <div className="gap-item-title">{gap.evidence_title}</div>
                              )}
                              <div className="gap-item-meta">
                                <span className="gap-controls-count">
                                  Required by {gap.required_by_controls.length} control{gap.required_by_controls.length !== 1 ? 's' : ''}
                                </span>
                                {gap.capable_systems.length > 0 && (
                                  <>
                                    <span className="gap-meta-divider">&bull;</span>
                                    <span className="gap-systems">
                                      {gap.capable_systems.slice(0, 2).join(', ')}
                                      {gap.capable_systems.length > 2 && ` +${gap.capable_systems.length - 2} more`}
                                    </span>
                                  </>
                                )}
                              </div>
                            </div>
                            {gap.recommended_action && (
                              <div className="gap-item-action" title={gap.recommended_action}>
                                Tip
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                      {evidenceGaps.gaps.length > 5 && (
                        <div className="gaps-list-footer">
                          <a href="#evidence" className="gaps-view-all">
                            View all {evidenceGaps.gaps.length} gaps &rarr;
                          </a>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </>
        )}

        {/* === FRAMEWORKS TAB === */}
        {activeTab === 'frameworks' && (
          <div className="framework-tracking-section">
            <div className="framework-section-header">
              <h2>Framework Coverage & Implementation Status</h2>
              <div className="framework-toolbar">
                <button
                  className={`scope-toggle-btn${!scopedOnly ? ' active' : ''}`}
                  onClick={() => setScopedOnly(false)}
                >
                  All Frameworks
                </button>
                <button
                  className={`scope-toggle-btn${scopedOnly ? ' active' : ''}`}
                  onClick={() => setScopedOnly(true)}
                >
                  Scoped Only
                </button>
              </div>
            </div>
            {loadingReadiness && (
              <div className="readiness-loading">Calculating readiness scores...</div>
            )}
            {groupedFrameworks.length === 0 ? (
              <div className="framework-empty-state">
                <p>No frameworks match the current filter. Try switching to "All Frameworks".</p>
              </div>
            ) : (
              groupedFrameworks.map(group => (
                <CollapsibleSection
                  key={group.id}
                  icon={group.emoji}
                  title={group.label}
                  count={group.frameworks.length}
                  defaultCollapsed={true}
                >
                  <div className="framework-grid">
                    {group.frameworks.map((fwStat) => {
                      const apiReadiness = readinessMap.get(fwStat.frameworkName)

                      const selectionPercentage = fwStat.totalControls > 0
                        ? Math.round((fwStat.selectedControls / fwStat.totalControls) * 100)
                        : 0

                      const readinessPercentage = apiReadiness?.readiness_score ?? (
                        fwStat.totalControls > 0
                          ? Math.round((fwStat.implementedControls / fwStat.totalControls) * 100)
                          : 0
                      )

                      const implementationScore = apiReadiness?.implementation_score ?? (
                        fwStat.selectedControls > 0
                          ? Math.round((fwStat.implementedControls / fwStat.selectedControls) * 100)
                          : 0
                      )

                      const evidenceScore = apiReadiness?.evidence_score ?? 0
                      const trackedEvidence = apiReadiness?.tracked_evidence ?? 0
                      const totalEvidence = apiReadiness?.total_evidence ?? 0

                      const readinessGrade = apiReadiness?.readiness_grade ?? (
                        readinessPercentage >= 90 ? 'excellent' :
                        readinessPercentage >= 70 ? 'good' :
                        readinessPercentage >= 50 ? 'fair' :
                        'needs-work'
                      )

                      return (
                        <div key={fwStat.frameworkKey} className="framework-card">
                          <div className="framework-card-header">
                            <div className="framework-logo-placeholder">
                              <FrameworkLogo frameworkName={fwStat.frameworkName} size={64} />
                            </div>
                            <div className="framework-card-title">
                              <h3>{fwStat.frameworkName}</h3>
                              <div className="framework-card-subtitle">{fwStat.frameworkKey}</div>
                            </div>
                          </div>

                          <div className="framework-stats-grid">
                            <div className="framework-stat-item">
                              <div className="framework-stat-value">{fwStat.totalControls}</div>
                              <div className="framework-stat-label">Total Controls</div>
                            </div>
                            <div className="framework-stat-item framework-stat-highlight">
                              <div className="framework-stat-value">{fwStat.selectedControls}</div>
                              <div className="framework-stat-label">In Scope</div>
                            </div>
                          </div>

                          <div className="framework-progress-section">
                            <div className="framework-progress-header">
                              <span className="framework-progress-label">Scope Coverage</span>
                              <span className="framework-progress-percentage">{selectionPercentage}%</span>
                            </div>
                            <div className="framework-progress-bar">
                              <div className="framework-progress-fill" style={{ width: `${selectionPercentage}%` }}></div>
                            </div>
                          </div>

                          {fwStat.selectedControls > 0 && (
                            <>
                              <div className="framework-implementation-breakdown">
                                <div className="framework-breakdown-title">Implementation Status</div>
                                <div className="framework-breakdown-bars">
                                  {([
                                    { status: 'implemented', label: 'Implemented', count: fwStat.implementedControls },
                                    { status: 'in_progress', label: 'In Progress', count: fwStat.inProgressControls },
                                    { status: 'at_risk', label: 'At Risk', count: fwStat.atRiskControls },
                                    { status: 'not_started', label: 'Not Started', count: fwStat.notStartedControls },
                                  ] as const).map(({ status, label, count }) => (
                                    <div key={status} className="framework-breakdown-item">
                                      <div className="framework-breakdown-label">
                                        <span className={`status-dot status-${status}`}></span>
                                        <span>{label}</span>
                                        <strong>{count}</strong>
                                      </div>
                                      <div className="framework-breakdown-bar">
                                        <div
                                          className={`framework-breakdown-fill fw-${status.replace('_', '-')}`}
                                          style={{ width: `${fwStat.selectedControls > 0 ? (count / fwStat.selectedControls) * 100 : 0}%` }}
                                        ></div>
                                      </div>
                                    </div>
                                  ))}
                                </div>
                              </div>

                              {totalEvidence > 0 && (
                                <div className="framework-evidence-breakdown">
                                  <div className="framework-breakdown-title">Evidence Collection</div>
                                  <div className="framework-evidence-stats">
                                    <span className="evidence-tracked">{trackedEvidence} tracked</span>
                                    <span className="evidence-divider">/</span>
                                    <span className="evidence-total">{totalEvidence} required</span>
                                    <span className="evidence-percentage">({evidenceScore.toFixed(0)}%)</span>
                                  </div>
                                  <div className="framework-evidence-bar">
                                    <div className="framework-evidence-fill" style={{ width: `${evidenceScore}%` }}></div>
                                  </div>
                                </div>
                              )}

                              <div
                                className="framework-readiness-badge"
                                title={apiReadiness
                                  ? `Readiness = (40% x Implementation ${implementationScore.toFixed(0)}%) + (60% x Evidence ${evidenceScore.toFixed(0)}%)`
                                  : 'Loading readiness calculation...'}
                              >
                                <span className="readiness-label">Framework Readiness:</span>
                                <span className={`readiness-value readiness-${readinessGrade}`}>
                                  {readinessGrade === 'excellent' ? 'Excellent' :
                                   readinessGrade === 'good' ? 'Good' :
                                   readinessGrade === 'fair' ? 'Fair' :
                                   'Needs Work'}
                                </span>
                                <span className="readiness-percentage">{readinessPercentage.toFixed(0)}%</span>
                              </div>

                              {apiReadiness && (
                                <div className="framework-readiness-breakdown">
                                  <div className="readiness-component">
                                    <span className="component-label">Implementation (40%)</span>
                                    <span className="component-value">{implementationScore.toFixed(0)}%</span>
                                  </div>
                                  <div className="readiness-component">
                                    <span className="component-label">Evidence (60%)</span>
                                    <span className="component-value">{evidenceScore.toFixed(0)}%</span>
                                  </div>
                                </div>
                              )}
                            </>
                          )}

                          {fwStat.gapControlIds.length > 0 && (
                            <button
                              className="framework-gap-toggle"
                              onClick={() => toggleFrameworkGap(fwStat.frameworkKey)}
                            >
                              <span className="framework-gap-badge">{fwStat.gapControlIds.length} gaps</span>
                              <span className="framework-gap-toggle-text">
                                {expandedFrameworkGaps.has(fwStat.frameworkKey) ? 'Hide Gap Analysis' : 'Show Gap Analysis'}
                              </span>
                              <span className={`framework-gap-toggle-icon ${expandedFrameworkGaps.has(fwStat.frameworkKey) ? 'expanded' : ''}`}>
                                &#9660;
                              </span>
                            </button>
                          )}

                          {expandedFrameworkGaps.has(fwStat.frameworkKey) && (
                            <FrameworkGapDetail
                              frameworkName={fwStat.frameworkName}
                              frameworkKey={fwStat.frameworkKey}
                              gapControlIds={fwStat.gapControlIds}
                              gapsByDomain={fwStat.gapsByDomain}
                              totalControls={fwStat.totalControls}
                              selectedControls={fwStat.selectedControls}
                              onScopingDataChange={onScopingDataChange}
                              scopingData={scopingData}
                              onNavigateToScoping={onNavigateToScoping}
                            />
                          )}
                        </div>
                      )
                    })}
                  </div>
                </CollapsibleSection>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  )
}
