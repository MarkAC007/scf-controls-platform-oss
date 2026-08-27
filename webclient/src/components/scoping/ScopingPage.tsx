/**
 * ScopingPage — container for the Control Scoping tab.
 *
 * Migrates all container logic from ControlScoping.tsx:
 *   - Data loading (scopingData, loadScopedControls)
 *   - Enrichment (artifacts from ERL, frameworksResolved with INTERNAL_MAPPING_PREFIXES)
 *   - updateField (300 ms debounce → updateScopedControl → onScopingDataChange)
 *   - toggleSelection (scope checkbox in detail)
 *   - ScopeByFrameworkModal + handleBulkScopeSuccess
 *   - Team assignments batch loading (accountableTeamFor → ownerByControlId map)
 *   - reloadTeamAssignments
 *   - navigateToId effect (resets 9 filters, seeds search, page-walks ≤ MAX_NAV_PAGE_FETCHES)
 *   - initialSelectedId: list context only (does NOT auto-open detail; navigateToId opens detail)
 *
 * Selection state is in-page: selectedId → renders ScopingDetailPage full-width;
 * Back → list with filters/scroll preserved (no URL contract for scoping).
 *
 * Bulk actions (ruling 1): sequential loop over updateScopedControl with progress
 * callback, toast summary, then refetch list + stats + onScopingDataChange.
 */
import { useState, useEffect, useMemo, useRef, useCallback, type JSX } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'

import type {
  ScopedControlsFile,
  ScopedControl,
  OwnerTeam,
  ERLFile,
  FrameworkNameMap,
  ResolvedArtifact,
} from '../../types'
import {
  loadScopedControls,
  getScopedControl,
  updateScopedControl,
} from '../../data/scopingService'
import {
  useScopedControlsQuery,
  useScopedControlsStats,
  flattenScopedControlPages,
} from '../../hooks/useScopedControlsQuery'
import { useOrganizationSettings } from '../../hooks/useOrganizationSettings'
import { useTeamAssignments, accountableTeamLabel } from '../../hooks/useTeamAssignments'
import { useIsOrgAdmin } from '../../hooks/useIsOrgAdmin'
import { useDebounce } from '../../hooks/useDebounce'
import type { BulkScopeFrameworkResponse, BulkUnscopeFrameworkResponse, ResetScopeResponse, ScopedControlWithCatalog } from '../../data/apiClient'
import { ScopeByFrameworkModal } from '../ScopeByFrameworkModal'

import ScopingList, { type ScopingFilters } from './ScopingList'
import ScopingBulkBar from './ScopingBulkBar'
import ScopingDetailPage, {
  type ScopingDetailControl,
  type ScopingEntry,
} from './ScopingDetailPage'

// ─── Constants ────────────────────────────────────────────────────────────────

const MAX_NAV_PAGE_FETCHES = 5

/**
 * Internal SCF mapping prefixes to exclude from framework display.
 * Keep in sync with ControlScoping.tsx and ScopingDetailPage.tsx.
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

const DEFAULT_FILTERS: ScopingFilters = {
  scope: 'in_scope',
  domain: 'all',
  csf: 'all',
  weight: 'all',
  framework: 'all',
  teamId: 'all',
  functionId: 'all',
  ownerType: 'all',
}

const DEFAULT_OWNER_TEAMS = [
  'Software Engineering',
  'Security Operations',
  'DevSecOps',
  'Cyber Security',
  'GRC',
]

// ─── Types ────────────────────────────────────────────────────────────────────

export interface ScopingPageProps {
  organizationId: string
  erlData?: ERLFile
  frameworkNames?: FrameworkNameMap
  /**
   * Restore list context on mount (does NOT auto-open detail).
   * Only navigateToId or a row click opens the detail view.
   */
  initialSelectedId?: string
  /**
   * A control the user asked to be taken to from elsewhere in the app.
   * Resets filters per ControlScoping behavior, seeds search to target id,
   * page-walks up to MAX_NAV_PAGE_FETCHES, then opens the detail.
   * Cleared by the parent via onNavigationConsumed.
   */
  navigateToId?: string
  /** Called once navigateToId has been acted on, so the parent can clear it. */
  onNavigationConsumed?: () => void
  /**
   * App-level scoping data (used for evidence tracking in detail page).
   * When provided, the page uses it directly; otherwise it loads its own.
   */
  scopingData?: ScopedControlsFile | null
  /** Called when scoping data changes (e.g. after a field update). */
  onScopingDataChange?: (data: ScopedControlsFile) => void
}

