/**
 * ScopingDetailPage — full-width scoping detail view.
 *
 * Fully prop-driven (presentational). All data fetching, debounce (300 ms),
 * API calls, and refetch semantics live in the ScopingPage container (Task 3).
 * This component fires callbacks immediately on each user action; the container
 * owns the debounce + PATCH + refetch cycle.
 *
 * Props contract:
 *   onFieldChange(field, value) — called on EVERY field change; NO debounce here.
 *   onToggleScope(scf_id)       — scope checkbox; container calls updateScopedControl.
 *   onReloadTeamAssignments()   — called after OwningTeams changes.
 *
 * Shell mirrors ControlDetailPage: breadcrumb "‹ Scoping / <id>" + "k of N"
 * pager + window keyboard ←/→/Esc with input/textarea/select/contentEditable
 * suppression + open-dropdown guard (.theme-menu-panel / .user-dropdown-menu).
 */
import { useState, useEffect, useMemo, type JSX } from 'react'
import type {
  ScopedControlsFile,
  ImplementationStatus,
  Priority,
  MaturityLevel,
  ResolvedArtifact,
  SCRMFocus,
  CMMaturityGuidance,
  BusinessSizeGuidance as BusinessSizeGuidanceType,
  RiskThreatMapping,
  NistCsfFunction,
} from '../../types'
import { getEvidenceTracking } from '../../data/scopingService'

import MaturityRoadmap from '../MaturityRoadmap'
import BusinessSizeGuidance from '../BusinessSizeGuidance'
import SCRMFocusBadges from '../SCRMFocusBadges'
import RiskThreatContext from '../RiskThreatContext'
import CDMControlPanel from '../CDMControlPanel'
import DeprecatedBadge, { getCatalogLifecycle } from '../DeprecatedBadge'
import { ModernCommentThread } from '../ModernCommentThread'
import { AuditLogPanel } from '../AuditLogPanel'
import { AssignmentPicker } from '../AssignmentPicker'
import OwningTeams from '../OwningTeams'
import TabRow from '../explorer/TabRow'

// ─── Types ────────────────────────────────────────────────────────────────────

/**
 * The subset of a control's catalog fields this page needs.
 * Task 3 will pass the enriched ScopedControlWithCatalog shape after resolving
 * artifacts + frameworksResolved from ERL / frameworkNames.
 */
export interface ScopingDetailControl {
  scf_id: string
  scf_domain: string
  control_name: string
  control_description: string
  control_question?: string
  validation_cadence?: string
  nist_csf_function?: NistCsfFunction
  control_weighting?: number
  scrm_focus?: SCRMFocus
  risk_threat_mapping?: RiskThreatMapping
  cmm_maturity?: CMMaturityGuidance
  business_size_guidance?: BusinessSizeGuidanceType
  /** Resolved ERL artifacts (enriched by container). */
  artifactsResolved: ResolvedArtifact[]
  /** Resolved framework names → refs (internal prefixes already filtered). */
  frameworksResolved: Record<string, string[]>
  frameworksCount: number
  /** Raw catalog lifecycle fields forwarded to DeprecatedBadge. */
  [key: string]: unknown
}

/**
 * The scoped-control record for the currently displayed control.
 * These are the fields displayed in DETAILS tab + header status badge.
 * The container keeps the authoritative copy; this component reflects it.
 */
export interface ScopingEntry {
  id?: string
  scf_id: string
  selected: boolean
  implementation_status?: ImplementationStatus
  priority?: Priority
  maturity_level?: MaturityLevel
  selection_reason?: string
  target_date?: string
  completion_date?: string
  implementation_notes?: string
}

export interface ScopingDetailPageProps {
  /** Enriched catalog+scoped control record. */
  control: ScopingDetailControl
  /** Scoping record for this control. Null/undefined when not yet saved. */
  scopingEntry?: ScopingEntry | null
  /**
   * null          → total unknown (pager fully hidden)
   * { index: null, total } → item not in filtered set; "— of N"; both disabled
   * { index: number, total } → normal pager
   */
  position: { index: number | null; total: number } | null
  onPrev: () => void
  onNext: () => void
  onBack: () => void
  /**
   * Called when user clicks the scope toggle checkbox.
   * Container calls updateScopedControl + refetch.
   */
  onToggleScope: (scfId: string) => void
  /**
   * Called for every field change in the DETAILS / NOTES tabs.
   * NO debounce in this component — container owns the 300 ms debounce.
   */
  onFieldChange: (field: string, value: unknown) => void
  /** Called after OwningTeams reports a change so the container can refetch. */
  onReloadTeamAssignments: () => void
  /** Organization ID required by comment/assignment/audit subcomponents. */
  organizationId: string
  /** Full scoping file for evidence tracking lookups (artifact tab). */
  scopingData?: ScopedControlsFile
  /**
   * The accountable team's name from the team system, for read-only display.
   * Writes happen on the Assignments tab (OwningTeams) — never here.
   */
  accountableTeamLabel?: string | null
  /** Whether the current user can manage owning-team assignments. */
  canManageTeams?: boolean
}

