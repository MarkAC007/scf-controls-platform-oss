/**
 * ScopingList.test.tsx — TDD tests for the full-width Explorer list view
 * for Control Scoping (Task 1, Phase 3).
 *
 * Mirrors LibraryList.test.tsx patterns.
 *
 * Mocks:
 *   - useScopedControlsQuery / useScopedControlsStats / flattenScopedControlPages
 *   - useCatalogFilters — same idiom
 *   - react-window FixedSizeList — renders all items flat
 *   - useDebounce — passthrough
 *   - listTeams / listFunctions — returns empty arrays to avoid network
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

// react-window: render all items in a flat div
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

vi.mock('../../../hooks/useDebounce', () => ({
  useDebounce: (value: unknown) => value,
}))

vi.mock('../../../hooks/useScopedControlsQuery', () => ({
  useScopedControlsQuery: vi.fn(),
  useScopedControlsStats: vi.fn(),
  flattenScopedControlPages: vi.fn(),
}))

vi.mock('../../../hooks/useCatalogFilters', () => ({
  useCatalogFilters: vi.fn(),
}))

// listTeams / listFunctions: return empty arrays (team filters use these async)
vi.mock('../../../data/apiClient', () => ({
  listTeams: vi.fn().mockResolvedValue([]),
  listFunctions: vi.fn().mockResolvedValue([]),
}))

// ─── Import after mocks ───────────────────────────────────────────────────────

import {
  useScopedControlsQuery,
  useScopedControlsStats,
  flattenScopedControlPages,
} from '../../../hooks/useScopedControlsQuery'
import { useCatalogFilters } from '../../../hooks/useCatalogFilters'
import ScopingList from '../ScopingList'
import type { ScopingFilters } from '../ScopingList'

const mockUseScopedControlsQuery = vi.mocked(useScopedControlsQuery)
const mockUseScopedControlsStats = vi.mocked(useScopedControlsStats)
const mockFlattenScopedControlPages = vi.mocked(flattenScopedControlPages)
const mockUseCatalogFilters = vi.mocked(useCatalogFilters)

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Minimal factory for ScopedControlWithCatalog test objects.
 * NOTE: maturity_level and owner are NOT in the slim paginated serializer
 * (backend api/scoped_controls.py only joins selected/implementation_status/
 * selection_reason). Fields below reflect actual API response shape.
 */
function makeScopedControl(overrides: Partial<{
  scf_id: string
  control_name: string
  control_description: string
  implementation_status: string | null
  selected: boolean
}> = {}) {
  return {
    scf_id: overrides.scf_id ?? 'GOV-01',
    control_name: overrides.control_name ?? 'Security Program',
    control_description: overrides.control_description ?? 'A description.',
    implementation_status: overrides.implementation_status ?? 'not_started',
    selected: overrides.selected ?? true,
    // Required ScopedControlWithCatalog fields
    scf_domain: 'Governance',
    framework_mappings: {} as Record<string, string[]>,
    is_scoped: true,
    evidence_requests: [],
    pptdf_applicability: { people: false, process: false, technology: false, data: false, facility: false },
    cmm_maturity: {},
    business_size_guidance: {},
    scrm_focus: { tier1_strategic: false, tier2_operational: false, tier3_tactical: false },
    risk_threat_mapping: { risk_codes: [], threat_codes: [] },
  }
}

const defaultFilters: ScopingFilters = {
  scope: 'in_scope',
  domain: 'all',
  csf: 'all',
  weight: 'all',
  framework: 'all',
  teamId: 'all',
  functionId: 'all',
  ownerType: 'all',
}

const defaultCatalogFilters = {
  domains: [{ value: 'GOV', label: 'GOV - Governance' }],
  nistCsfFunctions: [{ value: 'Identify', label: 'Identify' }],
  controlWeights: [{ value: '5', label: '5 - Medium' }],
  isLoading: false,
}

