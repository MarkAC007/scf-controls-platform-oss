/**
 * LibraryList — full-width Explorer list view for the Control Library tab.
 *
 * Replaces the ControlList split-panel pattern with:
 *   FilterSidebar (collapsible) + ListToolbar + react-window FixedSizeList of
 *   ExplorerListRow rows, all fully prop-driven (filters/search owned by parent).
 *
 * Data behaviour is identical to ControlList: same useControlsQuery query key,
 * same flattenControlPages + enrichControl pipeline — no caching fork.
 */
import { useState, useRef, useCallback, useMemo, useEffect, type JSX } from 'react'
import { FixedSizeList as List, type ListChildComponentProps } from 'react-window'

import type { ERLFile, FrameworkNameMap, EnrichedControl } from '../../types'
import { useControlsQuery, flattenControlPages } from '../../hooks/useControlsQuery'
import { useCatalogFilters } from '../../hooks/useCatalogFilters'
import { useDebounce } from '../../hooks/useDebounce'
import { enrichControl } from '../../data/loaders'
import { getCatalogLifecycle } from '../DeprecatedBadge'
import DeprecatedBadge from '../DeprecatedBadge'

import FilterSidebar, { FilterGroup, FilterSelect, defaultFiltersCollapsed } from '../explorer/FilterSidebar'
import ListToolbar from '../explorer/ListToolbar'
import ExplorerListRow, {
  RowChip,
  RowMeta,
  RowWeightBar,
  RowTickCircle,
} from '../explorer/ListRow'

// ─── Types ────────────────────────────────────────────────────────────────────

export interface LibraryFilters {
  domain: string
  csf: string
  weight: string
}

export interface LibraryListProps {
  filters: LibraryFilters
  onFiltersChange: (f: LibraryFilters) => void
  /** Raw input value; parent owns it. Debounce (300 ms) happens inside. */
  search: string
  onSearchChange: (v: string) => void
  onOpenControl: (scfId: string) => void
  /** scf_id → selected; from scopingData */
  scopeById?: Map<string, boolean>
  /** Count of in-scope controls (client-side); omit segment when absent */
  inScopeCount?: number
  initialScrollOffset: number
  onScrollOffsetChange: (offset: number) => void
  /** Match ControlList's current prop types exactly */
  erlData?: unknown
  frameworkNames?: Record<string, string>
  /**
   * Optional overlay from the App-level bulk catalog (full serializer).
   * When present, row extras (frameworksCount, pptdf_applicability) are
   * sourced from here rather than the slim paginated data, restoring visual
   * parity with the old ControlList that consumed bulk data directly.
   * Falls back to slim-derived values when the entry is absent (e.g. during
   * the brief window before the bulk load completes on a hard refresh).
   */
  bulkById?: Map<string, EnrichedControl>
}

// ─── Constants ────────────────────────────────────────────────────────────────

const ITEM_HEIGHT = 66
const DEFAULT_LIST_HEIGHT = 600

// ─── Helpers ─────────────────────────────────────────────────────────────────

/** Derive a human-readable PPTDF chip label from the applicability flags.
 *  Returns the first truthy dimension label, or undefined when none apply. */
function pptdfLabel(
  applicability: EnrichedControl['pptdf_applicability'],
): string | undefined {
  if (!applicability) return undefined
  if (applicability.people) return 'People'
  if (applicability.process) return 'Process'
  if (applicability.technology) return 'Technology'
  if (applicability.data) return 'Data'
  if (applicability.facility) return 'Facility'
  return undefined
}