type ScopingTab = 'details' | 'notes' | 'assignments' | 'history' | 'knowledge-base'

// ─── Constants ────────────────────────────────────────────────────────────────

/** Statuses that show the Target Date field. */
const TARGET_DATE_STATUSES: ImplementationStatus[] = [
  'not_started',
  'in_progress',
  'at_risk',
  'deferred',
]

/**
 * Internal SCF mapping prefixes to exclude from framework display.
 * Source of truth: ControlScoping.tsx INTERNAL_MAPPING_PREFIXES (keep in sync).
 * Defensive in-component filter guards against container pre-filtering regressions.
 */
const INTERNAL_MAPPING_PREFIXES = [
  'risk_',
  'threat_',
  'scf_core_',
  'control_threat_summary',
  'risk_threat_summary',
  'minimum_security_requirements_mcr_dsr',
  'identify_',
  'errata_',
]

function isInternalMapping(frameworkKey: string): boolean {
  return INTERNAL_MAPPING_PREFIXES.some((prefix) => frameworkKey.startsWith(prefix))
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

/** True when the keyboard event target should suppress page-level shortcuts. */
function isSuppressed(e: KeyboardEvent): boolean {
  const t = e.target
  if (!t || !(t instanceof Element)) return false
  const tag = (t as HTMLElement).tagName?.toLowerCase()
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true
  if ((t as HTMLElement).isContentEditable) return true
  return false
}

/** Format an ImplementationStatus slug for display. */
function formatStatus(status: string): string {
  return status
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function ScopingDetailPage({
  control,
  scopingEntry,
  position,
  onPrev,
  onNext,
  onBack,
  onToggleScope,
  onFieldChange,
  onReloadTeamAssignments,
  organizationId,
  scopingData,
  accountableTeamLabel = null,
  canManageTeams = false,
}: ScopingDetailPageProps): JSX.Element {
  const [activeTab, setActiveTab] = useState<ScopingTab>('details')
  const [frameworksCollapsed, setFrameworksCollapsed] = useState(true)
  // Local SOA counter value (reflects live typing; does NOT debounce)
  const [soaValue, setSoaValue] = useState(scopingEntry?.selection_reason ?? '')
  // Local implementation_notes mirror — prevents prop round-trip snap-back on 300 ms debounce
  const [notesValue, setNotesValue] = useState(scopingEntry?.implementation_notes ?? '')
  // Local maturity level override — makes roadmap highlight move immediately on select
  const [localMaturityLevel, setLocalMaturityLevel] = useState<string | undefined>(
    scopingEntry?.maturity_level,
  )

  // Reset tab + mirrors when control changes
  useEffect(() => {
    setActiveTab('details')
    setSoaValue(scopingEntry?.selection_reason ?? '')
    setNotesValue(scopingEntry?.implementation_notes ?? '')
    setLocalMaturityLevel(scopingEntry?.maturity_level)
    setFrameworksCollapsed(true)
  }, [control.scf_id]) // eslint-disable-line react-hooks/exhaustive-deps

  // Sync soaValue when scopingEntry updates from outside (e.g. container refresh)
  useEffect(() => {
    setSoaValue(scopingEntry?.selection_reason ?? '')
  }, [scopingEntry?.selection_reason])

  // Sync notesValue when scopingEntry updates from outside
  useEffect(() => {
    setNotesValue(scopingEntry?.implementation_notes ?? '')
  }, [scopingEntry?.implementation_notes])

  // Sync localMaturityLevel when prop catches up from container
  useEffect(() => {
    setLocalMaturityLevel(scopingEntry?.maturity_level)
  }, [scopingEntry?.maturity_level])

  // ── Keyboard shortcuts ──────────────────────────────────────────────────────

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent): void {
      if (isSuppressed(e)) return
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

  // ── Derived pager state ─────────────────────────────────────────────────────

  const isFirst = position === null || position.index === null || position.index === 0
  const isLast =
    position === null ||
    position.index === null ||
    position.index === position.total - 1

  const positionText =
    position === null
      ? null
      : position.index === null
        ? `— of ${position.total}`
        : `${position.index + 1} of ${position.total}`

  // ── Derived artifacts ───────────────────────────────────────────────────────

  const totalArtifacts = control.artifactsResolved.length
  const totalFrameworks = control.frameworksCount

  const trackedArtifactCount = useMemo(() => {
    if (!scopingData) return 0
    return control.artifactsResolved.filter((a) => {
      const tracking = getEvidenceTracking(scopingData, a.id)
      return tracking?.is_tracked
    }).length
  }, [control.artifactsResolved, scopingData])

  const groupedArtifacts = useMemo(() => {
    const groups: Record<string, ResolvedArtifact[]> = {}
    for (const a of control.artifactsResolved) {
      if (!groups[a.domain]) groups[a.domain] = []
      groups[a.domain].push(a)
    }
    return groups
  }, [control.artifactsResolved])

  // ── Form derived values ─────────────────────────────────────────────────────

  const isInScope = scopingEntry?.selected === true
  const currentStatus = scopingEntry?.implementation_status ?? 'not_started'
  const showTargetDate = TARGET_DATE_STATUSES.includes(currentStatus as ImplementationStatus)
  const controlDbId = scopingEntry?.id

  // Defensive filter: strip any internal-prefix keys the container may not have removed.
  const filteredFrameworksResolved = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(control.frameworksResolved).filter(([fw]) => !isInternalMapping(fw)),
      ),
    [control.frameworksResolved],
  )

  // ── Tabs ────────────────────────────────────────────────────────────────────

  const tabs = [
    { id: 'details', label: 'DETAILS' },
    { id: 'notes', label: 'NOTES & HISTORY' },
    { id: 'assignments', label: 'ASSIGNMENTS' },
    { id: 'history', label: 'AUDIT ARTIFACTS' },
    { id: 'knowledge-base', label: 'KNOWLEDGE BASE' },
  ]

  // ─────────────────────────────────────────────────────────────────────────────
  // Render
  // ─────────────────────────────────────────────────────────────────────────────

  return (
    <div className="scoping-detail-page">

      {/* ── Breadcrumb / pager bar ─────────────────────────────────────────── */}
      <div className="scoping-detail-breadcrumb">
        <button
          className="scoping-detail-back-btn"
          onClick={onBack}
          aria-label="Back to Scoping"
        >
          <svg
            className="scoping-detail-back-icon"
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
          Scoping
        </button>
        <span className="scoping-detail-breadcrumb-sep">/</span>
        <span className="scoping-detail-breadcrumb-id">{control.scf_id}</span>

        {position !== null && (
          <div className="scoping-detail-pager">
            {positionText && (
              <span className="scoping-detail-position">{positionText}</span>
            )}
            <div className="scoping-detail-pager-buttons">
              <button
                className="scoping-detail-pager-btn"
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
                className="scoping-detail-pager-btn"
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
        )}
      </div>

      {/* ── Scrollable body ────────────────────────────────────────────────── */}
      <div className="scoping-detail-body">

        {/* ── Header block ─────────────────────────────────────────────────── */}
        <div className="scoping-detail-header surface-bedrock" data-source="SCF Reference">
          <span className="scf-source-tag">SCF Catalog</span>
          <div className="scoping-detail-header-badges">
            <span className="scf-id-pill">{control.scf_id}</span>
            <DeprecatedBadge {...getCatalogLifecycle(control)} />
            {scopingEntry?.implementation_status && (
              <span className={`status-badge-compact status-${scopingEntry.implementation_status}`}>
                {formatStatus(scopingEntry.implementation_status)}
              </span>
            )}
            <div className="cadence-row">
              <span className="cadence-label">Domain:</span>
              <span className="cadence-badge">{control.scf_domain}</span>
              {control.validation_cadence && (
                <>
                  <span className="cadence-label">Validation Cadence:</span>
                  <span className="cadence-badge">{control.validation_cadence}</span>
                </>
              )}
            </div>
            <MaturityRoadmap
              variant="duo"
              maturity={control.cmm_maturity}
              level={(localMaturityLevel as MaturityLevel | undefined) ?? null}
            />
          </div>

          <h1 className="control-title">{control.control_name}</h1>

          {/* 3-column: description left, widgets center, SCRM right */}
          <div className="detail-header-split">
            <div className="detail-header-left">
              <p className="control-description">{control.control_description}</p>
              {control.control_question && (
                <div className="assessment-question-block">
                  <div className="assessment-question-label">Assessment Question</div>
                  <blockquote className="assessment-question-text">
                    &ldquo;{control.control_question}&rdquo;
                  </blockquote>
                </div>
              )}
            </div>
            <div className="detail-header-right">
              <div className="detail-widget-group">
                <div className="detail-widget-group-label">Classification</div>
                <div className="detail-widget-group-items">
                  {control.nist_csf_function && (
                    <div className={`detail-widget theme-${control.nist_csf_function.toLowerCase()}`}>
                      <span className="detail-widget-value">{control.nist_csf_function}</span>
                      <span className="detail-widget-label">CSF Function</span>
                    </div>
                  )}
                  {control.control_weighting != null && (
                    <div className="detail-widget widget-weight">
                      <span className="detail-widget-value">{control.control_weighting}</span>
                      <span className="detail-widget-label">Weight</span>
                    </div>
                  )}
                </div>
              </div>
              <div className="detail-widget-group">
                <div className="detail-widget-group-label">Coverage</div>
                <div className="detail-widget-group-items">
                  <div className="detail-widget widget-count">
                    <span className="detail-widget-value">{totalFrameworks}</span>
                    <span className="detail-widget-label">Frameworks</span>
                  </div>
                  <div className="detail-widget widget-count">
                    <span className="detail-widget-value">{totalArtifacts}</span>
                    <span className="detail-widget-label">Artifacts</span>
                  </div>
                </div>
              </div>
              <BusinessSizeGuidance guidance={control.business_size_guidance} />
            </div>
            <div className="detail-header-scrm">
              <SCRMFocusBadges focus={control.scrm_focus} />
            </div>
          </div>
        </div>

        {/* ── SCF-derived reference guidance ─────────────────────────────────── */}
        <div className="scoping-detail-content-compact surface-bedrock">
          <RiskThreatContext mapping={control.risk_threat_mapping} />
        </div>

        {/* ── Tab navigation ──────────────────────────────────────────────────── */}
        <TabRow
          tabs={tabs}
          activeId={activeTab}
          onSelect={(id) => setActiveTab(id as ScopingTab)}
          aria-label="Control implementation sections"
        />

        {/* ── Tab: DETAILS ────────────────────────────────────────────────────── */}
        {activeTab === 'details' && (
          <div className="detail-section-container surface-bench">
            <div className="container-header bench-header">
              <span className="container-title">Your Implementation Record</span>
            </div>
            <div className="container-content">

              {/* Scope toggle */}
              <div className="form-group">
                <label>
                  <input
                    type="checkbox"
                    checked={isInScope}
                    onChange={() => onToggleScope(control.scf_id)}
                  />
                  <strong> Include this control in scope</strong>
                </label>
              </div>

              {/* Implementation Status */}
              <div className="form-group">
                <label htmlFor="scoping-detail-status">Implementation Status</label>
                <select
                  id="scoping-detail-status"
                  value={scopingEntry?.implementation_status ?? 'not_started'}
                  onChange={(e) =>
                    onFieldChange('implementation_status', e.target.value as ImplementationStatus)
                  }
                  className="form-control"
                >
                  <option value="not_started">Not Started</option>
                  <option value="in_progress">In Progress</option>
                  <option value="implemented">Implemented</option>
                  <option value="ready_for_review">Ready for Review</option>
                  <option value="monitored">Monitored</option>
                  <option value="not_applicable">Not Applicable</option>
                  <option value="at_risk">At Risk</option>
                  <option value="deferred">Deferred</option>
                </select>
              </div>

              {/* Priority */}
              <div className="form-group">
                <label htmlFor="scoping-detail-priority">Priority</label>
                <select
                  id="scoping-detail-priority"
                  value={scopingEntry?.priority ?? 'medium'}
                  onChange={(e) => onFieldChange('priority', e.target.value as Priority)}
                  className="form-control"
                >
                  <option value="critical">Critical</option>
                  <option value="high">High</option>
                  <option value="medium">Medium</option>
                  <option value="low">Low</option>
                </select>
              </div>

              {/* Maturity Level */}
              <div className="form-group">
                <label htmlFor="scoping-detail-maturity">Control Maturity Level (SCF C|P-CMM)</label>
                <select
                  id="scoping-detail-maturity"
                  value={localMaturityLevel ?? ''}
                  onChange={(e) => {
                    const val = e.target.value as MaturityLevel
                    setLocalMaturityLevel(val)
                    onFieldChange('maturity_level', val)
                  }}
                  className="form-control maturity-select"
                >
                  <option value="" disabled>Select Maturity Level...</option>
                  <option value="L0">L0 - Initial</option>
                  <option value="L1">L1 - Repeatable</option>
                  <option value="L2">L2 - Defined</option>
                  <option value="L3">L3 - Managed</option>
                  <option value="L4">L4 - Measured</option>
                  <option value="L5">L5 - Optimized</option>
                </select>
              </div>

              {/* SOA / Selection Reason */}
              <div className="form-group">
                <label htmlFor="scoping-detail-soa">
                  {isInScope ? 'Applicability Statement' : 'Exclusion Rationale'}
                </label>
                <span className="form-hint-block">
                  {isInScope
                    ? 'This text appears in your Statement of Applicability (SOA)'
                    : 'Auditors will ask why this control was excluded'}
                </span>
                <textarea
                  id="scoping-detail-soa"
                  value={soaValue}
                  onChange={(e) => {
                    setSoaValue(e.target.value)
                    onFieldChange('selection_reason', e.target.value)
                  }}
                  placeholder={
                    isInScope
                      ? 'Why is this control in scope? Which frameworks require it?'
                      : 'Why is this control excluded from scope?'
                  }
                  className="form-control"
                  rows={3}
                />
                <span
                  className={`char-counter${soaValue.length > 120 ? ' warning' : ''}`}
                >
                  {soaValue.length}/120 chars
                  {soaValue.length > 120 && ' — SOA will truncate'}
                </span>
              </div>

              {/* Accountable team — read-only mirror of the team system.
                  The legacy free-text owner label is sunset: ownership is
                  exclusively the accountable team under Users → Teams. */}
              <div className="form-group">
                <label>Accountable Team</label>
                <span className="form-hint-block">
                  Recorded through the team system (Users &rarr; Teams). Change it
                  under &ldquo;Owning teams&rdquo; on the Assignments tab.
                </span>
                <div className="scoping-detail-owner-readonly" data-testid="accountable-team">
                  {accountableTeamLabel || 'No accountable team'}
                </div>
              </div>

              {/* Target Date — only for non-terminal statuses */}
              {showTargetDate && (
                <div className="form-group">
                  <label htmlFor="scoping-detail-target-date">Target Date</label>
                  <input
                    id="scoping-detail-target-date"
                    type="date"
                    value={scopingEntry?.target_date ?? ''}
                    onChange={(e) => onFieldChange('target_date', e.target.value)}
                    className="form-control"
                  />
                </div>
              )}

              {/* Completion Date — read-only */}
              {scopingEntry?.completion_date && (
                <div className="form-group">
                  <label>Completed</label>
                  <span className="form-control form-control-readonly">
                    {new Date(scopingEntry.completion_date + 'T00:00:00').toLocaleDateString(
                      'en-US',
                      { year: 'numeric', month: 'short', day: 'numeric' },
                    )}
                  </span>
                </div>
              )}

            </div>
          </div>
        )}

        {/* ── Tab: NOTES & HISTORY ────────────────────────────────────────────── */}
        {activeTab === 'notes' && (
          <>
            <div className="detail-section-container surface-bench">
              <div className="container-header bench-header">
                <span className="container-title">Your Implementation Notes</span>
              </div>
              <div className="container-content">
                <div className="form-group">
                  <label htmlFor="scoping-detail-notes">Implementation Notes</label>
                  <textarea
                    id="scoping-detail-notes"
                    value={notesValue}
                    onChange={(e) => {
                      setNotesValue(e.target.value)
                      onFieldChange('implementation_notes', e.target.value)
                    }}
                    placeholder="How is this control implemented? What tools or processes are used?"
                    className="form-control"
                    rows={6}
                  />
                </div>
              </div>
            </div>

            {controlDbId && organizationId ? (
              <div className="scoping-comments-section">
                <ModernCommentThread
                  commentableType="control"
                  commentableId={controlDbId}
                  organizationId={organizationId}
                />
              </div>
            ) : (
              <div className="scoping-save-hint">
                <p>Save this control to enable comments</p>
              </div>
            )}

            <div className="detail-section-container">
              <div className="container-header">
                <span className="container-icon">📋</span>
                <span className="container-title">Change History</span>
              </div>
              <div className="container-content">
                <AuditLogPanel
                  scfId={control.scf_id}
                  organizationId={organizationId}
                />
              </div>
            </div>
          </>
        )}

        {/* ── Tab: ASSIGNMENTS ────────────────────────────────────────────────── */}
        {activeTab === 'assignments' && (
          <div className="detail-section-container surface-bench">
            <div className="container-header bench-header">
              <span className="container-title">Your Assignments</span>
            </div>
            <div className="container-content">
              {controlDbId && organizationId ? (
                <>
                  <AssignmentPicker
                    organizationId={organizationId}
                    assignableType="control"
                    assignableId={controlDbId}
                    onAssignmentChange={() => {}}
                  />
                  <OwningTeams
                    organizationId={organizationId}
                    assignableType="control"
                    assignableId={controlDbId}
                    canManage={canManageTeams}
                    onChange={() => {
                      onReloadTeamAssignments()
                    }}
                  />
                </>
              ) : (
                <span className="form-hint">Save control to enable assignment</span>
              )}
            </div>
          </div>
        )}

        {/* ── Tab: AUDIT ARTIFACTS ────────────────────────────────────────────── */}
        {activeTab === 'history' && (
          <div className="detail-section-container">
            <div className="container-header">
              <span className="container-icon">📋</span>
              <span className="container-title">Audit Artifacts</span>
              <span className="container-count">{totalArtifacts}</span>
              {totalArtifacts > 0 && (
                <span className="container-tracking-badge">
                  {trackedArtifactCount}/{totalArtifacts} tracked (
                  {Math.round((trackedArtifactCount / totalArtifacts) * 100)}%)
                </span>
              )}
            </div>
            <div className="container-content">
              {totalArtifacts === 0 ? (
                <div className="muted">No artifacts listed</div>
              ) : (
                <div className="artifact-list-compact">
                  {Object.entries(groupedArtifacts).map(([domain, artifacts]) => (
                    <div key={domain} className="artifact-domain-group">
                      <div className="artifact-domain-title">{domain}</div>
                      <div className="artifact-items">
                        {artifacts.map((artifact) => {
                          const evidenceTracking = scopingData
                            ? getEvidenceTracking(scopingData, artifact.id)
                            : null
                          const isTracked = evidenceTracking?.is_tracked ?? false
                          return (
                            <div key={artifact.id} className="artifact-item-compact">
                              <span className="artifact-status-indicator-compact">
                                {isTracked ? '✅' : '⚪'}
                              </span>
                              <span className="artifact-id-badge">{artifact.id}</span>
                              <span className="artifact-title-text">{artifact.title}</span>
                              {isTracked && evidenceTracking?.collecting_system && (
                                <span className="artifact-system-tag">
                                  {evidenceTracking.collecting_system}
                                </span>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── Tab: KNOWLEDGE BASE ─────────────────────────────────────────────── */}
        {activeTab === 'knowledge-base' && (
          <CDMControlPanel
            organizationId={organizationId}
            scopedControlId={controlDbId}
            controlName={control.control_name}
            controlDescription={control.control_description}
          />
        )}

        {/* ── Framework Mappings — collapsible, collapsed by default ─────────── */}
        <div className={`detail-section-container${frameworksCollapsed ? ' collapsed' : ''}`}>
          <div
            className="container-header collapsible"
            onClick={() => setFrameworksCollapsed((prev) => !prev)}
          >
            <span className="container-icon">🔗</span>
            <span className="container-title">Framework Mappings</span>
            <span className="container-count">{Object.keys(filteredFrameworksResolved).length}</span>
            <span className="collapse-indicator">{frameworksCollapsed ? '▶' : '▼'}</span>
          </div>
          {!frameworksCollapsed && (
            <div className="container-content">
              {Object.keys(filteredFrameworksResolved).length === 0 ? (
                <div className="muted">No mappings listed</div>
              ) : (
                <div className="framework-list-compact">
                  {Object.entries(filteredFrameworksResolved).map(([fw, refs]) => (
                    <div key={fw} className="framework-item-compact">
                      <div className="framework-name-compact">{fw}</div>
                      <div className="framework-refs">
                        {refs.map((ref, i) => (
                          <span key={`${ref}-${i}`} className="ref-chip">
                            {ref}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

      </div>
    </div>
  )
}
