import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type JSX,
} from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import type {
  EnrichedControl,
  ERLFile,
  FrameworkNameMap,
  ScopedControl,
  ScopedControlsFile,
  Team,
} from '../../types'
import type { LibraryMode } from '../../data/appUrl'
import type { UnifiedLibraryFilters } from './UnifiedLibraryList'
import UnifiedLibraryList from './UnifiedLibraryList'
import ControlDetailPage from './ControlDetailPage'
import ScopingBulkBar from '../scoping/ScopingBulkBar'
import {
  batchAssignTeamToItems,
  batchUpdateScopedControls,
  listTeams,
  setControlScopeOverride,
} from '../../data/apiClient'
import {
  getScopedControl,
  loadScopedControls,
  updateScopedControl,
} from '../../data/scopingService'
import {
  flattenScopedControlPages,
  useScopedControlsQuery,
} from '../../hooks/useScopedControlsQuery'
import { useDebounce } from '../../hooks/useDebounce'
import { useIsOrgEditor } from '../../hooks/useHasOrgRole'
import { useIsOrgAdmin } from '../../hooks/useIsOrgAdmin'
import {
  accountableTeamLabel,
  useTeamAssignments,
} from '../../hooks/useTeamAssignments'

const DEFAULT_FILTERS: UnifiedLibraryFilters = {
  domain: 'all',
  csf: 'all',
  weight: 'all',
  framework: 'all',
  scope: 'all',
}

interface Props {
  item: string | null
  mode: LibraryMode
  onModeChange: (mode: LibraryMode) => void
  onItemChange: (id: string | null) => void
  scopingData: ScopedControlsFile | null
  onScopingDataChange?: (data: ScopedControlsFile) => void
  erlData?: ERLFile
  frameworkNames?: FrameworkNameMap
  onNavigateToEvidence?: (evidenceId: string) => void
  organizationId?: string
  controls?: EnrichedControl[]
  initialFramework?: string
}

