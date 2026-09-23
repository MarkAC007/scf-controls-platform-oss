import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type JSX,
  type ReactNode,
} from 'react'
import { FixedSizeList as List, type ListChildComponentProps } from 'react-window'
import type { EnrichedControl } from '../../types'
import type { LibraryMode } from '../../data/appUrl'
import {
  flattenScopedControlPages,
  useScopedControlsQuery,
  useScopedControlsStats,
} from '../../hooks/useScopedControlsQuery'
import { useWorkScope } from '../../contexts/WorkScopeContext'
import { listTeams } from '../../data/apiClient'
import { useCatalogFilters } from '../../hooks/useCatalogFilters'
import { useDebounce } from '../../hooks/useDebounce'
import { getCatalogLifecycle } from '../DeprecatedBadge'
import DeprecatedBadge from '../DeprecatedBadge'
import FilterSidebar, {
  FilterGroup,
  FilterSelect,
  defaultFiltersCollapsed,
} from '../explorer/FilterSidebar'
import ListToolbar from '../explorer/ListToolbar'
import ExplorerListRow, {
  RowChip,
  RowMeta,
  RowWeightBar,
  RowTickCircle,
} from '../explorer/ListRow'

export interface UnifiedLibraryFilters {
  domain: string
  csf: string
  weight: string
  framework: string
  scope: 'all' | 'in_scope' | 'out_of_scope'
}

interface Props {
  organizationId: string
  mode: LibraryMode
  filters: UnifiedLibraryFilters
  onFiltersChange: (filters: UnifiedLibraryFilters) => void
  search: string
  onSearchChange: (value: string) => void
  onOpenControl: (scfId: string) => void
  onScopeAction: (scfId: string, selected: boolean) => void
  canEdit: boolean
  selection: Set<string>
  onSelectionChange: (selection: Set<string>) => void
  bulkBar?: ReactNode
  initialScrollOffset: number
  onScrollOffsetChange: (offset: number) => void
  frameworkNames?: Record<string, string>
  ownerByControlId?: Record<string, string>
  bulkById?: Map<string, EnrichedControl>
}

const ITEM_HEIGHT = 76
const DEFAULT_LIST_HEIGHT = 600
const INTERNAL_PREFIXES = [
  'risk_',
  'threat_',
  'scf_core_',
  'control_threat_summary',
  'risk_threat_summary',
  'minimum_security_requirements_mcr_dsr',
  'identify_',
  'errata_',
]

