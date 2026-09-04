/**
 * ControlDetailPage — full-width control detail view with breadcrumb + prev/next pager.
 *
 * Replaces the ControlDetail.tsx panel (deleted in Task 4). Fully prop-driven.
 * Layout (DetailState.html): breadcrumb bar → header block → assessment question
 * → 3-card grid → risk & threat context → TabRow (4 tabs) with full parity
 * content ported from ControlDetail.tsx.
 *
 * Keyboard: ArrowLeft→onPrev, ArrowRight→onNext, Escape→onBack.
 * Suppressed when focus is in input/textarea/select/contentEditable.
 */
import { useMemo, useState, useEffect, useCallback, type JSX } from 'react'
import type { EnrichedControl, ScopedControlsFile } from '../../types'
import { getEvidenceTracking } from '../../data/scopingService'
import { getEvidenceHealth, type EvidenceHealthResponse } from '../../data/apiClient'

import GraphView from '../GraphView'
import SCRMFocusBadges from '../SCRMFocusBadges'
import RiskThreatContext from '../RiskThreatContext'
import AssessmentObjectivesList from '../AssessmentObjectivesList'
import DeprecatedBadge, { getCatalogLifecycle } from '../DeprecatedBadge'
import TabRow from '../explorer/TabRow'

// ─── Types ────────────────────────────────────────────────────────────────────

export interface ControlDetailPageProps {
  control: EnrichedControl
  scopingEntry?: { selected: boolean; implementation_status?: string; maturity?: string | number; owner?: string } | null
  /**
   * null → total unknown (still resolving, pager fully hidden)
   * { index: null, total } → item not in filtered set, show "— of N" with both buttons disabled
   * { index: number, total } → normal pager
   */
  position: { index: number | null; total: number } | null
  onPrev: () => void
  onNext: () => void
  onBack: () => void
  onNavigateToEvidence?: (evidenceId: string) => void
  organizationId?: string
  scopingData?: ScopedControlsFile
  frameworkNames?: Record<string, string>
}

type DetailTab = 'details' | 'assessment' | 'mappings'

// ─── Helpers ──────────────────────────────────────────────────────────────────

/** True when the keyboard event target should suppress pager shortcuts. */
function isSuppressed(e: KeyboardEvent): boolean {
  const t = e.target
  if (!t || !(t instanceof Element)) return false
  const tag = (t as HTMLElement).tagName?.toLowerCase()
  if (!tag) return false
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true
  if ((t as HTMLElement).isContentEditable) return true
  return false
}

