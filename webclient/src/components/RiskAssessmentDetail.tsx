/**
 * RiskAssessmentDetail Component - Detail view and edit form for a single risk
 *
 * Shows full risk details including treatment workflow, ownership,
 * and control linkage from SCF catalog.
 */
import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import type {
  RiskAssessment,
  RiskAssessmentUpdate,
  RiskCodesFile,
  TreatmentStatus,
  UserSimple,
  ScopedControl,
} from '../types'
import {
  getRiskLevel,
  getRiskLevelColor,
  LIKELIHOOD_LABELS,
  IMPACT_LABELS,
  TREATMENT_STATUS_LABELS
} from '../types'
import {
  getControlsForRisk,
  getScopedControls,
  addCustomRiskControl,
  removeCustomRiskControl,
  type ControlsForRiskResponse,
  type ScopedControlForRisk
} from '../data/apiClient'
import { useOrganization } from '../contexts/OrganizationContext'
import { WorkspaceRecord } from './provenance/WorkspaceRecord'

interface RiskAssessmentDetailProps {
  assessment: RiskAssessment | null
  riskCodes: RiskCodesFile
  onSave: (riskCode: string, updates: RiskAssessmentUpdate) => Promise<void>
  onClose: () => void
  users?: UserSimple[]
  onNavigateToControl?: (scfId: string) => void
  onDeleteCustomRisk?: (riskCode: string) => Promise<void>
}

