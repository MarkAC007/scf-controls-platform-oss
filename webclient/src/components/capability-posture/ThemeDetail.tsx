import { useState } from 'react'
import { useCapabilityThemeControls } from '../../hooks/useCapabilityThemes'
import type {
  CapabilityThemeResponse,
  CapabilityThemeEvidencePosture,
  CapabilityThemeControlItem,
} from '../../types'
import AxisCard from './AxisCard'
import MaturityHistogram from './MaturityHistogram'
import ThemeEvidenceCards from './ThemeEvidenceCards'
import { bandToClass, formatAxisPercent } from './axisHelpers'

// Raised from 50 to 200 so the maturity histogram has the full picture for
// typical themes (issue #549 Phase 2). Server caps the upper bound.
const PAGE_SIZE = 200

type TabKey = 'in_scope' | 'catalog' | 'evidence'

const STATUS_LABELS: Record<string, string> = {
  monitored: 'Monitored',
  implemented: 'Implemented',
  ready_for_review: 'Ready for Review',
  in_progress: 'In Progress',
  not_started: 'Not Started',
  at_risk: 'At Risk',
  not_applicable: 'N/A',
  deferred: 'Deferred',
}

const STATUS_COLORS: Record<string, string> = {
  monitored: '#8b5cf6',
  implemented: '#22c55e',
  ready_for_review: '#06b6d4',
  in_progress: '#3b82f6',
  not_started: '#94a3b8',
  at_risk: '#ef4444',
  not_applicable: '#d1d5db',
  deferred: '#f59e0b',
}

const ASSESSMENT_STATUS_COLORS: Record<string, string> = {
  sufficient: '#22c55e',
  partial: '#f59e0b',
  insufficient: '#ef4444',
  pending: '#3b82f6',
  unassessed: '#94a3b8',
}

const ASSESSMENT_STATUS_LABELS: Record<string, string> = {
  sufficient: 'Sufficient',
  partial: 'Partial',
  insufficient: 'Insufficient',
  pending: 'Pending',
  unassessed: 'Unassessed',
}

const CONFIDENCE_LABELS: Record<string, { label: string; color: string }> = {
  strong: { label: 'Strong', color: '#22c55e' },
  moderate: { label: 'Moderate', color: '#f59e0b' },
  weak: { label: 'Weak', color: '#ef4444' },
  none: { label: 'None', color: '#94a3b8' },
}

interface ThemeDetailProps {
  theme: CapabilityThemeResponse
  evidencePosture?: CapabilityThemeEvidencePosture
  organizationId: string
  onBack: () => void
}

