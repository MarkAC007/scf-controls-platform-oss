/**
 * RiskDashboard Component - Main Risk Register page
 *
 * Phase 3 Task 5: Explorer chrome restyle (per ruling 4)
 *  - Quiet level summary strip replaces the four hero cards
 *  - Matrix-type toggle + view toggle moved into toolbar region
 *  - "+ Add Custom Risk" wired via onAddCustomRisk prop to RiskAssessmentList
 *
 * Phase 4 Task 4: RiskDetailPage promotion
 *  - `riskItem` + `onRiskItemChange` props wire ?risk= deep links (App owns URL writes)
 *  - When riskItem is set, renders RiskDetailPage full-width via early return (list unmounts)
 *  - Slide-over (RiskAssessmentDetail) removed — parity proven in RiskDetailPage
 *  - Row click → onRiskItemChange (pushes URL via App), back → onRiskItemChange(null)
 *  - Pager walks the current filtered+sorted list from RiskAssessmentList
 */
import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { toast } from 'react-hot-toast'
import RiskMatrix from './RiskMatrix'
import RiskAssessmentList from './RiskAssessmentList'
import RiskDetailPage from './RiskDetailPage'
import type {
  RiskAssessment,
  RiskAssessmentUpdate,
  RiskCodesFile,
  RiskCategory,
  UserSimple,
  CustomRiskDefinition,
} from '../types'
import { getRiskLevel } from '../types'
import {
  getRiskAssessments,
  createOrUpdateRiskAssessment,
  updateRiskAssessment,
  getOrgMembers,
  getCustomRiskDefinitions,
  createCustomRisk,
  deleteCustomRisk,
} from '../data/apiClient'
import { useRiskProfile } from '../contexts/RiskProfileContext'
import riskCodesData from '../data/risk_codes.json'

interface RiskDashboardProps {
  organizationId: string
  onNavigateToControl?: (scfId: string) => void
  /** The risk code to show in detail view, from ?risk= URL param. null = list/matrix view. */
  riskItem?: string | null
  /** Called when user opens/closes/pages detail. App owns push/replace decision. */
  onRiskItemChange?: (riskCode: string | null) => void
}

type ViewMode = 'matrix' | 'list'