// ─── Enrichment helpers ────────────────────────────────────────────────────────

function convertCmmMaturity(api: ScopedControlWithCatalog['cmm_maturity']) {
  return {
    level_0: api.level_0 ?? undefined,
    level_1: api.level_1 ?? undefined,
    level_2: api.level_2 ?? undefined,
    level_3: api.level_3 ?? undefined,
    level_4: api.level_4 ?? undefined,
    level_5: api.level_5 ?? undefined,
  }
}

function convertBusinessSizeGuidance(api: ScopedControlWithCatalog['business_size_guidance']) {
  return {
    micro_small: api.micro_small ?? undefined,
    small: api.small ?? undefined,
    medium: api.medium ?? undefined,
    large: api.large ?? undefined,
    enterprise: api.enterprise ?? undefined,
  }
}

function enrichControl(
  control: ScopedControlWithCatalog,
  erlData: ERLFile,
  frameworkNames: FrameworkNameMap,
): ScopingDetailControl {
  // Resolve artifacts from ERL
  const artifactsResolved: ResolvedArtifact[] = (control.evidence_requests || [])
    .map((id) => {
      const entry = erlData[id]
      if (!entry) return null
      return {
        id,
        title: entry.artifact_title || entry.evidence_title || '',
        domain: entry.area_of_focus || entry.evidence_domain || '',
      }
    })
    .filter(Boolean) as ResolvedArtifact[]

  // Resolve framework names (filter internal mappings)
  const frameworksResolved: Record<string, string[]> = {}
  let frameworksCount = 0
  for (const [fwRefId, refs] of Object.entries(control.framework_mappings || {})) {
    if (isInternalMapping(fwRefId)) continue
    if (Array.isArray(refs) && refs.length > 0) {
      const baseId = fwRefId.endsWith('_ref') ? fwRefId.slice(0, -4) : fwRefId
      const friendly = frameworkNames[baseId] || baseId
      frameworksResolved[friendly] = refs
      frameworksCount += 1
    }
  }

  return {
    ...control,
    cmm_maturity: convertCmmMaturity(control.cmm_maturity),
    business_size_guidance: convertBusinessSizeGuidance(control.business_size_guidance),
    artifactsResolved,
    frameworksResolved,
    frameworksCount,
  } as ScopingDetailControl
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function ScopingPage({
  organizationId,
  erlData = {},
  frameworkNames = {},
  initialSelectedId,
  navigateToId,
  onNavigationConsumed,
  scopingData: propScopingData,
  onScopingDataChange,
}: ScopingPageProps): JSX.Element {
  const queryClient = useQueryClient()

  // ── Scoping data (internal copy for evidence tracking / writes) ────────────
  const emptyScopingData: ScopedControlsFile = useMemo(
    () => ({
      organizationId,
      organization: { id: organizationId, name: '', created_at: '', updated_at: '' },
      scoped_controls: [],
      evidence_tracking: {},
      metadata: { total_controls: 0, total_selected: 0, total_implemented: 0, last_updated: '' },
    }),
    [organizationId],
  )

  const [internalScopingData, setInternalScopingData] = useState<ScopedControlsFile>(
    propScopingData ?? emptyScopingData,
  )

  // Sync internal copy when prop changes (e.g. App-level cache update)
  useEffect(() => {
    if (propScopingData) setInternalScopingData(propScopingData)
  }, [propScopingData])

  // Effective scoping data (prop takes precedence for reads; internal for writes)
  const scopingData = propScopingData ?? internalScopingData

  const handleScopingDataChange = useCallback(
    (data: ScopedControlsFile | null) => {
      if (!data) return
      setInternalScopingData(data)
      onScopingDataChange?.(data)
      // Propagate to the ['scoping-data'] query cache
      queryClient.invalidateQueries({ queryKey: ['scoping-data'] })
    },
    [queryClient, onScopingDataChange],
  )

  // Load scoping data on mount and when org changes
  useEffect(() => {
    loadScopedControls().then((data) => {
      if (data) handleScopingDataChange(data)
    })
  }, [organizationId]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── In-page selection state ────────────────────────────────────────────────
  // initialSelectedId is NOT used to seed selectedId — it must not auto-open detail.
  // Detail opens only via navigateToId (cross-app deep-link) or a row click.
  const [selectedId, setSelectedId] = useState<string | undefined>(undefined)

  // ── Filter + search state ──────────────────────────────────────────────────
  const [filters, setFilters] = useState<ScopingFilters>(DEFAULT_FILTERS)
  const [search, setSearch] = useState('')
  const [scrollOffset, setScrollOffset] = useState(0)
  const debouncedSearch = useDebounce(search, 300)

  // ── Bulk row selection ─────────────────────────────────────────────────────
  const [rowSelection, setRowSelection] = useState<Set<string>>(new Set())

  // ── Modal state ────────────────────────────────────────────────────────────
  const [showFrameworkModal, setShowFrameworkModal] = useState(false)

  // ── Navigation state ───────────────────────────────────────────────────────
  const [pendingNavId, setPendingNavId] = useState<string | undefined>(navigateToId)
  const navPageFetchesRef = useRef(0)

  // ── Debounce save ─────────────────────────────────────────────────────────
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── Bulk action state ─────────────────────────────────────────────────────
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkProgress, setBulkProgress] = useState('')

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current)
    }
  }, [])

  // ── Paginated query ────────────────────────────────────────────────────────
  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading,
    isFetching,
    refetch,
  } = useScopedControlsQuery(
    {
      search: debouncedSearch || undefined,
      domain: filters.domain !== 'all' ? filters.domain : undefined,
      csf_function: filters.csf !== 'all' ? filters.csf : undefined,
      control_weighting: filters.weight !== 'all' ? parseInt(filters.weight, 10) : undefined,
      framework: filters.framework !== 'all' ? filters.framework : undefined,
      scope_status: filters.scope,
      team_id: filters.teamId !== 'all' ? filters.teamId : undefined,
      function_id: filters.functionId !== 'all' ? filters.functionId : undefined,
      accountable_owner_type:
        filters.ownerType !== 'all'
          ? (filters.ownerType as 'internal' | 'external_contractor')
          : undefined,
    },
    organizationId,
  )

  const { data: serverStats, refetch: refetchStats } = useScopedControlsStats(organizationId)

  const { controls: rawControls, total } = flattenScopedControlPages(data?.pages)

  // ── Enrich controls ────────────────────────────────────────────────────────
  const enrichedControls = useMemo(
    () => rawControls.map((c) => enrichControl(c, erlData, frameworkNames)),
    [rawControls, erlData, frameworkNames],
  )

  // ── Team assignments for owner column ─────────────────────────────────────
  const scopedDbIdByScfId = useMemo(() => {
    const map = new Map<string, string>()
    for (const scoped of scopingData.scoped_controls) {
      if (scoped.id) map.set(scoped.scf_id, scoped.id)
    }
    return map
  }, [scopingData])

  const loadedControlDbIds = useMemo(
    () =>
      rawControls
        .map((c) => scopedDbIdByScfId.get(c.scf_id))
        .filter((id): id is string => !!id),
    [rawControls, scopedDbIdByScfId],
  )

  const { accountableFor: accountableTeamFor, reload: reloadTeamAssignments } =
    useTeamAssignments(organizationId, 'control', { itemIds: loadedControlDbIds })

  // Build ownerByControlId map: scf_id → label string (or '' when unset)
  const ownerByControlId = useMemo(() => {
    const map: Record<string, string> = {}
    for (const c of rawControls) {
      const dbId = scopedDbIdByScfId.get(c.scf_id)
      const label = accountableTeamLabel(accountableTeamFor(dbId ?? ''))
      map[c.scf_id] = label ?? ''
    }
    return map
  }, [rawControls, scopedDbIdByScfId, accountableTeamFor])

  const canManageTeams = useIsOrgAdmin(organizationId)

  // ── Org settings (owner teams) ─────────────────────────────────────────────
  const { data: orgSettings } = useOrganizationSettings(organizationId)
  const orgOwnerTeams = orgSettings?.owner_teams?.length ? orgSettings.owner_teams : null
  const ownerTeams = orgOwnerTeams ?? DEFAULT_OWNER_TEAMS

  // Owner options for bulk assign (same source as detail page)
  const ownerOptions = useMemo(
    () => ownerTeams.map((t) => ({ value: t, label: t })),
    [ownerTeams],
  )

  // ── NavigateToId effect ────────────────────────────────────────────────────
  useEffect(() => {
    if (!navigateToId) return
    navPageFetchesRef.current = 0
    setPendingNavId(navigateToId)
    setSelectedId(navigateToId)
    setSearch(navigateToId)
    setFilters(DEFAULT_FILTERS)
    setFilters((prev) => ({ ...prev, scope: 'all' }))
  }, [navigateToId])

  // Resolve pending nav once the target lands in results
  useEffect(() => {
    if (!pendingNavId) return
    if (debouncedSearch !== pendingNavId) return
    if (isLoading || isFetching || isFetchingNextPage) return

    const index = enrichedControls.findIndex((c) => c.scf_id === pendingNavId)
    if (index !== -1) {
      setSelectedId(pendingNavId)
      setPendingNavId(undefined)
      onNavigationConsumed?.()
      return
    }

    if (hasNextPage && navPageFetchesRef.current < MAX_NAV_PAGE_FETCHES) {
      navPageFetchesRef.current += 1
      fetchNextPage()
      return
    }

    // Not found — give up
    setPendingNavId(undefined)
    setSelectedId(undefined)
    onNavigationConsumed?.()
  }, [
    enrichedControls,
    pendingNavId,
    debouncedSearch,
    isLoading,
    isFetching,
    isFetchingNextPage,
    hasNextPage,
    fetchNextPage,
    onNavigationConsumed,
  ])

  // Clear selection if selected control disappears from results (unless pending nav)
  useEffect(() => {
    if (pendingNavId) return
    if (selectedId && enrichedControls.length > 0) {
      const stillInResults = enrichedControls.some((c) => c.scf_id === selectedId)
      if (!stillInResults) setSelectedId(undefined)
    } else if (selectedId && enrichedControls.length === 0 && !isLoading) {
      setSelectedId(undefined)
    }
  }, [enrichedControls, selectedId, pendingNavId, isLoading])

  // ── Pager position ────────────────────────────────────────────────────────
  const selectedControl = useMemo(
    () => enrichedControls.find((c) => c.scf_id === selectedId),
    [enrichedControls, selectedId],
  )

  const selectedIndex = useMemo(
    () => (selectedId ? enrichedControls.findIndex((c) => c.scf_id === selectedId) : -1),
    [enrichedControls, selectedId],
  )

  const position = useMemo(() => {
    if (!selectedId) return null
    return {
      index: selectedIndex >= 0 ? selectedIndex : null,
      total: total,
    }
  }, [selectedId, selectedIndex, total])

  const handlePrev = useCallback(() => {
    if (selectedIndex > 0) {
      setSelectedId(enrichedControls[selectedIndex - 1].scf_id)
    }
  }, [enrichedControls, selectedIndex])

  const handleNext = useCallback(() => {
    if (selectedIndex < enrichedControls.length - 1) {
      setSelectedId(enrichedControls[selectedIndex + 1].scf_id)
    }
  }, [enrichedControls, selectedIndex])

  const handleBack = useCallback(() => {
    setSelectedId(undefined)
  }, [])

  // ── toggleScope (scope checkbox in detail) ─────────────────────────────────
  const toggleScope = useCallback(
    async (scf_id: string) => {
      let scoped = getScopedControl(scopingData, scf_id)
      if (!scoped) {
        scoped = { scf_id, selected: true }
      } else {
        scoped = { ...scoped, selected: !scoped.selected }
      }
      try {
        const updated = await updateScopedControl({ ...scopingData }, scoped)
        handleScopingDataChange(updated)
        refetch()
        refetchStats()
      } catch (err) {
        console.error('Failed to toggle scope:', err)
      }
    },
    [scopingData, handleScopingDataChange, refetch, refetchStats],
  )

  // ── updateField (300 ms debounce) ─────────────────────────────────────────
  const updateField = useCallback(
    (field: string, value: unknown) => {
      if (!selectedId) return
      if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current)
      saveTimeoutRef.current = setTimeout(async () => {
        try {
          let scoped = getScopedControl(scopingData, selectedId)
          if (!scoped) scoped = { scf_id: selectedId, selected: false }
          const updated = await updateScopedControl({ ...scopingData }, {
            ...scoped,
            [field]: value,
          } as ScopedControl)
          handleScopingDataChange(updated)
          if (field === 'implementation_status' || field === 'selected') {
            refetch()
            refetchStats()
          }
        } catch (err) {
          console.error('Failed to update field:', err)
        }
      }, 300)
    },
    [selectedId, scopingData, handleScopingDataChange, refetch, refetchStats],
  )

  // ── ScopeByFrameworkModal success ─────────────────────────────────────────
  const handleBulkScopeSuccess = useCallback(
    async (_result: BulkScopeFrameworkResponse | BulkUnscopeFrameworkResponse | ResetScopeResponse) => {
      setShowFrameworkModal(false)
      try {
        const freshData = await loadScopedControls()
        if (freshData) handleScopingDataChange(freshData)
        refetch()
        refetchStats()
      } catch (err) {
        console.error('Failed to reload after framework scope:', err)
      }
    },
    [handleScopingDataChange, refetch, refetchStats],
  )

  // ── Bulk row actions ───────────────────────────────────────────────────────

  type BulkActionType = 'applicable' | 'na' | 'owner'

  const runBulkAction = useCallback(
    async (actionType: BulkActionType, ownerValue?: string) => {
      const scfIds = Array.from(rowSelection)
      if (scfIds.length === 0) return

      setBulkBusy(true)
      setBulkProgress(`Updating 0 of ${scfIds.length}…`)

      let successCount = 0
      let failCount = 0

      for (let i = 0; i < scfIds.length; i++) {
        const scf_id = scfIds[i]
        setBulkProgress(`Updating ${i + 1} of ${scfIds.length}…`)
        try {
          let scoped = getScopedControl(scopingData, scf_id)
          if (!scoped) scoped = { scf_id, selected: false }

          let patch: Partial<ScopedControl> = {}
          if (actionType === 'applicable') {
            patch = { selected: true }
          } else if (actionType === 'na') {
            patch = { selected: false }
          } else if (actionType === 'owner') {
            patch = { owner: ownerValue as OwnerTeam | undefined }
          }

          const updated = await updateScopedControl({ ...scopingData }, { ...scoped, ...patch } as ScopedControl)
          handleScopingDataChange(updated)
          successCount++
        } catch (err) {
          console.error('Bulk update failed for control:', scf_id, err)
          failCount++
        }
      }

      // Toast summary
      if (failCount === 0) {
        toast.success(`${successCount} control${successCount !== 1 ? 's' : ''} updated`)
      } else {
        toast.error(`${successCount} updated · ${failCount} failed`)
      }

      // Refetch
      try {
        const freshData = await loadScopedControls()
        if (freshData) handleScopingDataChange(freshData)
        refetch()
        refetchStats()
      } catch (err) {
        console.error('Failed to reload after bulk action:', err)
      }

      setBulkBusy(false)
      setBulkProgress('')
      setRowSelection(new Set())
    },
    [rowSelection, scopingData, handleScopingDataChange, refetch, refetchStats],
  )

  const handleSetApplicable = useCallback(() => runBulkAction('applicable'), [runBulkAction])
  const handleSetNA = useCallback(() => runBulkAction('na'), [runBulkAction])
  const handleAssignOwner = useCallback(
    (owner: string) => runBulkAction('owner', owner),
    [runBulkAction],
  )

  // ── Scoping entry for detail page ─────────────────────────────────────────
  const scopingEntry: ScopingEntry | null = useMemo(() => {
    if (!selectedId) return null
    const sc = getScopedControl(scopingData, selectedId)
    if (!sc) return { scf_id: selectedId, selected: false }
    return {
      id: sc.id,
      scf_id: sc.scf_id,
      selected: sc.selected ?? false,
      implementation_status: sc.implementation_status as ScopingEntry['implementation_status'],
      priority: sc.priority as ScopingEntry['priority'],
      maturity_level: sc.maturity_level as ScopingEntry['maturity_level'],
      selection_reason: sc.selection_reason,
      owner: sc.owner,
      target_date: sc.target_date,
      completion_date: sc.completion_date,
      implementation_notes: sc.implementation_notes,
    }
  }, [selectedId, scopingData])

  // ── Render ────────────────────────────────────────────────────────────────

  // Detail view
  if (selectedId && selectedControl) {
    return (
      <>
        <ScopingDetailPage
          control={selectedControl}
          scopingEntry={scopingEntry}
          position={position}
          onPrev={handlePrev}
          onNext={handleNext}
          onBack={handleBack}
          onToggleScope={toggleScope}
          onFieldChange={updateField}
          onReloadTeamAssignments={() => void reloadTeamAssignments()}
          organizationId={organizationId}
          scopingData={scopingData}
          ownerTeams={ownerTeams}
          canManageTeams={canManageTeams}
        />
        {showFrameworkModal && (
          <ScopeByFrameworkModal
            organizationId={organizationId}
            existingScopedCount={serverStats?.in_scope ?? 0}
            onClose={() => setShowFrameworkModal(false)}
            onSuccess={handleBulkScopeSuccess}
          />
        )}
      </>
    )
  }

  // List view (selectedId is set but control not yet in enriched list — loading)
  if (selectedId && !selectedControl) {
    return <div className="scoping-page-loading" aria-busy="true">Loading control…</div>
  }

  // List view
  return (
    <div className="scoping-page">
      <ScopingList
        organizationId={organizationId}
        filters={filters}
        onFiltersChange={setFilters}
        search={search}
        onSearchChange={setSearch}
        onOpenControl={setSelectedId}
        onScopeByFramework={() => setShowFrameworkModal(true)}
        selection={rowSelection}
        onSelectionChange={setRowSelection}
        initialScrollOffset={scrollOffset}
        onScrollOffsetChange={setScrollOffset}
        frameworkNames={frameworkNames}
        ownerByControlId={ownerByControlId}
        bulkBar={
          rowSelection.size > 0 ? (
            <ScopingBulkBar
              selectedCount={rowSelection.size}
              visibleCount={enrichedControls.length}
              allVisibleSelected={
                enrichedControls.length > 0 &&
                enrichedControls.every((c) => rowSelection.has(c.scf_id))
              }
              ownerOptions={ownerOptions}
              busy={bulkBusy}
              progressText={bulkBusy ? bulkProgress : undefined}
              onSelectAllVisible={() => {
                const next = new Set(enrichedControls.map((c) => c.scf_id))
                setRowSelection(next)
              }}
              onSetApplicable={handleSetApplicable}
              onSetNA={handleSetNA}
              onAssignOwner={handleAssignOwner}
              onClear={() => setRowSelection(new Set())}
            />
          ) : undefined
        }
      />
      {showFrameworkModal && (
        <ScopeByFrameworkModal
          organizationId={organizationId}
          existingScopedCount={serverStats?.in_scope ?? 0}
          onClose={() => setShowFrameworkModal(false)}
          onSuccess={handleBulkScopeSuccess}
        />
      )}
    </div>
  )
}
