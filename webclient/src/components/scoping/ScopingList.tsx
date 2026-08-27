/**
 * ScopingList — full-width Explorer list view for the Control Scoping tab.
 *
 * Replaces the ControlScoping split-panel pattern with:
 *   FilterSidebar (collapsible) + ListToolbar + react-window FixedSizeList of
 *   ExplorerListRow rows, all fully prop-driven (filters/search/scroll/selection
 *   owned by parent).
 *
 * Data behaviour is identical to ControlScoping: same useScopedControlsQuery
 * query key shape, same useScopedControlsStats — no cache fork.
 *
 * Nine filters match ControlScoping exactly (ruling: byte-identical param
 * mapping):
 *   scope (radio: in_scope / out_of_scope / all — default in_scope)
 *   domain, csf_function, control_weighting, framework  → FilterSelect
 *   team_id, function_id                                → derived from
 *                                                          listTeams/listFunctions
 *   accountable_owner_type                              → FilterSelect
 */
import {
  useState,
  useRef,
  useCallback,
  useMemo,
  useEffect,
  type JSX,
  type ReactNode,
} from 'react'
import { FixedSizeList as List, type ListChildComponentProps } from 'react-window'

import type { FrameworkNameMap } from '../../types'
import {
  useScopedControlsQuery,
  useScopedControlsStats,
  flattenScopedControlPages,
} from '../../hooks/useScopedControlsQuery'
import { useCatalogFilters } from '../../hooks/useCatalogFilters'
import { useDebounce } from '../../hooks/useDebounce'
import { listTeams, listFunctions } from '../../data/apiClient'
import type { Team, OrgFunction } from '../../types'

import FilterSidebar, {
  FilterGroup,
  FilterSelect,
} from '../explorer/FilterSidebar'
import ListToolbar from '../explorer/ListToolbar'
import ExplorerListRow, { RowMeta, RowTickCircle } from '../explorer/ListRow'

// ─── Types ─────────────────────────────────────────────────────────────────

export interface ScopingFilters {
  /** Scope status radio value. Default: 'in_scope'. */
  scope: 'all' | 'in_scope' | 'out_of_scope'
  domain: string
  csf: string
  weight: string
  framework: string
  teamId: string
  functionId: string
  ownerType: string
}

export interface ScopingListProps {
  organizationId: string
  filters: ScopingFilters
  onFiltersChange: (f: ScopingFilters) => void
  /** Raw input value; parent owns. Debounce (300 ms) happens inside. */
  search: string
  onSearchChange: (v: string) => void
  onOpenControl: (scfId: string) => void
  /** Called when user clicks "Scope by Framework" button. */
  onScopeByFramework: () => void
  /** Set of scf_ids currently checked for bulk actions. */
  selection: Set<string>
  /** Parent updates selection in response to checkbox changes. */
  onSelectionChange: (selection: Set<string>) => void
  initialScrollOffset: number
  onScrollOffsetChange: (offset: number) => void
  frameworkNames?: FrameworkNameMap
  /**
   * Accountable-team label per scf_id, batch-loaded by the container from
   * useTeamAssignments. Displayed in each row using .scoping-list-owner CSS.
   * Absent or empty string → show placeholder text.
   */
  ownerByControlId?: Record<string, string>
  /**
   * Bulk-action bar, rendered between the toolbar and the rows (the artboard's
   * placement). Owned by the container so selection state stays there; passed
   * as a node rather than rendered as a page-level sibling, where the flex-row
   * .scoping-page container would turn it into a full-height column.
   */
  bulkBar?: ReactNode
}

// ─── Constants ─────────────────────────────────────────────────────────────

/**
 * ITEM_HEIGHT tuned to fit: checkbox + accent bar + id + title + one-line desc
 * + status badge + maturity + owner + tick. Matches the mockup row height
 * (~72 px with padding). Must be larger than ControlScoping's 100 because
 * that component's card is taller.
 */
const ITEM_HEIGHT = 72
const DEFAULT_LIST_HEIGHT = 600

const ALL = 'all'

// ─── Helpers ───────────────────────────────────────────────────────────────

function allOption(label: string) {
  return { value: ALL, label }
}

/**
 * Implementation status → human label for the badge.
 * Mirrors the 8-value list from ControlScoping's select options.
 */