function setupMocks(controls = [makeScopedControl()], total = 1) {
  const queryResult = {
    data: { pages: [{ controls, total, offset: 0 }] },
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
    isLoading: false,
    isFetching: false,
    isError: false,
    refetch: vi.fn(),
  }
  mockUseScopedControlsQuery.mockReturnValue(
    queryResult as unknown as ReturnType<typeof useScopedControlsQuery>,
  )
  mockFlattenScopedControlPages.mockReturnValue({ controls, total })
  mockUseCatalogFilters.mockReturnValue(defaultCatalogFilters)
  mockUseScopedControlsStats.mockReturnValue({
    data: { in_scope: 346, total_controls: 1534, implemented: 19 },
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useScopedControlsStats>)
}

function defaultProps(overrides: Record<string, unknown> = {}) {
  return {
    organizationId: 'org-1',
    filters: defaultFilters,
    onFiltersChange: vi.fn(),
    search: '',
    onSearchChange: vi.fn(),
    onOpenControl: vi.fn(),
    onScopeByFramework: vi.fn(),
    selection: new Set<string>(),
    onSelectionChange: vi.fn(),
    initialScrollOffset: 0,
    onScrollOffsetChange: vi.fn(),
    frameworkNames: {},
    ...overrides,
  }
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('ScopingList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setupMocks()
  })

  // ── Row rendering ──────────────────────────────────────────────────────────

  describe('row rendering', () => {
    it('renders scf_id in the row', () => {
      setupMocks([makeScopedControl({ scf_id: 'GOV-01' })])
      render(<ScopingList {...defaultProps()} />)
      expect(screen.getByText('GOV-01')).toBeInTheDocument()
    })

    it('renders control_name as title', () => {
      setupMocks([makeScopedControl({ control_name: 'Asset Governance' })])
      render(<ScopingList {...defaultProps()} />)
      expect(screen.getByText('Asset Governance')).toBeInTheDocument()
    })

    it('renders control_description as one-line description', () => {
      setupMocks([makeScopedControl({ control_description: 'Mechanisms exist.' })])
      render(<ScopingList {...defaultProps()} />)
      expect(screen.getByText('Mechanisms exist.')).toBeInTheDocument()
    })

    it('renders implementation-status badge', () => {
      setupMocks([makeScopedControl({ implementation_status: 'in_progress' })])
      const { container } = render(<ScopingList {...defaultProps()} />)
      // Implementation status badge uses status-badge-compact
      expect(container.querySelector('.status-badge-compact')).toBeInTheDocument()
    })

    it('renders maturity placeholder (—) since slim serializer omits maturity_level', () => {
      // DEVIATION: maturity_level is not in the paginated API response.
      // The component renders a dash placeholder gracefully.
      // Note: owner column also renders '—' when no ownerByControlId is provided,
      // so getAllByText is used to handle both occurrences.
      setupMocks([makeScopedControl()])
      render(<ScopingList {...defaultProps()} />)
      expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(1)
    })

    it('renders in-scope tick when control is selected', () => {
      setupMocks([makeScopedControl({ selected: true })])
      const { container } = render(<ScopingList {...defaultProps()} />)
      expect(
        container.querySelector('.explorer-row-tick-circle:not(.explorer-row-tick-circle--off)'),
      ).toBeInTheDocument()
    })

    it('renders off tick when control is not selected', () => {
      setupMocks([makeScopedControl({ selected: false })])
      const { container } = render(<ScopingList {...defaultProps()} />)
      expect(container.querySelector('.explorer-row-tick-circle--off')).toBeInTheDocument()
    })

    it('renders accent tick (explorer-row-tick--accent) for in-scope controls', () => {
      setupMocks([makeScopedControl({ selected: true })])
      const { container } = render(<ScopingList {...defaultProps()} />)
      expect(container.querySelector('.explorer-row-tick--accent')).toBeInTheDocument()
    })

    it('renders multiple rows', () => {
      setupMocks([
        makeScopedControl({ scf_id: 'GOV-01', control_name: 'First' }),
        makeScopedControl({ scf_id: 'GOV-02', control_name: 'Second' }),
      ])
      render(<ScopingList {...defaultProps()} />)
      expect(screen.getByText('GOV-01')).toBeInTheDocument()
      expect(screen.getByText('GOV-02')).toBeInTheDocument()
    })
  })

  // ── Row click ──────────────────────────────────────────────────────────────

  describe('row click → onOpenControl', () => {
    it('clicking a row calls onOpenControl with scf_id', () => {
      const onOpenControl = vi.fn()
      setupMocks([makeScopedControl({ scf_id: 'GOV-01' })])
      render(<ScopingList {...defaultProps({ onOpenControl })} />)
      const rowId = screen.getByText('GOV-01')
      const row = rowId.closest('[role="button"]') as HTMLElement
      fireEvent.click(row)
      expect(onOpenControl).toHaveBeenCalledWith('GOV-01')
    })
  })

  // ── Checkbox bulk selection ─────────────────────────────────────────────────

  describe('bulk checkbox selection', () => {
    it('renders a checkbox per row with aria-label', () => {
      setupMocks([makeScopedControl({ scf_id: 'GOV-01' })])
      render(<ScopingList {...defaultProps()} />)
      const checkbox = screen.getByRole('checkbox', { name: /GOV-01/i })
      expect(checkbox).toBeInTheDocument()
    })

    it('checking a row checkbox calls onSelectionChange adding the scf_id', () => {
      const onSelectionChange = vi.fn()
      setupMocks([makeScopedControl({ scf_id: 'GOV-01' })])
      render(<ScopingList {...defaultProps({ onSelectionChange })} />)
      const checkbox = screen.getByRole('checkbox', { name: /GOV-01/i })
      fireEvent.click(checkbox)
      expect(onSelectionChange).toHaveBeenCalledTimes(1)
      // The new Set passed should contain GOV-01
      const newSet = onSelectionChange.mock.calls[0][0] as Set<string>
      expect(newSet.has('GOV-01')).toBe(true)
    })

    it('unchecking a row checkbox calls onSelectionChange removing the scf_id', () => {
      const onSelectionChange = vi.fn()
      const selection = new Set(['GOV-01'])
      setupMocks([makeScopedControl({ scf_id: 'GOV-01' })])
      render(<ScopingList {...defaultProps({ onSelectionChange, selection })} />)
      const checkbox = screen.getByRole('checkbox', { name: /GOV-01/i })
      // checkbox should be checked since GOV-01 is in selection
      expect(checkbox).toBeChecked()
      fireEvent.click(checkbox)
      expect(onSelectionChange).toHaveBeenCalledTimes(1)
      const newSet = onSelectionChange.mock.calls[0][0] as Set<string>
      expect(newSet.has('GOV-01')).toBe(false)
    })
  })

  // ── Scope radio group (default in_scope) ───────────────────────────────────

  describe('scope radio group', () => {
    it('renders three scope radio options', () => {
      render(<ScopingList {...defaultProps()} />)
      const radios = screen.getAllByRole('radio')
      expect(radios.length).toBeGreaterThanOrEqual(3)
    })

    it('in_scope radio is checked by default', () => {
      render(<ScopingList {...defaultProps({ filters: { ...defaultFilters, scope: 'in_scope' } })} />)
      const inScopeRadio = screen.getByRole('radio', { name: /in scope/i })
      expect(inScopeRadio).toBeChecked()
    })

    it('selecting "all" radio calls onFiltersChange with scope: all', () => {
      const onFiltersChange = vi.fn()
      render(<ScopingList {...defaultProps({ onFiltersChange })} />)
      const allRadio = screen.getByRole('radio', { name: /all controls/i })
      fireEvent.click(allRadio)
      expect(onFiltersChange).toHaveBeenCalledWith(
        expect.objectContaining({ scope: 'all' }),
      )
    })

    it('selecting "out of scope" radio calls onFiltersChange with scope: out_of_scope', () => {
      const onFiltersChange = vi.fn()
      render(<ScopingList {...defaultProps({ onFiltersChange })} />)
      const outRadio = screen.getByRole('radio', { name: /out of scope/i })
      fireEvent.click(outRadio)
      expect(onFiltersChange).toHaveBeenCalledWith(
        expect.objectContaining({ scope: 'out_of_scope' }),
      )
    })
  })

  // ── Filter selects ─────────────────────────────────────────────────────────

  describe('filter selects → onFiltersChange', () => {
    it('changing domain select calls onFiltersChange with updated domain', () => {
      const onFiltersChange = vi.fn()
      mockUseCatalogFilters.mockReturnValue({
        ...defaultCatalogFilters,
        domains: [
          { value: 'all', label: 'All Domains' },
          { value: 'GOV', label: 'GOV - Governance' },
        ],
      })
      render(<ScopingList {...defaultProps({ onFiltersChange })} />)
      const selects = screen.getAllByRole('combobox')
      // domain is the first combobox (after scope radios in sidebar)
      const domainSelect = selects[0]
      fireEvent.change(domainSelect, { target: { value: 'GOV' } })
      expect(onFiltersChange).toHaveBeenCalledWith(
        expect.objectContaining({ domain: 'GOV' }),
      )
    })
  })

  // ── Toolbar ───────────────────────────────────────────────────────────────

  describe('toolbar', () => {
    it('renders search input', () => {
      render(<ScopingList {...defaultProps()} />)
      expect(screen.getByRole('searchbox')).toBeInTheDocument()
    })

    it('typing in search calls onSearchChange', () => {
      const onSearchChange = vi.fn()
      render(<ScopingList {...defaultProps({ onSearchChange })} />)
      const input = screen.getByRole('searchbox')
      fireEvent.change(input, { target: { value: 'GOV' } })
      expect(onSearchChange).toHaveBeenCalledWith('GOV')
    })

    it('shows in-scope count from stats in toolbar', () => {
      mockUseScopedControlsStats.mockReturnValue({
        data: { in_scope: 346, total_controls: 1534, implemented: 19 },
        refetch: vi.fn(),
      } as unknown as ReturnType<typeof useScopedControlsStats>)
      render(<ScopingList {...defaultProps()} />)
      // The toolbar count renders "346 in scope" in a span
      expect(screen.getByText(/346 in scope/i)).toBeInTheDocument()
    })

    it('renders "Scope by Framework" button', () => {
      render(<ScopingList {...defaultProps()} />)
      expect(screen.getByRole('button', { name: /scope by framework/i })).toBeInTheDocument()
    })

    it('clicking "Scope by Framework" calls onScopeByFramework', () => {
      const onScopeByFramework = vi.fn()
      render(<ScopingList {...defaultProps({ onScopeByFramework })} />)
      fireEvent.click(screen.getByRole('button', { name: /scope by framework/i }))
      expect(onScopeByFramework).toHaveBeenCalledTimes(1)
    })
  })

  // ── Loading / empty / error states ────────────────────────────────────────

  describe('loading state', () => {
    it('shows loading indicator when isLoading=true', () => {
      mockUseScopedControlsQuery.mockReturnValue({
        data: undefined,
        fetchNextPage: vi.fn(),
        hasNextPage: false,
        isFetchingNextPage: false,
        isLoading: true,
        isFetching: false,
        isError: false,
        refetch: vi.fn(),
      } as unknown as ReturnType<typeof useScopedControlsQuery>)
      mockFlattenScopedControlPages.mockReturnValue({ controls: [], total: 0 })
      render(<ScopingList {...defaultProps()} />)
      expect(screen.getByText(/loading/i)).toBeInTheDocument()
    })
  })

  describe('empty state', () => {
    it('shows empty message when controls array is empty', () => {
      setupMocks([], 0)
      render(<ScopingList {...defaultProps()} />)
      expect(screen.getByText(/no controls/i)).toBeInTheDocument()
    })
  })

  describe('error state', () => {
    it('shows error message when isError=true', () => {
      mockUseScopedControlsQuery.mockReturnValue({
        data: undefined,
        fetchNextPage: vi.fn(),
        hasNextPage: false,
        isFetchingNextPage: false,
        isLoading: false,
        isFetching: false,
        isError: true,
        refetch: vi.fn(),
      } as unknown as ReturnType<typeof useScopedControlsQuery>)
      mockFlattenScopedControlPages.mockReturnValue({ controls: [], total: 0 })
      render(<ScopingList {...defaultProps()} />)
      expect(screen.getByText(/failed to load/i)).toBeInTheDocument()
    })
  })

  // ── Scroll ────────────────────────────────────────────────────────────────

  describe('scroll callback', () => {
    it('fires onScrollOffsetChange when list scrolls', () => {
      const onScrollOffsetChange = vi.fn()
      setupMocks([makeScopedControl()])
      render(<ScopingList {...defaultProps({ onScrollOffsetChange })} />)
      const list = screen.getByTestId('fixed-size-list')
      fireEvent.scroll(list)
      expect(onScrollOffsetChange).toHaveBeenCalledWith(999)
    })
  })

  // ── Filter sidebar ────────────────────────────────────────────────────────

  describe('filter sidebar', () => {
    it('filter sidebar toggle button is present', () => {
      render(<ScopingList {...defaultProps()} />)
      expect(
        screen.getByRole('button', { name: /collapse filters|expand filters/i }),
      ).toBeInTheDocument()
    })
  })
})

