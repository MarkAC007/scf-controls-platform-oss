import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { FixedSizeList as List, ListChildComponentProps } from 'react-window'
import type {
  ScopedControlsFile,
  ScopedControl,
  ImplementationStatus,
  Priority,
  MaturityLevel,
  ERLFile,
  FrameworkNameMap,
  ResolvedArtifact
} from '../types'
import {
  getScopedControl,
  updateScopedControl,
  getEvidenceTracking,
  loadScopedControls
} from '../data/scopingService'
import { useQueryClient } from '@tanstack/react-query'
import { useScopedControlsQuery, useScopedControlsStats, flattenScopedControlPages } from '../hooks/useScopedControlsQuery'
import { useOrganizationSettings } from '../hooks/useOrganizationSettings'
import { useCatalogFilters } from '../hooks/useCatalogFilters'
import { useDebounce } from '../hooks/useDebounce'
import { AssignmentPicker } from './AssignmentPicker'
import { AuditLogPanel } from './AuditLogPanel'
import { ModernCommentThread } from './ModernCommentThread'
import { ScopeByFrameworkModal } from './ScopeByFrameworkModal'
import MaturityRoadmap from './MaturityRoadmap'
import BusinessSizeGuidance from './BusinessSizeGuidance'
import SCRMFocusBadges from './SCRMFocusBadges'
import RiskThreatContext from './RiskThreatContext'
import { SidebarControlCard } from './SidebarControlCard'
import CDMControlPanel from './CDMControlPanel'
import type { BulkScopeFrameworkResponse, BulkUnscopeFrameworkResponse, ResetScopeResponse, ScopedControlWithCatalog } from '../data/apiClient'

interface ControlScopingProps {
  organizationId: string
  erlData?: ERLFile
  frameworkNames?: FrameworkNameMap
  initialSelectedId?: string
}

// Enriched control type for display (extends API response with resolved data)
interface EnrichedScopedControl extends Omit<ScopedControlWithCatalog, 'cmm_maturity' | 'business_size_guidance'> {
  artifactsResolved: ResolvedArtifact[]
  frameworksResolved: Record<string, string[]>
  frameworksCount: number
  // Override with undefined instead of null (converted during enrichment)
  cmm_maturity: {
    level_0?: string
    level_1?: string
    level_2?: string
    level_3?: string
    level_4?: string
    level_5?: string
  }
  business_size_guidance: {
    micro_small?: string
    small?: string
    medium?: string
    large?: string
    enterprise?: string
  }
}

const ITEM_HEIGHT = 80 // Height of each scoping card in pixels
const DEFAULT_LIST_HEIGHT = 600

// Internal SCF mappings to exclude from framework display
// These are risk/threat codes and internal SCF metadata, not external compliance frameworks
const INTERNAL_MAPPING_PREFIXES = [
  'risk_',      // Risk mappings (R-GV-1, R-AC-1, etc.)
  'threat_',    // Threat mappings (NT-1, MT-1, etc.)
  'scf_core_',  // SCF core profiles
  'control_threat_summary',  // Summary field
  'risk_threat_summary',     // Summary field
  'minimum_security_requirements_mcr_dsr',  // Internal
  'identify_',   // MCR/DSR identification
  'errata_',     // Version errata
]

// Helper function to check if a framework key is internal/should be filtered
function isInternalMapping(frameworkKey: string): boolean {
  return INTERNAL_MAPPING_PREFIXES.some(prefix => frameworkKey.startsWith(prefix))
}

// Type-safe conversion of CMM maturity from API (null) to component format (undefined)
type CMMaturityComponent = EnrichedScopedControl['cmm_maturity']
function convertCmmMaturity(api: ScopedControlWithCatalog['cmm_maturity']): CMMaturityComponent {
  return {
    level_0: api.level_0 ?? undefined,
    level_1: api.level_1 ?? undefined,
    level_2: api.level_2 ?? undefined,
    level_3: api.level_3 ?? undefined,
    level_4: api.level_4 ?? undefined,
    level_5: api.level_5 ?? undefined,
  }
}

// Type-safe conversion of business size guidance from API (null) to component format (undefined)
type BusinessSizeComponent = EnrichedScopedControl['business_size_guidance']
function convertBusinessSizeGuidance(api: ScopedControlWithCatalog['business_size_guidance']): BusinessSizeComponent {
  return {
    micro_small: api.micro_small ?? undefined,
    small: api.small ?? undefined,
    medium: api.medium ?? undefined,
    large: api.large ?? undefined,
    enterprise: api.enterprise ?? undefined,
  }
}

// Default owner teams — used when org settings haven't been configured (#251)
const DEFAULT_OWNER_TEAMS = [
  'Software Engineering',
  'Security Operations',
  'DevSecOps',
  'Cyber Security',
  'GRC',
]

