import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import type {
  EnrichedControl,
  ScopedControlsFile,
  EvidenceTracking,
  EvidenceId,
  OwnerTeam,
  CollectionInterfacesFile,
  CollectionInterface,
  ERLFile,
  EvidenceMaturityLevel,
  CollectionGuidanceResponse,
  RecipeConfidence,
  EvidenceTemplatesFile,
} from '../types'
import {
  saveScopedControls,
  getScopedControl,
  getEvidenceTracking,
  updateEvidenceTracking as updateEvidenceTrackingInData
} from '../data/scopingService'
import { getSystems, getEvidenceSuggestions, submitRecipeFeedback } from '../data/apiClient'
import type { System, EvidenceSuggestionsResponse } from '../types'
import { AssignmentPicker } from './AssignmentPicker'
import { ModernCommentThread } from './ModernCommentThread'
import { EvidenceTaskList } from './EvidenceTaskList'
import { MaturityBadge, MaturityStepper, MaturityAdvisoryCard } from './maturity'
import { RecipeCard, RecipeConfidenceBadge, EvidenceTemplateGuidance, EvidenceFileUpload, EvidenceFileList, CollectionWizard } from './evidence'
import { WindowReviewPanel } from './evidence/WindowReviewPanel'
import { ScfReference } from './provenance/ScfReference'

// M4 (#574) — gate the per-window review panel mount on the build-time flag.
// When unset (default), the panel is not rendered and the legacy per-file
// review buttons in ``EvidenceFileList`` remain visible.
const PER_WINDOW_REVIEW_ENABLED =
  import.meta.env.VITE_ENABLE_PER_WINDOW_REVIEW === 'true'

interface EvidenceReviewProps {
  controls: EnrichedControl[]
  scopingData: ScopedControlsFile
  onScopingDataChange: (data: ScopedControlsFile) => void
  collectionInterfaces?: CollectionInterfacesFile
  erlData?: ERLFile
  evidenceTemplates?: EvidenceTemplatesFile
}

type ViewMode = 'control' | 'evidence'