/** Build the "all" sentinel option for filter selects. */
function allOption(label: string) {
  return { value: 'all', label }
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function LibraryList({
  filters,
  onFiltersChange,
  search,
  onSearchChange,
  onOpenControl,
  scopeById,
  inScopeCount,
  initialScrollOffset,
  onScrollOffsetChange,
  erlData,
  frameworkNames = {},
  bulkById,
}: LibraryListProps): JSX.Element {
  // FilterSidebar collapsed state is local — parent has no need to own it
  const [filtersCollapsed, setFiltersCollapsed] = useState(defaultFiltersCollapsed)

  // Measured container height for the virtualized list
  const [listHeight, setListHeight] = useState(DEFAULT_LIST_HEIGHT)
  const listContainerRef = useRef<HTMLDivElement>(null)

  // Debounce search before passing to the query (300 ms per spec)
  const debouncedSearch = useDebounce(search, 300)

  // Load filter options
  const { domains: domainOptions, nistCsfFunctions, controlWeights } = useCatalogFilters()

  // Query controls — exactly the same query key shape as ControlList
  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading,
    isError,
  } = useControlsQuery({
    search: debouncedSearch || undefined,
    domain: filters.domain !== 'all' ? filters.domain : undefined,
    csf_function: filters.csf !== 'all' ? filters.csf : undefined,
    control_weighting:
      filters.weight !== 'all' ? parseInt(filters.weight, 10) : undefined,
  })

  // Flatten paginated results
  const { controls: rawControls, total } = flattenControlPages(data?.pages)

  // Enrich controls — same logic as ControlList
  const controls = useMemo<EnrichedControl[]>(() => {
    if (!erlData) {
      return rawControls.map((c) => ({
        ...c,
        artifactsResolved: [],
        frameworksResolved: {},
        frameworksCount: 0,
      })) as EnrichedControl[]
    }
    return rawControls.map((c) =>
      enrichControl(
        c,
        {},
        erlData as ERLFile,
        frameworkNames as FrameworkNameMap,
      ),
    )
  }, [rawControls, erlData, frameworkNames])

  // Measure container height on mount and resize (copy of ControlList logic)
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
  }, [filtersCollapsed])

  // Infinite scroll: load more when 5 items from end
  const handleScroll = useCallback(
    ({
      scrollOffset,
      scrollUpdateWasRequested,
    }: {
      scrollOffset: number
      scrollUpdateWasRequested: boolean
    }) => {
      // Forward offset to parent for scroll-position preservation
      if (!scrollUpdateWasRequested) {
        onScrollOffsetChange(scrollOffset)
      }

      if (scrollUpdateWasRequested) return
      const scrollHeight = controls.length * ITEM_HEIGHT
      const threshold = scrollHeight - listHeight - ITEM_HEIGHT * 5
      if (scrollOffset > threshold && hasNextPage && !isFetchingNextPage) {
        fetchNextPage()
      }
    },
    [
      controls.length,
      listHeight,
      hasNextPage,
      isFetchingNextPage,
      fetchNextPage,
      onScrollOffsetChange,
    ],
  )

  // Row renderer — memoised so react-window doesn't re-create it on every render
  const Row = useCallback(
    ({ index, style }: ListChildComponentProps) => {
      const control = controls[index]
      if (!control) {
        return (
          <div style={style} className="library-row-loading">
            <div className="loading-skeleton" />
          </div>
        )
      }

      // Use bulk data for row extras when available (restores "N maps" + PPTDF chip parity)
      const bulk = bulkById?.get(control.scf_id)
      const displayControl = bulk ?? control

      const scoped = scopeById?.get(control.scf_id) ?? false
      const chip = pptdfLabel(displayControl.pptdf_applicability)
      const lifecycle = getCatalogLifecycle(control)

      return (
        <div style={style}>
          <ExplorerListRow
            monoId={control.scf_id}
            title={control.control_name}
            description={control.control_description}
            accent={scoped}
            onClick={() => onOpenControl(control.scf_id)}
          >
            {/* Lifecycle / deprecation badge inline with the title area */}
            {lifecycle.catalog_status === 'deprecated' && (
              <DeprecatedBadge
                catalog_status={lifecycle.catalog_status}
                retired_in_version={lifecycle.retired_in_version}
                superseded_by={lifecycle.superseded_by}
                compact
              />
            )}
            {chip !== undefined && <RowChip>{chip}</RowChip>}
            <RowMeta>{displayControl.frameworksCount} maps</RowMeta>
            {control.control_weighting !== undefined && (
              <RowWeightBar value={control.control_weighting} />
            )}
            <RowTickCircle on={scoped} />
          </ExplorerListRow>
        </div>
      )
    },
    [controls, scopeById, onOpenControl, bulkById],
  )

  // ─── Filter select options with "all" sentinels ──────────────────────────

  const domainSelectOptions = useMemo(
    () => [
      allOption('All Domains'),
      ...domainOptions.filter((opt) => opt.value !== 'all'),
    ],
    [domainOptions],
  )

  const csfSelectOptions = useMemo(
    () => [
      allOption('All NIST CSF Functions'),
      ...nistCsfFunctions.filter((opt) => opt.value !== 'all'),
    ],
    [nistCsfFunctions],
  )

  const weightSelectOptions = useMemo(
    () => [
      allOption('All Control Weights'),
      ...controlWeights.filter((opt) => opt.value !== 'all'),
    ],
    [controlWeights],
  )

  // ─── Toolbar count node (ruling 3) ──────────────────────────────────────

  const countNode = (
    <span className="library-toolbar-count">
      {inScopeCount !== undefined && (
        <>
          <span className="library-count-scope">{inScopeCount} in scope</span>
          <span className="library-count-sep"> · </span>
        </>
      )}
      <span className="library-count-total">{total} controls</span>
    </span>
  )

  // ─── Error state ─────────────────────────────────────────────────────────

  if (isError) {
    return (
      <div className="library-page library-page--error">
        <p className="library-error-msg">
          Failed to load controls. Please try refreshing the page.
        </p>
      </div>
    )
  }

  // ─── Render ──────────────────────────────────────────────────────────────

  return (
    <div className="library-page">
      <FilterSidebar
        collapsed={filtersCollapsed}
        onToggleCollapsed={() => setFiltersCollapsed((c) => !c)}
        aria-label="Control filters"
      >
        <FilterGroup label="DOMAIN">
          <FilterSelect
            value={filters.domain}
            onChange={(v) => onFiltersChange({ ...filters, domain: v })}
            options={domainSelectOptions}
          />
        </FilterGroup>

        <FilterGroup label="CSF FUNCTION">
          <FilterSelect
            value={filters.csf}
            onChange={(v) => onFiltersChange({ ...filters, csf: v })}
            options={csfSelectOptions}
          />
        </FilterGroup>

        <FilterGroup label="WEIGHT">
          <FilterSelect
            value={filters.weight}
            onChange={(v) => onFiltersChange({ ...filters, weight: v })}
            options={weightSelectOptions}
          />
        </FilterGroup>
      </FilterSidebar>

      <div className="library-list-body">
        <ListToolbar
          search={search}
          onSearchChange={onSearchChange}
          searchPlaceholder="Search controls — id, name, description…"
          count={countNode}
        />

        <div className="library-list-rows" ref={listContainerRef}>
          {isLoading ? (
            <div className="library-loading">
              <div className="loading-spinner-small" />
              <span>Loading controls…</span>
            </div>
          ) : controls.length === 0 ? (
            <div className="library-empty">
              No controls match your search criteria.
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
              {isFetchingNextPage && (
                <div className="library-loading-more">Loading more controls…</div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