export default function ControlScoping({
  organizationId,
  erlData = {},
  frameworkNames = {},
  initialSelectedId
}: ControlScopingProps) {
  const queryClient = useQueryClient()
  // Internal scoping data — React Query is primary read source for the list,
  // this backs evidence tracking lookups and single-control writes until full
  // migration to per-entity React Query queries.
  const emptyScopingData: ScopedControlsFile = {
    organizationId,
    organization: { id: organizationId, name: '', created_at: '', updated_at: '' },
    scoped_controls: [],
    evidence_tracking: {},
    metadata: { total_controls: 0, total_selected: 0, total_implemented: 0, last_updated: '' }
  }
  const [scopingData, setScopingData] = useState<ScopedControlsFile>(emptyScopingData)
  const onScopingDataChange = (data: ScopedControlsFile | null) => {
    if (data) setScopingData(data)
    // Propagate to the app-wide ['scoping-data'] source of truth so Evidence,
    // Dashboard and Mapping Matrix reflect scope changes without a page reload.
    queryClient.invalidateQueries({ queryKey: ['scoping-data'] })
  }

  // Load scoping data on mount and when org changes
  useEffect(() => {
    loadScopedControls().then(data => {
      if (data) setScopingData(data)
    })
  }, [organizationId])
  const [selectedId, setSelectedId] = useState<string | undefined>(initialSelectedId)
  const [searchQuery, setSearchQuery] = useState('')
  const [domainFilter, setDomainFilter] = useState<string>('all')
  const [csfFilter, setCsfFilter] = useState<string>('all')
  const [weightFilter, setWeightFilter] = useState<string>('all')
  const [frameworkFilter, setFrameworkFilter] = useState<string>('all')
  const [scopeFilter, setScopeFilter] = useState<'all' | 'in_scope' | 'out_of_scope'>('in_scope')
  const [showFilters, setShowFilters] = useState(false)
  const [saving, setSaving] = useState(false)
  const [listHeight, setListHeight] = useState(DEFAULT_LIST_HEIGHT)
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const listContainerRef = useRef<HTMLDivElement>(null)
  const [localFormState, setLocalFormState] = useState<Partial<ScopedControl>>({})
  const [showFrameworkModal, setShowFrameworkModal] = useState(false)
  const [activeTab, setActiveTab] = useState<'details' | 'notes' | 'assignments' | 'history' | 'knowledge-base'>('details')
  const [frameworksCollapsed, setFrameworksCollapsed] = useState(true)
  const [bulkScopeResult, setBulkScopeResult] = useState<(BulkScopeFrameworkResponse | BulkUnscopeFrameworkResponse | ResetScopeResponse) | null>(null)
  const { data: orgSettings } = useOrganizationSettings(organizationId)
  const orgOwnerTeams = orgSettings?.owner_teams?.length ? orgSettings.owner_teams : null

  // Sync selectedId when initialSelectedId prop changes (e.g., navigation from Risk Detail)
  useEffect(() => {
    if (initialSelectedId) {
      setSelectedId(initialSelectedId)
    }
  }, [initialSelectedId])

  // Debounce search input
  const debouncedSearch = useDebounce(searchQuery, 300)

  // Load filter options from API
  const { domains: domainOptions, nistCsfFunctions, controlWeights, isLoading: filtersLoading } = useCatalogFilters()

  // Query scoped controls with server-side filtering
  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading,
    isError,
    refetch,
  } = useScopedControlsQuery({
    search: debouncedSearch || undefined,
    domain: domainFilter !== 'all' ? domainFilter : undefined,
    csf_function: csfFilter !== 'all' ? csfFilter : undefined,
    control_weighting: weightFilter !== 'all' ? parseInt(weightFilter, 10) : undefined,
    framework: frameworkFilter !== 'all' ? frameworkFilter : undefined,
    scope_status: scopeFilter,
  }, organizationId)

  // Server-side stats for accurate totals (fixes #247 - stats bar bug)
  const { data: serverStats, refetch: refetchStats } = useScopedControlsStats(organizationId)

  // Flatten paginated results
  const { controls: rawControls, total } = flattenScopedControlPages(data?.pages)

  // Enrich controls with resolved artifacts and frameworks
  const controls: EnrichedScopedControl[] = useMemo(() => {
    return rawControls.map(control => {
      // Resolve artifacts from ERL
      const artifactsResolved: ResolvedArtifact[] = (control.evidence_requests || [])
        .map(id => {
          const entry = erlData[id]
          if (!entry) return null
          return {
            id,
            title: entry.artifact_title || entry.evidence_title || '',
            domain: entry.area_of_focus || entry.evidence_domain || ''
          }
        })
        .filter(Boolean) as ResolvedArtifact[]

      // Resolve framework names (filtering out internal SCF mappings like risk/threat codes)
      const frameworksResolved: Record<string, string[]> = {}
      let frameworksCount = 0
      for (const [fwRefId, refs] of Object.entries(control.framework_mappings || {})) {
        // Skip internal mappings (risk_, threat_, etc.) - these are shown in RiskThreatContext
        if (isInternalMapping(fwRefId)) {
          continue
        }
        if (Array.isArray(refs) && refs.length > 0) {
          const baseId = fwRefId.endsWith('_ref') ? fwRefId.slice(0, -4) : fwRefId
          const friendly = frameworkNames[baseId] || baseId
          frameworksResolved[friendly] = refs
          frameworksCount += 1
        }
      }

      return {
        ...control,
        // Convert null to undefined for nested objects (API returns null, components expect undefined)
        cmm_maturity: convertCmmMaturity(control.cmm_maturity),
        business_size_guidance: convertBusinessSizeGuidance(control.business_size_guidance),
        artifactsResolved,
        frameworksResolved,
        frameworksCount,
      }
    })
  }, [rawControls, erlData, frameworkNames])

  // Get unique frameworks from loaded controls for filter dropdown
  const frameworks = useMemo(() => {
    const frameworkSet = new Set<string>()
    controls.forEach(control => {
      Object.keys(control.framework_mappings || {}).forEach(fw => {
        frameworkSet.add(fw)
      })
    })
    return Array.from(frameworkSet).sort()
  }, [controls])

  // Clear selection if selected control is not in loaded results
  useEffect(() => {
    if (selectedId && controls.length > 0) {
      const stillInResults = controls.some(c => c.scf_id === selectedId)
      if (!stillInResults) {
        setSelectedId(undefined)
      }
    } else if (selectedId && controls.length === 0) {
      setSelectedId(undefined)
    }
  }, [controls, selectedId])

  // Get stats (based on server totals for in_scope vs API response)
  const stats = useMemo(() => {
    // Top-level stats from server-side endpoint (accurate regardless of pagination)
    const selected = serverStats?.in_scope ?? 0
    const implemented = serverStats?.implemented ?? 0
    const serverTotal = serverStats?.total_controls ?? total

    // Gap analysis from loaded controls (for advanced stats - browsing aid)
    const byDomain: Record<string, { total: number, selected: number, gap: number }> = {}
    const byTheme: Record<string, { total: number, selected: number, gap: number }> = {}

    controls.forEach(control => {
      const isSelected = control.selected

      // Track by domain
      if (!byDomain[control.scf_domain]) {
        byDomain[control.scf_domain] = { total: 0, selected: 0, gap: 0 }
      }
      byDomain[control.scf_domain].total++
      if (isSelected) byDomain[control.scf_domain].selected++

      // Track by theme (NIST CSF function)
      const theme = control.nist_csf_function || 'Unknown'
      if (!byTheme[theme]) {
        byTheme[theme] = { total: 0, selected: 0, gap: 0 }
      }
      byTheme[theme].total++
      if (isSelected) byTheme[theme].selected++
    })

    // Calculate gaps
    Object.keys(byDomain).forEach(key => {
      byDomain[key].gap = byDomain[key].total - byDomain[key].selected
    })
    Object.keys(byTheme).forEach(key => {
      byTheme[key].gap = byTheme[key].total - byTheme[key].selected
    })

    const gap = serverTotal - selected

    return {
      selected,
      implemented,
      gap,
      total: serverTotal,
      loaded: controls.length,
      byDomain,
      byTheme,
    }
  }, [controls, total, serverStats])

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current)
      }
    }
  }, [])

  // Measure list container height on mount and resize
  useEffect(() => {
    const updateHeight = () => {
      if (listContainerRef.current) {
        const rect = listContainerRef.current.getBoundingClientRect()
        setListHeight(Math.max(400, window.innerHeight - rect.top - 40))
      }
    }

    updateHeight()
    window.addEventListener('resize', updateHeight)
    return () => window.removeEventListener('resize', updateHeight)
  }, [])

  // Load more when scrolling near the end
  const handleScroll = useCallback(({ scrollOffset, scrollUpdateWasRequested }: { scrollOffset: number; scrollUpdateWasRequested: boolean }) => {
    if (scrollUpdateWasRequested) return

    const scrollHeight = controls.length * ITEM_HEIGHT
    const scrollThreshold = scrollHeight - listHeight - (ITEM_HEIGHT * 5)

    if (scrollOffset > scrollThreshold && hasNextPage && !isFetchingNextPage) {
      fetchNextPage()
    }
  }, [controls.length, listHeight, hasNextPage, isFetchingNextPage, fetchNextPage])

  // Toggle control selection
  const toggleSelection = async (scf_id: string) => {
    setSaving(true)
    try {
      let scoped = getScopedControl(scopingData, scf_id)
      if (!scoped) {
        scoped = { scf_id, selected: true }
      } else {
        scoped = { ...scoped, selected: !scoped.selected }
      }

      const updated = await updateScopedControl({ ...scopingData }, scoped)
      onScopingDataChange(updated)
      // Refetch to get updated server data
      refetch()
      refetchStats()
    } catch (error) {
      console.error('Failed to toggle selection:', error)
      alert('Failed to save changes. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  // Check if control is selected (from API data since we're server-side filtering)
  const isControlSelected = (scf_id: string): boolean => {
    const control = controls.find(c => c.scf_id === scf_id)
    return control?.selected || false
  }

  // Get current selected control details
  const selectedControl = useMemo(
    () => controls.find(c => c.scf_id === selectedId),
    [controls, selectedId]
  )

  const selectedScopedControl = useMemo(() => {
    if (!selectedId) return null
    return getScopedControl(scopingData, selectedId)
  }, [scopingData, selectedId])

  // Reset to Details tab when switching controls
  useEffect(() => {
    setActiveTab('details')
  }, [selectedId])

  // Sync local form state when switching controls or external data changes
  // Note: `saving` removed from deps to prevent overwriting localFormState
  // when save completes — the save callback already syncs via setLocalFormState(freshControl)
  useEffect(() => {
    if (!selectedId) return
    if (saving) return

    const scoped = getScopedControl(scopingData, selectedId)
    if (scoped) {
      setLocalFormState(scoped)
    } else {
      setLocalFormState({ scf_id: selectedId, selected: false })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, scopingData])

  // Update scoped control field with debounced save
  const updateField = (field: keyof ScopedControl, value: any) => {
    if (!selectedId) return

    setLocalFormState(prev => ({
      ...prev,
      [field]: value
    }))

    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current)
    }

    setSaving(true)
    saveTimeoutRef.current = setTimeout(async () => {
      try {
        let scoped = getScopedControl(scopingData, selectedId)
        if (!scoped) {
          scoped = { scf_id: selectedId, selected: false }
        }

        const updated = await updateScopedControl({ ...scopingData }, {
          ...scoped,
          [field]: value
        })
        onScopingDataChange(updated)
        // Sync local form state with server-computed fields (e.g. completion_date)
        const freshControl = getScopedControl(updated, selectedId)
        if (freshControl) {
          setLocalFormState(freshControl)
        }
        // Refresh stats and left-nav list when scope or status changes
        if (field === 'implementation_status' || field === 'selected') {
          refetchStats()
          refetch()  // Invalidate left-nav infinite query so sidebar reflects changes
        }
      } catch (error) {
        console.error('Failed to update field:', error)
      } finally {
        setSaving(false)
      }
    }, 300)
  }


  // Handle bulk scope/unscope/reset by framework success
  const handleBulkScopeSuccess = async (result: BulkScopeFrameworkResponse | BulkUnscopeFrameworkResponse | ResetScopeResponse) => {
    setBulkScopeResult(result)
    setShowFrameworkModal(false)

    try {
      const freshData = await loadScopedControls()
      if (freshData) {
        onScopingDataChange(freshData)
      }
      refetch()
      refetchStats()
    } catch (error) {
      console.error('Failed to reload scoping data:', error)
    }

    setTimeout(() => setBulkScopeResult(null), 5000)
  }

  // Row renderer for virtualized list
  const Row = useCallback(({ index, style }: ListChildComponentProps) => {
    const control = controls[index]
    if (!control) {
      return (
        <div style={style} className="scoping-card-loading">
          <div className="loading-skeleton" />
        </div>
      )
    }

    return (
      <SidebarControlCard
        style={style}
        scfId={control.scf_id}
        controlName={control.control_name}
        isSelected={selectedId === control.scf_id}
        onSelect={() => setSelectedId(control.scf_id)}
        checkbox={{
          checked: control.selected,
          onChange: () => toggleSelection(control.scf_id),
        }}
        scopeBadge={{ inScope: control.selected }}
        statusBadge={control.implementation_status}
      />
    )
  }, [controls, selectedId, toggleSelection])

  if (isError) {
    return (
      <div className="layout">
        <div className="sidebar">
          <div className="error-message">
            Failed to load controls. Please try refreshing the page.
          </div>
        </div>
        <div className="detail">
          <div className="empty">Unable to load control details</div>
        </div>
      </div>
    )
  }

  return (
    <div className="layout">
      {/* Left Panel - Control List */}
      <div className="sidebar">
        <div className="scoping-sidebar-header">
          <h2 className="panel-header-title">Control Scoping</h2>
          <div className="scoping-stats-modern">
            <div className="scoping-stat-main">
              <div className="scoping-stat-value">
                {isLoading ? '...' : stats.selected}
              </div>
              <div className="scoping-stat-label">
                Controls In Scope
              </div>
            </div>
            <div className="scoping-stat-secondary">
              <div className="scoping-mini-stat">
                <span className="mini-stat-value">{stats.total}</span>
                <span className="mini-stat-label">Total</span>
              </div>
              <div className="scoping-mini-stat">
                <span className="mini-stat-value">{stats.implemented}</span>
                <span className="mini-stat-label">Implemented</span>
              </div>
            </div>
          </div>
          <div className="scoping-progress-mini">
            <div className="scoping-progress-mini-bar">
              <div
                className="scoping-progress-mini-fill"
                style={{ width: `${stats.selected > 0 ? (stats.implemented / stats.selected) * 100 : 0}%` }}
              ></div>
            </div>
          </div>
        </div>

        <div className="bulk-actions">
          <button
            onClick={() => setShowFrameworkModal(true)}
            className="btn btn-small btn-framework"
            title="Add or remove controls by framework"
          >
            📋 Scope by Framework
          </button>
        </div>

        {bulkScopeResult && (
          <div className="bulk-scope-toast">
            <span className="toast-icon">✅</span>
            <span className="toast-message">{bulkScopeResult.message}</span>
            <button className="toast-dismiss" onClick={() => setBulkScopeResult(null)}>×</button>
          </div>
        )}

        <div className="search">
          <input
            type="text"
            placeholder="Search controls by id, name, description…"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
          />
          {debouncedSearch !== searchQuery && (
            <span className="search-indicator">...</span>
          )}
        </div>

        <div className="filter-group">
          <div className="scope-filter-toggle">
            <button
              className={`scope-toggle-btn ${scopeFilter === 'in_scope' ? 'active' : ''}`}
              onClick={() => setScopeFilter('in_scope')}
              title="Show only in-scope controls"
            >
              ✓ In Scope
            </button>
            <button
              className={`scope-toggle-btn ${scopeFilter === 'out_of_scope' ? 'active' : ''}`}
              onClick={() => setScopeFilter('out_of_scope')}
              title="Show only out-of-scope controls"
            >
              ○ Out of Scope
            </button>
            <button
              className={`scope-toggle-btn ${scopeFilter === 'all' ? 'active' : ''}`}
              onClick={() => setScopeFilter('all')}
              title="Show all controls"
            >
              All
            </button>
          </div>
          <button
            className={`filters-toggle-btn ${showFilters ? 'active' : ''}`}
            onClick={() => setShowFilters(!showFilters)}
          >
            ⚙ Filters {(domainFilter !== 'all' || csfFilter !== 'all' || weightFilter !== 'all') && (
              <span className="filter-badge">
                {[domainFilter !== 'all', csfFilter !== 'all', weightFilter !== 'all'].filter(Boolean).length}
              </span>
            )}
          </button>
          {showFilters && (
            <div className="filters-dropdown">
              <select
                value={domainFilter}
                onChange={e => setDomainFilter(e.target.value)}
                className="filter-select"
                disabled={filtersLoading}
              >
                <option value="all">All Domains</option>
                {domainOptions.map((domain) => (
                  <option key={domain.value} value={domain.value}>
                    {domain.label}
                  </option>
                ))}
              </select>
              <select
                value={csfFilter}
                onChange={e => setCsfFilter(e.target.value)}
                className="filter-select"
              >
                <option value="all">All NIST CSF Functions</option>
                {nistCsfFunctions.map((csf) => (
                  <option key={csf.value} value={csf.value}>
                    {csf.label}
                  </option>
                ))}
              </select>
              <select
                value={weightFilter}
                onChange={e => setWeightFilter(e.target.value)}
                className="filter-select"
              >
                <option value="all">All Control Weights</option>
                {controlWeights.map((weight) => (
                  <option key={weight.value} value={weight.value}>
                    {weight.label}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        <div className="list" ref={listContainerRef}>
          {isLoading ? (
            <div className="loading-controls">
              <div className="loading-spinner-small" />
              <span>Loading controls...</span>
            </div>
          ) : controls.length === 0 ? (
            <div className="no-results">
              No controls match your filter criteria.
            </div>
          ) : (
            <>
              <List
                height={listHeight}
                itemCount={controls.length}
                itemSize={ITEM_HEIGHT}
                width="100%"
                onScroll={handleScroll}
                className="virtualized-list"
              >
                {Row}
              </List>
              {isFetchingNextPage && (
                <div className="loading-more">
                  Loading more controls...
                </div>
              )}
            </>
          )}
        </div>

        {saving && (
          <div className="save-indicator">💾 Saving...</div>
        )}
      </div>

      {/* Right Panel - Implementation Details */}
      <div className="detail">
        {selectedControl ? (
          <>
            <div className="detail-header-redesign surface-bedrock" data-source="SCF Reference">
              <span className="scf-source-tag">SCF Catalog</span>
              <div className="detail-header-badges">
                <span className="scf-id-pill">{selectedControl.scf_id}</span>
                {selectedControl.implementation_status && (
                  <span className={`status-badge-compact status-${selectedControl.implementation_status}`}>
                    {selectedControl.implementation_status.replace('_', ' ')}
                  </span>
                )}
                <div className="cadence-row">
                  <span className="cadence-label">Domain:</span>
                  <span className="cadence-badge">{selectedControl.scf_domain}</span>
                  {selectedControl.validation_cadence && (
                    <>
                      <span className="cadence-label">Validation Cadence:</span>
                      <span className="cadence-badge">{selectedControl.validation_cadence}</span>
                    </>
                  )}
                </div>
              </div>
              <h1 className="control-title">{selectedControl.control_name}</h1>

              {/* 3-column: description left, widgets center, SCRM right */}
              <div className="detail-header-split">
                <div className="detail-header-left">
                  <p className="control-description">{selectedControl.control_description}</p>
                  {selectedControl.control_question && (
                    <div className="assessment-question-block">
                      <div className="assessment-question-label">Assessment Question</div>
                      <blockquote className="assessment-question-text">
                        "{selectedControl.control_question}"
                      </blockquote>
                    </div>
                  )}
                </div>
                <div className="detail-header-right">
                  <div className="detail-widget-group">
                    <div className="detail-widget-group-label">Classification</div>
                    <div className="detail-widget-group-items">
                      {selectedControl.nist_csf_function && (
                        <div className={`detail-widget theme-${selectedControl.nist_csf_function.toLowerCase()}`}>
                          <span className="detail-widget-value">{selectedControl.nist_csf_function}</span>
                          <span className="detail-widget-label">CSF Function</span>
                        </div>
                      )}
                      {selectedControl.control_weighting && (
                        <div className="detail-widget widget-weight">
                          <span className="detail-widget-value">{selectedControl.control_weighting}</span>
                          <span className="detail-widget-label">Weight</span>
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="detail-widget-group">
                    <div className="detail-widget-group-label">Coverage</div>
                    <div className="detail-widget-group-items">
                      <div className="detail-widget widget-count">
                        <span className="detail-widget-value">{selectedControl.frameworksCount}</span>
                        <span className="detail-widget-label">Frameworks</span>
                      </div>
                      <div className="detail-widget widget-count">
                        <span className="detail-widget-value">{selectedControl.artifactsResolved.length}</span>
                        <span className="detail-widget-label">Artifacts</span>
                      </div>
                    </div>
                  </div>
                </div>
                <div className="detail-header-scrm">
                  <SCRMFocusBadges focus={selectedControl.scrm_focus} />
                </div>
              </div>
            </div>

            <div className="detail-content-compact">
              {/* SCF-derived guidance — reference material, rendered flat */}
              <div className="surface-bedrock">
                {/* Risk & Threat Context — full width */}
                <RiskThreatContext mapping={selectedControl.risk_threat_mapping} />

                {/* Maturity + Right-Sizing — side by side */}
                <div className="scoping-card-grid">
                  <MaturityRoadmap maturity={selectedControl.cmm_maturity} targetLevel={localFormState?.maturity_level || null} />
                  <BusinessSizeGuidance guidance={selectedControl.business_size_guidance} />
                </div>
              </div>

              {/* ── Tab Navigation (#254) — above Framework Mappings ── */}
              <div className="detail-tabs">
                <button
                  className={`detail-tab ${activeTab === 'details' ? 'active' : ''}`}
                  onClick={() => setActiveTab('details')}
                >
                  DETAILS
                </button>
                <button
                  className={`detail-tab ${activeTab === 'notes' ? 'active' : ''}`}
                  onClick={() => setActiveTab('notes')}
                >
                  NOTES & HISTORY
                </button>
                <button
                  className={`detail-tab ${activeTab === 'assignments' ? 'active' : ''}`}
                  onClick={() => setActiveTab('assignments')}
                >
                  ASSIGNMENTS
                </button>
                <button
                  className={`detail-tab ${activeTab === 'history' ? 'active' : ''}`}
                  onClick={() => setActiveTab('history')}
                >
                  AUDIT ARTIFACTS
                </button>
                <button
                  className={`detail-tab ${activeTab === 'knowledge-base' ? 'active' : ''}`}
                  onClick={() => setActiveTab('knowledge-base')}
                >
                  KNOWLEDGE BASE
                </button>
              </div>

              {/* ── Tab: Details ── */}
              {activeTab === 'details' && (
                <div className="detail-section-container surface-bench">
                  <div className="container-header bench-header">
                    <span className="container-title">Your Implementation Record</span>
                  </div>
                  <div className="container-content">

                    <div className="form-group">
                      <label>
                        <input
                          type="checkbox"
                          checked={isControlSelected(selectedControl.scf_id)}
                          onChange={() => toggleSelection(selectedControl.scf_id)}
                        />
                        <strong> Include this control in scope</strong>
                      </label>
                    </div>

                    <div className="form-group">
                      <label>Implementation Status</label>
                      <select
                        value={localFormState?.implementation_status || 'not_started'}
                        onChange={e => updateField('implementation_status', e.target.value as ImplementationStatus)}
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

                    <div className="form-group">
                      <label>Priority</label>
                      <select
                        value={localFormState?.priority || 'medium'}
                        onChange={e => updateField('priority', e.target.value as Priority)}
                        className="form-control"
                      >
                        <option value="critical">Critical</option>
                        <option value="high">High</option>
                        <option value="medium">Medium</option>
                        <option value="low">Low</option>
                      </select>
                    </div>

                    <div className="form-group">
                      <label>Control Maturity Level (SCF C|P-CMM)</label>
                      <select
                        value={localFormState?.maturity_level || ''}
                        onChange={e => updateField('maturity_level', e.target.value as MaturityLevel)}
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

                    {/* #252: Applicability Statement (formerly Selection Reason) — positioned near scope toggle */}
                    <div className="form-group">
                      <label>
                        {isControlSelected(selectedControl.scf_id) ? 'Applicability Statement' : 'Exclusion Rationale'}
                      </label>
                      <span className="form-hint-block">
                        {isControlSelected(selectedControl.scf_id)
                          ? 'This text appears in your Statement of Applicability (SOA)'
                          : 'Auditors will ask why this control was excluded'}
                      </span>
                      <textarea
                        value={localFormState?.selection_reason || ''}
                        onChange={e => updateField('selection_reason', e.target.value)}
                        placeholder={isControlSelected(selectedControl.scf_id)
                          ? 'Why is this control in scope? Which frameworks require it?'
                          : 'Why is this control excluded from scope?'}
                        className="form-control"
                        rows={3}
                      />
                      <span className={`char-counter${(localFormState?.selection_reason || '').length > 120 ? ' warning' : ''}`}>
                        {(localFormState?.selection_reason || '').length}/120 chars
                        {(localFormState?.selection_reason || '').length > 120 && ' — SOA will truncate'}
                      </span>
                    </div>

                    <div className="form-group">
                      <label>Owner Team</label>
                      <select
                        value={localFormState?.owner || ''}
                        onChange={e => updateField('owner', e.target.value)}
                        className="form-control"
                      >
                        <option value="">Select Team...</option>
                        {(orgOwnerTeams || DEFAULT_OWNER_TEAMS).map(team => (
                          <option key={team} value={team}>{team}</option>
                        ))}
                      </select>
                    </div>

                    {/* #255: Target Date — shown for non-implemented controls */}
                    {['not_started', 'in_progress', 'at_risk', 'deferred'].includes(localFormState?.implementation_status || 'not_started') && (
                      <div className="form-group">
                        <label>Target Date</label>
                        <input
                          type="date"
                          value={localFormState?.target_date || ''}
                          onChange={e => updateField('target_date', e.target.value)}
                          className="form-control"
                        />
                      </div>
                    )}

                    {/* #250: Completion Date — read-only, auto-set by backend on status transitions */}
                    {localFormState?.completion_date && (
                      <div className="form-group">
                        <label>Completed</label>
                        <span className="form-control form-control-readonly">
                          {new Date(localFormState.completion_date + 'T00:00:00').toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })}
                        </span>
                      </div>
                    )}

                  </div>
                </div>
              )}

              {/* ── Tab: Notes & History ── */}
              {activeTab === 'notes' && (
                <>
                  <div className="detail-section-container surface-bench">
                    <div className="container-header bench-header">
                      <span className="container-title">Your Implementation Notes</span>
                    </div>
                    <div className="container-content">
                      <div className="form-group">
                        <textarea
                          value={localFormState?.implementation_notes || ''}
                          onChange={e => updateField('implementation_notes', e.target.value)}
                          placeholder="How is this control implemented? What tools or processes are used?"
                          className="form-control"
                          rows={6}
                        />
                      </div>
                    </div>
                  </div>

                  {(() => {
                    const scopedControl = getScopedControl(scopingData, selectedControl.scf_id);
                    const controlDbId = scopedControl?.id;

                    if (controlDbId && organizationId) {
                      return (
                        <div className="scoping-comments-section">
                          <ModernCommentThread
                            commentableType="control"
                            commentableId={controlDbId}
                            organizationId={organizationId}
                          />
                        </div>
                      );
                    }

                    return (
                      <div className="scoping-save-hint">
                        <p>
                          Save this control to enable comments
                        </p>
                      </div>
                    );
                  })()}

                  <div className="detail-section-container">
                    <div className="container-header">
                      <span className="container-icon">📋</span>
                      <span className="container-title">Change History</span>
                    </div>
                    <div className="container-content">
                      <AuditLogPanel
                        scfId={selectedControl.scf_id}
                        organizationId={organizationId}
                      />
                    </div>
                  </div>
                </>
              )}

              {/* ── Tab: Assignments ── */}
              {activeTab === 'assignments' && (
                <div className="detail-section-container surface-bench">
                  <div className="container-header bench-header">
                    <span className="container-title">Your Assignments</span>
                  </div>
                  <div className="container-content">
                    {(() => {
                      const scopedCtrl = getScopedControl(scopingData, selectedControl.scf_id);
                      const ctrlDbId = scopedCtrl?.id;
                      if (ctrlDbId && organizationId) {
                        return (
                          <AssignmentPicker
                            organizationId={organizationId}
                            assignableType="control"
                            assignableId={ctrlDbId}
                            onAssignmentChange={() => {}}
                          />
                        );
                      }
                      return <span className="form-hint">Save control to enable assignment</span>;
                    })()}
                  </div>
                </div>
              )}

              {activeTab === 'history' && (
                <div className="detail-section-container">
                  <div className="container-header">
                    <span className="container-icon">📋</span>
                    <span className="container-title">Audit Artifacts</span>
                    <span className="container-count">{selectedControl.artifactsResolved.length}</span>
                    {selectedControl.artifactsResolved.length > 0 && (() => {
                      const trackedCount = selectedControl.artifactsResolved.filter(a =>
                        getEvidenceTracking(scopingData, a.id)?.is_tracked
                      ).length
                      const total = selectedControl.artifactsResolved.length
                      const percentage = Math.round((trackedCount / total) * 100)
                      return (
                        <span className="container-tracking-badge">
                          {trackedCount}/{total} tracked ({percentage}%)
                        </span>
                      )
                    })()}
                  </div>
                  <div className="container-content">
                    {selectedControl.artifactsResolved.length === 0 ? (
                      <div className="muted">No artifacts listed</div>
                    ) : (
                      <div className="artifact-list-compact">
                        {Object.entries(
                          selectedControl.artifactsResolved.reduce((groups, artifact) => {
                            if (!groups[artifact.domain]) groups[artifact.domain] = []
                            groups[artifact.domain].push(artifact)
                            return groups
                          }, {} as Record<string, typeof selectedControl.artifactsResolved>)
                        ).map(([domain, artifacts]) => (
                          <div key={domain} className="artifact-domain-group">
                            <div className="artifact-domain-title">{domain}</div>
                            <div className="artifact-items">
                              {artifacts.map(artifact => {
                                const evidenceTracking = getEvidenceTracking(scopingData, artifact.id)
                                const isTracked = evidenceTracking?.is_tracked || false

                                return (
                                  <div key={artifact.id} className="artifact-item-compact">
                                    <span className="artifact-status-indicator-compact">
                                      {isTracked ? '✅' : '⚪'}
                                    </span>
                                    <span className="artifact-id-badge">{artifact.id}</span>
                                    <span className="artifact-title-text">{artifact.title}</span>
                                    {isTracked && evidenceTracking?.collecting_system && (
                                      <span className="artifact-system-tag">{evidenceTracking.collecting_system}</span>
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

              {activeTab === 'knowledge-base' && (
                <CDMControlPanel
                  organizationId={organizationId}
                  scopedControlId={
                    scopingData?.scoped_controls.find(
                      (sc) => sc.scf_id === selectedControl.scf_id,
                    )?.id
                  }
                  controlName={selectedControl.control_name}
                  controlDescription={selectedControl.control_description}
                />
              )}

              {/* Framework Mappings — collapsible, collapsed by default */}
              <div className={`detail-section-container${frameworksCollapsed ? ' collapsed' : ''}`}>
                <div
                  className="container-header collapsible"
                  onClick={() => setFrameworksCollapsed(prev => !prev)}
                >
                  <span className="container-icon">🔗</span>
                  <span className="container-title">Framework Mappings</span>
                  <span className="container-count">{Object.keys(selectedControl.frameworksResolved).length}</span>
                  <span className="collapse-indicator">{frameworksCollapsed ? '▶' : '▼'}</span>
                </div>
                {!frameworksCollapsed && (
                  <div className="container-content">
                    {Object.keys(selectedControl.frameworksResolved).length === 0 ? (
                      <div className="muted">No mappings listed</div>
                    ) : (
                      <div className="framework-list-compact">
                        {Object.entries(selectedControl.frameworksResolved).map(([fw, refs]) => (
                          <div key={fw} className="framework-item-compact">
                            <div className="framework-name-compact">{fw}</div>
                            <div className="framework-refs">
                              {refs.map((ref, i) => (
                                <span key={`${ref}-${i}`} className="ref-chip">{ref}</span>
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
          </>
        ) : (
          <div className="empty">Select a control to view implementation details</div>
        )}
      </div>

      {/* Scope by Framework Modal */}
      {showFrameworkModal && (
        <ScopeByFrameworkModal
          organizationId={organizationId}
          existingScopedCount={stats.selected}
          onClose={() => setShowFrameworkModal(false)}
          onSuccess={handleBulkScopeSuccess}
        />
      )}
    </div>
  )
}
