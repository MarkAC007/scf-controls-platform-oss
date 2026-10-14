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
import { useState, useEffect, useMemo, useRef, useCallback, Fragment, type JSX } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
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
  addCustomRiskControl,
  removeCustomRiskControl,
  type ControlsForRiskResponse,
  type ScopedControlForRisk,
} from '../data/apiClient'
import { useOrganization } from '../contexts/OrganizationContext'
import { WorkspaceRecord } from './provenance/WorkspaceRecord'
import RiskAcceptancePanel, {
  RiskAcceptanceBadge,
  approvalInForce,
  saveEffectOnApproval,
  withdrawApprovalMessage,
} from './RiskAcceptancePanel'
import CustomRiskLinkDocument, { LinkDocumentSelect } from './CustomRiskLinkDocument'
import { useConfirm } from '../contexts/ConfirmContext'
import { customRiskLinksKey, useCustomRiskLinks } from '../hooks/useCustomRiskLinks'
import { useIsOrgAdmin } from '../hooks/useIsOrgAdmin'
import { JOURNEY_QUERY_ROOT } from '../hooks/useJourney'
import { useDebounce } from '../hooks/useDebounce'
import { rankControlMatches, searchInScopeControls } from './riskControlSearch'
import { keyEventBelongsToModal } from '../hooks/useModalDismiss'

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
  /** The server's record after an acceptance decision (PIG R7). */
  onAssessmentChange?: (updated: RiskAssessment) => void
  /**
   * Re-read one risk: a control-link change can withdraw its approval
   * server-side. Resolves to the fresh record, or null when it could not be read.
   */
  onRefreshAssessment?: (riskCode: string) => Promise<RiskAssessment | null> | void
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

