/**
 * RiskDashboard Component - Main Risk Register page
 *
 * Combines the risk matrix, assessment list, and detail panel into
 * a cohesive risk management interface.
 */
import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { toast } from 'react-hot-toast'
import RiskMatrix from './RiskMatrix'
import RiskAssessmentList from './RiskAssessmentList'
import RiskAssessmentDetail from './RiskAssessmentDetail'
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
}

type ViewMode = 'matrix' | 'list'

export default function RiskDashboard({ organizationId, onNavigateToControl }: RiskDashboardProps) {
  const { riskThresholds } = useRiskProfile()

  // State
  const [assessments, setAssessments] = useState<RiskAssessment[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<ViewMode>('matrix')
  const [matrixType, setMatrixType] = useState<'inherent' | 'residual'>('inherent')
  const [selectedRiskCode, setSelectedRiskCode] = useState<string | null>(null)
  const [selectedCell, setSelectedCell] = useState<{ likelihood: number; impact: number } | null>(null)
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

  // Compute risk level counts for summary cards
  const riskLevelCounts = useMemo(() => {
    const counts = { low: 0, medium: 0, high: 0, critical: 0 }
    for (const a of assessments) {
      const score = (a.likelihood ?? 1) * (a.impact ?? 1)
      const level = getRiskLevel(score, riskThresholds)
      counts[level]++
    }
    return counts
  }, [assessments, riskThresholds])

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
      setSelectedRiskCode(null)
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
    // If only one risk in cell, select it
    if (riskCodes.length === 1) {
      setSelectedRiskCode(riskCodes[0])
    }
  }

  // Handle risk selection in list
  const handleSelectRisk = (riskCode: string) => {
    if (riskCode === '') {
      setSelectedCell(null)
      setSelectedRiskCode(null)
    } else {
      setSelectedRiskCode(riskCode)
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

  // Handle save from detail panel
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

  // Get selected assessment
  const selectedAssessment = assessments.find(a => a.risk_code === selectedRiskCode) || null

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

  return (
    <div className="risk-dashboard">
      {/* Header */}
      <div className="risk-dashboard-header">
        <div className="header-left">
          <h1 className="page-title">Risk Register</h1>
          <span className="risk-count-badge">{assessments.length} risks</span>
        </div>

        <div className="header-controls">
          <button
            className="btn-add-custom-risk"
            onClick={() => setShowCreateModal(true)}
          >
            + Add Custom Risk
          </button>

          {/* View mode toggle */}
          <div className="view-toggle">
            <button
              className={viewMode === 'matrix' ? 'active' : ''}
              onClick={() => setViewMode('matrix')}
            >
              Matrix View
            </button>
            <button
              className={viewMode === 'list' ? 'active' : ''}
              onClick={() => setViewMode('list')}
            >
              List View
            </button>
          </div>

          {/* Matrix type toggle */}
          <div className="matrix-type-toggle">
            <button
              className={matrixType === 'inherent' ? 'active' : ''}
              onClick={() => setMatrixType('inherent')}
            >
              Inherent Risk
            </button>
            <button
              className={matrixType === 'residual' ? 'active' : ''}
              onClick={() => setMatrixType('residual')}
            >
              Residual Risk
            </button>
          </div>
        </div>
      </div>

      {/* Risk level summary cards */}
      <div className="risk-summary-row">
        {[
          { label: 'Low Risk', count: riskLevelCounts.low, color: 'var(--success)', icon: '\u2713' },
          { label: 'Medium Risk', count: riskLevelCounts.medium, color: 'var(--warning)', icon: '\u2139' },
          { label: 'High Risk', count: riskLevelCounts.high, color: '#fb923c', icon: '\u26A0' },
          { label: 'Critical Risk', count: riskLevelCounts.critical, color: 'var(--destructive)', icon: '\u26D4' },
        ].map(({ label, count, color, icon }) => (
          <div key={label} className="risk-summary-card">
            <div>
              <p className="risk-summary-label">{label}</p>
              <h3 className="risk-summary-value" style={{ color }}>{count}</h3>
            </div>
            <div className="risk-summary-icon" style={{ backgroundColor: `${color}15`, color }}>
              <span>{icon}</span>
            </div>
          </div>
        ))}
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
                    selectedRiskCode={selectedRiskCode}
                    filterByCell={selectedCell}
                    matrixType={matrixType}
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
              selectedRiskCode={selectedRiskCode}
              filterByCell={null}
              matrixType={matrixType}
            />
          )}
        </div>
      </div>

      {/* Slide-over detail panel */}
      <div className={`risk-detail-overlay ${selectedRiskCode ? 'visible' : ''}`}>
        <div
          className="risk-detail-backdrop"
          onClick={() => setSelectedRiskCode(null)}
        />
        <div className="risk-detail-panel-container">
          <RiskAssessmentDetail
            assessment={selectedAssessment}
            riskCodes={riskCodes}
            onSave={handleDetailSave}
            onClose={() => setSelectedRiskCode(null)}
            users={users}
            onNavigateToControl={onNavigateToControl}
            onDeleteCustomRisk={handleDeleteCustomRisk}
          />
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