/** Format an implementation_status slug for display. */
function formatStatus(status: string): string {
  return status
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

/** Extract the active PPTDF label(s) as a short string (first active only, for a single chip). */
function getPptdfLabel(pptdf?: EnrichedControl['pptdf_applicability']): string | null {
  if (!pptdf) return null
  const map: [keyof typeof pptdf, string][] = [
    ['people', 'People'],
    ['process', 'Process'],
    ['technology', 'Technology'],
    ['data', 'Data'],
    ['facility', 'Facility'],
  ]
  const active = map.filter(([k]) => pptdf[k]).map(([, label]) => label)
  return active.length ? active.join(', ') : null
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function ControlDetailPage({
  control,
  scopingEntry,
  position,
  onPrev,
  onNext,
  onBack,
  onNavigateToEvidence,
  organizationId,
  scopingData,
  frameworkNames: _frameworkNames,
}: ControlDetailPageProps): JSX.Element {
  const [showGraph, setShowGraph] = useState(false)
  const [activeTab, setActiveTab] = useState<DetailTab>('details')
  const [healthData, setHealthData] = useState<EvidenceHealthResponse | null>(null)

  // ── Health data ─────────────────────────────────────────────────────────

  const loadHealthData = useCallback(async () => {
    if (!organizationId) return
    try {
      const result = await getEvidenceHealth(organizationId)
      setHealthData(result)
    } catch {
      // Optional enhancement — fail silently
    }
  }, [organizationId])

  useEffect(() => {
    loadHealthData()
  }, [loadHealthData])

  // ── Keyboard shortcuts ──────────────────────────────────────────────────

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent): void {
      if (isSuppressed(e)) return
      // Skip Escape when a dropdown is open — the dropdown's document-level handler
      // already consumed it (document fires before window) via stopPropagation.
      // This guard covers the rare jsdom ordering edge case in tests.
      if (
        e.key === 'Escape' &&
        document.querySelector('.theme-menu-panel, .user-dropdown-menu')
      ) return

      if (e.key === 'ArrowLeft') {
        e.preventDefault()
        onPrev()
      } else if (e.key === 'ArrowRight') {
        e.preventDefault()
        onNext()
      } else if (e.key === 'Escape') {
        e.preventDefault()
        onBack()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onPrev, onNext, onBack])

  // ── Derived data ────────────────────────────────────────────────────────

  const evidenceStatusItems = useMemo(() => {
    if (!control.artifactsResolved.length) return []
    return control.artifactsResolved.map((artifact) => {
      const tracking = scopingData ? getEvidenceTracking(scopingData, artifact.id) : null
      const healthItem = healthData?.items.find((i) => i.evidence_id === artifact.id)
      return {
        id: artifact.id,
        title: artifact.title,
        isTracked: tracking?.is_tracked || false,
        status: healthItem?.status || 'unknown',
        fileCount: healthItem?.file_count || 0,
      }
    })
  }, [control, scopingData, healthData])

  const groupedArtifacts = useMemo(() => {
    const groups: Record<string, { id: string; title: string }[]> = {}
    for (const a of control.artifactsResolved) {
      if (!groups[a.domain]) groups[a.domain] = []
      groups[a.domain].push({ id: a.id, title: a.title })
    }
    return groups
  }, [control])

  const totalArtifacts = control.artifactsResolved.length
  const totalFrameworks = Object.keys(control.frameworksResolved).length

  // ── Pager state ─────────────────────────────────────────────────────────

  const isFirst = position === null || position.index === null || position.index === 0
  const isLast =
    position === null ||
    position.index === null ||
    position.index === position.total - 1
  // position null → no text (total unknown); index null → "— of N" (not in filtered set)
  const positionText =
    position === null
      ? null
      : position.index === null
        ? `— of ${position.total}`
        : `${position.index + 1} of ${position.total}`

  // ── Scope / implementation state ────────────────────────────────────────

  const inScope = scopingEntry?.selected === true

  // ── Framework summary card: first 3 + "+N more" ─────────────────────────

  const frameworkKeys = Object.keys(control.frameworksResolved)
  const firstThreeFrameworks = frameworkKeys.slice(0, 3)
  const extraFrameworks = frameworkKeys.length - 3

  // ── Evidence card: linked / tracked counts ──────────────────────────────

  const linkedCount = totalArtifacts
  const trackedCount = evidenceStatusItems.filter((i) => i.isTracked).length

  // ── Tabs ─────────────────────────────────────────────────────────────────

  const tabs = [
    { id: 'details', label: 'Details' },
    { id: 'assessment', label: 'Assessment' },
    { id: 'mappings', label: 'Mappings', count: totalFrameworks },
  ]

  const pptdfLabel = getPptdfLabel(control.pptdf_applicability)

  // ─────────────────────────────────────────────────────────────────────────
  // Render
  // ─────────────────────────────────────────────────────────────────────────

  return (
    <div className="control-detail-page">
      {/* ── Breadcrumb / pager bar ─────────────────────────────────────────── */}
      <div className="control-detail-breadcrumb">
        <button
          className="control-detail-back-btn"
          onClick={onBack}
          aria-label="Back to Controls"
        >
          <svg
            className="control-detail-back-icon"
            width="14"
            height="14"
            viewBox="0 0 14 14"
            fill="none"
            aria-hidden="true"
          >
            <path
              d="M9 2L4 7l5 5"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          Controls
        </button>
        <span className="control-detail-breadcrumb-sep">/</span>
        <span className="control-detail-breadcrumb-id">{control.scf_id}</span>

        <div className="control-detail-pager">
          {positionText && (
            <span className="control-detail-position">{positionText}</span>
          )}
          <div className="control-detail-pager-buttons">
            <button
              className="control-detail-pager-btn"
              onClick={onPrev}
              disabled={isFirst}
              aria-label="Previous control"
            >
              <svg width="12" height="12" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                <path
                  d="M9 2L4 7l5 5"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
            <button
              className="control-detail-pager-btn"
              onClick={onNext}
              disabled={isLast}
              aria-label="Next control"
            >
              <svg width="12" height="12" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                <path
                  d="M5 2l5 5-5 5"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          </div>
        </div>
      </div>

      {/* ── Scrollable body ────────────────────────────────────────────────── */}
      <div className="control-detail-body">
        {/* ── Header block ──────────────────────────────────────────────────── */}
        <div className="control-detail-header">
          {/* 4px tick bar */}
          <div
            className={`control-detail-tick-bar${inScope ? ' control-detail-tick-bar--in-scope' : ''}`}
          />

          <div className="control-detail-header-inner">
            {/* Source tag row */}
            <div className="control-detail-source-row">
              <span className="scf-source-tag">SCF Catalog</span>
              <DeprecatedBadge {...getCatalogLifecycle(control)} />
              <button
                className={`btn-graph-toggle${showGraph ? ' active' : ''}`}
                onClick={() => setShowGraph((v) => !v)}
                title={showGraph ? 'Hide graph view' : 'Show graph view'}
                aria-label={showGraph ? 'Hide graph view' : 'Show graph view'}
              >
                📊
              </button>
              <div className="control-detail-meta-row">
                <span className="cadence-label">Domain:</span>
                <span className="cadence-badge">{control.scf_domain}</span>
                {control.validation_cadence && (
                  <>
                    <span className="cadence-label">Validation Cadence:</span>
                    <span className="cadence-badge">{control.validation_cadence}</span>
                  </>
                )}
              </div>
              <SCRMFocusBadges focus={control.scrm_focus} variant="bar" />
            </div>

            {/* Id + chips + weight bar row */}
            <div className="control-detail-chips-row">
              <span className="control-detail-mono-id">{control.scf_id}</span>
              {pptdfLabel && (
                <span className="control-detail-chip">{pptdfLabel}</span>
              )}
              {control.nist_csf_function && (
                <span className="control-detail-chip">{control.nist_csf_function}</span>
              )}
              {inScope && (
                <span className="control-detail-chip control-detail-chip--in-scope">
                  <svg
                    width="10"
                    height="10"
                    viewBox="0 0 10 10"
                    fill="none"
                    aria-hidden="true"
                  >
                    <path
                      d="M1.5 5.5l2.5 2.5 4.5-5"
                      stroke="currentColor"
                      strokeWidth="1.6"
                      strokeLinecap="round"
                    />
                  </svg>
                  In scope
                </span>
              )}
              {control.control_weighting != null && (
                <div className="control-detail-weight-bar-group">
                  <span className="control-detail-weight-label">Weight</span>
                  <div className="control-detail-weight-track">
                    <div
                      className="control-detail-weight-fill"
                      style={{ width: `${Math.min((control.control_weighting / 10) * 100, 100)}%` }}
                    />
                  </div>
                  <span className="control-detail-weight-value">{control.control_weighting}</span>
                </div>
              )}
            </div>

            {/* Title */}
            <h1 className="control-detail-title">{control.control_name}</h1>

            {/* Description */}
            <p className="control-detail-description">{control.control_description}</p>
          </div>
        </div>

        {/* ── Assessment question ────────────────────────────────────────────── */}
        {control.control_question && (
          <div className="control-detail-question-block">
            <div className="control-detail-question-label">ASSESSMENT QUESTION</div>
            <blockquote className="control-detail-question-text">
              &ldquo;{control.control_question}&rdquo;
            </blockquote>
          </div>
        )}

        {/* ── 3-card grid ────────────────────────────────────────────────────── */}
        <div className="control-detail-cards">
          {/* IMPLEMENTATION card */}
          <div className="control-detail-card">
            <div className="control-detail-card-label">IMPLEMENTATION</div>
            {inScope && scopingEntry ? (
              <div className="control-detail-card-body">
                {scopingEntry.implementation_status && (
                  <span className="control-detail-status-chip">
                    {formatStatus(scopingEntry.implementation_status)}
                  </span>
                )}
                {scopingEntry.maturity != null && (
                  <span className="control-detail-maturity">
                    Maturity {scopingEntry.maturity}
                  </span>
                )}
                {scopingEntry.owner && (
                  <div className="control-detail-owner">
                    Accountable — {scopingEntry.owner}
                  </div>
                )}
              </div>
            ) : (
              <div className="control-detail-not-in-scope">Not in scope</div>
            )}
          </div>

          {/* FRAMEWORK MAPPINGS card */}
          <div className="control-detail-card">
            <div className="control-detail-card-label">FRAMEWORK MAPPINGS</div>
            <div className="control-detail-fw-chips">
              {firstThreeFrameworks.map((fw) => (
                <span key={fw} className="control-detail-fw-chip">
                  {fw}
                </span>
              ))}
              {extraFrameworks > 0 && (
                <span className="control-detail-fw-more">+{extraFrameworks} more</span>
              )}
            </div>
          </div>

          {/* EVIDENCE card */}
          <div className="control-detail-card">
            <div className="control-detail-card-label">EVIDENCE</div>
            <div className="control-detail-evidence-counts">
              {linkedCount} items linked
              {trackedCount > 0 && <> · {trackedCount} tracked</>}
            </div>
            {onNavigateToEvidence && control.artifactsResolved.length > 0 && (
              <button
                className="control-detail-evidence-link"
                onClick={() => onNavigateToEvidence(control.artifactsResolved[0].id)}
              >
                Open in Evidence workspace
              </button>
            )}
          </div>
        </div>

        {/* ── Risk & Threat Context ──────────────────────────────────────────── */}
        <RiskThreatContext mapping={control.risk_threat_mapping} />

        {/* ── Graph or content ──────────────────────────────────────────────── */}
        {showGraph ? (
          <div className="graph-container">
            <GraphView control={control} />
          </div>
        ) : (
          <>
            {/* ── Tabs ───────────────────────────────────────────────────────── */}
            <TabRow
              tabs={tabs}
              activeId={activeTab}
              onSelect={(id) => setActiveTab(id as DetailTab)}
              aria-label="Control detail sections"
            />

            {/* ── Details Tab ─────────────────────────────────────────────────── */}
            {activeTab === 'details' && (
              <>
                {/* Additional Guidance */}
                {(control.policy_standard ||
                  control.implementation_guidance ||
                  control.testing_procedure) && (
                  <div className="detail-section-container surface-bedrock">
                    <div className="container-header">
                      <span className="container-icon">📄</span>
                      <span className="container-title">Additional Guidance</span>
                    </div>
                    <div className="container-content">
                      {control.policy_standard && (
                        <div className="detail-field">
                          <div className="field-label">
                            <span className="field-icon">📜</span>
                            Policy Standard
                          </div>
                          <div className="field-content">{control.policy_standard}</div>
                        </div>
                      )}
                      {control.implementation_guidance && (
                        <div className="detail-field">
                          <div className="field-label">
                            <span className="field-icon">💡</span>
                            Implementation Guidance
                          </div>
                          <div className="field-content prewrap">
                            {control.implementation_guidance}
                          </div>
                        </div>
                      )}
                      {control.testing_procedure && (
                        <div className="detail-field">
                          <div className="field-label">
                            <span className="field-icon">🔍</span>
                            Testing Procedure
                          </div>
                          <div className="field-content prewrap">
                            {control.testing_procedure}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Audit Artifacts */}
                <div className="detail-section-container surface-bedrock">
                  <div className="container-header">
                    <span className="container-icon">📋</span>
                    <span className="container-title">Audit Artifacts</span>
                    <span className="container-count">{totalArtifacts}</span>
                  </div>
                  <div className="container-content">
                    {Object.keys(groupedArtifacts).length === 0 ? (
                      <div className="muted">No artifacts listed</div>
                    ) : (
                      <div className="artifact-list-compact">
                        {Object.entries(groupedArtifacts).map(([domain, items]) => (
                          <div key={domain} className="artifact-domain-group">
                            <div className="artifact-domain-title">{domain}</div>
                            <div className="artifact-items">
                              {items.map((it) => (
                                <div key={it.id} className="artifact-item-compact">
                                  <span className="artifact-id-badge">{it.id}</span>
                                  <span className="artifact-title-text">{it.title}</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                {/* Evidence Status */}
                {evidenceStatusItems.length > 0 && (
                  <div className="detail-section-container">
                    <div className="container-header">
                      <span className="container-icon">&#x2714;&#xFE0F;</span>
                      <span className="container-title">Evidence Status</span>
                      <span className="container-count">{evidenceStatusItems.length}</span>
                    </div>
                    <div className="container-content">
                      <div className="evidence-status-grid">
                        {evidenceStatusItems.map((item) => (
                          <div
                            key={item.id}
                            className={`evidence-status-row${onNavigateToEvidence ? ' cursor-pointer' : ''}`}
                            onClick={() => onNavigateToEvidence?.(item.id)}
                          >
                            <span className={`ehd-status-dot ehd-dot-${item.status}`} />
                            <span className="evidence-status-id">{item.id}</span>
                            <span className="evidence-status-title">{item.title}</span>
                            <span className="evidence-status-files">
                              {item.fileCount > 0
                                ? `${item.fileCount} file${item.fileCount !== 1 ? 's' : ''}`
                                : 'No files'}
                            </span>
                            <span
                              className={`evidence-status-tracked ${item.isTracked ? 'tracked' : 'not-tracked'}`}
                            >
                              {item.isTracked ? 'Tracked' : 'Not tracked'}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </>
            )}

            {/* ── Assessment Tab ──────────────────────────────────────────────── */}
            {activeTab === 'assessment' && (
              <AssessmentObjectivesList scfId={control.scf_id} />
            )}

            {/* ── Mappings Tab ────────────────────────────────────────────────── */}
            {activeTab === 'mappings' && (
              <div className="detail-section-container surface-bedrock">
                <div className="container-header">
                  <span className="container-icon">🔗</span>
                  <span className="container-title">Framework Mappings</span>
                  <span className="container-count">{totalFrameworks}</span>
                </div>
                <div className="container-content">
                  {Object.keys(control.frameworksResolved).length === 0 ? (
                    <div className="muted">No mappings listed</div>
                  ) : (
                    <div className="framework-list-compact">
                      {Object.entries(control.frameworksResolved).map(([fw, refs]) => (
                        <div key={fw} className="framework-item-compact">
                          <div className="framework-name-compact">{fw}</div>
                          <div className="framework-refs">
                            {refs.map((r, i) => (
                              <span key={`${r}-${i}`} className="ref-chip">
                                {r}
                              </span>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