/** True when the keyboard event target should suppress pager shortcuts. */
function isSuppressed(e: KeyboardEvent): boolean {
  // A key pressed in an open dialog is the dialog's, not the pager's.
  if (keyEventBelongsToModal(e)) return true
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
  onAssessmentChange,
  onRefreshAssessment,
}: RiskDetailPageProps): JSX.Element {
  const { currentOrg } = useOrganization()
  const queryClient = useQueryClient()
  const confirm = useConfirm()
  const isAdmin = useIsOrgAdmin(currentOrg?.id)

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
  const [addingControl, setAddingControl] = useState(false)
  // PIG R7: the document a new link carries, and a link write the server refused.
  const [newLinkDocumentId, setNewLinkDocumentId] = useState('')
  const [linkError, setLinkError] = useState<string | null>(null)

  const riskCode = assessment?.risk_code ?? ''
  const isCustom = riskCode.startsWith('R-ORG-')
  const customLinks = useCustomRiskLinks(currentOrg?.id, riskCode, isCustom)
  const documentByControl = useMemo(
    () => new Map((customLinks.data ?? []).map((link) => [link.scf_id, link.document_id])),
    [customLinks.data],
  )

  // A document picked for one risk's link is never carried to another risk.
  useEffect(() => {
    setNewLinkDocumentId('')
    setLinkError(null)
  }, [riskCode])

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

  // ── Search the organisation's in-scope controls ─────────────────────────
  // The server's search returns each control's catalogue title, which the
  // plain scoped-controls list does not carry (#1156: results read as a bare
  // id). Results show only once they answer the term as typed now.

  const debouncedSearchTerm = useDebounce(controlSearchTerm.trim(), 250)
  const controlSearch = useQuery({
    queryKey: ['risk-control-search', currentOrg?.id, debouncedSearchTerm],
    queryFn: () => searchInScopeControls(currentOrg!.id, debouncedSearchTerm),
    enabled: showControlSearch && !!currentOrg && !!debouncedSearchTerm,
    staleTime: 60_000,
  })
  const searchSettled =
    debouncedSearchTerm === controlSearchTerm.trim() && !controlSearch.isFetching

  const filteredSearchControls = useMemo(() => {
    if (!showControlSearch || !debouncedSearchTerm) return []
    const linked = new Set(controlsData?.catalog_control_ids || [])
    return rankControlMatches(controlSearch.data ?? [], debouncedSearchTerm, linked)
  }, [showControlSearch, debouncedSearchTerm, controlSearch.data, controlsData])

  // ── Handle adding a control to a custom risk ──────────────────────────────

  // Any link change on an accepted exception withdraws its approval
  // server-side, so the change is confirmed first while one is in force.
  const linkConfirmMessage =
    assessment && approvalInForce(assessment) ? withdrawApprovalMessage(assessment.risk_code) : null

  // The risk itself is re-read along with its links, and the caches that
  // count approvals (the journey gate, the risk context) are refreshed.
  const afterLinkChange = async (done: string) => {
    if (!assessment || !currentOrg) return
    const code = assessment.risk_code
    const hadApproval = !!assessment.accepted_at
    void queryClient.invalidateQueries({ queryKey: customRiskLinksKey(currentOrg.id, code) })
    void queryClient.invalidateQueries({ queryKey: ['risk-assessments', currentOrg.id] })
    void queryClient.invalidateQueries({ queryKey: [JOURNEY_QUERY_ROOT, currentOrg.id] })
    const fresh = await onRefreshAssessment?.(code)
    if (hadApproval && fresh && !fresh.accepted_at) {
      toast.success(`${done}. Approval withdrawn: ${code}'s control links changed.`)
    } else {
      toast.success(done)
    }
  }

  const handleAddControl = async (scfId: string) => {
    if (!assessment || !currentOrg) return
    if (
      linkConfirmMessage &&
      !(await confirm({ title: 'Link a control', message: linkConfirmMessage, confirmLabel: 'Link control', destructive: true }))
    )
      return
    setAddingControl(true)
    setLinkError(null)
    try {
      await addCustomRiskControl(assessment.risk_code, scfId, currentOrg.id, newLinkDocumentId || null)
      const data = await getControlsForRisk(assessment.risk_code, currentOrg.id)
      setControlsData(data)
      setControlSearchTerm('')
      setNewLinkDocumentId('')
      setShowControlSearch(false)
      void afterLinkChange(`${scfId} linked`)
    } catch (err) {
      console.error('Failed to add control:', err)
      setLinkError(err instanceof Error ? err.message : 'Could not link the control')
    } finally {
      setAddingControl(false)
    }
  }

  // ── Handle removing a control from a custom risk ──────────────────────────

  const handleRemoveControl = async (scfId: string) => {
    if (!assessment || !currentOrg) return
    if (
      linkConfirmMessage &&
      !(await confirm({ title: 'Unlink a control', message: linkConfirmMessage, confirmLabel: 'Unlink control', destructive: true }))
    )
      return
    setLinkError(null)
    try {
      await removeCustomRiskControl(assessment.risk_code, scfId, currentOrg.id)
      const data = await getControlsForRisk(assessment.risk_code, currentOrg.id)
      setControlsData(data)
      void afterLinkChange(`${scfId} unlinked`)
    } catch (err) {
      console.error('Failed to remove control:', err)
      setLinkError(err instanceof Error ? err.message : 'Could not remove the control link')
    }
  }

  // ── Debounced auto-save ───────────────────────────────────────────────────

  // Every field changed since the last save. Two changes can land in one
  // render (both waiting on the same approval question), and each call below
  // closes over that render's state: without this the second save drops the
  // first change.
  const pendingUpdatesRef = useRef<Partial<RiskAssessmentUpdate>>({})

  const debouncedSave = useCallback(
    (updates: Partial<RiskAssessmentUpdate>) => {
      if (!assessment) return
      if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current)
      setSaving(true)
      pendingUpdatesRef.current = { ...pendingUpdatesRef.current, ...updates }
      saveTimeoutRef.current = setTimeout(async () => {
        const pending = pendingUpdatesRef.current
        pendingUpdatesRef.current = {}
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
            ...pending,
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

  // An approval in force is withdrawn by the server on a re-score or (by an
  // admin) a status change away from Accepted; an editor's status change is
  // refused. Either way the user is asked first, once per approval.
  const confirmedApprovalRef = useRef<string | null>(null)
  // The withdraw question while it is open. A second change to the same
  // approval made meanwhile waits on this answer instead of asking again.
  const askingApprovalRef = useRef<{ key: string; answer: Promise<boolean> } | null>(null)

  //
  // Null when there is nothing to ask, and the change applies at once: the
  // notes and plan fields are controlled inputs, and a state update deferred
  // past the keystroke's event makes the caret jump. Otherwise the answer.
  const confirmApprovalEffect = (field: keyof RiskAssessmentUpdate, value: unknown): Promise<boolean> | null => {
    if (!assessment) return null
    const effect = saveEffectOnApproval(assessment, field, value, isAdmin)
    if (!effect) return null
    const riskCode = assessment.risk_code
    const approvalKey = `${riskCode}|${assessment.accepted_at}`
    if (effect === 'withdraw') {
      if (confirmedApprovalRef.current === approvalKey) return null
      if (askingApprovalRef.current?.key === approvalKey) return askingApprovalRef.current.answer
      const answer = (async () => {
        const ok = await confirm({
          title: 'Withdraw the approval',
          message: withdrawApprovalMessage(riskCode),
          confirmLabel: 'Continue',
          destructive: true,
        })
        if (ok) confirmedApprovalRef.current = approvalKey
        return ok
      })()
      askingApprovalRef.current = { key: approvalKey, answer }
      const done = () => {
        if (askingApprovalRef.current?.answer === answer) askingApprovalRef.current = null
      }
      answer.then(done, done)
      return answer
    }
    return confirm({
      title: 'Change an approved risk',
      message:
        `${riskCode}'s acceptance is approved, and only an admin can withdraw it: ` +
        'the status will stay Accepted. Continue?',
      confirmLabel: 'Continue',
    })
  }

  const updateField = <K extends keyof RiskAssessmentUpdate>(
    field: K,
    value: RiskAssessmentUpdate[K],
  ) => {
    const asking = confirmApprovalEffect(field, value)
    if (asking === null) {
      applyField(field, value)
      return
    }
    void asking.then((ok) => {
      if (ok) applyField(field, value)
    })
  }

  const applyField = <K extends keyof RiskAssessmentUpdate>(
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
  const isCustomRisk = isCustom
  // While the links (and their documents) load or after they fail, an empty
  // map would read as "no document": offer no edit until they are known.
  const linksLoading = customLinks.isLoading
  const linksReady = !customLinks.isLoading && !customLinks.isError

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
            <RiskAcceptanceBadge assessment={assessment} />
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

        {/* ── Risk acceptance (PIG R7) ──────────────────────────────────────── */}
        {currentOrg && (
          <RiskAcceptancePanel
            organizationId={currentOrg.id}
            assessment={assessment}
            users={users}
            autosavePending={saving}
            onChange={(updated) => onAssessmentChange?.(updated)}
            onRefresh={(code) => void onRefreshAssessment?.(code)}
          />
        )}

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
          {isCustomRisk && assessment.accepted_at && (
            <p className="gov-note">
              This risk's acceptance was approved for these controls and documents. Changing them withdraws
              the approval.
            </p>
          )}
          {linkError && <div role="alert" className="gov-error">{linkError}</div>}
          {isCustomRisk && customLinks.isError && (
            <div role="alert" className="gov-error">
              Could not load the control links' supporting documents:{' '}
              {customLinks.error instanceof Error ? customLinks.error.message : 'unknown error'}
            </div>
          )}

          {!loadingControls && !controlsError && (
            <>
              {controlsData && controlsData.scoped_controls.length > 0 ? (
                <div className="risk-detail-page-controls-list">
                  {controlsData.scoped_controls.map((control: ScopedControlForRisk) => (
                    <Fragment key={control.scf_id}>
                    <div className="risk-detail-page-control-row">
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
                    {isCustomRisk && currentOrg && (linksLoading ? (
                      <div className="risk-link-document">Loading supporting document…</div>
                    ) : linksReady ? (
                      <CustomRiskLinkDocument
                        organizationId={currentOrg.id}
                        riskCode={assessment.risk_code}
                        scfId={control.scf_id}
                        documentId={documentByControl.get(control.scf_id) ?? null}
                        onChanged={() => void afterLinkChange('Supporting document saved')}
                        confirmMessage={linkConfirmMessage}
                      />
                    ) : null)}
                    </Fragment>
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
              {isCustomRisk && linksReady && (
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
                            setNewLinkDocumentId('')
                          }}
                        >
                          Cancel
                        </button>
                      </div>
                      {controlSearchTerm.trim() && (
                        <div className="control-search-results">
                          {!searchSettled ? (
                            <div className="control-search-no-results">Searching…</div>
                          ) : controlSearch.isError ? (
                            <div className="control-search-no-results" role="alert">
                              Could not search the controls
                            </div>
                          ) : filteredSearchControls.length > 0 ? (
                            filteredSearchControls.map((c) => (
                              <button
                                key={c.scf_id}
                                onClick={() => handleAddControl(c.scf_id)}
                                disabled={addingControl}
                                className="control-search-result-btn"
                              >
                                <span className="control-search-result-id">{c.scf_id}</span>
                                <span className="control-search-result-name">
                                  {c.control_name ? ` — ${c.control_name}` : ''}
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
                      {currentOrg && (
                        <LinkDocumentSelect
                          organizationId={currentOrg.id}
                          value={newLinkDocumentId}
                          onChange={setNewLinkDocumentId}
                          disabled={addingControl}
                        />
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
                const riskCode = assessment.risk_code
                void confirm({
                  title: 'Delete custom risk',
                  message: `Delete custom risk ${riskCode}? This cannot be undone.`,
                  confirmLabel: 'Delete risk',
                  destructive: true,
                }).then((ok) => {
                  if (ok) onDeleteCustomRisk(riskCode)
                })
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