function statusLabel(status: string | null | undefined): string {
  if (!status) return 'Not Started'
  return status
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

// ─── Component ─────────────────────────────────────────────────────────────

export default function ScopingList({
  organizationId,
  filters,
  onFiltersChange,
  search,
  onSearchChange,
  onOpenControl,
  onScopeByFramework,
  selection,
  onSelectionChange,
  initialScrollOffset,
  onScrollOffsetChange,
  frameworkNames = {},
  ownerByControlId,
  bulkBar,
}: ScopingListProps): JSX.Element {
  const [filtersCollapsed, setFiltersCollapsed] = useState(false)
  const [listHeight, setListHeight] = useState(DEFAULT_LIST_HEIGHT)
  const listContainerRef = useRef<HTMLDivElement>(null)

  // Team/function data for filter selects (same source as TeamListFilters)
  const [teams, setTeams] = useState<Team[]>([])
  const [functions, setFunctions] = useState<OrgFunction[]>([])

  useEffect(() => {
    let cancelled = false
    Promise.all([listTeams(organizationId), listFunctions()])
      .then(([teamList, fns]) => {
        if (cancelled) return
        setTeams(teamList)
        setFunctions(fns)
      })
      .catch((err) => {
        console.error('Failed to load team filter options:', err)
      })
    return () => {
      cancelled = true
    }
  }, [organizationId])

  const debouncedSearch = useDebounce(search, 300)

  const { domains: domainOptions, nistCsfFunctions, controlWeights } =
    useCatalogFilters()

  // ── Query — byte-identical param mapping to ControlScoping ────────────────
  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading,
    isError,
  } = useScopedControlsQuery(
    {
      search: debouncedSearch || undefined,
      domain: filters.domain !== ALL ? filters.domain : undefined,
      csf_function: filters.csf !== ALL ? filters.csf : undefined,
      control_weighting:
        filters.weight !== ALL ? parseInt(filters.weight, 10) : undefined,
      framework: filters.framework !== ALL ? filters.framework : undefined,
      scope_status: filters.scope,
      team_id: filters.teamId !== ALL ? filters.teamId : undefined,
      function_id: filters.functionId !== ALL ? filters.functionId : undefined,
      accountable_owner_type:
        filters.ownerType !== ALL
          ? (filters.ownerType as 'internal' | 'external_contractor')
          : undefined,
    },
    organizationId,
  )

  // Stats for the toolbar count segment
  const { data: serverStats } = useScopedControlsStats(organizationId)

  const { controls, total } = flattenScopedControlPages(data?.pages)

  // ── Measure container height ──────────────────────────────────────────────
  // hasBulkBar: the bar mounts above the rows, pushing rect.top down — without
  // re-measuring, the virtualized list would overflow past the viewport while
  // a selection is active.
  const hasBulkBar = bulkBar != null
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
  }, [filtersCollapsed, hasBulkBar])

  // ── Infinite scroll: load more when 5 items from end ─────────────────────
  const handleScroll = useCallback(
    ({
      scrollOffset,
      scrollUpdateWasRequested,
    }: {
      scrollOffset: number
      scrollUpdateWasRequested: boolean
    }) => {
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

  // ── Checkbox toggle ───────────────────────────────────────────────────────
  const toggleCheck = useCallback(
    (scfId: string) => {
      const next = new Set(selection)
      if (next.has(scfId)) {
        next.delete(scfId)
      } else {
        next.add(scfId)
      }
      onSelectionChange(next)
    },
    [selection, onSelectionChange],
  )

  // ── Row renderer ──────────────────────────────────────────────────────────
  const Row = useCallback(
    ({ index, style }: ListChildComponentProps) => {
      const control = controls[index]
      if (!control) {
        return (
          <div style={style} className="scoping-list-row-loading">
            <div className="loading-skeleton" />
          </div>
        )
      }

      const checked = selection.has(control.scf_id)
      // DEVIATION: maturity_level is not returned by the paginated list endpoint
      // (slim serializer — only selected/implementation_status/selection_reason).
      // Rendered gracefully-absent: maturity defaults to '—'.
      // Owner comes from the container's useTeamAssignments batch load (ownerByControlId).
      const maturity = '—'
      const ownerLabel = ownerByControlId?.[control.scf_id] ?? ''

      return (
        <div style={style} className="scoping-list-row-wrap">
          {/* Bulk checkbox — sits OUTSIDE the row-click target */}
          <input
            type="checkbox"
            className="scoping-list-row-checkbox"
            aria-label={`Select ${control.scf_id}`}
            checked={checked}
            onChange={() => toggleCheck(control.scf_id)}
          />
          <ExplorerListRow
            monoId={control.scf_id}
            title={control.control_name}
            description={control.control_description}
            accent={control.selected}
            onClick={() => onOpenControl(control.scf_id)}
          >
            {/* Implementation status badge */}
            <span
              className={`status-badge-compact status-${control.implementation_status ?? 'not_started'}`}
            >
              {statusLabel(control.implementation_status)}
            </span>

            {/* Maturity level (absent from slim serializer — shown as dash) */}
            <RowMeta>
              <span className="scoping-list-maturity">{maturity}</span>
            </RowMeta>

            {/* Accountable-team owner (batch-loaded by ScopingPage via useTeamAssignments) */}
            <RowMeta>
              <span
                className={`scoping-list-owner${ownerLabel ? '' : ' scoping-list-owner--unset'}`}
              >
                {ownerLabel || '—'}
              </span>
            </RowMeta>

            {/* In-scope tick */}
            <RowTickCircle on={control.selected} />
          </ExplorerListRow>
        </div>
      )
    },
    [controls, selection, toggleCheck, onOpenControl, ownerByControlId],
  )

  // ── Filter option lists ───────────────────────────────────────────────────

  const domainSelectOptions = useMemo(
    () => [
      allOption('All Domains'),
      ...domainOptions.filter((o) => o.value !== ALL),
    ],
    [domainOptions],
  )

  const csfSelectOptions = useMemo(
    () => [
      allOption('All CSF Functions'),
      ...nistCsfFunctions.filter((o) => o.value !== ALL),
    ],
    [nistCsfFunctions],
  )

  const weightSelectOptions = useMemo(
    () => [
      allOption('Any Weight'),
      ...controlWeights.filter((o) => o.value !== ALL),
    ],
    [controlWeights],
  )

  // Framework options: derive from frameworkNames prop (passed by container)
  const frameworkSelectOptions = useMemo(() => {
    const names = Object.entries(frameworkNames)
      .map(([k, v]) => ({ value: k, label: v ?? k }))
      .sort((a, b) => a.label.localeCompare(b.label))
    return [allOption('All Frameworks'), ...names]
  }, [frameworkNames])

  // Function options: only functions that have teams
  const usedFunctions = useMemo(() => {
    const withTeams = new Set(teams.flatMap((t) => t.function_ids ?? (t.function_id ? [t.function_id] : [])))
    return functions
      .filter((fn) => withTeams.has(fn.id))
      .sort((a, b) => {
        const oa = a.display_order ?? Number.MAX_SAFE_INTEGER
        const ob = b.display_order ?? Number.MAX_SAFE_INTEGER
        if (oa !== ob) return oa - ob
        return a.name.localeCompare(b.name)
      })
  }, [teams, functions])

  const functionSelectOptions = useMemo(
    () => [allOption('All Functions'), ...usedFunctions.map((fn) => ({ value: fn.id, label: fn.name }))],
    [usedFunctions],
  )

  // Narrow team list to selected function
  const selectableTeams = useMemo(() => {
    const scoped =
      filters.functionId === ALL
        ? teams
        : teams.filter((t) =>
            (t.function_ids ?? (t.function_id ? [t.function_id] : [])).includes(filters.functionId),
          )
    return [...scoped].sort((a, b) => a.name.localeCompare(b.name))
  }, [teams, filters.functionId])

  const teamSelectOptions = useMemo(
    () => [allOption('All Teams'), ...selectableTeams.map((t) => ({ value: t.id, label: t.name }))],
    [selectableTeams],
  )

  const ownerTypeSelectOptions = [
    { value: ALL, label: 'All Owner Types' },
    { value: 'external_contractor', label: 'Contractor-owned' },
    { value: 'internal', label: 'Internally owned' },
  ]

  // ── Toolbar count ─────────────────────────────────────────────────────────
  const inScopeCount = serverStats?.in_scope
  const countNode = (
    <span className="scoping-toolbar-count">
      {inScopeCount !== undefined && (
        <>
          <span className="scoping-count-scope">{inScopeCount} in scope</span>
          <span className="scoping-count-sep"> · </span>
        </>
      )}
      <span className="scoping-count-total">{total} controls</span>
    </span>
  )

  // ── Error ─────────────────────────────────────────────────────────────────
  if (isError) {
    return (
      <div className="scoping-page scoping-page--error">
        <p className="scoping-error-msg">
          Failed to load controls. Please try refreshing the page.
        </p>
      </div>
    )
  }

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="scoping-page">
      <FilterSidebar
        collapsed={filtersCollapsed}
        onToggleCollapsed={() => setFiltersCollapsed((c) => !c)}
        aria-label="Control scoping filters"
      >
        {/* SCOPE — radio-style group */}
        <FilterGroup label="SCOPE">
          <div className="scoping-scope-radio-group" role="radiogroup" aria-label="Scope filter">
            {(
              [
                { value: 'in_scope', label: 'In scope' },
                { value: 'out_of_scope', label: 'Out of scope' },
                { value: 'all', label: 'All controls' },
              ] as const
            ).map(({ value, label }) => (
              <label key={value} className="scoping-scope-radio">
                <input
                  type="radio"
                  name="scope-filter"
                  value={value}
                  checked={filters.scope === value}
                  onChange={() => onFiltersChange({ ...filters, scope: value })}
                />
                <span className="scoping-scope-radio-dot" aria-hidden="true" />
                <span className="scoping-scope-radio-label">{label}</span>
              </label>
            ))}
          </div>
        </FilterGroup>

        <FilterGroup label="DOMAIN">
          <FilterSelect
            value={filters.domain}
            onChange={(v) => onFiltersChange({ ...filters, domain: v })}
            options={domainSelectOptions}
          />
        </FilterGroup>

        <FilterGroup label="NIST CSF FUNCTION">
          <FilterSelect
            value={filters.csf}
            onChange={(v) => onFiltersChange({ ...filters, csf: v })}
            options={csfSelectOptions}
          />
        </FilterGroup>

        <FilterGroup label="CONTROL WEIGHT">
          <FilterSelect
            value={filters.weight}
            onChange={(v) => onFiltersChange({ ...filters, weight: v })}
            options={weightSelectOptions}
          />
        </FilterGroup>

        <FilterGroup label="FRAMEWORK">
          <FilterSelect
            value={filters.framework}
            onChange={(v) => onFiltersChange({ ...filters, framework: v })}
            options={frameworkSelectOptions}
          />
        </FilterGroup>

        <FilterGroup label="BUSINESS FUNCTION">
          <FilterSelect
            value={filters.functionId}
            onChange={(v) => {
              // Choosing a function may strand the team — reset team too
              onFiltersChange({ ...filters, functionId: v, teamId: ALL })
            }}
            options={functionSelectOptions}
          />
        </FilterGroup>

        <FilterGroup label="OWNING TEAM">
          <FilterSelect
            value={filters.teamId}
            onChange={(v) => onFiltersChange({ ...filters, teamId: v })}
            options={teamSelectOptions}
          />
        </FilterGroup>

        <FilterGroup label="ACCOUNTABLE OWNER">
          <FilterSelect
            value={filters.ownerType}
            onChange={(v) => onFiltersChange({ ...filters, ownerType: v })}
            options={ownerTypeSelectOptions}
          />
        </FilterGroup>
      </FilterSidebar>

      <div className="scoping-list-body">
        <ListToolbar
          search={search}
          onSearchChange={onSearchChange}
          searchPlaceholder="Search controls — id, name, description…"
          count={countNode}
          actions={
            <button
              type="button"
              className="btn btn-primary scoping-fw-btn"
              onClick={onScopeByFramework}
              aria-label="Scope by Framework"
            >
              Scope by Framework
            </button>
          }
        />

        {bulkBar}

        <div className="scoping-list-rows" ref={listContainerRef}>
          {isLoading ? (
            <div className="scoping-loading">
              <div className="loading-spinner-small" />
              <span>Loading controls…</span>
            </div>
          ) : controls.length === 0 ? (
            <div className="scoping-empty">
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
                initialScrollOffset={initialScrollOffset}
                className="scoping-virtualized-list"
              >
                {Row}
              </List>
              {isFetchingNextPage && (
                <div className="scoping-loading-more">
                  Loading more controls…
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