export default function EvidenceReview({ controls, scopingData, onScopingDataChange, collectionInterfaces = {}, erlData = {}, evidenceTemplates = {} }: EvidenceReviewProps) {
  const [viewMode, setViewMode] = useState<ViewMode>('evidence')
  const [selectedId, setSelectedId] = useState<string | undefined>(undefined)
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<EvidenceId | undefined>(undefined)
  const [query, setQuery] = useState('')
  const [domainFilter, setDomainFilter] = useState<string>('all')
  const [saving, setSaving] = useState(false)
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [localEvidenceState, setLocalEvidenceState] = useState<Record<EvidenceId, EvidenceTracking>>({})
  const [systems, setSystems] = useState<System[]>([]) // Systems from registry for picker
  const [suggestions, setSuggestions] = useState<EvidenceSuggestionsResponse | null>(null)
  const [loadingSuggestions, setLoadingSuggestions] = useState(false)
  const [collectionGuidance, setCollectionGuidance] = useState<CollectionGuidanceResponse | null>(null)
  const [loadingGuidance, setLoadingGuidance] = useState(false)
  const [feedbackSubmitted, setFeedbackSubmitted] = useState<string | null>(null)
  const [fileListRefreshTrigger, setFileListRefreshTrigger] = useState(0)
  const [showCollectionWizard, setShowCollectionWizard] = useState(false)

  // Filter to only selected controls with artifacts
  const selectedControls = useMemo(() => {
    return controls.filter(c => {
      const scoped = getScopedControl(scopingData, c.scf_id)
      return scoped?.selected && c.artifactsResolved.length > 0
    })
  }, [controls, scopingData])

  // Helper function to find which controls require a specific evidence item
  const getControlsRequiringEvidence = (evidenceId: EvidenceId): EnrichedControl[] => {
    return selectedControls.filter(control =>
      control.artifactsResolved.some(artifact => artifact.id === evidenceId)
    )
  }

  // Helper function to get collection interfaces for an evidence item
  const getCollectionMethodsForEvidence = (evidenceId: EvidenceId): { id: string; ci: CollectionInterface }[] => {
    const erlEntry = erlData[evidenceId]
    if (!erlEntry?.collection_interfaces) return []

    return erlEntry.collection_interfaces
      .map(ciId => {
        const ci = collectionInterfaces[ciId]
        return ci ? { id: ciId, ci } : null
      })
      .filter((item): item is { id: string; ci: CollectionInterface } => item !== null)
  }

  // Get all unique evidence items from selected controls
  const uniqueEvidenceItems = useMemo(() => {
    const evidenceMap = new Map<EvidenceId, { id: EvidenceId; title: string; domain: string; controlCount: number }>()

    selectedControls.forEach(control => {
      control.artifactsResolved.forEach(artifact => {
        if (evidenceMap.has(artifact.id)) {
          const existing = evidenceMap.get(artifact.id)!
          evidenceMap.set(artifact.id, { ...existing, controlCount: existing.controlCount + 1 })
        } else {
          evidenceMap.set(artifact.id, {
            id: artifact.id,
            title: artifact.title,
            domain: artifact.domain,
            controlCount: 1
          })
        }
      })
    })

    return Array.from(evidenceMap.values()).sort((a, b) => a.title.localeCompare(b.title))
  }, [selectedControls])

  // Get all unique evidence domains
  const evidenceDomains = useMemo(() => {
    const domainSet = new Set<string>()
    uniqueEvidenceItems.forEach(item => {
      domainSet.add(item.domain)
    })
    return Array.from(domainSet).sort()
  }, [uniqueEvidenceItems])

  // Check for navigation request from task dashboard
  useEffect(() => {
    const navigateToEvidence = sessionStorage.getItem('navigate_to_evidence')
    if (navigateToEvidence) {
      // Switch to evidence view mode
      setViewMode('evidence')
      // Clear filters so the target item is not hidden
      setQuery('')
      setDomainFilter('all')
      // Set the selected evidence
      setSelectedEvidenceId(navigateToEvidence)
      // Note: sessionStorage is cleared by the auto-select guard below
      console.log(`Navigated to evidence: ${navigateToEvidence}`)
    }
  }, [])

  // Scroll the active evidence card into view when selection changes
  useEffect(() => {
    if (!selectedEvidenceId) return
    const timer = setTimeout(() => {
      document.querySelector(`[data-evidence-id="${selectedEvidenceId}"]`)
        ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }, 50)
    return () => clearTimeout(timer)
  }, [selectedEvidenceId])

  // Load systems for the picker
  useEffect(() => {
    const loadSystems = async () => {
      try {
        const systemList = await getSystems(scopingData.organizationId)
        setSystems(systemList.filter(s => s.status === 'active'))
      } catch (error) {
        console.error('Failed to load systems:', error)
      }
    }
    if (scopingData.organizationId) {
      loadSystems()
    }
  }, [scopingData.organizationId])

  // Load suggestions when evidence item is selected
  useEffect(() => {
    const loadSuggestions = async () => {
      if (!selectedEvidenceId || !scopingData.organizationId) {
        setSuggestions(null)
        return
      }
      setLoadingSuggestions(true)
      try {
        const result = await getEvidenceSuggestions(selectedEvidenceId, scopingData.organizationId)
        setSuggestions(result)
      } catch (error) {
        console.error('Failed to load suggestions:', error)
        setSuggestions(null)
      } finally {
        setLoadingSuggestions(false)
      }
    }
    if (viewMode === 'evidence') {
      loadSuggestions()
    }
  }, [selectedEvidenceId, scopingData.organizationId, viewMode])

  // Derived tracking values for the selected evidence — used as explicit useEffect dependencies
  const currentCollectingSystem = selectedEvidenceId ? localEvidenceState[selectedEvidenceId]?.collecting_system : undefined
  const currentMaturityLevel = selectedEvidenceId ? localEvidenceState[selectedEvidenceId]?.maturity_level : undefined

  // Load collection guidance when collecting_system or maturity_level changes
  useEffect(() => {
    const loadGuidance = async () => {
      if (!selectedEvidenceId || !scopingData.organizationId) return
      if (!currentCollectingSystem) {
        setCollectionGuidance(null)
        return
      }
      // Find the system ID from the systems list
      const matchedSystem = systems.find(s => s.name === currentCollectingSystem)
      if (!matchedSystem) {
        setCollectionGuidance(null)
        return
      }
      setLoadingGuidance(true)
      setFeedbackSubmitted(null)
      try {
        const result = await getEvidenceSuggestions(selectedEvidenceId, scopingData.organizationId, {
          systemId: matchedSystem.id,
          maturityLevel: currentMaturityLevel || 'L1',
        })
        setCollectionGuidance(result.collection_guidance || null)
      } catch (error) {
        console.error('Failed to load collection guidance:', error)
        setCollectionGuidance(null)
      } finally {
        setLoadingGuidance(false)
      }
    }
    if (viewMode === 'evidence') {
      loadGuidance()
    }
  }, [selectedEvidenceId, scopingData.organizationId, viewMode, currentCollectingSystem, currentMaturityLevel, systems])

  // Handle recipe feedback submission
  const handleRecipeFeedback = useCallback(async (feedbackType: 'helpful' | 'not_matching') => {
    if (!collectionGuidance || !scopingData.organizationId || !selectedEvidenceId) return
    try {
      await submitRecipeFeedback(selectedEvidenceId, {
        system_type: collectionGuidance.system_type,
        vendor: collectionGuidance.vendor,
        feedback_type: feedbackType,
        maturity_level: collectionGuidance.current_maturity as EvidenceMaturityLevel,
      }, scopingData.organizationId)
      setFeedbackSubmitted(feedbackType)
    } catch (error) {
      console.error('Failed to submit feedback:', error)
    }
  }, [collectionGuidance, scopingData.organizationId, selectedEvidenceId])

  // Select first item on mount based on view mode
  useEffect(() => {
    if (viewMode === 'control') {
      if (!selectedId && selectedControls.length > 0) {
        setSelectedId(selectedControls[0].scf_id)
      }
    } else {
      // Don't auto-select if we have a navigation request pending
      const hasNavigation = sessionStorage.getItem('navigate_to_evidence')
      if (hasNavigation) {
        sessionStorage.removeItem('navigate_to_evidence')
        return
      }
      if (!selectedEvidenceId && uniqueEvidenceItems.length > 0) {
        setSelectedEvidenceId(uniqueEvidenceItems[0].id)
      }
    }
  }, [viewMode, selectedControls, selectedId, uniqueEvidenceItems, selectedEvidenceId])

  // Get all unique domains from selected controls
  const domains = useMemo(() => {
    const domainSet = new Set<string>()
    selectedControls.forEach(control => {
      domainSet.add(control.scf_domain)
    })
    return Array.from(domainSet).sort()
  }, [selectedControls])

  // Filter controls based on search and domain
  const filteredControls = useMemo(() => {
    let filtered = selectedControls

    // Domain filter
    if (domainFilter !== 'all') {
      filtered = filtered.filter(c => c.scf_domain === domainFilter)
    }

    // Search filter
    if (query) {
      const q = query.toLowerCase()
      filtered = filtered.filter(c =>
        c.scf_id.toLowerCase().includes(q) ||
        c.control_name.toLowerCase().includes(q) ||
        c.scf_domain.toLowerCase().includes(q) ||
        c.artifactsResolved.some(a =>
          a.title.toLowerCase().includes(q) ||
          a.domain.toLowerCase().includes(q)
        )
      )
    }

    return filtered
  }, [selectedControls, query, domainFilter])

  // Filter evidence items based on search and domain
  const filteredEvidenceItems = useMemo(() => {
    let filtered = uniqueEvidenceItems

    // Domain filter
    if (domainFilter !== 'all') {
      filtered = filtered.filter(item => item.domain === domainFilter)
    }

    // Search filter
    if (query) {
      const q = query.toLowerCase()
      filtered = filtered.filter(item =>
        item.id.toLowerCase().includes(q) ||
        item.title.toLowerCase().includes(q) ||
        item.domain.toLowerCase().includes(q)
      )
    }

    return filtered
  }, [uniqueEvidenceItems, query, domainFilter])

  // Get stats (only for selected controls)
  const stats = useMemo(() => {
    let tracked = 0
    let total = 0
    const seenEvidence = new Set<EvidenceId>()

    selectedControls.forEach(control => {
      control.artifactsResolved.forEach(artifact => {
        if (!seenEvidence.has(artifact.id)) {
          seenEvidence.add(artifact.id)
          total++
          const evidenceTracking = getEvidenceTracking(scopingData, artifact.id)
          if (evidenceTracking?.is_tracked) {
            tracked++
          }
        }
      })
    })

    return { tracked, total }
  }, [scopingData, selectedControls])

  // Auto-save helper with debounce
  const saveData = async (data: ScopedControlsFile) => {
    // Clear any existing timeout
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current)
    }

    // Debounce both the file save and parent state update
    setSaving(true)
    saveTimeoutRef.current = setTimeout(async () => {
      await saveScopedControls(data)
      onScopingDataChange(data)
      setSaving(false)
    }, 300)
  }

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current)
      }
    }
  }, [])

  // Sync local evidence state from parent
  // Only sync when not actively saving to prevent overwriting user edits
  useEffect(() => {
    if (scopingData.evidence_tracking && !saving) {
      setLocalEvidenceState(scopingData.evidence_tracking)
    }
  }, [scopingData.evidence_tracking, saving])

  // Get current selected control details
  const selectedControl = useMemo(
    () => controls.find(c => c.scf_id === selectedId),
    [controls, selectedId]
  )

  // Update evidence tracking for a specific artifact
  const updateEvidenceTracking = async (evidenceId: EvidenceId, field: keyof EvidenceTracking, value: string | boolean) => {
    const currentTracking = localEvidenceState[evidenceId] || {}

    // Handle boolean field (is_tracked) vs string fields
    let fieldValue: any
    if (field === 'is_tracked') {
      // For is_tracked, use the boolean value directly (true or false, never undefined)
      fieldValue = Boolean(value)
    } else {
      // For string fields, preserve empty strings (don't convert to undefined)
      // Empty string "" is a valid cleared state and should be saved as-is
      fieldValue = value
    }

    const updatedTracking = {
      ...currentTracking,
      [field]: fieldValue
    }

    // Update local state immediately
    setLocalEvidenceState(prev => ({
      ...prev,
      [evidenceId]: updatedTracking
    }))

    // Clear any existing timeout
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current)
    }

    // Debounce the API call
    setSaving(true)
    saveTimeoutRef.current = setTimeout(async () => {
      try {
        const updated = await updateEvidenceTrackingInData({ ...scopingData }, evidenceId, updatedTracking)
        onScopingDataChange(updated)
      } catch (error) {
        console.error('Failed to update evidence tracking:', error)
      } finally {
        setSaving(false)
      }
    }, 300)
  }

  // Show message if no controls are selected
  if (selectedControls.length === 0) {
    return (
      <div className="tab-content">
        <div className="placeholder">
          <h2>No Controls Selected</h2>
          <p>Please select controls in the Control Scoping tab first.</p>
          <p className="muted">Evidence review is only available for selected controls with audit artifacts.</p>
        </div>
      </div>
    )
  }

  // Determine which domains/filters to use based on view mode
  const activedomains = viewMode === 'control' ? domains : evidenceDomains
  const activeFilteredItems = viewMode === 'control' ? filteredControls : filteredEvidenceItems

  return (
    <div className="layout">
      {/* Left Panel - Control or Evidence List */}
      <div className="sidebar">
        {/* View Mode Toggle */}
        <div className="evidence-view-toggle">
          <span className="toggle-label">View by:</span>
          <button
            className={`toggle-option ${viewMode === 'evidence' ? 'active' : ''}`}
            onClick={() => setViewMode('evidence')}
          >
            Evidence
          </button>
          <button
            className={`toggle-option ${viewMode === 'control' ? 'active' : ''}`}
            onClick={() => setViewMode('control')}
          >
            Control
          </button>
          <button
            className="btn-secondary btn-sm"
            onClick={() => setShowCollectionWizard(true)}
            title="Set up automated evidence collection"
          >
            Set Up Collection
          </button>
        </div>

        <div className="evidence-sidebar-header">
          <div className="evidence-stats-compact">
            <div className="stat-compact stat-tracked">
              <div className="stat-compact-value">{stats.tracked}</div>
              <div className="stat-compact-label">Tracked</div>
            </div>
            <div className="stat-compact-divider">/</div>
            <div className="stat-compact stat-total-evidence">
              <div className="stat-compact-value">{stats.total}</div>
              <div className="stat-compact-label">Evidence</div>
            </div>
          </div>
          <div className="evidence-progress-mini">
            <div className="evidence-progress-mini-bar">
              <div
                className="evidence-progress-mini-fill"
                style={{ width: `${stats.total > 0 ? (stats.tracked / stats.total) * 100 : 0}%` }}
              ></div>
            </div>
          </div>
        </div>

        <div className="search">
          <input
            type="text"
            placeholder="Search controls or evidence..."
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
        </div>

        <div className="filter">
          <select
            value={domainFilter}
            onChange={e => setDomainFilter(e.target.value)}
            className="framework-select"
          >
            <option value="all">All Domains ({activeFilteredItems.length})</option>
            {activedomains.map(domain => {
              const count = viewMode === 'control'
                ? selectedControls.filter(c => c.scf_domain === domain).length
                : uniqueEvidenceItems.filter(item => item.domain === domain).length
              return (
                <option key={domain} value={domain}>
                  {domain} ({count})
                </option>
              )
            })}
          </select>
        </div>

        <div className="list">
          {viewMode === 'control' ? (
            /* Control-First View */
            filteredControls.map(control => {
            const trackedCount = control.artifactsResolved.filter(a =>
              getEvidenceTracking(scopingData, a.id)?.is_tracked
            ).length
            const trackingPercentage = control.artifactsResolved.length > 0
              ? Math.round((trackedCount / control.artifactsResolved.length) * 100)
              : 0

            return (
              <div
                key={control.scf_id}
                className={`evidence-card-modern ${selectedId === control.scf_id ? 'active' : ''}`}
                onClick={() => setSelectedId(control.scf_id)}
              >
                <div className="evidence-card-header">
                  <span className="badge-modern">{control.scf_id}</span>
                  <div className="evidence-tracking-badge">
                    <span className="tracking-count">{trackedCount}/{control.artifactsResolved.length}</span>
                  </div>
                </div>
                <div className="evidence-card-name">{control.control_name}</div>
                <div className="evidence-card-footer">
                  <span className="evidence-card-domain">{control.scf_domain}</span>
                  <div className="mini-progress">
                    <div className="mini-progress-fill" style={{ width: `${trackingPercentage}%` }}></div>
                  </div>
                </div>
              </div>
            )
          })
          ) : (
            /* Evidence-First View */
            filteredEvidenceItems.map(evidenceItem => {
              const tracking = localEvidenceState[evidenceItem.id] || {}
              const isTracked = tracking.is_tracked || false

              return (
                <div
                  key={evidenceItem.id}
                  data-evidence-id={evidenceItem.id}
                  className={`evidence-card-modern ${selectedEvidenceId === evidenceItem.id ? 'active' : ''}`}
                  onClick={() => setSelectedEvidenceId(evidenceItem.id)}
                >
                  <div className="evidence-card-header">
                    <span className="badge-modern">{evidenceItem.id}</span>
                    <div className="evidence-tracking-badge">
                      {isTracked ? (
                        <span className="tracking-count tracked-pill">Tracked</span>
                      ) : (
                        <span className="tracking-count">Not Tracked</span>
                      )}
                    </div>
                  </div>
                  <div className="evidence-card-name">{evidenceItem.title}</div>
                  <div className="evidence-card-footer">
                    <span className="evidence-card-domain">{evidenceItem.domain}</span>
                    {tracking.maturity_level ? (
                      <MaturityBadge level={tracking.maturity_level} size="small" showLabel={false} showTooltip={false} />
                    ) : (
                      <span className="evidence-card-domain" style={{ opacity: 0.7 }}>
                        {evidenceItem.controlCount} ctrl{evidenceItem.controlCount !== 1 ? 's' : ''}
                      </span>
                    )}
                  </div>
                </div>
              )
            })
          )}
        </div>

        {saving && (
          <div className="save-indicator">💾 Saving...</div>
        )}
      </div>

      {/* Right Panel - Evidence or Control Details */}
      <div className="detail">
        {viewMode === 'control' && selectedControl ? (
          <>
            <div className="detail-header-compact">
              <div className="detail-header-main surface-bedrock" data-source="SCF Reference">
                <span className="scf-source-tag">SCF Catalog</span>
                <div className="detail-id-compact">{selectedControl.scf_id}</div>
                <h2 className="detail-name-compact">{selectedControl.control_name}</h2>
                <div className="detail-meta-row">
                  <span className="detail-domain-compact">{selectedControl.scf_domain}</span>
                  <div className="detail-badges">
                    <span className={`badge-theme theme-${(selectedControl.nist_csf_function || selectedControl.control_theme || 'unknown').toLowerCase().replace(/\s+/g, '-')}`}>
                      {selectedControl.nist_csf_function || selectedControl.control_theme || 'N/A'}
                    </span>
                    {selectedControl.control_type && (
                      <span className={`badge-type type-${selectedControl.control_type.toLowerCase().replace(/\s+/g, '-')}`}>
                        {selectedControl.control_type}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </div>

            <div className="detail-content-compact">
              {/* Control Details — SCF reference content, rendered flat */}
              <ScfReference>
              <div className="detail-section-container">
                <div className="container-header">
                  <span className="container-icon">📄</span>
                  <span className="container-title">Control Details</span>
                </div>
                <div className="container-content">
                  <div className="detail-field">
                    <div className="field-label">
                      <span className="field-icon">📝</span>
                      Description
                    </div>
                    <div className="field-content">
                      {selectedControl.control_description}
                    </div>
                  </div>

                  <div className="detail-field">
                    <div className="field-label">
                      <span className="field-icon">📜</span>
                      Policy Standard
                    </div>
                    <div className="field-content">
                      {selectedControl.policy_standard}
                    </div>
                  </div>

                  <div className="detail-field">
                    <div className="field-label">
                      <span className="field-icon">💡</span>
                      Implementation Guidance
                    </div>
                    <div className="field-content prewrap">
                      {selectedControl.implementation_guidance}
                    </div>
                  </div>

                  <div className="detail-field">
                    <div className="field-label">
                      <span className="field-icon">🔍</span>
                      Testing Procedure
                    </div>
                    <div className="field-content prewrap">
                      {selectedControl.testing_procedure}
                    </div>
                  </div>
                </div>
              </div>
              </ScfReference>

              {/* Evidence & Audit Artifacts */}
              <div className="evidence-section-wrapper">
                <div className="evidence-section-title">
                  <span className="section-icon">📋</span>
                  <h3>Evidence & Audit Artifacts</h3>
                  <span className="evidence-section-count">{selectedControl.artifactsResolved.length} items</span>
                </div>

                {selectedControl.artifactsResolved.length === 0 ? (
                  <p className="muted">No audit artifacts defined for this control.</p>
                ) : (
                  <div className="evidence-list">
                  {selectedControl.artifactsResolved.map(artifact => {
                    const tracking = localEvidenceState[artifact.id] || {}
                    const isTracked = tracking.is_tracked || false
                    const requiringControls = getControlsRequiringEvidence(artifact.id)

                    return (
                      <details key={artifact.id} className="evidence-item-modern" open={isTracked}>
                        <summary>
                          <div className="evidence-summary-modern">
                            <div className="evidence-summary-left">
                              <div className="evidence-id-badge">{artifact.id}</div>
                              <div className="evidence-info">
                                <div className="evidence-title-modern">{artifact.title}</div>
                                <div className="evidence-domain-tag">{artifact.domain}</div>
                              </div>
                            </div>
                            <div className="evidence-summary-right">
                              {isTracked ? (
                                <span className="tracked-badge-modern">✓ Tracked</span>
                              ) : (
                                <span className="untracked-badge-modern">Not Tracked</span>
                              )}
                            </div>
                          </div>
                        </summary>

                        <div className="evidence-form-modern surface-bench">
                          {requiringControls.length > 0 && (
                            <div className="evidence-context-modern">
                              <div className="context-header">
                                <span className="context-icon">🔗</span>
                                <span className="context-title">
                                  {requiringControls.length === 1
                                    ? 'Required by 1 control'
                                    : `Required by ${requiringControls.length} controls`}
                                </span>
                              </div>
                              <div className="requiring-controls-pills">
                                {requiringControls.map(ctrl => {
                                  const tooltipId = `tooltip-${artifact.id}-${ctrl.scf_id}`
                                  const isCurrentControl = ctrl.scf_id === selectedControl?.scf_id
                                  const ctrlScopedData = getScopedControl(scopingData, ctrl.scf_id)
                                  const implStatus = ctrlScopedData?.implementation_status || 'not_started'

                                  // Status display helpers
                                  const statusConfig = {
                                    implemented: { label: 'IMPLEMENTED', icon: '✅', class: 'status-implemented' },
                                    in_progress: { label: 'IN PROGRESS', icon: '🔄', class: 'status-in-progress' },
                                    not_started: { label: 'NOT STARTED', icon: '⭕', class: 'status-not-started' },
                                    at_risk: { label: 'AT RISK', icon: '⚠️', class: 'status-at-risk' },
                                    not_applicable: { label: 'NOT APPLICABLE', icon: '❌', class: 'status-not-applicable' },
                                    deferred: { label: 'DEFERRED', icon: '⏸️', class: 'status-deferred' }
                                  }

                                  const status = statusConfig[implStatus as keyof typeof statusConfig] || statusConfig.not_started
                                  const pillStatusClass = implStatus === 'not_applicable' ? 'pill-not-applicable' :
                                                         implStatus === 'deferred' ? 'pill-deferred' :
                                                         implStatus === 'at_risk' ? 'pill-at-risk' : ''

                                  return (
                                    <div key={ctrl.scf_id} className="control-pill-wrapper">
                                      <button
                                        className={`control-pill ${isCurrentControl ? 'current-control' : ''} ${pillStatusClass}`}
                                        onClick={() => setSelectedId(ctrl.scf_id)}
                                        onMouseEnter={(e) => {
                                          const tooltip = document.getElementById(tooltipId)
                                          if (tooltip) {
                                            const rect = e.currentTarget.getBoundingClientRect()
                                            tooltip.style.top = `${rect.top - tooltip.offsetHeight - 8}px`
                                            tooltip.style.left = `${Math.max(10, rect.left + rect.width / 2 - 200)}px`
                                          }
                                        }}
                                      >
                                        {ctrl.scf_id} — {ctrl.control_name}
                                        {isCurrentControl && ' (current)'}
                                      </button>
                                      <div id={tooltipId} className="control-tooltip">
                                        <div className="tooltip-header">
                                          <strong>{ctrl.scf_id}</strong> — {ctrl.control_name}
                                        </div>
                                        <div className="tooltip-domain">{ctrl.scf_domain}</div>

                                        {ctrlScopedData && status && (
                                          <div className={`tooltip-status-box ${status.class}`}>
                                            <div className="status-row">
                                              <span className="status-label">Status:</span>
                                              <span className="status-value">
                                                {status.icon} {status.label}
                                              </span>
                                            </div>
                                            {ctrlScopedData.owner && (
                                              <div className="status-row">
                                                <span className="status-label">Owner:</span>
                                                <span className="status-value">{ctrlScopedData.owner}</span>
                                              </div>
                                            )}
                                            {ctrlScopedData.completion_date && (
                                              <div className="status-row">
                                                <span className="status-label">Target Date:</span>
                                                <span className="status-value">{ctrlScopedData.completion_date}</span>
                                              </div>
                                            )}
                                            {ctrlScopedData.maturity_level && (
                                              <div className="status-row">
                                                <span className="status-label">Maturity:</span>
                                                <span className="status-value">
                                                  {ctrlScopedData.maturity_level.charAt(0).toUpperCase() + ctrlScopedData.maturity_level.slice(1)}
                                                </span>
                                              </div>
                                            )}
                                          </div>
                                        )}

                                        {ctrlScopedData?.selection_reason && (
                                          <div className="tooltip-section">
                                            <strong>Selection Reason:</strong>
                                            <p>{ctrlScopedData.selection_reason}</p>
                                          </div>
                                        )}

                                        <div className="tooltip-section">
                                          <strong>Description:</strong>
                                          <p>{ctrl.control_description}</p>
                                        </div>
                                        <div className="tooltip-section">
                                          <strong>Testing Procedure:</strong>
                                          <p>{ctrl.testing_procedure || 'No testing procedure defined'}</p>
                                        </div>
                                      </div>
                                    </div>
                                  )
                                })}
                              </div>
                            </div>
                          )}

                          <div className="tracking-toggle-section">
                            <label className="tracking-toggle-label">
                              <input
                                type="checkbox"
                                checked={isTracked}
                                onChange={e => updateEvidenceTracking(artifact.id, 'is_tracked', e.target.checked)}
                                className="tracking-checkbox"
                              />
                              <div className="tracking-toggle-content">
                                <div className="tracking-toggle-title">Evidence Collection Active</div>
                                <div className="tracking-toggle-hint">Mark this evidence as being actively collected for compliance</div>
                              </div>
                            </label>
                          </div>

                          <div className="form-group">
                            <label>Method of Collection</label>
                            <input
                              type="text"
                              value={tracking.method_of_collection || ''}
                              onChange={e => updateEvidenceTracking(artifact.id, 'method_of_collection', e.target.value)}
                              placeholder="e.g., Automated export, Manual review, Screenshot"
                              className="form-control"
                            />
                          </div>

                          <div className="form-group">
                            <label>Collecting System</label>
                            <select
                              value={tracking.collecting_system || ''}
                              onChange={e => updateEvidenceTracking(artifact.id, 'collecting_system', e.target.value)}
                              className="form-control"
                            >
                              <option value="">Select System...</option>
                              {systems.map(system => (
                                <option key={system.id} value={system.name}>
                                  {system.name} ({system.vendor || system.system_type})
                                </option>
                              ))}
                              <option value="__other__" disabled>───────────────</option>
                              <option value="Manual">Manual / Not Automated</option>
                            </select>
                          </div>

                          <div className="form-row">
                            <div className="form-group">
                              <label>Owner Team</label>
                              <select
                                value={tracking.owner || ''}
                                onChange={e => updateEvidenceTracking(artifact.id, 'owner', e.target.value)}
                                className="form-control"
                              >
                                <option value="">Select Team...</option>
                                <option value="Software Engineering">Software Engineering</option>
                                <option value="Security Operations">Security Operations</option>
                                <option value="DevSecOps">DevSecOps</option>
                                <option value="Cyber Security">Cyber Security</option>
                                <option value="GRC">GRC</option>
                              </select>
                            </div>

                            <div className="form-group">
                              <label>Frequency</label>
                              <input
                                type="text"
                                value={tracking.frequency || ''}
                                onChange={e => updateEvidenceTracking(artifact.id, 'frequency', e.target.value)}
                                placeholder="e.g., Monthly, Quarterly, Annual"
                                className="form-control"
                              />
                            </div>
                          </div>

                          <div className="form-group">
                            <label>Comments</label>
                            <textarea
                              value={tracking.comments || ''}
                              onChange={e => updateEvidenceTracking(artifact.id, 'comments', e.target.value)}
                              placeholder="Additional notes about evidence collection..."
                              className="form-control"
                              rows={3}
                            />
                          </div>
                        </div>
                      </details>
                    )
                  })}
                </div>
              )}
              </div>
            </div>
          </>
        ) : viewMode === 'evidence' && selectedEvidenceId ? (
          /* Evidence-First Detail View */
          (() => {
            const evidenceItem = uniqueEvidenceItems.find(item => item.id === selectedEvidenceId)
            if (!evidenceItem) return <div className="empty">Evidence not found</div>

            const tracking = localEvidenceState[evidenceItem.id] || {}
            const isTracked = tracking.is_tracked || false
            const requiringControls = getControlsRequiringEvidence(evidenceItem.id)

            return (
              <>
                <div className="detail-header-compact">
                  <div className="detail-header-main surface-bedrock" data-source="SCF Evidence Requirements">
                    <span className="scf-source-tag">SCF ERL</span>
                    <div className="detail-id-compact">{evidenceItem.id}</div>
                    <h2 className="detail-name-compact">{evidenceItem.title}</h2>
                    <div className="detail-meta-row">
                      <span className="detail-domain-compact">{evidenceItem.domain}</span>
                      <div className="detail-badges">
                        {isTracked ? (
                          <span className="badge-theme theme-process">Tracked</span>
                        ) : (
                          <span className="badge-type type-detective">Not Tracked</span>
                        )}
                        {tracking.maturity_level && (
                          <MaturityBadge level={tracking.maturity_level} size="small" />
                        )}
                        {saving && <span className="detail-save-chip">Saving…</span>}
                      </div>
                    </div>
                  </div>
                </div>

                <div className="detail-content-compact">
                  {/* Required by Controls — SCF/ERL reference mapping, rendered flat */}
                  <ScfReference>
                  <div className="detail-section-container">
                    <div className="container-header">
                      <span className="container-icon">🔗</span>
                      <span className="container-title">Required by Controls</span>
                      <span className="container-count">{requiringControls.length}</span>
                    </div>
                    <div className="container-content">
                      {requiringControls.length === 0 ? (
                        <p className="muted">No controls require this evidence</p>
                      ) : (
                        <div className="requiring-controls-pills">
                          {requiringControls.map(ctrl => {
                            const tooltipId = `tooltip-ev-${evidenceItem.id}-${ctrl.scf_id}`
                            const ctrlScopedData = getScopedControl(scopingData, ctrl.scf_id)
                            const implStatus = ctrlScopedData?.implementation_status || 'not_started'

                            // Status display helpers
                            const statusConfig = {
                              implemented: { label: 'IMPLEMENTED', icon: '✅', class: 'status-implemented' },
                              in_progress: { label: 'IN PROGRESS', icon: '🔄', class: 'status-in-progress' },
                              not_started: { label: 'NOT STARTED', icon: '⭕', class: 'status-not-started' },
                              at_risk: { label: 'AT RISK', icon: '⚠️', class: 'status-at-risk' },
                              not_applicable: { label: 'NOT APPLICABLE', icon: '❌', class: 'status-not-applicable' },
                              deferred: { label: 'DEFERRED', icon: '⏸️', class: 'status-deferred' }
                            }

                            const status = statusConfig[implStatus as keyof typeof statusConfig] || statusConfig.not_started
                            const pillStatusClass = implStatus === 'not_applicable' ? 'pill-not-applicable' :
                                                   implStatus === 'deferred' ? 'pill-deferred' :
                                                   implStatus === 'at_risk' ? 'pill-at-risk' : ''

                            return (
                              <div key={ctrl.scf_id} className="control-pill-wrapper">
                                <button
                                  className={`control-pill ${pillStatusClass}`}
                                  onClick={() => {
                                    setViewMode('control')
                                    setSelectedId(ctrl.scf_id)
                                  }}
                                  onMouseEnter={(e) => {
                                    const tooltip = document.getElementById(tooltipId)
                                    if (tooltip) {
                                      const rect = e.currentTarget.getBoundingClientRect()
                                      tooltip.style.top = `${rect.top - tooltip.offsetHeight - 8}px`
                                      tooltip.style.left = `${Math.max(10, rect.left + rect.width / 2 - 200)}px`
                                    }
                                  }}
                                >
                                  {ctrl.scf_id} — {ctrl.control_name}
                                </button>
                                <div id={tooltipId} className="control-tooltip">
                                  <div className="tooltip-header">
                                    <strong>{ctrl.scf_id}</strong> — {ctrl.control_name}
                                  </div>
                                  <div className="tooltip-domain">{ctrl.scf_domain}</div>

                                  {ctrlScopedData && status && (
                                    <div className={`tooltip-status-box ${status.class}`}>
                                      <div className="status-row">
                                        <span className="status-label">Status:</span>
                                        <span className="status-value">
                                          {status.icon} {status.label}
                                        </span>
                                      </div>
                                      {ctrlScopedData.owner && (
                                        <div className="status-row">
                                          <span className="status-label">Owner:</span>
                                          <span className="status-value">{ctrlScopedData.owner}</span>
                                        </div>
                                      )}
                                      {ctrlScopedData.completion_date && (
                                        <div className="status-row">
                                          <span className="status-label">Target Date:</span>
                                          <span className="status-value">{ctrlScopedData.completion_date}</span>
                                        </div>
                                      )}
                                    </div>
                                  )}

                                  <div className="tooltip-section">
                                    <strong>Description:</strong>
                                    <p>{ctrl.control_description}</p>
                                  </div>
                                </div>
                              </div>
                            )
                          })}
                        </div>
                      )}
                    </div>
                  </div>
                  </ScfReference>

                  {scopingData.organizationId && PER_WINDOW_REVIEW_ENABLED && (
                    <WindowReviewPanel
                      orgId={scopingData.organizationId}
                      evidenceId={selectedEvidenceId}
                      refreshTrigger={fileListRefreshTrigger}
                    />
                  )}

                  {scopingData.organizationId && (
                    <div className="detail-section-container surface-bench">
                      <div className="container-header bench-header">
                        <span className="container-icon">{'\uD83D\uDCC1'}</span>
                        <span className="container-title">Your Evidence Files</span>
                      </div>
                      <div className="container-content">
                        <EvidenceFileUpload
                          orgId={scopingData.organizationId}
                          evidenceId={selectedEvidenceId}
                          onUploadComplete={() => setFileListRefreshTrigger(prev => prev + 1)}
                        />
                        <EvidenceFileList
                          orgId={scopingData.organizationId}
                          evidenceId={selectedEvidenceId}
                          refreshTrigger={fileListRefreshTrigger}
                        />
                      </div>
                    </div>
                  )}

                  {/* Evidence Tracking Form */}
                  <div className="detail-section-container surface-bench">
                    <div className="container-header bench-header">
                      <span className="container-icon">📋</span>
                      <span className="container-title">Your Collection Record</span>
                      {isTracked && <span className="container-tracking-badge">✓ Active</span>}
                    </div>
                    <div className="container-content">
                      <div className="tracking-toggle-section">
                        <label className="tracking-toggle-label">
                          <input
                            type="checkbox"
                            checked={isTracked}
                            onChange={e => updateEvidenceTracking(evidenceItem.id, 'is_tracked', e.target.checked)}
                            className="tracking-checkbox"
                          />
                          <div className="tracking-toggle-content">
                            <div className="tracking-toggle-title">Evidence Collection Active</div>
                            <div className="tracking-toggle-hint">Mark this evidence as being actively collected for compliance</div>
                          </div>
                        </label>
                      </div>

                      <div className="form-group">
                        <label>Collecting System</label>
                        {(() => {
                          // Smart defaults: suggested systems at top, then divider, then rest
                          const suggestedNames = new Set(
                            (suggestions?.capable_systems || []).map(s => s.name)
                          )
                          const suggestedSystems = systems.filter(s => suggestedNames.has(s.name))
                          const otherSystems = systems.filter(s => !suggestedNames.has(s.name))
                          return (
                            <select
                              value={tracking.collecting_system || ''}
                              onChange={e => updateEvidenceTracking(evidenceItem.id, 'collecting_system', e.target.value)}
                              className="form-control"
                            >
                              <option value="">Select System...</option>
                              {suggestedSystems.length > 0 && (
                                <optgroup label="Suggested for this evidence">
                                  {suggestedSystems.map(system => {
                                    const cap = suggestions?.capable_systems.find(s => s.name === system.name)
                                    return (
                                      <option key={system.id} value={system.name}>
                                        {system.name} ({system.vendor || system.system_type}){cap ? ` \u2014 ${cap.capability_status}` : ''}
                                      </option>
                                    )
                                  })}
                                </optgroup>
                              )}
                              {otherSystems.length > 0 && (
                                <optgroup label="All systems">
                                  {otherSystems.map(system => (
                                    <option key={system.id} value={system.name}>
                                      {system.name} ({system.vendor || system.system_type})
                                    </option>
                                  ))}
                                </optgroup>
                              )}
                              <optgroup label="Other">
                                <option value="Manual">Manual / Not Automated</option>
                              </optgroup>
                            </select>
                          )
                        })()}
                        {suggestions?.recommendation && !tracking.collecting_system && (
                          <div className="form-hint suggestion-inline-hint">
                            {'\u2728'} Recommended: <strong>{suggestions.recommendation.system_name}</strong> — {suggestions.recommendation.reason}
                          </div>
                        )}
                      </div>

                      <div className="form-group">
                        <label>Collection Maturity</label>
                        <MaturityStepper
                          value={tracking.maturity_level}
                          onChange={level => updateEvidenceTracking(evidenceItem.id, 'maturity_level', level)}
                        />
                      </div>

                      {/* Inline Collection Guide — reacts to the selected system and maturity level above */}
                      {tracking.collecting_system && tracking.collecting_system !== 'Manual' && (
                        <div className="inline-collection-guide">
                          {loadingGuidance ? (
                            <div className="inline-guide-loading">Loading collection guide...</div>
                          ) : collectionGuidance?.recipe ? (
                            <details className="inline-guide-details" open>
                              <summary className="inline-guide-summary">
                                <span className="inline-guide-icon">{'\uD83D\uDCD6'}</span>
                                <span>Collection Guide for {collectionGuidance.system_name}</span>
                                <RecipeConfidenceBadge confidence={collectionGuidance.recipe_confidence as RecipeConfidence} />
                              </summary>
                              <div className="inline-guide-content">
                                <RecipeCard
                                  recipe={collectionGuidance.recipe}
                                  confidence={collectionGuidance.recipe_confidence as RecipeConfidence}
                                />
                                <div className="recipe-feedback">
                                  {feedbackSubmitted ? (
                                    <div className="recipe-feedback-thanks">
                                      {'\u2705'} Thanks for your feedback!
                                    </div>
                                  ) : (
                                    <>
                                      <span className="recipe-feedback-label">Was this helpful?</span>
                                      <button
                                        className="recipe-feedback-btn recipe-feedback-yes"
                                        onClick={() => handleRecipeFeedback('helpful')}
                                      >
                                        {'\uD83D\uDC4D'} This helped
                                      </button>
                                      <button
                                        className="recipe-feedback-btn recipe-feedback-no"
                                        onClick={() => handleRecipeFeedback('not_matching')}
                                      >
                                        {'\uD83D\uDC4E'} Didn't match
                                      </button>
                                    </>
                                  )}
                                </div>
                              </div>
                            </details>
                          ) : collectionGuidance && !collectionGuidance.recipe ? (
                            <div className="inline-guide-empty">
                              <span className="inline-guide-icon">{'\uD83D\uDCD6'}</span>
                              No collection recipe available for {collectionGuidance.system_name} at {collectionGuidance.current_maturity}.
                            </div>
                          ) : null}
                        </div>
                      )}

                      {tracking.maturity_level && (
                        <MaturityAdvisoryCard
                          currentLevel={tracking.maturity_level}
                          evidenceId={evidenceItem.id}
                          evidenceTitle={evidenceItem.title}
                          nextLevelRecipe={collectionGuidance?.next_level_preview || undefined}
                          systemName={collectionGuidance?.system_name || undefined}
                        />
                      )}

                      <div className="form-group">
                        <label>Method of Collection</label>
                        <input
                          type="text"
                          value={tracking.method_of_collection || ''}
                          onChange={e => updateEvidenceTracking(evidenceItem.id, 'method_of_collection', e.target.value)}
                          placeholder="e.g., Automated export, Manual review, Screenshot"
                          className="form-control"
                        />
                      </div>

                      <div className="form-row">
                        <div className="form-group">
                          <label>Owner Team</label>
                          <select
                            value={tracking.owner || ''}
                            onChange={e => updateEvidenceTracking(evidenceItem.id, 'owner', e.target.value)}
                            className="form-control"
                          >
                            <option value="">Select Team...</option>
                            <option value="Software Engineering">Software Engineering</option>
                            <option value="Security Operations">Security Operations</option>
                            <option value="DevSecOps">DevSecOps</option>
                            <option value="Cyber Security">Cyber Security</option>
                            <option value="GRC">GRC</option>
                          </select>
                        </div>

                        <div className="form-group">
                          <label>Frequency</label>
                          <input
                            type="text"
                            value={tracking.frequency || ''}
                            onChange={e => updateEvidenceTracking(evidenceItem.id, 'frequency', e.target.value)}
                            placeholder="e.g., Monthly, Quarterly, Annual"
                            className="form-control"
                          />
                        </div>
                      </div>

                      <div className="form-group">
                        <label>Comments</label>
                        <textarea
                          value={tracking.comments || ''}
                          onChange={e => updateEvidenceTracking(evidenceItem.id, 'comments', e.target.value)}
                          placeholder="Additional notes about evidence collection..."
                          className="form-control"
                          rows={3}
                        />
                      </div>
                    </div>
                  </div>

                  {/* Evidence Template Guidance (Issue #326) — collapsed by default; SCF/ERL reference, rendered flat */}
                  <ScfReference>
                    <EvidenceTemplateGuidance
                      evidenceId={selectedEvidenceId}
                      evidenceTemplates={evidenceTemplates}
                      orgId={scopingData.organizationId}
                    />
                  </ScfReference>

                  {/* Tasks, Assignment and Comments */}
                  {(() => {
                    const evidenceTracking = getEvidenceTracking(scopingData, selectedEvidenceId);
                    const evidenceDbId = evidenceTracking?.id;

                    // Only show if we have a database ID (evidence is saved to DB)
                    if (evidenceDbId && scopingData.organizationId) {
                      return (
                        <div className="evidence-collaboration-container">
                          {/* Collection Tasks */}
                          <EvidenceTaskList
                            evidenceTrackingId={evidenceDbId}
                            evidenceId={selectedEvidenceId}
                            organizationId={scopingData.organizationId}
                            onTaskChange={() => {
                              // Optional: trigger data refresh
                            }}
                          />

                          {/* Assignments */}
                          <div className="evidence-collaboration-section">
                            <AssignmentPicker
                              organizationId={scopingData.organizationId}
                              assignableType="evidence"
                              assignableId={evidenceDbId}
                              onAssignmentChange={() => {
                                // Optional: trigger data refresh
                              }}
                            />
                          </div>

                          {/* Comments */}
                          <div className="evidence-collaboration-section">
                            <ModernCommentThread
                              commentableType="evidence"
                              commentableId={evidenceDbId}
                              organizationId={scopingData.organizationId}
                            />
                          </div>
                        </div>
                      );
                    }

                    // Show helpful message if not yet saved
                    return (
                      <div className="evidence-save-hint">
                        <p>
                          Save this evidence tracking to enable tasks, assignments and comments
                        </p>
                      </div>
                    );
                  })()}
                </div>
              </>
            )
          })()
        ) : (
          <div className="empty">
            {viewMode === 'control' ? 'Select a control to review evidence' : 'Select an evidence item to track'}
          </div>
        )}
      </div>

      {/* Collection Wizard Modal */}
      {showCollectionWizard && scopingData.organizationId && (
        <CollectionWizard
          orgId={scopingData.organizationId}
          onClose={() => setShowCollectionWizard(false)}
        />
      )}
    </div>
  )
}