export default function UnifiedLibraryPage({
  item,
  mode,
  onModeChange,
  onItemChange,
  scopingData,
  onScopingDataChange,
  erlData,
  frameworkNames = {},
  onNavigateToEvidence,
  organizationId,
  controls: bulkControls = [],
  initialFramework,
}: Props): JSX.Element {
  const queryClient = useQueryClient()
  const orgId = organizationId ?? scopingData?.organizationId ?? ''
  const canEdit = useIsOrgEditor(orgId)
  const canManageTeams = useIsOrgAdmin(orgId)
  const [filtersByMode, setFiltersByMode] = useState<Record<LibraryMode, UnifiedLibraryFilters>>({
    'in-scope': { ...DEFAULT_FILTERS },
    'full-library': { ...DEFAULT_FILTERS },
  })
  const [searchByMode, setSearchByMode] = useState<Record<LibraryMode, string>>({
    'in-scope': '',
    'full-library': '',
  })
  const [scrollByMode, setScrollByMode] = useState<Record<LibraryMode, number>>({
    'in-scope': 0,
    'full-library': 0,
  })
  const [selection, setSelection] = useState<Set<string>>(new Set())
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkProgress, setBulkProgress] = useState('')
  const [teams, setTeams] = useState<Team[]>([])
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!initialFramework) return
    setFiltersByMode((current) => ({
      ...current,
      'full-library': { ...current['full-library'], framework: initialFramework },
    }))
  }, [initialFramework])

  const filters = filtersByMode[mode]
  const search = searchByMode[mode]
  const debouncedSearch = useDebounce(search, 300)
  const listing = useScopedControlsQuery(
    {
      search: debouncedSearch || undefined,
      domain: filters.domain !== 'all' ? filters.domain : undefined,
      csf_function: filters.csf !== 'all' ? filters.csf : undefined,
      control_weighting: filters.weight !== 'all' ? Number(filters.weight) : undefined,
      framework: filters.framework !== 'all' ? filters.framework : undefined,
      scope_status: mode === 'in-scope' ? 'in_scope' : filters.scope,
    },
    orgId,
  )
  const { controls: listedControls, total } = flattenScopedControlPages(listing.data?.pages)
  const detailListing = useScopedControlsQuery(
    { search: item ?? undefined, scope_status: 'all' },
    orgId,
    Boolean(item),
  )
  const { controls: detailControls } = flattenScopedControlPages(detailListing.data?.pages)
  const bulkById = useMemo(
    () => new Map(bulkControls.map((control) => [control.scf_id, control])),
    [bulkControls],
  )
  const scopedById = useMemo(
    () => new Map((scopingData?.scoped_controls ?? []).map((control) => [control.scf_id, control])),
    [scopingData],
  )
  const dbIdByScfId = useMemo(() => {
    const result = new Map<string, string>()
    for (const control of listedControls) {
      if (control.scoped_control_id) result.set(control.scf_id, control.scoped_control_id)
    }
    return result
  }, [listedControls])
  const loadedDbIds = useMemo(() => Array.from(dbIdByScfId.values()), [dbIdByScfId])
  const { accountableFor, reload: reloadTeams } = useTeamAssignments(
    orgId,
    'control',
    { itemIds: loadedDbIds },
  )
  const ownerByControlId = useMemo(() => {
    const result: Record<string, string> = {}
    for (const [scfId, dbId] of dbIdByScfId) {
      result[scfId] = accountableTeamLabel(accountableFor(dbId)) ?? ''
    }
    return result
  }, [dbIdByScfId, accountableFor])

  useEffect(() => {
    if (!orgId) return
    void listTeams(orgId).then(setTeams).catch(() => setTeams([]))
  }, [orgId])

  useEffect(() => () => {
    if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current)
  }, [])

  const refresh = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['scoped-controls', orgId] }),
      queryClient.invalidateQueries({ queryKey: ['scoped-controls-stats', orgId] }),
      queryClient.invalidateQueries({ queryKey: ['framework-scoping', orgId] }),
    ])
    const fresh = await loadScopedControls()
    if (fresh) onScopingDataChange?.(fresh)
  }, [orgId, onScopingDataChange, queryClient])

  const applyScopeAction = useCallback(async (
    scfId: string,
    selected: boolean,
  ) => {
    if (!canEdit) return
    let reason: string | undefined
    if (selected) {
      reason = window.prompt('Why should this control be removed from scope?')?.trim()
      if (!reason) return
    }
    try {
      await setControlScopeOverride(
        orgId,
        scfId,
        selected ? 'exclude' : 'include',
        reason,
      )
      toast.success(selected ? 'Control removed from scope' : 'Control added to scope')
      await refresh()
      if (selected) onItemChange(null)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not update scope')
    }
  }, [canEdit, orgId, onItemChange, refresh])

  const updateImplementationField = useCallback((field: string, value: unknown) => {
    if (!item || !scopingData || !canEdit) return
    if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current)
    saveTimeoutRef.current = setTimeout(async () => {
      try {
        const existing = getScopedControl(scopingData, item)
        if (!existing?.selected) return
        const updated = await updateScopedControl(
          { ...scopingData, scoped_controls: [...scopingData.scoped_controls] },
          { ...existing, [field]: value } as ScopedControl,
        )
        onScopingDataChange?.(updated)
        if (field === 'implementation_status') {
          await Promise.all([
            queryClient.invalidateQueries({ queryKey: ['scoped-controls', orgId] }),
            queryClient.invalidateQueries({ queryKey: ['scoped-controls-stats', orgId] }),
          ])
        }
      } catch (error) {
        toast.error(error instanceof Error ? error.message : 'Could not update implementation record')
      }
    }, 300)
  }, [canEdit, item, onScopingDataChange, orgId, queryClient, scopingData])

  const runBulkUpdate = useCallback(async (
    values: { maturity_level?: string; implementation_status?: string },
  ) => {
    if (!selection.size) return
    setBulkBusy(true)
    setBulkProgress(`Updating ${selection.size} controls…`)
    try {
      const result = await batchUpdateScopedControls(
        Array.from(selection).map((scf_id) => ({ scf_id, ...values })),
        orgId,
      )
      if (result.failed) toast.error(`${result.updated + result.created} updated · ${result.failed} failed`)
      else toast.success(`${result.updated + result.created} controls updated`)
      setSelection(new Set())
      await refresh()
    } catch {
      toast.error('Bulk update failed')
    } finally {
      setBulkBusy(false)
      setBulkProgress('')
    }
  }, [selection, orgId, refresh])

  const assignOwner = useCallback(async (teamId: string) => {
    const itemIds = Array.from(selection)
      .map((scfId) => dbIdByScfId.get(scfId))
      .filter((value): value is string => Boolean(value))
    if (!itemIds.length) return
    setBulkBusy(true)
    setBulkProgress(`Assigning team to ${itemIds.length} controls…`)
    try {
      await batchAssignTeamToItems(orgId, {
        type: 'control',
        team_id: teamId,
        item_ids: itemIds,
        is_accountable: true,
      })
      await reloadTeams()
      setSelection(new Set())
      toast.success('Accountable team updated')
    } catch {
      toast.error('Could not assign accountable team')
    } finally {
      setBulkBusy(false)
      setBulkProgress('')
    }
  }, [selection, dbIdByScfId, orgId, reloadTeams])

  const activeListed = item
    ? listedControls.find((control) => control.scf_id === item)
      ?? detailControls.find((control) => control.scf_id === item)
    : undefined
  const activeControl = useMemo(() => {
    if (!item) return undefined
    const catalogControl = bulkById.get(item)
    if (catalogControl) return catalogControl
    if (!activeListed) return undefined
    const frameworksResolved: Record<string, string[]> = {}
    for (const [frameworkId, refs] of Object.entries(activeListed.framework_mappings ?? {})) {
      if ([
        'risk_', 'threat_', 'scf_core_', 'control_threat_summary',
        'risk_threat_summary', 'minimum_security_requirements_mcr_dsr',
        'identify_', 'errata_',
      ].some((prefix) => frameworkId.startsWith(prefix))) continue
      const baseId = frameworkId.endsWith('_ref') ? frameworkId.slice(0, -4) : frameworkId
      frameworksResolved[frameworkNames[baseId] ?? baseId] = refs
    }
    const artifactsResolved = (activeListed.evidence_requests ?? []).flatMap((evidenceId) => {
      const entry = erlData?.[evidenceId]
      return entry ? [{
        id: evidenceId,
        title: entry.artifact_title ?? entry.evidence_title ?? '',
        domain: entry.area_of_focus ?? entry.evidence_domain ?? '',
      }] : []
    })
    return {
      ...activeListed,
      control_question: activeListed.control_question ?? undefined,
      validation_cadence: activeListed.validation_cadence ?? undefined,
      control_weighting: activeListed.control_weighting ?? undefined,
      nist_csf_function: activeListed.nist_csf_function ?? undefined,
      artifactsResolved,
      frameworksResolved,
      frameworksCount: Object.keys(frameworksResolved).length,
    } as EnrichedControl
  }, [activeListed, bulkById, erlData, frameworkNames, item])
  const activeScoping = item ? scopedById.get(item) : undefined
  const listedIndex = item ? listedControls.findIndex((control) => control.scf_id === item) : -1
  const position = item
    ? { index: listedIndex >= 0 ? listedIndex : null, total }
    : null

  const navigateSibling = (offset: number) => {
    if (listedIndex < 0) return
    const sibling = listedControls[listedIndex + offset]
    if (sibling) onItemChange(sibling.scf_id)
  }

  const modeSelector = (
    <div className="library-mode-header">
      <div>
        <h1>Control Library</h1>
        <p>
          {mode === 'in-scope'
            ? 'Manage implementation records for controls in your effective scope.'
            : 'Browse the read-only SCF catalog and add individual controls to scope.'}
        </p>
      </div>
      <div className="library-mode-selector" role="tablist" aria-label="Control Library mode">
        <button
          type="button"
          role="tab"
          aria-selected={mode === 'in-scope'}
          className={mode === 'in-scope' ? 'active' : ''}
          onClick={() => onModeChange('in-scope')}
        >
          In Scope
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === 'full-library'}
          className={mode === 'full-library' ? 'active' : ''}
          onClick={() => onModeChange('full-library')}
        >
          Full Library
        </button>
      </div>
    </div>
  )

  if (item && activeControl) {
    const selected = activeScoping?.selected ?? activeListed?.selected ?? false
    return (
      <>
        {modeSelector}
        <div className="library-scope-action-bar">
          {selected ? (
            <>
              <span>This control has an implementation record.</span>
              {mode === 'full-library' && (
                <button type="button" className="btn-primary" onClick={() => onModeChange('in-scope')}>
                  Open implementation record
                </button>
              )}
              {mode === 'in-scope' && canEdit && (
                <button type="button" className="btn-danger" onClick={() => void applyScopeAction(item, true)}>
                  Remove from scope
                </button>
              )}
            </>
          ) : (
            <>
              <span>Catalog fields are read-only. This control is not in scope.</span>
              {canEdit && (
                <button type="button" className="btn-primary" onClick={() => void applyScopeAction(item, false)}>
                  Add to scope
                </button>
              )}
            </>
          )}
        </div>
        <ControlDetailPage
          control={activeControl}
          scopingEntry={activeScoping ? {
            selected: activeScoping.selected,
            implementation_status: activeScoping.implementation_status,
            maturity: activeScoping.maturity_level,
            owner: ownerByControlId[item],
          } : null}
          position={position}
          onPrev={() => navigateSibling(-1)}
          onNext={() => navigateSibling(1)}
          onBack={() => onItemChange(null)}
          onNavigateToEvidence={onNavigateToEvidence}
          organizationId={orgId}
          scopingData={scopingData ?? undefined}
          frameworkNames={frameworkNames}
          implementationRecord={activeScoping ?? null}
          onImplementationFieldChange={updateImplementationField}
          onReloadTeamAssignments={() => void reloadTeams()}
          canEditImplementation={canEdit && selected}
          canManageTeams={canManageTeams}
          accountableTeamLabel={ownerByControlId[item] || null}
        />
      </>
    )
  }

  const bulkBar = mode === 'in-scope' && selection.size > 0 ? (
    <ScopingBulkBar
      selectedCount={selection.size}
      visibleCount={listedControls.length}
      allVisibleSelected={listedControls.length > 0 && listedControls.every((control) => selection.has(control.scf_id))}
      teamOptions={canManageTeams ? teams.map((team) => ({ value: team.id, label: team.name })) : null}
      busy={bulkBusy}
      progressText={bulkProgress}
      onSelectAllVisible={() => setSelection(new Set(listedControls.map((control) => control.scf_id)))}
      onSetMaturity={(value) => void runBulkUpdate({ maturity_level: value })}
      onSetStatus={(value) => void runBulkUpdate({ implementation_status: value })}
      onAssignOwner={(teamId) => void assignOwner(teamId)}
      onClear={() => setSelection(new Set())}
    />
  ) : undefined

  return (
    <>
      {modeSelector}
      <UnifiedLibraryList
        organizationId={orgId}
        mode={mode}
        filters={filters}
        onFiltersChange={(next) => setFiltersByMode((current) => ({ ...current, [mode]: next }))}
        search={search}
        onSearchChange={(next) => setSearchByMode((current) => ({ ...current, [mode]: next }))}
        onOpenControl={onItemChange}
        onScopeAction={(scfId, selected) => void applyScopeAction(scfId, selected)}
        canEdit={canEdit}
        selection={selection}
        onSelectionChange={setSelection}
        bulkBar={bulkBar}
        initialScrollOffset={scrollByMode[mode]}
        onScrollOffsetChange={(next) => setScrollByMode((current) => ({ ...current, [mode]: next }))}
        frameworkNames={frameworkNames}
        ownerByControlId={ownerByControlId}
        bulkById={bulkById}
      />
    </>
  )
}
