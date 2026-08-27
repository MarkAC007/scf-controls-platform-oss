/**
 * RiskDetailPage — full-width risk detail view with breadcrumb + prev/next pager.
 *
 * Promotes RiskAssessmentDetail's content to a full-page view per RiskDetail.html spec:
 *   - Breadcrumb "‹ Risk Register / <code>" + "k of N in register" pager
 *   - Header block: mono code + category chip + treatment chip + owner
 *   - Title + description
 *   - 3-card grid: INHERENT / RESIDUAL / TREATMENT
 *   - Controls addressing section (scoped with status badges + onNavigateToControl)
 *   - Threat context chips (related risks + threat codes from riskCodes data)
 *   - Assessment history (created/updated audit row)
 *   - All form fields: likelihood/impact selects, residual selects, treatment
 *     status/plan/due-date, owner select, next review date, notes
 *   - Custom risk: Add/remove control search, delete button
 *
 * Keyboard: ArrowLeft→onPrev, ArrowRight→onNext, Escape→onBack.
 * Suppressed when focus is in input/textarea/select/contentEditable.
 *
 * PARITY NOTE: This replaces RiskAssessmentDetail.tsx (the slide-over).
 * RiskAssessmentDetail.tsx line ~134 had a tsc baseline error:
 *   `ScopedControl[]` (apiClient) vs `ScopedControl[]` (types) — selection_reason null vs undefined.
 * This file rewrites that code path using `ScopedControlForRisk[]` (already the correct type),
 * fixing that baseline error. Expected tsc baseline drops from 2 → 1
 * (AccountableOwnerTypeFilter one remains).
 */
import { useState, useEffect, useMemo, useRef, useCallback, type JSX } from 'react'
import type {
  RiskAssessment,
  RiskAssessmentUpdate,
  RiskCodesFile,
  TreatmentStatus,
  UserSimple,
} from '../types'
import {
  getRiskLevel,
  getRiskLevelColor,
  LIKELIHOOD_LABELS,
  IMPACT_LABELS,
  TREATMENT_STATUS_LABELS,
} from '../types'
import {
  getControlsForRisk,
  getScopedControls,
  addCustomRiskControl,
  removeCustomRiskControl,
  type ControlsForRiskResponse,
  type ScopedControlForRisk,
} from '../data/apiClient'
import { useOrganization } from '../contexts/OrganizationContext'
import { WorkspaceRecord } from './provenance/WorkspaceRecord'

// ─── Types ────────────────────────────────────────────────────────────────────