export default function ThemeDetail({ theme, evidencePosture, organizationId, onBack }: ThemeDetailProps) {
  const [activeTab, setActiveTab] = useState<TabKey>('in_scope')
  const { data, isLoading } = useCapabilityThemeControls(
    theme.theme_code,
    { limit: PAGE_SIZE, offset: 0 }
  )

  const posture = theme.posture
  const statusEntries = Object.entries(posture).filter(([, v]) => v > 0)
  const allControls = data?.controls ?? []
  const inScopeControls = allControls.filter(c => c.selected)

  return (
    <div className="cp-theme-detail">
      <div className="cp-detail-header">
        <button className="cp-back-btn" onClick={onBack}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="15 18 9 12 15 6" />
          </svg>
          Back to Themes
        </button>
        <div className="cp-detail-title-row">
          <h2 className="cp-detail-title">{theme.name}</h2>
          {theme.ksi_reference && (
            <span className="cp-ksi-badge">{theme.ksi_reference}</span>
          )}
        </div>
        <p className="cp-detail-description">{theme.description}</p>
      </div>

      <div className="cp-detail-composite">
        <div className="cp-detail-composite-value-block">
          <span className={`cp-detail-composite-value ${bandToClass(theme.composite_band)}`}>
            {formatAxisPercent(theme.composite_score)}
          </span>
          <div className="cp-detail-composite-meta">
            <span className="cp-detail-composite-caption">KSI Posture Score</span>
            {theme.composite_band && (
              <span className={`cp-detail-composite-band ${bandToClass(theme.composite_band)}`}>
                {theme.composite_band}
              </span>
            )}
            <span className="cp-detail-composite-formula" title="Composite KPS = 0.35·IC + 0.20·(M/5) + 0.20·EC + 0.25·EQ (null axes redistributed)">
              weighted 0.35 / 0.20 / 0.20 / 0.25
            </span>
          </div>
        </div>
        <div className="cp-detail-composite-scoped">
          <span className="cp-detail-composite-scoped-value">{theme.scoped_controls}</span>
          <span className="cp-detail-composite-scoped-label">of {theme.total_controls} scoped</span>
        </div>
      </div>

      <div className="cp-axis-card-grid">
        <AxisCard
          axis="IC"
          value={theme.implementation_coverage}
          band={theme.implementation_band}
        />
        <AxisCard
          axis="M"
          value={theme.maturity_score !== null ? theme.maturity_score / 5 : null}
          band={theme.maturity_band}
          maturityScore={theme.maturity_score}
        />
        <AxisCard
          axis="EC"
          value={theme.evidence_coverage}
          band={theme.evidence_coverage_band}
        />
        <AxisCard
          axis="EQ"
          value={theme.evidence_quality}
          band={theme.evidence_quality_band}
          warning={theme.evidence_quality_warning}
        />
      </div>

      <div className="cp-tab-nav" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'in_scope'}
          className={`cp-tab ${activeTab === 'in_scope' ? 'cp-tab-active' : ''}`}
          onClick={() => setActiveTab('in_scope')}
        >
          In Scope
          <span className="cp-tab-count">{inScopeControls.length}</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'catalog'}
          className={`cp-tab ${activeTab === 'catalog' ? 'cp-tab-active' : ''}`}
          onClick={() => setActiveTab('catalog')}
        >
          Catalog
          <span className="cp-tab-count">{allControls.length}</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'evidence'}
          className={`cp-tab ${activeTab === 'evidence' ? 'cp-tab-active' : ''}`}
          onClick={() => setActiveTab('evidence')}
        >
          Evidence
          {evidencePosture && (
            <span className="cp-tab-count">{evidencePosture.total_evidence_files}</span>
          )}
        </button>
      </div>

      <div className="cp-tab-panel" role="tabpanel">
        {activeTab === 'in_scope' && (
          <InScopeTab
            controls={inScopeControls}
            statusEntries={statusEntries}
            isLoading={isLoading}
          />
        )}
        {activeTab === 'catalog' && (
          <CatalogTab controls={allControls} isLoading={isLoading} />
        )}
        {activeTab === 'evidence' && (
          <EvidenceTab
            theme={theme}
            evidencePosture={evidencePosture}
            organizationId={organizationId}
            themeScfIds={allControls.map(c => c.scf_id)}
          />
        )}
      </div>
    </div>
  )
}

