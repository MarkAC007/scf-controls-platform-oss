import { useState, useRef, useCallback, useMemo, useEffect } from 'react'
import { FixedSizeList as List, ListChildComponentProps } from 'react-window'
import type { CollectionInterfacesFile, ERLFile, FrameworkNameMap, EnrichedControl } from '../types'
import { useControlsQuery, flattenControlPages } from '../hooks/useControlsQuery'
import { useCatalogFilters } from '../hooks/useCatalogFilters'
import { useDebounce } from '../hooks/useDebounce'
import { enrichControl } from '../data/loaders'
import { SidebarControlCard } from './SidebarControlCard'

interface Props {
  selectedId?: string
  onSelect: (id: string) => void
  collectionInterfaces?: CollectionInterfacesFile
  erlData?: ERLFile
  frameworkNames?: FrameworkNameMap
}

const ITEM_HEIGHT = 70 // Height of each control card in pixels
const LIST_HEIGHT = 600 // Default list height

export default function ControlList({
  selectedId,
  onSelect,
  collectionInterfaces,
  erlData,
  frameworkNames = {},
}: Props) {
  // Local state for filters and search
  const [domainFilter, setDomainFilter] = useState<string>('all')
  const [csfFilter, setCsfFilter] = useState<string>('all')
  const [weightFilter, setWeightFilter] = useState<string>('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [showFilters, setShowFilters] = useState(false)
  const [listHeight, setListHeight] = useState(LIST_HEIGHT)

  // Debounce search input by 300ms
  const debouncedSearch = useDebounce(searchQuery, 300)

  // Container ref for measuring height
  const containerRef = useRef<HTMLDivElement>(null)
  const listContainerRef = useRef<HTMLDivElement>(null)

  // Load filter options from API
  const { domains: domainOptions, nistCsfFunctions, controlWeights, isLoading: filtersLoading } = useCatalogFilters()

  // Query controls with filters - refetches when filters change
  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading,
    isError,
  } = useControlsQuery({
    search: debouncedSearch || undefined,
    domain: domainFilter !== 'all' ? domainFilter : undefined,
    csf_function: csfFilter !== 'all' ? csfFilter : undefined,
    control_weighting: weightFilter !== 'all' ? parseInt(weightFilter, 10) : undefined,
  })

  // Flatten paginated results
  const { controls: rawControls, total } = flattenControlPages(data?.pages)

  // Enrich controls for display (adds artifactsResolved, frameworksCount)
  const controls: EnrichedControl[] = useMemo(() => {
    if (!erlData) return rawControls.map(c => ({
      ...c,
      artifactsResolved: [],
      frameworksResolved: {},
      frameworksCount: 0,
    }))
    return rawControls.map(c => enrichControl(c, {}, erlData, frameworkNames))
  }, [rawControls, erlData, frameworkNames])

  // Measure container height on mount and resize
  useEffect(() => {
    const updateHeight = () => {
      if (listContainerRef.current) {
        const rect = listContainerRef.current.getBoundingClientRect()
        // Use available height, min 400px
        setListHeight(Math.max(400, window.innerHeight - rect.top - 40))
      }
    }

    updateHeight()
    window.addEventListener('resize', updateHeight)
    return () => window.removeEventListener('resize', updateHeight)
  }, [showFilters])

  // Load more when scrolling near the end
  const handleScroll = useCallback(({ scrollOffset, scrollUpdateWasRequested }: { scrollOffset: number; scrollUpdateWasRequested: boolean }) => {
    if (scrollUpdateWasRequested) return // Ignore programmatic scrolls

    const scrollHeight = controls.length * ITEM_HEIGHT
    const scrollThreshold = scrollHeight - listHeight - (ITEM_HEIGHT * 5) // Load when 5 items from end

    if (scrollOffset > scrollThreshold && hasNextPage && !isFetchingNextPage) {
      fetchNextPage()
    }
  }, [controls.length, listHeight, hasNextPage, isFetchingNextPage, fetchNextPage])

  // Clear selection when filters change
  const handleDomainChange = useCallback((value: string) => {
    setDomainFilter(value)
    onSelect('') // Clear selection
  }, [onSelect])

  const handleCsfChange = useCallback((value: string) => {
    setCsfFilter(value)
    onSelect('') // Clear selection
  }, [onSelect])

  const handleWeightChange = useCallback((value: string) => {
    setWeightFilter(value)
    onSelect('') // Clear selection
  }, [onSelect])

  const handleSearchChange = useCallback((value: string) => {
    setSearchQuery(value)
  }, [])

  // Row renderer for virtualized list
  const Row = useCallback(({ index, style }: ListChildComponentProps) => {
    const control = controls[index]
    if (!control) {
      return (
        <div style={style} className="control-card-loading">
          <div className="loading-skeleton" />
        </div>
      )
    }

    return (
      <SidebarControlCard
        style={style}
        scfId={control.scf_id}
        controlName={control.control_name}
        isSelected={control.scf_id === selectedId}
        onSelect={() => onSelect(control.scf_id)}
      />
    )
  }, [controls, selectedId, onSelect])

  const activeFiltersCount = [
    domainFilter !== 'all',
    csfFilter !== 'all',
    weightFilter !== 'all',
  ].filter(Boolean).length

  if (isError) {
    return (
      <div className="sidebar">
        <div className="error-message">
          Failed to load controls. Please try refreshing the page.
        </div>
      </div>
    )
  }

  return (
    <div className="sidebar" ref={containerRef}>
      <div className="library-sidebar-header">
        <div className="library-stats-compact">
          <div className="stat-compact">
            <div className="stat-compact-value">
              {isLoading ? '...' : controls.length}
            </div>
            <div className="stat-compact-label">
              Controls Showing
            </div>
          </div>
          {controls.length !== total && (
            <>
              <div className="stat-compact-divider">/</div>
              <div className="stat-compact">
                <div className="stat-compact-value">{total}</div>
                <div className="stat-compact-label">Total</div>
              </div>
            </>
          )}
        </div>
      </div>

      <div className="search">
        <input
          type="text"
          placeholder="Search controls by id, name, description…"
          value={searchQuery}
          onChange={(e) => handleSearchChange(e.target.value)}
        />
        {debouncedSearch !== searchQuery && (
          <span className="search-indicator">...</span>
        )}
      </div>

      <button
        className={`filters-toggle-btn ${showFilters ? 'active' : ''}`}
        onClick={() => setShowFilters(!showFilters)}
      >
        ⚙ Filters {activeFiltersCount > 0 && (
          <span className="filter-badge">{activeFiltersCount}</span>
        )}
      </button>

      {showFilters && (
        <div className="filters-dropdown">
          <select
            value={domainFilter}
            onChange={(e) => handleDomainChange(e.target.value)}
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
            onChange={(e) => handleCsfChange(e.target.value)}
            className="filter-select"
          >
            <option value="all">All NIST CSF Functions</option>
            {nistCsfFunctions.map((func) => (
              <option key={func.value} value={func.value}>
                {func.label}
              </option>
            ))}
          </select>

          <select
            value={weightFilter}
            onChange={(e) => handleWeightChange(e.target.value)}
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

      <div className="list" ref={listContainerRef}>
        {isLoading ? (
          <div className="loading-controls">
            <div className="loading-spinner-small" />
            <span>Loading controls...</span>
          </div>
        ) : controls.length === 0 ? (
          <div className="no-results">
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
    </div>
  )
}