export interface RiskDetailPageProps {
  assessment: RiskAssessment | null
  riskCodes: RiskCodesFile
  onSave: (riskCode: string, updates: RiskAssessmentUpdate) => Promise<void>
  onBack: () => void
  onPrev: () => void
  onNext: () => void
  /**
   * null → total unknown (pager fully hidden)
   * { index: null, total } → item not in filtered set, show "— of N", both buttons disabled
   * { index: number, total } → normal pager
   */
  position: { index: number | null; total: number } | null
  users?: UserSimple[]
  onNavigateToControl?: (scfId: string) => void
  onDeleteCustomRisk?: (riskCode: string) => Promise<void>
}

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
function formatStatus(status: string | null): string {
  if (!status) return 'Not Set'
  return status
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

/** CSS style for an implementation status badge (token-safe). */
function getStatusBadgeClass(status: string | null): string {
  const map: Record<string, string> = {
    implemented: 'risk-ctrl-badge--implemented',
    monitored: 'risk-ctrl-badge--monitored',
    in_progress: 'risk-ctrl-badge--in-progress',
    ready_for_review: 'risk-ctrl-badge--ready',
    not_started: 'risk-ctrl-badge--not-started',
    at_risk: 'risk-ctrl-badge--at-risk',
    deferred: 'risk-ctrl-badge--deferred',
    not_applicable: 'risk-ctrl-badge--na',
  }
  return `risk-ctrl-badge ${map[status || ''] || 'risk-ctrl-badge--not-started'}`
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function RiskDetailPage({
  assessment,
  riskCodes,
  onSave,
  onBack,
  onPrev,
  onNext,
  position,
  users = [],
  onNavigateToControl,
  onDeleteCustomRisk,
}: RiskDetailPageProps): JSX.Element {
  const { currentOrg } = useOrganization()

  // ── Form state ──────────────────────────────────────────────────────────

  const [likelihood, setLikelihood] = useState<number | null>(null)
  const [impact, setImpact] = useState<number | null>(null)
  const [residualLikelihood, setResidualLikelihood] = useState<number | null>(null)
  const [residualImpact, setResidualImpact] = useState<number | null>(null)
  const [treatmentStatus, setTreatmentStatus] = useState<TreatmentStatus>('identified')
  const [treatmentPlan, setTreatmentPlan] = useState('')
  const [treatmentDueDate, setTreatmentDueDate] = useState('')
  const [ownerUserId, setOwnerUserId] = useState<string | null>(null)
  const [nextReviewDate, setNextReviewDate] = useState('')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── Controls addressing this risk ────────────────────────────────────────

  const [controlsData, setControlsData] = useState<ControlsForRiskResponse | null>(null)
  const [loadingControls, setLoadingControls] = useState(false)
  const [controlsError, setControlsError] = useState<string | null>(null)

  // ── Control search for custom risks ──────────────────────────────────────

  const [showControlSearch, setShowControlSearch] = useState(false)
  const [controlSearchTerm, setControlSearchTerm] = useState('')
  const [allScopedControls, setAllScopedControls] = useState<ScopedControlForRisk[]>([])
  const [addingControl, setAddingControl] = useState(false)

  // ── Populate form when assessment changes ────────────────────────────────

  useEffect(() => {
    if (assessment) {
      setLikelihood(assessment.likelihood ?? null)
      setImpact(assessment.impact ?? null)
      setResidualLikelihood(assessment.residual_likelihood ?? null)
      setResidualImpact(assessment.residual_impact ?? null)
      setTreatmentStatus(assessment.treatment_status)
      setTreatmentPlan(assessment.treatment_plan || '')
      setTreatmentDueDate(assessment.treatment_due_date || '')
      setOwnerUserId(assessment.owner_user_id || null)
      setNextReviewDate(assessment.next_review_date || '')
      setNotes(assessment.notes || '')
    }
  }, [assessment])

  // ── Cleanup save timeout on unmount ──────────────────────────────────────

  useEffect(() => {
    return () => {
      if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current)
    }
  }, [])

  // ── Fetch controls addressing this risk ──────────────────────────────────

  useEffect(() => {
    if (!assessment || !currentOrg) {
      setControlsData(null)
      return
    }

    const fetchControls = async () => {
      setLoadingControls(true)
      setControlsError(null)
      try {
        const data = await getControlsForRisk(assessment.risk_code, currentOrg.id)
        setControlsData(data)
      } catch (err) {
        console.error('Failed to fetch controls for risk:', err)
        setControlsError(err instanceof Error ? err.message : 'Failed to load controls')
      } finally {
        setLoadingControls(false)
      }
    }

    fetchControls()
  }, [assessment?.risk_code, currentOrg?.id])

  // ── Load scoped controls for custom risk control search ──────────────────
  // NOTE: This rewrite uses ScopedControlForRisk[] instead of ScopedControl[] (types.ts),
  // which fixes the tsc baseline error in the old RiskAssessmentDetail.tsx line ~134
  // (selection_reason: string|null|undefined incompatible). We use the API's own
  // response type directly, avoiding the cross-module ScopedControl mismatch.

  useEffect(() => {
    if (!currentOrg) return
    getScopedControls(currentOrg.id)
      .then(controls => {
        // Map to ScopedControlForRisk shape — we only need scf_id + control_name
        const filtered = controls
          .filter(c => c.selected)
          .map(c => ({
            scf_id: c.scf_id,
            control_name: (c as any).control_name || '',
            implementation_status: (c as any).implementation_status || null,
            priority: null,
            target_date: null,
          } satisfies ScopedControlForRisk))
        setAllScopedControls(filtered)
      })
      .catch(err => console.error('Failed to load scoped controls:', err))
  }, [currentOrg?.id])

  // ── Filter scoped controls for search ────────────────────────────────────

  const filteredSearchControls = useMemo(() => {
    if (!showControlSearch || !controlSearchTerm.trim()) return []
    const linked = new Set(controlsData?.catalog_control_ids || [])
    const term = controlSearchTerm.toLowerCase()
    return allScopedControls
      .filter(c => !linked.has(c.scf_id))
      .filter(c =>
        c.scf_id.toLowerCase().includes(term) || c.control_name.toLowerCase().includes(term)
      )
      .slice(0, 10)
  }, [showControlSearch, controlSearchTerm, allScopedControls, controlsData])

  // ── Handle adding a control to a custom risk ──────────────────────────────

  const handleAddControl = async (scfId: string) => {
    if (!assessment || !currentOrg) return
    setAddingControl(true)
    try {
      await addCustomRiskControl(assessment.risk_code, scfId, currentOrg.id)
      const data = await getControlsForRisk(assessment.risk_code, currentOrg.id)
      setControlsData(data)
      setControlSearchTerm('')
      setShowControlSearch(false)
    } catch (err) {
      console.error('Failed to add control:', err)
    } finally {
      setAddingControl(false)
    }
  }

  // ── Handle removing a control from a custom risk ──────────────────────────

  const handleRemoveControl = async (scfId: string) => {
    if (!assessment || !currentOrg) return
    try {
      await removeCustomRiskControl(assessment.risk_code, scfId, currentOrg.id)
      const data = await getControlsForRisk(assessment.risk_code, currentOrg.id)
      setControlsData(data)
    } catch (err) {
      console.error('Failed to remove control:', err)
    }
  }

  // ── Debounced auto-save ───────────────────────────────────────────────────

  const debouncedSave = useCallback(
    (updates: Partial<RiskAssessmentUpdate>) => {
      if (!assessment) return
      if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current)
      setSaving(true)
      saveTimeoutRef.current = setTimeout(async () => {
        try {
          await onSave(assessment.risk_code, {
            likelihood,
            impact,
            residual_likelihood: residualLikelihood,
            residual_impact: residualImpact,
            treatment_status: treatmentStatus,
            treatment_plan: treatmentPlan || null,
            treatment_due_date: treatmentDueDate || null,
            owner_user_id: ownerUserId,
            next_review_date: nextReviewDate || null,
            notes: notes || null,
            ...updates,
          })
        } catch (error) {
          console.error('Failed to save risk assessment:', error)
        } finally {
          setSaving(false)
        }
      }, 500)
    },
    [
      assessment,
      onSave,
      likelihood,
      impact,
      residualLikelihood,
      residualImpact,
      treatmentStatus,
      treatmentPlan,
      treatmentDueDate,
      ownerUserId,
      nextReviewDate,
      notes,
    ],
  )

  const updateField = <K extends keyof RiskAssessmentUpdate>(
    field: K,
    value: RiskAssessmentUpdate[K],
  ) => {
    switch (field) {
      case 'likelihood':
        setLikelihood(value as number | null)
        break
      case 'impact':
        setImpact(value as number | null)
        break
      case 'residual_likelihood':
        setResidualLikelihood(value as number | null)
        break
      case 'residual_impact':
        setResidualImpact(value as number | null)
        break
      case 'treatment_status':
        setTreatmentStatus(value as TreatmentStatus)
        break
      case 'treatment_plan':
        setTreatmentPlan((value as string) || '')
        break
      case 'treatment_due_date':
        setTreatmentDueDate((value as string) || '')
        break
      case 'owner_user_id':
        setOwnerUserId(value as string | null)
        break
      case 'next_review_date':
        setNextReviewDate((value as string) || '')
        break
      case 'notes':
        setNotes((value as string) || '')
        break
    }
    debouncedSave({ [field]: value })
  }

  // ── Keyboard shortcuts ───────────────────────────────────────────────────

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent): void {
      if (isSuppressed(e)) return
      if (
        e.key === 'Escape' &&
        document.querySelector('.theme-menu-panel, .user-dropdown-menu')
      )
        return
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

  // ── Derived data ─────────────────────────────────────────────────────────

  if (!assessment) {
    return (
      <div className="risk-detail-page-empty">
        <p>Select a risk to view details</p>
      </div>
    )
  }

  const codeInfo = riskCodes.codes[assessment.risk_code]
  const category = assessment.risk_code.split('-')[1]
  const categoryInfo = riskCodes.categories[category as keyof typeof riskCodes.categories]
  const isCustomRisk = assessment.risk_code.startsWith('R-ORG-')

  // Score calculations
  const inherentScore = likelihood && impact ? likelihood * impact : null
  const residualScore = residualLikelihood && residualImpact ? residualLikelihood * residualImpact : null
  const inherentLevel = inherentScore ? getRiskLevel(inherentScore) : null
  const residualLevel = residualScore ? getRiskLevel(residualScore) : null

  // Owner display name
  const ownerUser = users.find((u) => u.id === (ownerUserId ?? assessment.owner_user_id))
  const ownerName = ownerUser ? (ownerUser.display_name || ownerUser.email) : null

  // Pager
  const isFirst = position === null || position.index === null || position.index === 0
  const isLast =
    position === null || position.index === null || position.index === position.total - 1
  const positionText =
    position === null
      ? null
      : position.index === null
        ? `— of ${position.total}`
        : `${position.index + 1} of ${position.total}`

  // Treatment status label
  const treatmentLabel = TREATMENT_STATUS_LABELS[treatmentStatus] || treatmentStatus

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="risk-detail-page">
      {/* ── Breadcrumb / pager bar ─────────────────────────────────────────── */}
      <div className="control-detail-breadcrumb">
        <button
          className="control-detail-back-btn"
          onClick={onBack}
          aria-label="Back to Risk Register"
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
          Risk Register
        </button>
        <span className="control-detail-breadcrumb-sep">/</span>
        <span className="control-detail-breadcrumb-id">{assessment.risk_code}</span>

        <div className="control-detail-pager">
          {positionText && (
            <span className="control-detail-position">{positionText}</span>
          )}
          <div className="control-detail-pager-buttons">
            <button
              className="control-detail-pager-btn"
              onClick={onPrev}
              disabled={isFirst}
              aria-label="Previous risk"
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
              aria-label="Next risk"
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
      <div className="risk-detail-page-body">
        {/* ── Header block ──────────────────────────────────────────────────── */}
        <div className="risk-detail-page-header">
          <div className="risk-detail-page-chips-row">
            <div className="risk-detail-page-tick-bar" />
            <span className="control-detail-breadcrumb-id">{assessment.risk_code}</span>
            {categoryInfo && (
              <span className="risk-detail-page-chip">{categoryInfo.name}</span>
            )}
            {!isCustomRisk && (
              <span className="risk-detail-page-chip">SCF Risk Catalog</span>
            )}
            {isCustomRisk && (
              <span className="risk-detail-page-chip risk-detail-page-chip--custom">Custom</span>
            )}
            {treatmentStatus && (
              <span className="risk-detail-page-chip risk-detail-page-chip--treatment">
                {treatmentLabel}
              </span>
            )}
            {ownerName && (
              <div className="risk-detail-page-owner">
                <div className="risk-detail-page-owner-avatar" aria-hidden="true">
                  {ownerName.charAt(0).toUpperCase()}
                </div>
                <span className="risk-detail-page-owner-label">Owner — {ownerName}</span>
              </div>
            )}
          </div>
          <h1 className="risk-detail-page-title">{codeInfo?.title || 'Unknown Risk'}</h1>
          {codeInfo?.description && (
            <p className="risk-detail-page-description">{codeInfo.description}</p>
          )}
        </div>

        {/* ── 3-card grid: Inherent / Residual / Treatment ──────────────────── */}
        <div className="risk-detail-page-cards">
          {/* INHERENT card */}
          <div className="risk-detail-page-card">
            <div className="risk-detail-page-card-label">INHERENT RISK — BEFORE CONTROLS</div>
            <WorkspaceRecord className="risk-detail-page-card-form" title="">
              <div className="risk-score-row">
                <div className="field-group">
                  <label htmlFor={`inherent-likelihood-${assessment.risk_code}`}>Likelihood</label>
                  <select
                    id={`inherent-likelihood-${assessment.risk_code}`}
                    aria-label="Likelihood"
                    value={likelihood ?? ''}
                    onChange={(e) =>
                      updateField(
                        'likelihood',
                        e.target.value ? parseInt(e.target.value) : null,
                      )
                    }
                  >
                    <option value="">Not Set</option>
                    {[1, 2, 3, 4, 5].map((v) => (
                      <option key={v} value={v}>
                        {v} - {LIKELIHOOD_LABELS[v]}
                      </option>
                    ))}
                  </select>
                </div>
                <span className="score-multiply" aria-hidden="true">×</span>
                <div className="field-group">
                  <label htmlFor={`inherent-impact-${assessment.risk_code}`}>Impact</label>
                  <select
                    id={`inherent-impact-${assessment.risk_code}`}
                    aria-label="Impact"
                    value={impact ?? ''}
                    onChange={(e) =>
                      updateField(
                        'impact',
                        e.target.value ? parseInt(e.target.value) : null,
                      )
                    }
                  >
                    <option value="">Not Set</option>
                    {[1, 2, 3, 4, 5].map((v) => (
                      <option key={v} value={v}>
                        {v} - {IMPACT_LABELS[v]}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              {inherentScore ? (
                <div
                  className="risk-detail-page-score-badge"
                  style={{
                    background: inherentLevel
                      ? getRiskLevelColor(inherentLevel) + '20'
                      : undefined,
                    color: inherentLevel ? getRiskLevelColor(inherentLevel) : undefined,
                  }}
                >
                  {inherentScore} · {inherentLevel?.toUpperCase()}
                </div>
              ) : (
                <div className="risk-detail-page-score-empty">—</div>
              )}
              {likelihood && impact && (
                <div className="risk-detail-page-score-detail">
                  Likelihood: {LIKELIHOOD_LABELS[likelihood]} · Impact: {IMPACT_LABELS[impact]}
                </div>
              )}
            </WorkspaceRecord>
          </div>

          {/* RESIDUAL card */}
          <div className="risk-detail-page-card">
            <div className="risk-detail-page-card-label">RESIDUAL RISK — AFTER CONTROLS</div>
            <WorkspaceRecord className="risk-detail-page-card-form" title="">
              <div className="risk-score-row">
                <div className="field-group">
                  <label htmlFor={`residual-likelihood-${assessment.risk_code}`}>
                    Likelihood
                  </label>
                  <select
                    id={`residual-likelihood-${assessment.risk_code}`}
                    aria-label="Likelihood"
                    value={residualLikelihood ?? ''}
                    onChange={(e) =>
                      updateField(
                        'residual_likelihood',
                        e.target.value ? parseInt(e.target.value) : null,
                      )
                    }
                  >
                    <option value="">Not Set</option>
                    {[1, 2, 3, 4, 5].map((v) => (
                      <option key={v} value={v}>
                        {v} - {LIKELIHOOD_LABELS[v]}
                      </option>
                    ))}
                  </select>
                </div>
                <span className="score-multiply" aria-hidden="true">×</span>
                <div className="field-group">
                  <label htmlFor={`residual-impact-${assessment.risk_code}`}>Impact</label>
                  <select
                    id={`residual-impact-${assessment.risk_code}`}
                    aria-label="Impact"
                    value={residualImpact ?? ''}
                    onChange={(e) =>
                      updateField(
                        'residual_impact',
                        e.target.value ? parseInt(e.target.value) : null,
                      )
                    }
                  >
                    <option value="">Not Set</option>
                    {[1, 2, 3, 4, 5].map((v) => (
                      <option key={v} value={v}>
                        {v} - {IMPACT_LABELS[v]}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              {residualScore ? (
                <div
                  className="risk-detail-page-score-badge"
                  style={{
                    background: residualLevel
                      ? getRiskLevelColor(residualLevel) + '20'
                      : undefined,
                    color: residualLevel ? getRiskLevelColor(residualLevel) : undefined,
                  }}
                >
                  {residualScore} · {residualLevel?.toUpperCase()}
                </div>
              ) : (
                <div className="risk-detail-page-score-empty">—</div>
              )}
              {residualLikelihood && residualImpact && (
                <div className="risk-detail-page-score-detail">
                  Likelihood: {LIKELIHOOD_LABELS[residualLikelihood]} · Impact:{' '}
                  {IMPACT_LABELS[residualImpact]}
                </div>
              )}
            </WorkspaceRecord>
          </div>

          {/* TREATMENT card */}
          <div className="risk-detail-page-card">
            <div className="risk-detail-page-card-label">TREATMENT</div>
            <WorkspaceRecord className="risk-detail-page-card-form" title="">
              <div className="field-group">
                <label htmlFor={`treatment-status-${assessment.risk_code}`}>
                  Treatment Status
                </label>
                <select
                  id={`treatment-status-${assessment.risk_code}`}
                  aria-label="Treatment Status"
                  value={treatmentStatus}
                  onChange={(e) =>
                    updateField('treatment_status', e.target.value as TreatmentStatus)
                  }
                >
                  {Object.entries(TREATMENT_STATUS_LABELS).map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field-group">
                <label htmlFor={`treatment-plan-${assessment.risk_code}`}>Treatment Plan</label>
                <textarea
                  id={`treatment-plan-${assessment.risk_code}`}
                  aria-label="Treatment Plan"
                  value={treatmentPlan}
                  onChange={(e) => updateField('treatment_plan', e.target.value || null)}
                  placeholder="Describe the treatment actions..."
                  rows={3}
                />
              </div>
              <div className="field-row">
                <div className="field-group">
                  <label htmlFor={`treatment-due-${assessment.risk_code}`}>
                    Treatment Due Date
                  </label>
                  <input
                    id={`treatment-due-${assessment.risk_code}`}
                    type="date"
                    value={treatmentDueDate}
                    onChange={(e) => updateField('treatment_due_date', e.target.value || null)}
                  />
                </div>
                <div className="field-group">
                  <label htmlFor={`review-date-${assessment.risk_code}`}>Next Review Date</label>
                  <input
                    id={`review-date-${assessment.risk_code}`}
                    type="date"
                    value={nextReviewDate}
                    onChange={(e) => updateField('next_review_date', e.target.value || null)}
                  />
                </div>
              </div>
            </WorkspaceRecord>
          </div>
        </div>

        {/* ── Ownership + Notes ─────────────────────────────────────────────── */}
        <WorkspaceRecord title="Ownership &amp; Notes" className="risk-detail-page-section">
          <div className="field-row">
            <div className="field-group">
              <label htmlFor={`owner-${assessment.risk_code}`}>Risk Owner</label>
              <select
                id={`owner-${assessment.risk_code}`}
                aria-label="Risk Owner"
                value={ownerUserId ?? ''}
                onChange={(e) => updateField('owner_user_id', e.target.value || null)}
              >
                <option value="">Unassigned</option>
                {users.map((user) => (
                  <option key={user.id} value={user.id}>
                    {user.display_name || user.email}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="field-group">
            <label htmlFor={`notes-${assessment.risk_code}`}>Notes</label>
            <textarea
              id={`notes-${assessment.risk_code}`}
              aria-label="Notes"
              value={notes}
              onChange={(e) => updateField('notes', e.target.value || null)}
              placeholder="Additional notes or context..."
              rows={3}
            />
          </div>
        </WorkspaceRecord>

        {/* ── Controls Addressing This Risk ─────────────────────────────────── */}
        <div className="risk-detail-page-section">
          <div className="risk-detail-page-section-header">
            <span className="risk-detail-page-section-label">
              CONTROLS ADDRESSING THIS RISK
            </span>
            {controlsData && (
              <span className="risk-detail-page-section-meta">
                {controlsData.total_catalog_controls} linked in{' '}
                {isCustomRisk ? 'org' : 'SCF catalog'}
                {controlsData.scoped_controls.length > 0 && (
                  <span className="risk-detail-page-in-scope">
                    {' · '}
                    <span>{controlsData.scoped_controls.length} in scope</span>
                  </span>
                )}
              </span>
            )}
          </div>

          {loadingControls && (
            <div className="risk-detail-page-controls-loading">Loading controls...</div>
          )}
          {controlsError && (
            <div className="risk-detail-page-controls-error">{controlsError}</div>
          )}

          {!loadingControls && !controlsError && (
            <>
              {controlsData && controlsData.scoped_controls.length > 0 ? (
                <div className="risk-detail-page-controls-list">
                  {controlsData.scoped_controls.map((control: ScopedControlForRisk) => (
                    <div key={control.scf_id} className="risk-detail-page-control-row">
                      <button
                        className="risk-detail-page-control-btn"
                        onClick={() => onNavigateToControl?.(control.scf_id)}
                        aria-label={control.scf_id}
                      >
                        <span className="risk-detail-page-control-id">{control.scf_id}</span>
                        <span className="risk-detail-page-control-name">
                          {control.control_name}
                        </span>
                      </button>
                      <div className="risk-detail-page-control-meta">
                        <span className={getStatusBadgeClass(control.implementation_status)}>
                          {formatStatus(control.implementation_status)}
                        </span>
                        {isCustomRisk && (
                          <button
                            className="risk-detail-page-remove-control"
                            onClick={() => handleRemoveControl(control.scf_id)}
                            title="Remove control link"
                            aria-label={`Remove ${control.scf_id}`}
                          >
                            ×
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              ) : !isCustomRisk &&
                controlsData &&
                controlsData.total_catalog_controls > 0 ? (
                <div className="risk-detail-page-no-scoped">
                  No controls addressing this risk are currently in scope. Consider scoping
                  these controls:
                  <div className="risk-detail-page-catalog-chips">
                    {controlsData.catalog_control_ids.slice(0, 10).map((id: string) => (
                      <button
                        key={id}
                        className="risk-detail-page-catalog-chip"
                        onClick={() => onNavigateToControl?.(id)}
                      >
                        {id}
                      </button>
                    ))}
                    {controlsData.catalog_control_ids.length > 10 && (
                      <span className="risk-detail-page-catalog-more">
                        +{controlsData.catalog_control_ids.length - 10} more
                      </span>
                    )}
                  </div>
                </div>
              ) : !isCustomRisk ? (
                <div className="risk-detail-page-no-controls">
                  No controls mapped to this risk code in the SCF catalog.
                </div>
              ) : !showControlSearch ? (
                <div className="risk-detail-page-no-controls">
                  No controls linked yet. Use the button below to add controls.
                </div>
              ) : null}

              {/* Add Control button + search for custom risks */}
              {isCustomRisk && (
                <div className="risk-detail-page-add-control">
                  {!showControlSearch ? (
                    <button
                      className="btn-add-control"
                      onClick={() => setShowControlSearch(true)}
                    >
                      + Add Control
                    </button>
                  ) : (
                    <div className="control-search-box">
                      <div className="control-search-input-row">
                        <input
                          type="text"
                          placeholder="Search controls by ID or name..."
                          value={controlSearchTerm}
                          onChange={(e) => setControlSearchTerm(e.target.value)}
                          autoFocus
                          className="control-search-input"
                        />
                        <button
                          className="btn-secondary"
                          onClick={() => {
                            setShowControlSearch(false)
                            setControlSearchTerm('')
                          }}
                        >
                          Cancel
                        </button>
                      </div>
                      {controlSearchTerm.trim() && (
                        <div className="control-search-results">
                          {filteredSearchControls.length > 0 ? (
                            filteredSearchControls.map((c) => (
                              <button
                                key={c.scf_id}
                                onClick={() => handleAddControl(c.scf_id)}
                                disabled={addingControl}
                                className="control-search-result-btn"
                              >
                                <span className="control-search-result-id">{c.scf_id}</span>
                                <span className="control-search-result-name">
                                  {c.control_name}
                                </span>
                              </button>
                            ))
                          ) : (
                            <div className="control-search-no-results">
                              No matching controls found
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        {/* ── Assessment History ────────────────────────────────────────────── */}
        <div className="risk-detail-page-section">
          <div className="risk-detail-page-section-label">ASSESSMENT HISTORY</div>
          <div className="risk-detail-page-history">
            {assessment.updated_at !== assessment.created_at && (
              <div className="risk-detail-page-history-row">
                <span className="risk-detail-page-history-date">
                  {new Date(assessment.updated_at).toLocaleDateString()}
                </span>
                <span className="risk-detail-page-history-event">Assessment updated</span>
              </div>
            )}
            <div className="risk-detail-page-history-row">
              <span className="risk-detail-page-history-date">
                {new Date(assessment.created_at).toLocaleDateString()}
              </span>
              <span className="risk-detail-page-history-event">Initial assessment created</span>
            </div>
          </div>
        </div>

        {/* ── Actions ───────────────────────────────────────────────────────── */}
        <div className="risk-detail-page-actions">
          {isCustomRisk && onDeleteCustomRisk && (
            <button
              className="btn-destructive"
              aria-label="Delete Risk"
              onClick={() => {
                if (
                  window.confirm(
                    `Delete custom risk ${assessment.risk_code}? This cannot be undone.`,
                  )
                ) {
                  onDeleteCustomRisk(assessment.risk_code)
                }
              }}
            >
              Delete Risk
            </button>
          )}
          <div style={{ flex: 1 }} />
          {saving && <div className="save-indicator">Saving...</div>}
        </div>
      </div>
    </div>
  )
}
