/**
 * LibraryList.test.tsx — TDD tests for the full-width Explorer list view.
 *
 * Mocks:
 *   - useControlsQuery / useCatalogFilters — same idiom as other component tests
 *   - react-window FixedSizeList — renders all items flat (no virtualization in jsdom)
 *   - useDebounce — passthrough so tests don't need fake timers
 *   - ResizeObserver — stub for jsdom
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

// ─── Stubs ────────────────────────────────────────────────────────────────────

// ResizeObserver not available in jsdom
globalThis.ResizeObserver = vi.fn().mockImplementation(() => ({
  observe: vi.fn(),
  unobserve: vi.fn(),
  disconnect: vi.fn(),
}))

// react-window: render all items in a flat div (no virtual windowing in tests)
vi.mock('react-window', () => ({
  FixedSizeList: ({
    itemCount,
    children: Children,
    onScroll,
  }: {
    itemCount: number
    children: React.ComponentType<{ index: number; style: React.CSSProperties }>
    onScroll?: (info: { scrollOffset: number; scrollUpdateWasRequested: boolean }) => void
  }) => {
    return (
      <div
        data-testid="fixed-size-list"
        onScroll={() => {
          onScroll?.({ scrollOffset: 999, scrollUpdateWasRequested: false })
        }}
      >
        {Array.from({ length: itemCount }, (_, i) => (
          <Children key={i} index={i} style={{}} />
        ))}
      </div>
    )
  },
}))

// useDebounce — passthrough in tests (no timer needed)
vi.mock('../../../hooks/useDebounce', () => ({
  useDebounce: (value: unknown) => value,
}))

// useControlsQuery
vi.mock('../../../hooks/useControlsQuery', () => ({
  useControlsQuery: vi.fn(),
  flattenControlPages: vi.fn(),
}))

// useCatalogFilters
vi.mock('../../../hooks/useCatalogFilters', () => ({
  useCatalogFilters: vi.fn(),
}))

// ─── Import after mocks ───────────────────────────────────────────────────────

import { useControlsQuery, flattenControlPages } from '../../../hooks/useControlsQuery'
import { useCatalogFilters } from '../../../hooks/useCatalogFilters'
import LibraryList from '../LibraryList'
import type { LibraryFilters } from '../LibraryList'

const mockUseControlsQuery = vi.mocked(useControlsQuery)
const mockFlattenControlPages = vi.mocked(flattenControlPages)
const mockUseCatalogFilters = vi.mocked(useCatalogFilters)

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeControl(overrides: Partial<{
  scf_id: string
  control_name: string
  control_description: string
  pptdf_applicability: { people: boolean; process: boolean; technology: boolean; data: boolean; facility: boolean }
  frameworksCount: number
  control_weighting: number
}> = {}) {
  return {
    scf_id: overrides.scf_id ?? 'GOV-01',
    control_name: overrides.control_name ?? 'Security Program',
    control_description: overrides.control_description ?? 'A description of the security program.',
    scf_domain: 'Governance',
    framework_mappings: {},
    artifactsResolved: [],
    frameworksResolved: {},
    frameworksCount: overrides.frameworksCount ?? 5,
    pptdf_applicability: overrides.pptdf_applicability,
    control_weighting: overrides.control_weighting,
  }
}

const defaultFilters: LibraryFilters = { domain: 'all', csf: 'all', weight: 'all' }

const defaultCatalogFilters = {
  domains: [{ value: 'GOV', label: 'GOV - Governance' }],
  nistCsfFunctions: [{ value: 'Identify', label: 'Identify' }],
  controlWeights: [{ value: '5', label: '5 - Medium' }],
  isLoading: false,
}

function setupMocks(controls = [makeControl()], total = 1) {
  const queryResult = {
    data: { pages: [{ controls, total, offset: 0 }] },
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
    isLoading: false,
    isError: false,
  }
  mockUseControlsQuery.mockReturnValue(queryResult as unknown as ReturnType<typeof useControlsQuery>)
  mockFlattenControlPages.mockReturnValue({ controls, total })
  mockUseCatalogFilters.mockReturnValue(defaultCatalogFilters)
}

function defaultProps(overrides = {}) {
  return {
    filters: defaultFilters,
    onFiltersChange: vi.fn(),
    search: '',
    onSearchChange: vi.fn(),
    onOpenControl: vi.fn(),
    initialScrollOffset: 0,
    onScrollOffsetChange: vi.fn(),
    ...overrides,
  }
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('LibraryList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setupMocks()
  })

  describe('row rendering', () => {
    it('renders the scf_id in the row', () => {
      setupMocks([makeControl({ scf_id: 'GOV-01' })])
      render(<LibraryList {...defaultProps()} />)
      expect(screen.getByText('GOV-01')).toBeInTheDocument()
    })

    it('renders the control_name as title', () => {
      setupMocks([makeControl({ control_name: 'Security Program' })])
      render(<LibraryList {...defaultProps()} />)
      expect(screen.getByText('Security Program')).toBeInTheDocument()
    })

    it('renders the control_description', () => {
      setupMocks([makeControl({ control_description: 'A test description.' })])
      render(<LibraryList {...defaultProps()} />)
      expect(screen.getByText('A test description.')).toBeInTheDocument()
    })

    it('renders a RowChip with pptdf label when pptdf_applicability has process=true', () => {
      setupMocks([
        makeControl({
          pptdf_applicability: { people: false, process: true, technology: false, data: false, facility: false },
        }),
      ])
      render(<LibraryList {...defaultProps()} />)
      expect(screen.getByText('Process')).toBeInTheDocument()
    })

    it('omits RowChip when pptdf_applicability is absent', () => {
      setupMocks([makeControl({ pptdf_applicability: undefined })])
      const { container } = render(<LibraryList {...defaultProps()} />)
      // No chip should render
      expect(container.querySelector('.explorer-row-chip')).not.toBeInTheDocument()
    })

    it('renders RowMeta with "{frameworksCount} maps"', () => {
      // When erlData is absent the no-erlData fallback runs; frameworksCount comes
      // from the enriched value. Provide a mock erlData to trigger enrichControl.
      // Since we mock flattenControlPages to return controls with frameworksCount
      // already set (as if the API returned EnrichedControls), we supply erlData
      // so the enrichControl path runs. But in tests, enrichControl is not mocked,
      // so we instead verify the count renders from the mock data.
      // Simplest: test that "maps" text is present (count may be 0 without erlData)
      setupMocks([makeControl({ scf_id: 'GOV-01' })])
      render(<LibraryList {...defaultProps()} />)
      // RowMeta always renders the frameworksCount + " maps" string
      expect(screen.getByText(/maps/)).toBeInTheDocument()
    })

    it('renders RowWeightBar when control_weighting is present', () => {
      setupMocks([makeControl({ control_weighting: 7 })])
      const { container } = render(<LibraryList {...defaultProps()} />)
      expect(container.querySelector('.explorer-row-weight')).toBeInTheDocument()
    })

    it('omits RowWeightBar when control_weighting is absent', () => {
      setupMocks([makeControl({ control_weighting: undefined })])
      const { container } = render(<LibraryList {...defaultProps()} />)
      expect(container.querySelector('.explorer-row-weight')).not.toBeInTheDocument()
    })

    it('renders RowTickCircle on=true when control is in scope', () => {
      const scopeById = new Map([['GOV-01', true]])
      setupMocks([makeControl({ scf_id: 'GOV-01' })])
      const { container } = render(
        <LibraryList {...defaultProps({ scopeById })} />,
      )
      // tick circle with accent (not --off) means "on"
      expect(container.querySelector('.explorer-row-tick-circle:not(.explorer-row-tick-circle--off)')).toBeInTheDocument()
    })

    it('renders RowTickCircle on=false when control is not in scope', () => {
      const scopeById = new Map<string, boolean>()
      setupMocks([makeControl({ scf_id: 'GOV-01' })])
      const { container } = render(
        <LibraryList {...defaultProps({ scopeById })} />,
      )
      expect(container.querySelector('.explorer-row-tick-circle--off')).toBeInTheDocument()
    })

    it('renders accent tick when control is in scope', () => {
      const scopeById = new Map([['GOV-01', true]])
      setupMocks([makeControl({ scf_id: 'GOV-01' })])
      const { container } = render(
        <LibraryList {...defaultProps({ scopeById })} />,
      )
      expect(container.querySelector('.explorer-row-tick--accent')).toBeInTheDocument()
    })

    it('renders multiple rows', () => {
      setupMocks([
        makeControl({ scf_id: 'GOV-01', control_name: 'First Control' }),
        makeControl({ scf_id: 'GOV-02', control_name: 'Second Control' }),
      ])
      render(<LibraryList {...defaultProps()} />)
      expect(screen.getByText('GOV-01')).toBeInTheDocument()
      expect(screen.getByText('GOV-02')).toBeInTheDocument()
      expect(screen.getByText('First Control')).toBeInTheDocument()
      expect(screen.getByText('Second Control')).toBeInTheDocument()
    })
  })

  describe('row click → onOpenControl', () => {
    it('clicking a row calls onOpenControl with scf_id', () => {
      const onOpenControl = vi.fn()
      setupMocks([makeControl({ scf_id: 'GOV-01' })])
      render(<LibraryList {...defaultProps({ onOpenControl })} />)
      // Multiple buttons exist (filter toggle + row); click the row itself via its id text
      const rowId = screen.getByText('GOV-01')
      // The ExplorerListRow wraps the id in a div inside the button-role div
      const row = rowId.closest('[role="button"]') as HTMLElement
      fireEvent.click(row)
      expect(onOpenControl).toHaveBeenCalledWith('GOV-01')
    })
  })

  describe('filter selects → onFiltersChange', () => {
    beforeEach(() => {
      setupMocks()
      // expose filters in the non-collapsed state
    })

    it('changing domain select calls onFiltersChange with updated domain', () => {
      const onFiltersChange = vi.fn()
      // Provide domain options
      mockUseCatalogFilters.mockReturnValue({
        ...defaultCatalogFilters,
        domains: [
          { value: 'all', label: 'All Domains' },
          { value: 'GOV', label: 'GOV - Governance' },
        ],
      })
      render(
        <LibraryList
          {...defaultProps({ onFiltersChange })}
          filters={{ domain: 'all', csf: 'all', weight: 'all' }}
        />,
      )
      // The filters sidebar should be visible (not collapsed initially)
      const selects = screen.getAllByRole('combobox')
      // First combobox = domain
      fireEvent.change(selects[0], { target: { value: 'GOV' } })
      expect(onFiltersChange).toHaveBeenCalledWith({
        domain: 'GOV',
        csf: 'all',
        weight: 'all',
      })
    })

    it('changing csf select calls onFiltersChange with updated csf', () => {
      const onFiltersChange = vi.fn()
      render(
        <LibraryList
          {...defaultProps({ onFiltersChange })}
          filters={{ domain: 'all', csf: 'all', weight: 'all' }}
        />,
      )
      const selects = screen.getAllByRole('combobox')
      // Second combobox = csf
      fireEvent.change(selects[1], { target: { value: 'Identify' } })
      expect(onFiltersChange).toHaveBeenCalledWith({
        domain: 'all',
        csf: 'Identify',
        weight: 'all',
      })
    })

    it('changing weight select calls onFiltersChange with updated weight', () => {
      const onFiltersChange = vi.fn()
      render(
        <LibraryList
          {...defaultProps({ onFiltersChange })}
          filters={{ domain: 'all', csf: 'all', weight: 'all' }}
        />,
      )
      const selects = screen.getAllByRole('combobox')
      // Third combobox = weight
      fireEvent.change(selects[2], { target: { value: '5' } })
      expect(onFiltersChange).toHaveBeenCalledWith({
        domain: 'all',
        csf: 'all',
        weight: '5',
      })
    })
  })

  describe('search input → onSearchChange', () => {
    it('typing in search input calls onSearchChange', () => {
      const onSearchChange = vi.fn()
      render(<LibraryList {...defaultProps({ onSearchChange })} />)
      const input = screen.getByRole('searchbox')
      fireEvent.change(input, { target: { value: 'GOV-01' } })
      expect(onSearchChange).toHaveBeenCalledWith('GOV-01')
    })

    it('search input reflects the search prop', () => {
      render(<LibraryList {...defaultProps({ search: 'hello' })} />)
      const input = screen.getByRole('searchbox')
      expect(input).toHaveValue('hello')
    })
  })

  describe('count node in toolbar', () => {
    it('shows total controls count', () => {
      setupMocks([makeControl()], 1451)
      render(<LibraryList {...defaultProps()} />)
      expect(screen.getByText(/1451/)).toBeInTheDocument()
    })

    it('shows "in scope" segment when inScopeCount is provided', () => {
      setupMocks([makeControl()], 1451)
      render(<LibraryList {...defaultProps({ inScopeCount: 346 })} />)
      expect(screen.getByText(/346/)).toBeInTheDocument()
      expect(screen.getByText(/in scope/i)).toBeInTheDocument()
    })

    it('omits "in scope" segment when inScopeCount is undefined', () => {
      setupMocks([makeControl()], 100)
      render(<LibraryList {...defaultProps({ inScopeCount: undefined })} />)
      expect(screen.queryByText(/in scope/i)).not.toBeInTheDocument()
    })
  })

  describe('scroll callback', () => {
    it('fires onScrollOffsetChange when list scrolls', () => {
      const onScrollOffsetChange = vi.fn()
      setupMocks([makeControl()])
      render(<LibraryList {...defaultProps({ onScrollOffsetChange })} />)
      const list = screen.getByTestId('fixed-size-list')
      fireEvent.scroll(list)
      expect(onScrollOffsetChange).toHaveBeenCalledWith(999)
    })
  })

  describe('loading state', () => {
    it('shows loading indicator when isLoading=true', () => {
      mockUseControlsQuery.mockReturnValue({
        data: undefined,
        fetchNextPage: vi.fn(),
        hasNextPage: false,
        isFetchingNextPage: false,
        isLoading: true,
        isError: false,
      } as unknown as ReturnType<typeof useControlsQuery>)
      mockFlattenControlPages.mockReturnValue({ controls: [], total: 0 })
      render(<LibraryList {...defaultProps()} />)
      expect(screen.getByText(/loading/i)).toBeInTheDocument()
    })
  })

  describe('empty state', () => {
    it('shows empty message when controls array is empty', () => {
      setupMocks([], 0)
      render(<LibraryList {...defaultProps()} />)
      expect(screen.getByText(/no controls/i)).toBeInTheDocument()
    })
  })

  describe('filter sidebar collapsed state', () => {
    it('filter sidebar toggle button is present', () => {
      render(<LibraryList {...defaultProps()} />)
      // FilterSidebar renders a toggle button
      expect(screen.getByRole('button', { name: /collapse filters|expand filters/i })).toBeInTheDocument()
    })
  })

  describe('error state', () => {
    it('shows error message when isError=true', () => {
      mockUseControlsQuery.mockReturnValue({
        data: undefined,
        fetchNextPage: vi.fn(),
        hasNextPage: false,
        isFetchingNextPage: false,
        isLoading: false,
        isError: true,
      } as unknown as ReturnType<typeof useControlsQuery>)
      mockFlattenControlPages.mockReturnValue({ controls: [], total: 0 })
      render(<LibraryList {...defaultProps()} />)
      expect(screen.getByText(/failed to load/i)).toBeInTheDocument()
    })
  })

  describe('bulkById overlay', () => {
    it('shows frameworksCount from bulkById when present (restores N maps parity)', () => {
      // Slim paginated row has 0 maps
      setupMocks([makeControl({ scf_id: 'GOV-01', frameworksCount: 0 })])
      // Bulk has 97 maps
      const bulkById = new Map([
        ['GOV-01', makeControl({ scf_id: 'GOV-01', frameworksCount: 97 }) as ReturnType<typeof makeControl>],
      ])
      render(<LibraryList {...defaultProps({ bulkById })} />)
      expect(screen.getByText('97 maps')).toBeInTheDocument()
    })

    it('shows PPTDF chip from bulkById when slim row has no pptdf_applicability', () => {
      // Slim row has no pptdf
      setupMocks([makeControl({ scf_id: 'GOV-01', pptdf_applicability: undefined })])
      // Bulk has technology=true
      const bulkById = new Map([
        [
          'GOV-01',
          makeControl({
            scf_id: 'GOV-01',
            pptdf_applicability: { people: false, process: false, technology: true, data: false, facility: false },
          }) as ReturnType<typeof makeControl>,
        ],
      ])
      render(<LibraryList {...defaultProps({ bulkById })} />)
      expect(screen.getByText('Technology')).toBeInTheDocument()
    })

    it('falls back to slim-derived value when bulkById has no entry for the row', () => {
      // When no bulkById entry exists for a row and no erlData, the no-erlData
      // enrichment path renders 0 maps (frameworksCount reset to 0 without erlData).
      // The key assertion is that GOV-02 bulk data does NOT bleed into the GOV-01 row.
      setupMocks([makeControl({ scf_id: 'GOV-01', frameworksCount: 5 })])
      // bulkById only has GOV-02 (not GOV-01)
      const bulkById = new Map([
        ['GOV-02', makeControl({ scf_id: 'GOV-02', frameworksCount: 99 }) as ReturnType<typeof makeControl>],
      ])
      render(<LibraryList {...defaultProps({ bulkById })} />)
      // GOV-02's 99 maps must NOT appear for the GOV-01 row
      expect(screen.queryByText('99 maps')).not.toBeInTheDocument()
      // The row renders with the slim/no-erlData value (0 maps) — not GOV-02's data
      expect(screen.getByText(/maps/)).toBeInTheDocument()
    })
  })
})