function InScopeTab({
  controls,
  statusEntries,
  isLoading,
}: {
  controls: CapabilityThemeControlItem[]
  statusEntries: [string, number][]
  isLoading: boolean
}) {
  if (isLoading) {
    return <div className="cp-detail-loading"><div className="loading-spinner" /></div>
  }
  return (
    <>
      {statusEntries.length > 0 && (
        <div className="cp-detail-distribution">
          <h3 className="cp-detail-section-title">Status Distribution</h3>
          <div className="cp-detail-status-bars">
            {statusEntries.map(([key, value]) => (
              <div key={key} className="cp-detail-status-row">
                <span
                  className="cp-detail-status-dot"
                  style={{ backgroundColor: STATUS_COLORS[key] || '#94a3b8' }}
                />
                <span className="cp-detail-status-label">
                  {STATUS_LABELS[key] || key}
                </span>
                <span className="cp-detail-status-count">{value}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="cp-detail-distribution">
        <h3 className="cp-detail-section-title">Maturity Distribution</h3>
        <MaturityHistogram controls={controls} />
      </div>

      <ControlsTable controls={controls} showSelectedColumn={false} />
    </>
  )
}

function CatalogTab({
  controls,
  isLoading,
}: {
  controls: CapabilityThemeControlItem[]
  isLoading: boolean
}) {
  if (isLoading) {
    return <div className="cp-detail-loading"><div className="loading-spinner" /></div>
  }
  return <ControlsTable controls={controls} showSelectedColumn={true} />
}

function EvidenceTab({
  theme,
  evidencePosture,
  organizationId,
  themeScfIds,
}: {
  theme: CapabilityThemeResponse
  evidencePosture?: CapabilityThemeEvidencePosture
  organizationId: string
  themeScfIds: string[]
}) {
  if (!evidencePosture) {
    return <p className="cp-detail-empty">No evidence posture data available for this theme.</p>
  }
  const assessmentBreakdown = [
    { key: 'sufficient', count: evidencePosture.sufficient_count },
    { key: 'partial', count: evidencePosture.partial_count },
    { key: 'insufficient', count: evidencePosture.insufficient_count },
    { key: 'pending', count: evidencePosture.pending_count },
    { key: 'unassessed', count: evidencePosture.unassessed_count },
  ].filter(s => s.count > 0)

  return (
    <div className="cp-detail-evidence">
      <div className="cp-detail-evidence-summary">
        <div className="cp-detail-stat">
          <span
            className="cp-detail-stat-value"
            style={{ color: CONFIDENCE_LABELS[evidencePosture.evidence_confidence]?.color }}
          >
            {CONFIDENCE_LABELS[evidencePosture.evidence_confidence]?.label || 'None'}
          </span>
          <span className="cp-detail-stat-label">Confidence</span>
        </div>
        <div className="cp-detail-stat">
          <span className="cp-detail-stat-value">
            {evidencePosture.controls_with_evidence}/{theme.scoped_controls}
          </span>
          <span className="cp-detail-stat-label">Controls with Evidence</span>
        </div>
        <div className="cp-detail-stat">
          <span className="cp-detail-stat-value">{evidencePosture.total_evidence_files}</span>
          <span className="cp-detail-stat-label">Evidence Files</span>
        </div>
        {evidencePosture.average_relevance_score !== null && (
          <div className="cp-detail-stat">
            <span className="cp-detail-stat-value">{evidencePosture.average_relevance_score}</span>
            <span className="cp-detail-stat-label">Avg Relevance</span>
          </div>
        )}
      </div>

      {evidencePosture.total_evidence_files > 0 && (
        <>
          <div className="cp-detail-evidence-bar">
            {assessmentBreakdown.map(({ key, count }) => (
              <div
                key={key}
                className="cp-posture-bar-segment"
                style={{
                  width: `${(count / evidencePosture.total_evidence_files) * 100}%`,
                  backgroundColor: ASSESSMENT_STATUS_COLORS[key],
                }}
                title={`${ASSESSMENT_STATUS_LABELS[key]}: ${count}`}
              />
            ))}
          </div>
          <div className="cp-detail-status-bars">
            {assessmentBreakdown.map(({ key, count }) => (
              <div key={key} className="cp-detail-status-row">
                <span
                  className="cp-detail-status-dot"
                  style={{ backgroundColor: ASSESSMENT_STATUS_COLORS[key] }}
                />
                <span className="cp-detail-status-label">{ASSESSMENT_STATUS_LABELS[key]}</span>
                <span className="cp-detail-status-count">{count}</span>
              </div>
            ))}
          </div>
        </>
      )}

      <ThemeEvidenceCards
        organizationId={organizationId}
        themeScfIds={themeScfIds}
      />
    </div>
  )
}

function ControlsTable({
  controls,
  showSelectedColumn,
}: {
  controls: CapabilityThemeControlItem[]
  showSelectedColumn: boolean
}) {
  if (controls.length === 0) {
    return <p className="cp-detail-empty">No controls found for this theme.</p>
  }
  return (
    <div className="cp-controls-table-wrapper">
      <table className="cp-controls-table">
        <thead>
          <tr>
            <th>SCF ID</th>
            <th>Control Name</th>
            <th>Domain</th>
            {showSelectedColumn && <th>Scoped</th>}
            <th>Status</th>
            <th>Maturity</th>
            <th>Relevance</th>
          </tr>
        </thead>
        <tbody>
          {controls.map((ctrl) => (
            <tr key={ctrl.scf_id}>
              <td className="cp-controls-id">{ctrl.scf_id}</td>
              <td>{ctrl.control_name || '--'}</td>
              <td>{ctrl.scf_domain || '--'}</td>
              {showSelectedColumn && (
                <td>
                  <span className={`cp-selected-badge cp-selected-${ctrl.selected ? 'yes' : 'no'}`}>
                    {ctrl.selected ? 'In Scope' : '—'}
                  </span>
                </td>
              )}
              <td>
                {ctrl.implementation_status ? (
                  <span
                    className="cp-status-badge"
                    style={{
                      backgroundColor: STATUS_COLORS[ctrl.implementation_status] || '#94a3b8',
                    }}
                  >
                    {STATUS_LABELS[ctrl.implementation_status] || ctrl.implementation_status}
                  </span>
                ) : (
                  <span className="cp-status-badge cp-status-unset">Unset</span>
                )}
              </td>
              <td>
                {ctrl.maturity_level ? (
                  <span className="cp-maturity-badge" data-level={ctrl.maturity_level}>
                    {ctrl.maturity_level}
                  </span>
                ) : '--'}
              </td>
              <td>
                <span className={`cp-relevance-badge cp-relevance-${ctrl.relevance}`}>
                  {ctrl.relevance}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