export default function RiskDashboard({ organizationId, onNavigateToControl, riskItem = null, onRiskItemChange }: RiskDashboardProps) {
  const { riskThresholds } = useRiskProfile()

  // State
  const [assessments, setAssessments] = useState<RiskAssessment[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<ViewMode>('matrix')
  const [matrixType, setMatrixType] = useState<'inherent' | 'residual'>('inherent')
  const [selectedCell, setSelectedCell] = useState<{ likelihood: number; impact: number } | null>(null)
  // filteredList: the current sorted+filtered assessment list from RiskAssessmentList (for paging)
  const [filteredList, setFilteredList] = useState<RiskAssessment[]>([])
  const [users, setUsers] = useState<UserSimple[]>([])
  const [customDefs, setCustomDefs] = useState<CustomRiskDefinition[]>([])
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [createForm, setCreateForm] = useState({ title: '', description: '', category_name: 'Custom', category_color: '#6b7280' })
  const [creating, setCreating] = useState(false)

  // Cast the imported JSON to the correct type
  const scfRiskCodes = riskCodesData as RiskCodesFile

  // Merge SCF catalog with custom risk definitions
  const riskCodes = useMemo<RiskCodesFile>(() => {
    const merged: RiskCodesFile = {
      categories: { ...scfRiskCodes.categories, ORG: { name: 'Custom', color: '#6b7280' } },
      codes: { ...scfRiskCodes.codes },
    }
    for (const def of customDefs) {
      merged.codes[def.risk_code] = {
        category: 'ORG' as RiskCategory,
        title: def.title,
        description: def.description,
      }
    }
    return merged
  }, [scfRiskCodes, customDefs])

  // Compute risk level counts for summary strip
  const riskLevelCounts = useMemo(() => {
    const counts = { low: 0, medium: 0, high: 0, critical: 0 }
    for (const a of assessments) {
      const score = (a.likelihood ?? 1) * (a.impact ?? 1)
      const level = getRiskLevel(score, riskThresholds)
      counts[level]++
    }
    return counts
  }, [assessments, riskThresholds])

  // Computed assessed/unassessed counts for the strip
  const assessedCount = useMemo(() =>
    assessments.filter(a => a.likelihood != null && a.impact != null).length,
    [assessments]
  )

  // Load assessments
  const loadAssessments = useCallback(async () => {
    try {
      setLoading(true)
      const data = await getRiskAssessments(undefined, organizationId)
      setAssessments(data)
      setError(null)
    } catch (err: any) {
      console.error('Failed to load risk assessments:', err)
      setError(err.message || 'Failed to load risk assessments')
    } finally {
      setLoading(false)
    }
  }, [organizationId])

  // Initial load
  useEffect(() => {
    loadAssessments()
  }, [loadAssessments])

  // Fetch org members for Risk Owner dropdown
  const membersFetched = useRef(false)
  useEffect(() => {
    if (membersFetched.current) return
    membersFetched.current = true

    getOrgMembers(organizationId)
      .then(setUsers)
      .catch(err => console.error('Failed to load org members:', err))
  }, [organizationId])

  // Fetch custom risk definitions
  const loadCustomDefs = useCallback(async () => {
    try {
      const defs = await getCustomRiskDefinitions(organizationId)
      setCustomDefs(defs)
    } catch (err) {
      console.error('Failed to load custom risk definitions:', err)
    }
  }, [organizationId])

  useEffect(() => {
    loadCustomDefs()
  }, [loadCustomDefs])

  // Handle creating a custom risk
  const handleCreateCustomRisk = async () => {
    if (!createForm.title.trim() || !createForm.description.trim()) return
    setCreating(true)
    try {
      await createCustomRisk({
        title: createForm.title.trim(),
        description: createForm.description.trim(),
        category_name: createForm.category_name || 'Custom',
        category_color: createForm.category_color || '#6b7280',
      }, organizationId)
      setShowCreateModal(false)
      setCreateForm({ title: '', description: '', category_name: 'Custom', category_color: '#6b7280' })
      toast.success('Custom risk created')
      // Reload both definitions and assessments
      await Promise.all([loadCustomDefs(), loadAssessments()])
    } catch (err: any) {
      console.error('Failed to create custom risk:', err)
      toast.error(err.message || 'Failed to create custom risk')
    } finally {
      setCreating(false)
    }
  }

  // Handle deleting a custom risk
  const handleDeleteCustomRisk = async (riskCode: string) => {
    try {
      await deleteCustomRisk(riskCode, organizationId)
      toast.success('Custom risk deleted')
      onRiskItemChange?.(null)
      await Promise.all([loadCustomDefs(), loadAssessments()])
    } catch (err: any) {
      console.error('Failed to delete custom risk:', err)
      toast.error(err.message || 'Failed to delete custom risk')
    }
  }

  // One-shot guard for the lazy-create effect below. The effect depends on
  // `assessments`, so if a creation POST persistently fails the assessment list
  // never fills, the effect re-fires, and it retries forever — an infinite
  // request storm that presents as the register "spinning" (#660). This ref
  // pins the attempt to a single org so creation runs at most once per org.
  const lazyCreateAttemptedOrg = useRef<string | null>(null)

  // Ensure all risk codes have assessment records (lazy create)
  useEffect(() => {
    // Reset the one-shot guard whenever the active organization changes.
    if (lazyCreateAttemptedOrg.current !== organizationId) {
      lazyCreateAttemptedOrg.current = null
    }

    const allRiskCodes = Object.keys(riskCodes.codes)
    const existingCodes = new Set(assessments.map(a => a.risk_code))
    const missingCodes = allRiskCodes.filter(code => !existingCodes.has(code))

    // Create missing assessments in the background — exactly once per org, even
    // if some POSTs fail, so a persistent failure can never loop indefinitely.
    if (missingCodes.length > 0 && !loading && lazyCreateAttemptedOrg.current !== organizationId) {
      lazyCreateAttemptedOrg.current = organizationId
      const createMissing = async () => {
        for (const code of missingCodes) {
          try {
            await createOrUpdateRiskAssessment({
              risk_code: code,
              treatment_status: 'identified'
            }, organizationId)
          } catch (err) {
            console.error(`Failed to create assessment for ${code}:`, err)
          }
        }
        // Reload to get the newly created assessments
        loadAssessments()
      }
      createMissing()
    }
  }, [assessments, loading, organizationId, riskCodes.codes])

  // Handle cell click in matrix
  const handleCellClick = (likelihood: number, impact: number, riskCodes: string[]) => {
    if (riskCodes.length === 0) {
      setSelectedCell(null)
      return
    }
    setSelectedCell({ likelihood, impact })
    // If only one risk in cell, open detail
    if (riskCodes.length === 1) {
      onRiskItemChange?.(riskCodes[0])
    }
  }

  // Handle risk selection in list (row click → open detail)
  const handleSelectRisk = (riskCode: string) => {
    if (riskCode === '') {
      setSelectedCell(null)
      onRiskItemChange?.(null)
    } else {
      onRiskItemChange?.(riskCode)
    }
  }

  // Handle inline update from list
  const handleInlineUpdate = async (riskCode: string, updates: Partial<RiskAssessment>) => {
    try {
      await updateRiskAssessment(riskCode, updates, organizationId)
      // Update local state
      setAssessments(prev => prev.map(a =>
        a.risk_code === riskCode ? { ...a, ...updates } : a
      ))
    } catch (err: any) {
      console.error('Failed to update risk:', err)
      toast.error(err.message || 'Failed to update risk')
    }
  }

  // Handle save from detail page
  const handleDetailSave = async (riskCode: string, updates: RiskAssessmentUpdate) => {
    try {
      const updated = await updateRiskAssessment(riskCode, updates, organizationId)
      setAssessments(prev => prev.map(a =>
        a.risk_code === riskCode ? updated : a
      ))
      toast.success('Risk assessment saved')
    } catch (err: any) {
      console.error('Failed to save risk:', err)
      toast.error(err.message || 'Failed to save risk')
      throw err
    }
  }

  // Get selected assessment (from riskItem prop)
  const selectedAssessment = assessments.find(a => a.risk_code === riskItem) || null

  // Pager: derive position in the filtered list
  const riskItemIndex = riskItem ? filteredList.findIndex(a => a.risk_code === riskItem) : -1
  const pagerPosition = useMemo(() => {
    if (assessments.length === 0) return null
    const total = filteredList.length || assessments.length
    if (riskItemIndex === -1 && riskItem) return { index: null, total }
    if (riskItemIndex === -1) return null
    return { index: riskItemIndex, total }
  }, [riskItem, riskItemIndex, filteredList, assessments])

  // Pager navigation: walk the filtered list
  const handlePrev = useCallback(() => {
    if (riskItemIndex <= 0) return
    const list = filteredList.length > 0 ? filteredList : assessments
    onRiskItemChange?.(list[riskItemIndex - 1].risk_code)
  }, [riskItemIndex, filteredList, assessments, onRiskItemChange])

  const handleNext = useCallback(() => {
    const list = filteredList.length > 0 ? filteredList : assessments
    if (riskItemIndex >= list.length - 1) return
    onRiskItemChange?.(list[riskItemIndex + 1].risk_code)
  }, [riskItemIndex, filteredList, assessments, onRiskItemChange])

  if (loading && assessments.length === 0) {
    return (
      <div className="risk-dashboard loading">
        <div className="loading-spinner" />
        <p>Loading risk register...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="risk-dashboard error">
        <p>Error: {error}</p>
        <button onClick={loadAssessments}>Retry</button>
      </div>
    )
  }

  // Detail page open: riskItem is non-null
  const showDetail = !!riskItem

  if (showDetail) {
    // Full-width detail page — list NOT mounted (early return); pager position
    // is tracked in filteredIds/pagerPosition state that persists across renders.
    return (
      <div className="risk-dashboard">
        {/* Detail page — full-width, list hidden */}
        <RiskDetailPage
          assessment={selectedAssessment}
          riskCodes={riskCodes}
          onSave={handleDetailSave}
          onBack={() => onRiskItemChange?.(null)}
          onPrev={handlePrev}
          onNext={handleNext}
          position={pagerPosition}
          users={users}
          onNavigateToControl={onNavigateToControl}
          onDeleteCustomRisk={handleDeleteCustomRisk}
        />

        {/* Create Custom Risk Modal */}
        {showCreateModal && (
          <div className="modal-overlay" onClick={() => setShowCreateModal(false)}>
            <div className="modal-content custom-risk-modal" onClick={e => e.stopPropagation()}>
              <div className="modal-header">
                <h2>Add Custom Risk</h2>
                <button className="modal-close" onClick={() => setShowCreateModal(false)}>x</button>
              </div>
              <div className="modal-body">
                <div className="form-group">
                  <label>Title *</label>
                  <input
                    type="text"
                    value={createForm.title}
                    onChange={e => setCreateForm(f => ({ ...f, title: e.target.value }))}
                    placeholder="e.g., Physical Security Breach"
                    maxLength={100}
                  />
                </div>
                <div className="form-group">
                  <label>Description *</label>
                  <textarea
                    value={createForm.description}
                    onChange={e => setCreateForm(f => ({ ...f, description: e.target.value }))}
                    placeholder="Describe the risk scenario..."
                    rows={3}
                  />
                </div>
                <div className="form-group">
                  <label>Category Name</label>
                  <input
                    type="text"
                    value={createForm.category_name}
                    onChange={e => setCreateForm(f => ({ ...f, category_name: e.target.value }))}
                    placeholder="e.g., Physical Security"
                    maxLength={50}
                  />
                </div>
                <div className="form-group">
                  <label>Category Color</label>
                  <input
                    type="color"
                    value={createForm.category_color}
                    onChange={e => setCreateForm(f => ({ ...f, category_color: e.target.value }))}
                  />
                </div>
              </div>
              <div className="modal-footer">
                <button
                  className="btn-secondary"
                  onClick={() => setShowCreateModal(false)}
                >
                  Cancel
                </button>
                <button
                  className="btn-primary"
                  onClick={handleCreateCustomRisk}
                  disabled={creating || !createForm.title.trim() || !createForm.description.trim()}
                >
                  {creating ? 'Creating...' : 'Create Risk'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="risk-dashboard">
      {/* Quiet level summary strip (replaces four hero cards) */}
      <div className="risk-level-strip">
        <div className="risk-level-item risk-level-item--low">
          <div className="risk-level-dot risk-level-dot--low" aria-hidden="true" />
          <span className="risk-level-count">{riskLevelCounts.low}</span>
          <span className="risk-level-label">LOW</span>
        </div>
        <div className="risk-level-item risk-level-item--medium">
          <div className="risk-level-dot risk-level-dot--medium" aria-hidden="true" />
          <span className="risk-level-count">{riskLevelCounts.medium}</span>
          <span className="risk-level-label">MEDIUM</span>
        </div>
        <div className="risk-level-item risk-level-item--high">
          <div className="risk-level-dot risk-level-dot--high" aria-hidden="true" />
          <span className="risk-level-count">{riskLevelCounts.high}</span>
          <span className="risk-level-label">HIGH</span>
        </div>
        <div className="risk-level-item risk-level-item--critical">
          <div className="risk-level-dot risk-level-dot--critical" aria-hidden="true" />
          <span className="risk-level-count">{riskLevelCounts.critical}</span>
          <span className="risk-level-label">CRITICAL</span>
        </div>
        <div className="risk-level-strip-divider" aria-hidden="true" />
        <div className="risk-level-item">
          <span className="risk-level-count">{assessedCount}</span>
          <span className="risk-level-label">ASSESSED</span>
        </div>
        <div className="risk-level-item">
          <span className="risk-level-count risk-level-count--muted">
            {assessments.length - assessedCount}
          </span>
          <span className="risk-level-label">NOT ASSESSED</span>
        </div>

        {/* View mode + matrix type toggles + Add Custom Risk pushed to the right */}
        <div className="risk-level-strip-controls">
          {/* + Add Custom Risk button — always visible */}
          <button
            className="risk-add-custom-btn"
            onClick={() => setShowCreateModal(true)}
            title="Create a custom risk code"
          >
            + Add Custom Risk
          </button>

          {/* Matrix type toggle */}
          <div className="toggle-group">
            <button
              className={`toggle-btn${matrixType === 'inherent' ? ' active' : ''}`}
              onClick={() => setMatrixType('inherent')}
            >
              Inherent
            </button>
            <button
              className={`toggle-btn${matrixType === 'residual' ? ' active' : ''}`}
              onClick={() => setMatrixType('residual')}
            >
              Residual
            </button>
          </div>

          {/* View mode toggle */}
          <div className="toggle-group">
            <button
              className={`toggle-btn${viewMode === 'matrix' ? ' active' : ''}`}
              onClick={() => setViewMode('matrix')}
            >
              Matrix
            </button>
            <button
              className={`toggle-btn${viewMode === 'list' ? ' active' : ''}`}
              onClick={() => setViewMode('list')}
            >
              List
            </button>
          </div>
        </div>
      </div>

      {/* Content area */}
      <div className="risk-dashboard-content">
        {/* Main area - Matrix or List */}
        <div className="risk-main-area">
          {viewMode === 'matrix' ? (
            <div className="risk-matrix-section">
              <RiskMatrix
                assessments={assessments}
                riskCodes={riskCodes}
                matrixType={matrixType}
                onCellClick={handleCellClick}
                selectedCell={selectedCell}
                thresholds={riskThresholds}
              />

              {/* Show list below matrix when cell is selected */}
              {selectedCell && (
                <div className="risk-cell-list">
                  <h3>
                    Risks at Likelihood {selectedCell.likelihood}, Impact {selectedCell.impact}
                  </h3>
                  <RiskAssessmentList
                    assessments={assessments}
                    riskCodes={riskCodes}
                    onSelectRisk={handleSelectRisk}
                    onUpdateRisk={handleInlineUpdate}
                    selectedRiskCode={riskItem}
                    filterByCell={selectedCell}
                    matrixType={matrixType}
                    onFilteredListChange={setFilteredList}
                  />
                </div>
              )}
            </div>
          ) : (
            <RiskAssessmentList
              assessments={assessments}
              riskCodes={riskCodes}
              onSelectRisk={handleSelectRisk}
              onUpdateRisk={handleInlineUpdate}
              selectedRiskCode={riskItem}
              filterByCell={null}
              matrixType={matrixType}
              onFilteredListChange={setFilteredList}
            />
          )}
        </div>
      </div>

      {/* Create Custom Risk Modal */}
      {showCreateModal && (
        <div className="modal-overlay" onClick={() => setShowCreateModal(false)}>
          <div className="modal-content custom-risk-modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Add Custom Risk</h2>
              <button className="modal-close" onClick={() => setShowCreateModal(false)}>x</button>
            </div>
            <div className="modal-body">
              <div className="form-group">
                <label>Title *</label>
                <input
                  type="text"
                  value={createForm.title}
                  onChange={e => setCreateForm(f => ({ ...f, title: e.target.value }))}
                  placeholder="e.g., Physical Security Breach"
                  maxLength={100}
                />
              </div>
              <div className="form-group">
                <label>Description *</label>
                <textarea
                  value={createForm.description}
                  onChange={e => setCreateForm(f => ({ ...f, description: e.target.value }))}
                  placeholder="Describe the risk scenario..."
                  rows={3}
                />
              </div>
              <div className="form-group">
                <label>Category Name</label>
                <input
                  type="text"
                  value={createForm.category_name}
                  onChange={e => setCreateForm(f => ({ ...f, category_name: e.target.value }))}
                  placeholder="e.g., Physical Security"
                  maxLength={50}
                />
              </div>
              <div className="form-group">
                <label>Category Color</label>
                <input
                  type="color"
                  value={createForm.category_color}
                  onChange={e => setCreateForm(f => ({ ...f, category_color: e.target.value }))}
                />
              </div>
            </div>
            <div className="modal-footer">
              <button
                className="btn-secondary"
                onClick={() => setShowCreateModal(false)}
              >
                Cancel
              </button>
              <button
                className="btn-primary"
                onClick={handleCreateCustomRisk}
                disabled={creating || !createForm.title.trim() || !createForm.description.trim()}
              >
                {creating ? 'Creating...' : 'Create Risk'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