describe('bulk bar placement', () => {
  // The artboard puts the bulk bar as a strip between the toolbar and the
  // rows. Rendered as a page-level sibling instead, the flex-row
  // .scoping-page container turns it into a full-height column that crushes
  // the list (shipped that way originally) — so the placement is pinned.
  it('renders the bulkBar node between the toolbar and the rows', () => {
    setupMocks([makeScopedControl({ scf_id: 'GOV-01' })])
    const { container } = render(
      <ScopingList
        {...defaultProps({
          bulkBar: <div data-testid="test-bulk-bar">bulk actions</div>,
        })}
      />,
    )
    const body = container.querySelector('.scoping-list-body')
    expect(body).not.toBeNull()
    const children = Array.from(body!.children)
    const toolbarIdx = children.findIndex((el) => el.querySelector('input[type="search"], input') !== null || el.className.includes('toolbar'))
    const barIdx = children.findIndex((el) => el.querySelector('[data-testid="test-bulk-bar"]') !== null || (el as HTMLElement).dataset.testid === 'test-bulk-bar')
    const rowsIdx = children.findIndex((el) => el.className.includes('scoping-list-rows'))
    expect(barIdx).toBeGreaterThan(-1)
    expect(rowsIdx).toBeGreaterThan(-1)
    expect(barIdx).toBeGreaterThan(toolbarIdx)
    expect(barIdx).toBeLessThan(rowsIdx)
  })

  it('renders no bar when the prop is absent', () => {
    setupMocks([makeScopedControl({ scf_id: 'GOV-01' })])
    render(<ScopingList {...defaultProps()} />)
    expect(screen.queryByTestId('test-bulk-bar')).toBeNull()
  })
})