export default function RiskAssessmentDetail({
  assessment,
  riskCodes,
  onSave,
  onClose,
  users = [],
  onNavigateToControl,
  onDeleteCustomRisk
}: RiskAssessmentDetailProps) {
  // Organisation context
  const { currentOrg } = useOrganization()

  // Form state
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

  // Controls addressing this risk
  const [controlsData, setControlsData] = useState<ControlsForRiskResponse | null>(null)
  const [loadingControls, setLoadingControls] = useState(false)
  const [controlsError, setControlsError] = useState<string | null>(null)

  // Control search for custom risks
  const [showControlSearch, setShowControlSearch] = useState(false)
  const [controlSearchTerm, setControlSearchTerm] = useState('')
  const [allScopedControls, setAllScopedControls] = useState<ScopedControl[]>([])
  const [addingControl, setAddingControl] = useState(false)

  // Populate form when assessment changes
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

  // Cleanup save timeout on unmount
  useEffect(() => {
    return () => {
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current)
      }
    }
  }, [])

  // Fetch controls addressing this risk (SCF catalog for SCF risks, custom mappings for custom risks)
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

  // Load scoped controls for the custom risk control search
  useEffect(() => {
    if (!currentOrg) return
    getScopedControls(currentOrg.id)
      .then(controls => setAllScopedControls(controls.filter(c => c.selected)))
      .catch(err => console.error('Failed to load scoped controls:', err))
  }, [currentOrg?.id])

  // Filter scoped controls for search (exclude already-linked ones)
  const filteredSearchControls = useMemo(() => {
    if (!showControlSearch || !controlSearchTerm.trim()) return []
    const linked = new Set(controlsData?.catalog_control_ids || [])
    const term = controlSearchTerm.toLowerCase()
    return allScopedControls
      .filter(c => !linked.has(c.scf_id))
      .filter(c => {
        const name = (c as any).control_name || ''
        return c.scf_id.toLowerCase().includes(term) || name.toLowerCase().includes(term)
      })
      .slice(0, 10)
  }, [showControlSearch, controlSearchTerm, allScopedControls, controlsData])

  // Handle adding a control to a custom risk
  const handleAddControl = async (scfId: string) => {
    if (!assessment || !currentOrg) return
    setAddingControl(true)
    try {
      await addCustomRiskControl(assessment.risk_code, scfId, currentOrg.id)
      // Re-fetch controls
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

  // Handle removing a control from a custom risk
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

  // Debounced auto-save — matches ControlScoping and EvidenceReview pattern
  const debouncedSave = useCallback((updates: Partial<RiskAssessmentUpdate>) => {
    if (!assessment) return

    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current)
    }

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
          ...updates
        })
      } catch (error) {
        console.error('Failed to save risk assessment:', error)
      } finally {
        setSaving(false)
      }
    }, 500)
  }, [assessment, onSave, likelihood, impact, residualLikelihood, residualImpact, treatmentStatus, treatmentPlan, treatmentDueDate, ownerUserId, nextReviewDate, notes])

  // Field update helper — sets local state and triggers debounced save
  const updateField = <K extends keyof RiskAssessmentUpdate>(field: K, value: RiskAssessmentUpdate[K]) => {
    switch (field) {
      case 'likelihood': setLikelihood(value as number | null); break
      case 'impact': setImpact(value as number | null); break
      case 'residual_likelihood': setResidualLikelihood(value as number | null); break
      case 'residual_impact': setResidualImpact(value as number | null); break
      case 'treatment_status': setTreatmentStatus(value as TreatmentStatus); break
      case 'treatment_plan': setTreatmentPlan((value as string) || ''); break
      case 'treatment_due_date': setTreatmentDueDate((value as string) || ''); break
      case 'owner_user_id': setOwnerUserId(value as string | null); break
      case 'next_review_date': setNextReviewDate((value as string) || ''); break
      case 'notes': setNotes((value as string) || ''); break
    }
    debouncedSave({ [field]: value })
  }

  if (!assessment) {
    return (
      <div className="risk-detail-empty">
        <p>Select a risk to view details</p>
      </div>
    )
  }

  const codeInfo = riskCodes.codes[assessment.risk_code]
  const category = assessment.risk_code.split('-')[1]
  const categoryInfo = riskCodes.categories[category as keyof typeof riskCodes.categories]
  const isCustomRisk = assessment.risk_code.startsWith('R-ORG-')

  // Calculate scores
  const inherentScore = likelihood && impact ? likelihood * impact : null
  const residualScore = residualLikelihood && residualImpact ? residualLikelihood * residualImpact : null
  const inherentLevel = inherentScore ? getRiskLevel(inherentScore) : null
  const residualLevel = residualScore ? getRiskLevel(residualScore) : null

  // Helper to get implementation status styling
  const getStatusBadgeStyle = (status: string | null) => {
    const statusColors: Record<string, { bg: string; text: string }> = {
      monitored: { bg: '#dcfce7', text: '#166534' },
      implemented: { bg: '#dbeafe', text: '#1e40af' },
      ready_for_review: { bg: '#e0e7ff', text: '#3730a3' },
      in_progress: { bg: '#fef3c7', text: '#92400e' },
      not_started: { bg: '#f3f4f6', text: '#6b7280' },
      not_applicable: { bg: '#f5f5f5', text: '#9ca3af' },
      at_risk: { bg: '#fee2e2', text: '#991b1b' },
      deferred: { bg: '#fef3c7', text: '#92400e' },
    }
    const colors = statusColors[status || ''] || { bg: '#f3f4f6', text: '#6b7280' }
    return {
      backgroundColor: colors.bg,
      color: colors.text,
      padding: '2px 8px',
      borderRadius: '4px',
      fontSize: '0.75rem',
      fontWeight: 500,
    }
  }

  // Format status for display
  const formatStatus = (status: string | null) => {
    if (!status) return 'Not Set'
    return status
      .split('_')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ')
  }

  return (
    <div className="risk-detail-panel">
      {/* Header — SCF risk catalog content renders as bedrock; custom risks are user-authored */}
      <div
        className={`risk-detail-header${isCustomRisk ? '' : ' surface-bedrock'}`}
        {...(isCustomRisk ? {} : { 'data-source': 'SCF Risk Catalog' })}
      >
        {!isCustomRisk && <span className="scf-source-tag">SCF Catalog</span>}
        <div className="risk-detail-title-row">
          <span
            className="risk-category-badge"
            style={{ backgroundColor: categoryInfo?.color }}
          >
            {assessment.risk_code}
          </span>
          {isCustomRisk && (
            <span className="custom-risk-badge">Custom</span>
          )}
          <h3>{codeInfo?.title || 'Unknown Risk'}</h3>
          <button className="close-btn" onClick={onClose}>x</button>
        </div>
        <p className="risk-description">{codeInfo?.description}</p>
      </div>

      {/* Form sections */}
      <div className="risk-detail-content">
        {/* Your assessment inputs — the organization's editable record (workbench) */}
        <WorkspaceRecord title="Your Risk Assessment" className="risk-section">
        {/* Inherent Risk */}
        <section className="risk-section">
          <h4>Inherent Risk (Before Controls)</h4>
          <div className="risk-score-row">
            <div className="field-group">
              <label>Likelihood</label>
              <select
                value={likelihood ?? ''}
                onChange={e => updateField('likelihood', e.target.value ? parseInt(e.target.value) : null)}
              >
                <option value="">Not Set</option>
                {[1, 2, 3, 4, 5].map(v => (
                  <option key={v} value={v}>{v} - {LIKELIHOOD_LABELS[v]}</option>
                ))}
              </select>
            </div>
            <span className="score-multiply">×</span>
            <div className="field-group">
              <label>Impact</label>
              <select
                value={impact ?? ''}
                onChange={e => updateField('impact', e.target.value ? parseInt(e.target.value) : null)}
              >
                <option value="">Not Set</option>
                {[1, 2, 3, 4, 5].map(v => (
                  <option key={v} value={v}>{v} - {IMPACT_LABELS[v]}</option>
                ))}
              </select>
            </div>
            <span className="score-equals">=</span>
            <div className="field-group score-result">
              <label>Score</label>
              {inherentScore ? (
                <span
                  className="score-badge large"
                  style={{
                    backgroundColor: inherentLevel ? getRiskLevelColor(inherentLevel) + '20' : undefined,
                    color: inherentLevel ? getRiskLevelColor(inherentLevel) : undefined,
                    borderColor: inherentLevel ? getRiskLevelColor(inherentLevel) : undefined
                  }}
                >
                  {inherentScore} ({inherentLevel?.toUpperCase()})
                </span>
              ) : (
                <span className="score-empty">-</span>
              )}
            </div>
          </div>
        </section>

        {/* Residual Risk */}
        <section className="risk-section">
          <h4>Residual Risk (After Controls)</h4>
          <div className="risk-score-row">
            <div className="field-group">
              <label>Likelihood</label>
              <select
                value={residualLikelihood ?? ''}
                onChange={e => updateField('residual_likelihood', e.target.value ? parseInt(e.target.value) : null)}
              >
                <option value="">Not Set</option>
                {[1, 2, 3, 4, 5].map(v => (
                  <option key={v} value={v}>{v} - {LIKELIHOOD_LABELS[v]}</option>
                ))}
              </select>
            </div>
            <span className="score-multiply">×</span>
            <div className="field-group">
              <label>Impact</label>
              <select
                value={residualImpact ?? ''}
                onChange={e => updateField('residual_impact', e.target.value ? parseInt(e.target.value) : null)}
              >
                <option value="">Not Set</option>
                {[1, 2, 3, 4, 5].map(v => (
                  <option key={v} value={v}>{v} - {IMPACT_LABELS[v]}</option>
                ))}
              </select>
            </div>
            <span className="score-equals">=</span>
            <div className="field-group score-result">
              <label>Score</label>
              {residualScore ? (
                <span
                  className="score-badge large"
                  style={{
                    backgroundColor: residualLevel ? getRiskLevelColor(residualLevel) + '20' : undefined,
                    color: residualLevel ? getRiskLevelColor(residualLevel) : undefined,
                    borderColor: residualLevel ? getRiskLevelColor(residualLevel) : undefined
                  }}
                >
                  {residualScore} ({residualLevel?.toUpperCase()})
                </span>
              ) : (
                <span className="score-empty">-</span>
              )}
            </div>
          </div>
        </section>

        {/* Treatment */}
        <section className="risk-section">
          <h4>Treatment Workflow</h4>
          <div className="field-group">
            <label>Treatment Status</label>
            <select
              value={treatmentStatus}
              onChange={e => updateField('treatment_status', e.target.value as TreatmentStatus)}
            >
              {Object.entries(TREATMENT_STATUS_LABELS).map(([key, label]) => (
                <option key={key} value={key}>{label}</option>
              ))}
            </select>
          </div>

          <div className="field-group">
            <label>Treatment Plan</label>
            <textarea
              value={treatmentPlan}
              onChange={e => updateField('treatment_plan', e.target.value || null)}
              placeholder="Describe the treatment actions..."
              rows={3}
            />
          </div>

          <div className="field-row">
            <div className="field-group">
              <label>Treatment Due Date</label>
              <input
                type="date"
                value={treatmentDueDate}
                onChange={e => updateField('treatment_due_date', e.target.value || null)}
              />
            </div>
            <div className="field-group">
              <label>Next Review Date</label>
              <input
                type="date"
                value={nextReviewDate}
                onChange={e => updateField('next_review_date', e.target.value || null)}
              />
            </div>
          </div>
        </section>

        {/* Ownership */}
        <section className="risk-section">
          <h4>Ownership</h4>
          <div className="field-group">
            <label>Risk Owner</label>
            <select
              value={ownerUserId ?? ''}
              onChange={e => updateField('owner_user_id', e.target.value || null)}
            >
              <option value="">Unassigned</option>
              {users.map(user => (
                <option key={user.id} value={user.id}>
                  {user.display_name || user.email}
                </option>
              ))}
            </select>
          </div>
        </section>

        {/* Notes */}
        <section className="risk-section">
          <h4>Notes</h4>
          <div className="field-group">
            <textarea
              value={notes}
              onChange={e => updateField('notes', e.target.value || null)}
              placeholder="Additional notes or context..."
              rows={3}
            />
          </div>
        </section>
        </WorkspaceRecord>

        {/* Controls Addressing This Risk */}
        <section className="risk-section">
          <h4>Controls Addressing This Risk</h4>
          <p className="section-description" style={{ fontSize: '0.875rem', color: 'var(--muted)', marginBottom: '0.75rem' }}>
            {isCustomRisk
              ? 'Controls manually linked to this custom risk'
              : 'Controls mapped to this risk code in the SCF catalog'}
          </p>

          {loadingControls && (
            <div className="controls-loading" style={{ padding: '1rem', color: 'var(--muted)' }}>
              Loading controls...
            </div>
          )}

          {controlsError && (
            <div className="controls-error" style={{ padding: '1rem', color: '#dc2626' }}>
              {controlsError}
            </div>
          )}

          {!loadingControls && !controlsError && (
            <>
              {controlsData && controlsData.total_catalog_controls > 0 && (
                <div className="controls-summary" style={{ marginBottom: '0.75rem', fontSize: '0.875rem' }}>
                  <span style={{ color: 'var(--muted)' }}>
                    {controlsData.total_catalog_controls} control{controlsData.total_catalog_controls !== 1 ? 's' : ''} linked
                  </span>
                  {controlsData.scoped_controls.length > 0 && (
                    <span style={{ color: '#059669', marginLeft: '0.5rem' }}>
                      ({controlsData.scoped_controls.length} in scope)
                    </span>
                  )}
                </div>
              )}

              {controlsData && controlsData.scoped_controls.length > 0 ? (
                <div className="controls-list" style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                  {controlsData.scoped_controls.map((control: ScopedControlForRisk) => (
                    <div
                      key={control.scf_id}
                      className="control-item"
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        padding: '0.75rem',
                        backgroundColor: 'var(--secondary)',
                        borderRadius: '6px',
                        border: '1px solid var(--border)',
                      }}
                    >
                      <button
                        onClick={() => onNavigateToControl?.(control.scf_id)}
                        style={{
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '0.25rem',
                          background: 'none',
                          border: 'none',
                          cursor: 'pointer',
                          textAlign: 'left',
                          padding: 0,
                          color: 'inherit',
                          flex: 1,
                        }}
                      >
                        <span style={{ fontWeight: 500, color: 'var(--text)' }}>
                          {control.scf_id}
                        </span>
                        <span style={{ fontSize: '0.875rem', color: 'var(--muted)' }}>
                          {control.control_name}
                        </span>
                      </button>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <span style={getStatusBadgeStyle(control.implementation_status)}>
                          {formatStatus(control.implementation_status)}
                        </span>
                        {isCustomRisk && (
                          <button
                            className="btn-remove-control"
                            onClick={() => handleRemoveControl(control.scf_id)}
                            title="Remove control link"
                          >
                            x
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              ) : !isCustomRisk && controlsData && controlsData.total_catalog_controls > 0 ? (
                <div className="no-scoped-controls" style={{ padding: '1rem', backgroundColor: 'var(--secondary)', borderRadius: '6px', border: '1px solid var(--border)', color: 'var(--muted)', fontSize: '0.875rem' }}>
                  No controls addressing this risk are currently in scope. Consider scoping these controls:
                  <div style={{ marginTop: '0.5rem', display: 'flex', flexWrap: 'wrap', gap: '0.25rem' }}>
                    {controlsData.catalog_control_ids.slice(0, 10).map((id: string) => (
                      <a
                        key={id}
                        href={`/scoping?search=${id}`}
                        style={{
                          padding: '2px 6px',
                          backgroundColor: 'var(--card)',
                          border: '1px solid var(--border)',
                          borderRadius: '4px',
                          fontSize: '0.75rem',
                          color: 'var(--text)',
                          textDecoration: 'none',
                        }}
                      >
                        {id}
                      </a>
                    ))}
                    {controlsData.catalog_control_ids.length > 10 && (
                      <span style={{ fontSize: '0.75rem', color: 'var(--muted)' }}>
                        +{controlsData.catalog_control_ids.length - 10} more
                      </span>
                    )}
                  </div>
                </div>
              ) : (!controlsData || controlsData.total_catalog_controls === 0) && !isCustomRisk ? (
                <div className="no-controls" style={{ padding: '1rem', color: 'var(--muted)', fontSize: '0.875rem' }}>
                  No controls mapped to this risk code in the SCF catalog.
                </div>
              ) : (!controlsData || controlsData.total_catalog_controls === 0) && isCustomRisk && !showControlSearch ? (
                <div className="no-controls" style={{ padding: '1rem', color: 'var(--muted)', fontSize: '0.875rem' }}>
                  No controls linked yet. Use the button below to add controls.
                </div>
              ) : null}

              {/* Add Control button + search for custom risks */}
              {isCustomRisk && (
                <div style={{ marginTop: '0.75rem' }}>
                  {!showControlSearch ? (
                    <button
                      className="btn-add-control"
                      onClick={() => setShowControlSearch(true)}
                    >
                      + Add Control
                    </button>
                  ) : (
                    <div className="control-search-box">
                      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
                        <input
                          type="text"
                          placeholder="Search controls by ID or name..."
                          value={controlSearchTerm}
                          onChange={e => setControlSearchTerm(e.target.value)}
                          autoFocus
                          style={{
                            flex: 1,
                            padding: '8px 12px',
                            border: '1px solid var(--border)',
                            borderRadius: 'var(--radius)',
                            backgroundColor: 'var(--bg)',
                            color: 'var(--text)',
                            fontSize: '0.875rem',
                          }}
                        />
                        <button
                          className="btn-secondary"
                          onClick={() => { setShowControlSearch(false); setControlSearchTerm('') }}
                          style={{ padding: '8px 12px', fontSize: '0.875rem' }}
                        >
                          Cancel
                        </button>
                      </div>
                      {controlSearchTerm.trim() && (
                        <div className="control-search-results" style={{
                          maxHeight: '200px',
                          overflowY: 'auto',
                          border: '1px solid var(--border)',
                          borderRadius: 'var(--radius)',
                          backgroundColor: 'var(--bg)',
                        }}>
                          {filteredSearchControls.length > 0 ? (
                            filteredSearchControls.map(c => (
                              <button
                                key={c.scf_id}
                                onClick={() => handleAddControl(c.scf_id)}
                                disabled={addingControl}
                                style={{
                                  display: 'flex',
                                  flexDirection: 'column',
                                  gap: '2px',
                                  width: '100%',
                                  padding: '8px 12px',
                                  border: 'none',
                                  borderBottom: '1px solid var(--border)',
                                  background: 'none',
                                  cursor: 'pointer',
                                  textAlign: 'left',
                                  color: 'var(--text)',
                                  fontSize: '0.875rem',
                                }}
                                onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--secondary)')}
                                onMouseLeave={e => (e.currentTarget.style.backgroundColor = '')}
                              >
                                <span style={{ fontWeight: 500 }}>{c.scf_id}</span>
                                <span style={{ fontSize: '0.8rem', color: 'var(--muted)' }}>{(c as any).control_name || ''}</span>
                              </button>
                            ))
                          ) : (
                            <div style={{ padding: '8px 12px', color: 'var(--muted)', fontSize: '0.875rem' }}>
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
        </section>

        {/* Audit info */}
        <section className="risk-section risk-audit">
          <div className="audit-info">
            <span>Created: {new Date(assessment.created_at).toLocaleDateString()}</span>
            <span>Updated: {new Date(assessment.updated_at).toLocaleDateString()}</span>
          </div>
        </section>
      </div>

      {/* Actions */}
      <div className="risk-detail-actions">
        {isCustomRisk && onDeleteCustomRisk && (
          <button
            className="btn-destructive"
            onClick={() => {
              if (window.confirm(`Delete custom risk ${assessment.risk_code}? This cannot be undone.`)) {
                onDeleteCustomRisk(assessment.risk_code)
              }
            }}
          >
            Delete Risk
          </button>
        )}
        <div style={{ flex: 1 }} />
        {saving && (
          <div className="save-indicator">💾 Saving...</div>
        )}
        <button
          className="btn-secondary"
          onClick={onClose}
        >
          Close
        </button>
      </div>
    </div>
  )
}
