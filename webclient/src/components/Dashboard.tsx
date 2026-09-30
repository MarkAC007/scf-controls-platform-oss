import { useState, useEffect } from 'react'
import type { EnrichedControl, ScopedControlsFile, EvidenceGapsResponse } from '../types'
import { getEvidenceGaps } from '../data/apiClient'
import { useDashboardStats } from '../hooks/useDashboardStats'
import { MaturityDistributionWidget } from './maturity'
import { FrequencyHealthTile } from './dashboard/FrequencyHealthTile'
import WorkQueuePanel from './dashboard/WorkQueuePanel'

// M4 (#574) — gate the Frequency Health tile mount on the build-time flag.
import { PER_WINDOW_REVIEW_ENABLED } from '../data/featureFlags'

interface DashboardProps {
  controls: EnrichedControl[]
  scopingData: ScopedControlsFile
  onScopingDataChange: (data: ScopedControlsFile) => void
  onNavigateToScoping?: (framework?: string) => void
  onNavigateToEvidence?: (evidenceId: string) => void
  onNavigateToControl?: (scfId: string) => void
}

export default function Dashboard({ controls, scopingData, onNavigateToScoping, onNavigateToEvidence, onNavigateToControl }: DashboardProps) {
  const stats = useDashboardStats(controls, scopingData)

  // Evidence gaps
  const [evidenceGaps, setEvidenceGaps] = useState<EvidenceGapsResponse | null>(null)
  const [loadingGaps, setLoadingGaps] = useState(false)
  const [showAllGaps, setShowAllGaps] = useState(false)

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

  const hasData = stats.selectedCount > 0

  if (!hasData) {
    return (
      <div className="dashboard-empty">
        <div className="empty-state">
          <div className="empty-icon">--</div>
          <h2>Welcome to Your GRC Dashboard</h2>
          <p>Your SCF catalogue is loaded. Select baseline frameworks in Framework Scoping or add individual controls from the Control Library — your posture and metrics will appear here once controls are in scope.</p>
        </div>
      </div>
    )
  }

  const statusSegments = [
    { key: 'implemented', label: 'Implemented', count: stats.statusCounts.implemented },
    { key: 'in_progress', label: 'In progress', count: stats.statusCounts.in_progress },
    { key: 'at_risk', label: 'At risk', count: stats.statusCounts.at_risk },
    { key: 'not_started', label: 'Not started', count: stats.statusCounts.not_started },
  ] as const

  const maturityLabel =
    stats.averageMaturity >= 4 ? 'Excellent' :
    stats.averageMaturity >= 3 ? 'Good' :
    stats.averageMaturity >= 2 ? 'Developing' :
    stats.averageMaturity > 0 ? 'Initial' : null

  const hasEvidenceMaturity = Object.values(stats.evidenceMaturityDistribution).some(count => count > 0)

  const controlOwners = Object.entries(stats.controlsByTeam).sort(([, a], [, b]) => b - a)
  const evidenceOwners = Object.entries(stats.evidenceByOwnerCounts).sort(([, a], [, b]) => b.total - a.total)

  return (
    <div className="dashboard">
      <div className="dashboard-header">
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
          <div className="kpi-secondary">{stats.statusCounts.at_risk > 0 ? 'Immediate action required' : 'No controls at risk'}</div>
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

      {/*
        One page, three questions — what needs doing, where we are, who owns
        it — instead of tabs split by data source (Implementation / Maturity /
        Evidence), which put the one actionable list beside two charts and gave
        a whole tab to a single maturity number. Framework-level coverage lives
        on Framework Scoping.
      */}
      <section className="dashboard-section" aria-labelledby="dashboard-attention">
        <h2 id="dashboard-attention" className="dashboard-section-title">Needs attention</h2>
        <div className="dashboard-attention-grid">
          {scopingData.organizationId && (
            <WorkQueuePanel
              orgId={scopingData.organizationId}
              onNavigateToEvidence={onNavigateToEvidence}
              onNavigateToControl={onNavigateToControl}
            />
          )}
          <div className="dashboard-attention-side">
            <div className="dashboard-card evidence-gaps-card">
              <div className="dashboard-card-header">
                <h3>Evidence not yet tracked</h3>
                {evidenceGaps && evidenceGaps.total_gaps > 0 && (
                  <span className="dashboard-card-count">{evidenceGaps.total_gaps}</span>
                )}
              </div>
              {loadingGaps ? (
                <div className="gaps-loading">Loading gap analysis...</div>
              ) : !evidenceGaps ? (
                <div className="gaps-error">Unable to load evidence gaps. Ensure Systems Registry is configured.</div>
              ) : evidenceGaps.total_gaps === 0 ? (
                <p className="dashboard-card-empty">
                  Every evidence item a registered system can collect is tracked.
                </p>
              ) : (
                <>
                  <p className="dashboard-card-note">
                    A registered system can collect these, but nobody tracks them yet.
                  </p>
                  <div className="gaps-list">
                    {(showAllGaps ? evidenceGaps.gaps : evidenceGaps.gaps.slice(0, 5)).map((gap) => (
                      <div
                        key={gap.evidence_id}
                        className="gap-item"
                        {...(onNavigateToEvidence
                          ? {
                              role: 'button',
                              tabIndex: 0,
                              onClick: () => onNavigateToEvidence(gap.evidence_id),
                              onKeyDown: (e: React.KeyboardEvent) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                  e.preventDefault()
                                  onNavigateToEvidence(gap.evidence_id)
                                }
                              },
                            }
                          : {})}
                      >
                        <div className="gap-item-main">
                          <div className="gap-item-id">{gap.evidence_id}</div>
                          {gap.evidence_title && <div className="gap-item-title">{gap.evidence_title}</div>}
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
                      </div>
                    ))}
                  </div>
                  {evidenceGaps.gaps.length > 5 && (
                    <button type="button" className="gaps-view-all" onClick={() => setShowAllGaps(prev => !prev)}>
                      {showAllGaps ? 'Show top 5' : `View all ${evidenceGaps.gaps.length}`}
                    </button>
                  )}
                </>
              )}
            </div>
            {/* M4 (#574) — mounts only when ENABLE_PER_WINDOW_REVIEW is on. */}
            {PER_WINDOW_REVIEW_ENABLED && scopingData.organizationId && (
              <FrequencyHealthTile orgId={scopingData.organizationId} />
            )}
          </div>
        </div>
      </section>

      {/*
        Status and ownership stack on the left; evidence maturity, the tallest
        card, runs beside both so neither section leaves a gap under it.
      */}
      <div className="dashboard-overview">
        <div className="dashboard-overview-main">
          <section className="dashboard-section" aria-labelledby="dashboard-status">
            <h2 id="dashboard-status" className="dashboard-section-title">Where we are</h2>
            <div className="dashboard-status-grid">
              <div className="dashboard-card">
                <div className="dashboard-card-header">
                  <h3>Implementation</h3>
                  <span className="dashboard-card-count">{stats.selectedCount} controls</span>
                </div>
                <div
                  className="dashboard-status-bar"
                  role="img"
                  aria-label={statusSegments.map(seg => `${seg.count} ${seg.label.toLowerCase()}`).join(', ')}
                >
                  {statusSegments.map(seg =>
                    seg.count > 0 && stats.selectedCount > 0 ? (
                      <span
                        key={seg.key}
                        className={`dashboard-status-segment dashboard-status-segment--${seg.key}`}
                        style={{ width: `${(seg.count / stats.selectedCount) * 100}%` }}
                      />
                    ) : null,
                  )}
                </div>
                <ul className="dashboard-status-legend">
                  {statusSegments.map(seg => (
                    <li key={seg.key}>
                      <i className={`dashboard-status-segment--${seg.key}`} />
                      <span>{seg.label}</span>
                      <strong>{seg.count}</strong>
                    </li>
                  ))}
                </ul>
                {onNavigateToScoping && (
                  <button type="button" className="dashboard-card-link" onClick={() => onNavigateToScoping()}>
                    By framework →
                  </button>
                )}
              </div>

              <div className="dashboard-card">
                <div className="dashboard-card-header">
                  <h3>Control maturity</h3>
                  {maturityLabel && <span className="dashboard-card-count">{maturityLabel}</span>}
                </div>
                {maturityLabel ? (
                  <>
                    <div className="dashboard-card-figure">{stats.averageMaturity.toFixed(1)}</div>
                    <div className="dashboard-card-note">Average maturity level</div>
                    <div className="dashboard-maturity-levels" role="img" aria-label="Controls per maturity level">
                      {(['L0', 'L1', 'L2', 'L3', 'L4', 'L5'] as const).map(level => (
                        <span key={level} title={`${level}: ${stats.maturityCounts[level]} controls`}>
                          <strong>{stats.maturityCounts[level]}</strong>
                          {level}
                        </span>
                      ))}
                    </div>
                  </>
                ) : (
                  <p className="dashboard-card-empty">
                    No control has a maturity level yet. Set one on a control's implementation record.
                  </p>
                )}
              </div>
            </div>
          </section>

          <section className="dashboard-section" aria-labelledby="dashboard-owners">
            <h2 id="dashboard-owners" className="dashboard-section-title">Who owns it</h2>
            <div className="dashboard-owner-grid">
              <OwnerList
                title="Controls by owner"
                rows={controlOwners.map(([owner, count]) => ({ owner, value: count, share: stats.selectedCount ? count / stats.selectedCount : 0, detail: `${count}` }))}
              />
              <OwnerList
                title="Evidence by owner"
                rows={evidenceOwners.map(([owner, data]) => ({
                  owner,
                  value: data.total,
                  share: data.total ? data.tracked / data.total : 0,
                  detail: `${data.tracked}/${data.total} tracked`,
                }))}
              />
            </div>
          </section>
        </div>
        {/* Part of "Where we are"; offset so its top lines up with that section's cards. */}
        <div className="dashboard-overview-side">
          {hasEvidenceMaturity ? (
            <MaturityDistributionWidget
              distribution={stats.evidenceMaturityDistribution}
              title="Evidence collection maturity"
              showScore={true}
              showLegend={true}
            />
          ) : (
            <div className="dashboard-card">
              <div className="dashboard-card-header">
                <h3>Evidence collection maturity</h3>
              </div>
              <p className="dashboard-card-empty">No tracked evidence has been assessed yet.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/* Unassigned is listed first and flagged: it is the row that needs someone. */
function OwnerList({
  title,
  rows,
}: {
  title: string
  rows: { owner: string; value: number; share: number; detail: string }[]
}) {
  const ordered = [...rows].sort(
    (a, b) => Number(b.owner === 'Unassigned') - Number(a.owner === 'Unassigned') || b.value - a.value,
  )
  return (
    <div className="dashboard-card">
      <div className="dashboard-card-header">
        <h3>{title}</h3>
      </div>
      {ordered.length === 0 ? (
        <p className="dashboard-card-empty">Nothing to show yet.</p>
      ) : (
        <ul className="dashboard-owner-list">
          {ordered.map(row => (
            <li key={row.owner} className={row.owner === 'Unassigned' ? 'is-unassigned' : undefined}>
              <span className="dashboard-owner-name">{row.owner}</span>
              <span className="dashboard-owner-bar" aria-hidden="true">
                <span style={{ width: `${Math.round(row.share * 100)}%` }} />
              </span>
              <span className="dashboard-owner-detail">{row.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