function label(value?: string | null): string {
  if (!value) return '—'
  return value
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function pptdfLabel(applicability?: EnrichedControl['pptdf_applicability']): string | undefined {
  if (!applicability) return undefined
  const active = [
    applicability.people && 'People',
    applicability.process && 'Process',
    applicability.technology && 'Technology',
    applicability.data && 'Data',
    applicability.facility && 'Facility',
  ].filter(Boolean)
  return active[0] as string | undefined
}

export default function UnifiedLibraryList({
  organizationId,
  mode,
  filters,
  onFiltersChange,
  search,
  onSearchChange,
  onOpenControl,
  onScopeAction,
  canEdit,
  selection,
  onSelectionChange,
  bulkBar,
  initialScrollOffset,
  onScrollOffsetChange,
  frameworkNames = {},
  ownerByControlId = {},
  bulkById,
}: Props): JSX.Element {
  const [filtersCollapsed, setFiltersCollapsed] = useState(defaultFiltersCollapsed)
  const [listHeight, setListHeight] = useState(DEFAULT_LIST_HEIGHT)
  const listContainerRef = useRef<HTMLDivElement>(null)
  const debouncedSearch = useDebounce(search, 300)
  const { domains, nistCsfFunctions, controlWeights } = useCatalogFilters()
  const { isMyTeams } = useWorkScope()

  // Applied in BOTH modes on purpose. A catalog control nobody has scoped has
  // no team assignment, so it drops out of "My teams" — which is the honest
  // answer, not a bug. Special-casing full-library would put the header control
  // back in the position of labelling a list it does not govern.
  const query = useScopedControlsQuery(
    {
      search: debouncedSearch || undefined,
      domain: filters.domain !== 'all' ? filters.domain : undefined,
      csf_function: filters.csf !== 'all' ? filters.csf : undefined,
      control_weighting: filters.weight !== 'all' ? Number(filters.weight) : undefined,
      framework: filters.framework !== 'all' ? filters.framework : undefined,
      scope_status: mode === 'in-scope' ? 'in_scope' : filters.scope,
      my_teams: isMyTeams || undefined,
    },
    organizationId,
  )
  const { controls, total } = flattenScopedControlPages(query.data?.pages)

  // Organisation-wide and deliberately unfiltered — it is the denominator the
  // narrowed count is measured against, so it must not move when the scope does.
  const { data: serverStats } = useScopedControlsStats(organizationId)
  const orgTotal = serverStats?.total_controls

  // Only to tell "your teams own nothing yet" apart from "you are on no team",
  // which are different problems with different fixes. Not fetched otherwise.
  const [callerTeamCount, setCallerTeamCount] = useState<number | null>(null)
  useEffect(() => {
    if (!isMyTeams || !organizationId) {
      setCallerTeamCount(null)
      return
    }
    let cancelled = false
    listTeams(organizationId, { mine: true })
      .then((teams) => {
        if (!cancelled) setCallerTeamCount(teams.length)
      })
      .catch(() => {
        // Leave it null: the generic "nothing assigned to your teams" copy is
        // correct either way, and a failed lookup must not invent a diagnosis.
        if (!cancelled) setCallerTeamCount(null)
      })
    return () => {
      cancelled = true
    }
  }, [isMyTeams, organizationId])

  useEffect(() => {
    const updateHeight = () => {
      if (!listContainerRef.current) return
      const rect = listContainerRef.current.getBoundingClientRect()
      setListHeight(Math.max(400, window.innerHeight - rect.top - 40))
    }
    updateHeight()
    window.addEventListener('resize', updateHeight)
    return () => window.removeEventListener('resize', updateHeight)
  }, [filtersCollapsed])

  const handleScroll = useCallback(
    ({ scrollOffset, scrollUpdateWasRequested }: { scrollOffset: number; scrollUpdateWasRequested: boolean }) => {
      if (!scrollUpdateWasRequested) onScrollOffsetChange(scrollOffset)
      const threshold = controls.length * ITEM_HEIGHT - listHeight - ITEM_HEIGHT * 5
      if (
        !scrollUpdateWasRequested &&
        scrollOffset > threshold &&
        query.hasNextPage &&
        !query.isFetchingNextPage
      ) {
        query.fetchNextPage()
      }
    },
    [controls.length, listHeight, onScrollOffsetChange, query],
  )

  const frameworkOptions = useMemo(
    () => [
      { value: 'all', label: 'All Frameworks' },
      ...Object.entries(frameworkNames)
        .filter(([id]) => !INTERNAL_PREFIXES.some((prefix) => id.startsWith(prefix)))
        .sort((a, b) => a[1].localeCompare(b[1]))
        .map(([value, optionLabel]) => ({ value, label: optionLabel })),
    ],
    [frameworkNames],
  )

  const Row = useCallback(
    ({ index, style }: ListChildComponentProps) => {
      const control = controls[index]
      if (!control) return <div style={style} className="library-row-loading" />
      const checked = selection.has(control.scf_id)
      const bulk = bulkById?.get(control.scf_id)
      const lifecycle = getCatalogLifecycle(control)
      const mappingsCount = bulk?.frameworksCount ?? Object.keys(control.framework_mappings || {})
        .filter((id) => !INTERNAL_PREFIXES.some((prefix) => id.startsWith(prefix))).length
      const pptdf = pptdfLabel(bulk?.pptdf_applicability ?? control.pptdf_applicability)

      return (
        <div style={style}>
          <ExplorerListRow
            monoId={control.scf_id}
            title={control.control_name}
            description={control.control_description}
            accent={control.selected}
            onClick={() => onOpenControl(control.scf_id)}
          >
            {mode === 'in-scope' && canEdit && (
              <input
                type="checkbox"
                aria-label={`Select ${control.scf_id}`}
                checked={checked}
                onClick={(event) => event.stopPropagation()}
                onChange={() => {
                  const next = new Set(selection)
                  if (checked) next.delete(control.scf_id)
                  else next.add(control.scf_id)
                  onSelectionChange(next)
                }}
              />
            )}
            {lifecycle.catalog_status === 'deprecated' && (
              <DeprecatedBadge {...lifecycle} compact />
            )}
            {mode === 'in-scope' ? (
              <>
                <RowChip tone={control.implementation_status}>{label(control.implementation_status)}</RowChip>
                <RowMeta>Maturity {control.maturity_level ?? '—'}</RowMeta>
                <RowMeta>Priority {label(control.priority)}</RowMeta>
                <RowMeta>{ownerByControlId[control.scf_id] || 'No accountable team'}</RowMeta>
                <RowTickCircle on />
              </>
            ) : (
              <>
                <RowChip>{control.scf_domain || 'No domain'}</RowChip>
                {control.nist_csf_function && <RowChip>{control.nist_csf_function}</RowChip>}
                {pptdf && <RowChip>{pptdf}</RowChip>}
                <RowMeta>{mappingsCount} mappings</RowMeta>
                {control.control_weighting != null && <RowWeightBar value={control.control_weighting} />}
                <RowTickCircle on={control.selected} />
                {canEdit && (
                  <button
                    type="button"
                    className={control.selected ? 'btn-secondary btn-small' : 'btn-primary btn-small'}
                    onClick={(event) => {
                      event.stopPropagation()
                      if (control.selected) onOpenControl(control.scf_id)
                      else onScopeAction(control.scf_id, false)
                    }}
                  >
                    {control.selected ? 'Open implementation record' : 'Add to scope'}
                  </button>
                )}
              </>
            )}
          </ExplorerListRow>
        </div>
      )
    },
    [
      controls,
      mode,
      canEdit,
      selection,
      onSelectionChange,
      onOpenControl,
      onScopeAction,
      ownerByControlId,
      bulkById,
    ],
  )

  const optionWithAll = (allLabel: string, values: { value: string; label: string }[]) => [
    { value: 'all', label: allLabel },
    ...values.filter((option) => option.value !== 'all'),
  ]

  if (query.isError) {
    return <div className="library-page library-page--error">Failed to load controls.</div>
  }

  return (
    <div className="library-page">
      <FilterSidebar
        collapsed={filtersCollapsed}
        onToggleCollapsed={() => setFiltersCollapsed((value) => !value)}
        aria-label="Control filters"
      >
        {mode === 'full-library' && (
          <FilterGroup label="SCOPE">
            <FilterSelect
              value={filters.scope}
              onChange={(value) => onFiltersChange({ ...filters, scope: value as UnifiedLibraryFilters['scope'] })}
              options={[
                { value: 'all', label: 'All controls' },
                { value: 'in_scope', label: 'In scope' },
                { value: 'out_of_scope', label: 'Out of scope' },
              ]}
            />
          </FilterGroup>
        )}
        <FilterGroup label="DOMAIN">
          <FilterSelect
            value={filters.domain}
            onChange={(value) => onFiltersChange({ ...filters, domain: value })}
            options={optionWithAll('All Domains', domains)}
          />
        </FilterGroup>
        <FilterGroup label="CSF FUNCTION">
          <FilterSelect
            value={filters.csf}
            onChange={(value) => onFiltersChange({ ...filters, csf: value })}
            options={optionWithAll('All NIST CSF Functions', nistCsfFunctions)}
          />
        </FilterGroup>
        <FilterGroup label="FRAMEWORK">
          <FilterSelect
            value={filters.framework}
            onChange={(value) => onFiltersChange({ ...filters, framework: value })}
            options={frameworkOptions}
          />
        </FilterGroup>
        <FilterGroup label="WEIGHT">
          <FilterSelect
            value={filters.weight}
            onChange={(value) => onFiltersChange({ ...filters, weight: value })}
            options={optionWithAll('All Control Weights', controlWeights)}
          />
        </FilterGroup>
      </FilterSidebar>

      <div className="library-list-body">
        <ListToolbar
          search={search}
          onSearchChange={onSearchChange}
          searchPlaceholder="Search controls — id, name, description…"
          count={
            <span>
              {total.toLocaleString()} {mode === 'in-scope' ? 'in-scope ' : ''}
              {total === 1 ? 'control' : 'controls'}
              {isMyTeams && (
                <span className="work-scope-count" aria-live="polite">
                  Showing {total.toLocaleString()} of{' '}
                  {(orgTotal ?? total).toLocaleString()} controls in the
                  organisation · My teams
                </span>
              )}
            </span>
          }
        />
        {/* Pinned above the rows: the header owns changing the scope, this owns
            saying why the list below is short. */}
        {isMyTeams && (
          <div className="work-scope-chip-row">
            <span className="work-scope-chip">My teams</span>
          </div>
        )}
        {bulkBar}
        <div className="library-list-rows" ref={listContainerRef}>
          {query.isLoading ? (
            <div className="library-loading">Loading controls…</div>
          ) : controls.length === 0 ? (
            <div className="library-empty">
              {isMyTeams && callerTeamCount === 0
                ? 'You are not a member of any team yet, so "My teams" has nothing to show. Switch Showing to Everything, or ask an administrator to add you to a team.'
                : isMyTeams
                  ? 'No controls are assigned to your teams yet.'
                  : 'No controls match your search criteria.'}
            </div>
          ) : (
            <>
              <List
                height={listHeight}
                itemCount={controls.length}
                itemSize={ITEM_HEIGHT}
                width="100%"
                onScroll={handleScroll}
                initialScrollOffset={initialScrollOffset}
                className="library-virtualized-list"
              >
                {Row}
              </List>
              {query.isFetchingNextPage && (
                <div className="library-loading-more">Loading more controls…</div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
