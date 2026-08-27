/**
 * LibraryPage.test.tsx — TDD tests for the library container.
 *
 * LibraryPage owns filters/search/scrollOffset state; renders LibraryList
 * when item==null, else ControlDetailPage for the resolved control.
 *
 * Mocks:
 *   - LibraryList — record prop calls
 *   - ControlDetailPage — record prop calls
 *   - useControlsQuery / fetchControlsPage — for deep-link resolution
 */
import { render, screen, act, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

// ─── Mocks ────────────────────────────────────────────────────────────────────

// Capture the last props each child receives
let lastLibraryListProps: Record<string, unknown> = {}
let lastDetailPageProps: Record<string, unknown> = {}

vi.mock('../LibraryList', () => ({
  default: (props: Record<string, unknown>) => {
    lastLibraryListProps = props
    return <div data-testid="library-list" />
  },
}))

vi.mock('../ControlDetailPage', () => ({
  default: (props: Record<string, unknown>) => {
    lastDetailPageProps = props
    const control = props.control as { scf_id?: string } | undefined
    return (
      <div
        data-testid="control-detail-page"
        data-scf-id={control?.scf_id ?? ''}
      />
    )
  },
}))

// useControlsQuery — for the infinite query used by LibraryPage to load the
// full filtered list so it can derive position
vi.mock('../../../hooks/useControlsQuery', () => ({
  useControlsQuery: vi.fn(),
  flattenControlPages: vi.fn(),
}))

// fetchControlsPage — for deep-link resolution (ruling 5)
vi.mock('../../../data/catalogApi', () => ({
  fetchControlsPage: vi.fn(),
}))

// useDebounce — passthrough so tests don't need fake timers
vi.mock('../../../hooks/useDebounce', () => ({
  useDebounce: (value: unknown) => value,
}))

// enrichControl — lightweight stub
vi.mock('../../../data/loaders', () => ({
  enrichControl: vi.fn((c: unknown) => c),
}))

// ─── Imports (after mocks) ────────────────────────────────────────────────────

import { useControlsQuery, flattenControlPages } from '../../../hooks/useControlsQuery'
import { fetchControlsPage } from '../../../data/catalogApi'
import LibraryPage from '../LibraryPage'
import type { LibraryPageProps } from '../LibraryPage'
import type { EnrichedControl } from '../../../types'

const mockUseControlsQuery = vi.mocked(useControlsQuery)
const mockFlattenControlPages = vi.mocked(flattenControlPages)
const mockFetchControlsPage = vi.mocked(fetchControlsPage)

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeControl(scf_id: string, overrides: Record<string, unknown> = {}) {
  return {
    scf_id,
    control_name: `Control ${scf_id}`,
    control_description: `Description of ${scf_id}`,
    scf_domain: 'Governance',
    framework_mappings: {},
    artifactsResolved: [],
    frameworksResolved: {},
    frameworksCount: 0,
    ...overrides,
  }
}

/** Create a bulk-style control with full fields (as returned by /api/catalog/bulk/controls). */
function makeBulkControl(scf_id: string, extraOverrides: Record<string, unknown> = {}) {
  return {
    ...makeControl(scf_id),
    // Fields only present in the bulk serializer
    control_question: `Bulk question for ${scf_id}`,
    pptdf_applicability: { people: true, process: false, technology: false, data: false, facility: false },
    frameworksResolved: { 'NIST SP 800-53': ['AC-1', 'AC-2'] },
    frameworksCount: 97,
    ...extraOverrides,
  }
}

function setupQueryMock(controls = [makeControl('GOV-01')], total = 1) {
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
}

function defaultProps(overrides: Partial<LibraryPageProps> = {}): LibraryPageProps {
  return {
    item: null,
    onItemChange: vi.fn(),
    scopingData: null,
    erlData: {},
    frameworkNames: {},
    onNavigateToEvidence: vi.fn(),
    ...overrides,
  }
}

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('LibraryPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    lastLibraryListProps = {}
    lastDetailPageProps = {}
    setupQueryMock()
    mockFetchControlsPage.mockResolvedValue({
      controls: [],
      total: 0,
      limit: 50,
      offset: 0,
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  // ── Default: renders list ─────────────────────────────────────────────────

  it('renders LibraryList when item is null', () => {
    render(<LibraryPage {...defaultProps()} />, { wrapper: wrapper() })
    expect(screen.getByTestId('library-list')).toBeInTheDocument()
    expect(screen.queryByTestId('control-detail-page')).not.toBeInTheDocument()
  })

  // ── item set → renders detail ──────────────────────────────────────────────

  it('renders ControlDetailPage when item is set and control is in the loaded list', () => {
    setupQueryMock([makeControl('GOV-01'), makeControl('GOV-02')], 2)
    render(
      <LibraryPage {...defaultProps({ item: 'GOV-01' })} />,
      { wrapper: wrapper() },
    )
    expect(screen.getByTestId('control-detail-page')).toBeInTheDocument()
    expect(screen.queryByTestId('library-list')).not.toBeInTheDocument()
  })

  // ── onOpenControl → onItemChange ──────────────────────────────────────────

  it('onOpenControl passed to LibraryList calls onItemChange with the id', () => {
    const onItemChange = vi.fn()
    render(<LibraryPage {...defaultProps({ onItemChange })} />, { wrapper: wrapper() })

    // LibraryList received onOpenControl — simulate calling it
    const { onOpenControl } = lastLibraryListProps as { onOpenControl: (id: string) => void }
    act(() => {
      onOpenControl('GOV-01')
    })

    expect(onItemChange).toHaveBeenCalledWith('GOV-01')
  })

  // ── pager next → onItemChange with next filtered id ───────────────────────

  it('onNext passed to ControlDetailPage calls onItemChange with next id', () => {
    const onItemChange = vi.fn()
    setupQueryMock([makeControl('GOV-01'), makeControl('GOV-02'), makeControl('GOV-03')], 3)
    render(
      <LibraryPage {...defaultProps({ item: 'GOV-01', onItemChange })} />,
      { wrapper: wrapper() },
    )

    const { onNext } = lastDetailPageProps as { onNext: () => void }
    act(() => {
      onNext()
    })

    // GOV-01 is index 0, next is GOV-02
    expect(onItemChange).toHaveBeenCalledWith('GOV-02')
  })

  it('onPrev passed to ControlDetailPage calls onItemChange with prev id', () => {
    const onItemChange = vi.fn()
    setupQueryMock([makeControl('GOV-01'), makeControl('GOV-02'), makeControl('GOV-03')], 3)
    render(
      <LibraryPage {...defaultProps({ item: 'GOV-02', onItemChange })} />,
      { wrapper: wrapper() },
    )

    const { onPrev } = lastDetailPageProps as { onPrev: () => void }
    act(() => {
      onPrev()
    })

    // GOV-02 is index 1, prev is GOV-01
    expect(onItemChange).toHaveBeenCalledWith('GOV-01')
  })

  // ── back → onItemChange(null) ─────────────────────────────────────────────

  it('onBack passed to ControlDetailPage calls onItemChange with null', () => {
    const onItemChange = vi.fn()
    setupQueryMock([makeControl('GOV-01')], 1)
    render(
      <LibraryPage {...defaultProps({ item: 'GOV-01', onItemChange })} />,
      { wrapper: wrapper() },
    )

    const { onBack } = lastDetailPageProps as { onBack: () => void }
    act(() => {
      onBack()
    })

    expect(onItemChange).toHaveBeenCalledWith(null)
  })

  // ── filters + scroll survive round-trip ──────────────────────────────────

  it('filters and scrollOffset survive detail round-trip (same values on back)', () => {
    const onItemChange = vi.fn()
    setupQueryMock([makeControl('GOV-01'), makeControl('GOV-02')], 2)
    const { rerender } = render(
      <LibraryPage {...defaultProps({ item: null, onItemChange })} />,
      { wrapper: wrapper() },
    )

    // Change filters and scroll via LibraryList props
    const { onFiltersChange, onScrollOffsetChange } = lastLibraryListProps as {
      onFiltersChange: (f: { domain: string; csf: string; weight: string }) => void
      onScrollOffsetChange: (offset: number) => void
    }
    act(() => {
      onFiltersChange({ domain: 'GOV', csf: 'Identify', weight: '5' })
      onScrollOffsetChange(450)
    })

    // Navigate to detail (simulate App passing item='GOV-01')
    rerender(
      <LibraryPage
        {...defaultProps({ item: 'GOV-01', onItemChange })}
      />,
    )
    expect(screen.getByTestId('control-detail-page')).toBeInTheDocument()

    // Navigate back (simulate App passing item=null)
    rerender(
      <LibraryPage {...defaultProps({ item: null, onItemChange })} />,
    )

    // LibraryList should have received the preserved filters and scroll offset
    expect(lastLibraryListProps.filters).toEqual({
      domain: 'GOV',
      csf: 'Identify',
      weight: '5',
    })
    expect(lastLibraryListProps.initialScrollOffset).toBe(450)
  })

  // ── position derived from filtered list ──────────────────────────────────

  it('position passed to ControlDetailPage is index in filtered list + total', () => {
    setupQueryMock(
      [makeControl('GOV-01'), makeControl('GOV-02'), makeControl('GOV-03')],
      3,
    )
    render(
      <LibraryPage {...defaultProps({ item: 'GOV-02' })} />,
      { wrapper: wrapper() },
    )
    const { position } = lastDetailPageProps as {
      position: { index: number; total: number } | null
    }
    // GOV-02 is index 1 in the list of 3
    expect(position).toEqual({ index: 1, total: 3 })
  })

  // ── deep-link item not in loaded pages → one-shot fetch ──────────────────

  it('resolves a deep-linked item not in the loaded list via one-shot fetch', async () => {
    setupQueryMock([makeControl('GOV-01')], 1) // Current filtered list does NOT contain GOV-99
    const deepLinkedControl = makeControl('GOV-99')
    mockFetchControlsPage.mockResolvedValue({
      controls: [deepLinkedControl],
      total: 1,
      limit: 1,
      offset: 0,
    })

    render(
      <LibraryPage {...defaultProps({ item: 'GOV-99' })} />,
      { wrapper: wrapper() },
    )

    await waitFor(() => {
      expect(screen.getByTestId('control-detail-page')).toBeInTheDocument()
    })

    // Position should be { index: null, total: 1 } — item resolved but not in filtered set
    // (shows "— of N" in ControlDetailPage; both pager buttons disabled)
    expect(lastDetailPageProps.position).toEqual({ index: null, total: 1 })
  })

  // ── deep-link not in filtered set → { index: null, total } wiring ────────

  it('passes { index: null, total } to ControlDetailPage when item resolved but not in filtered set', async () => {
    // Filtered list has 3 controls (total=3); item is not among them
    setupQueryMock([makeControl('GOV-01'), makeControl('GOV-02'), makeControl('GOV-03')], 3)
    const deepLinkedControl = makeControl('GOV-99')
    mockFetchControlsPage.mockResolvedValue({
      controls: [deepLinkedControl],
      total: 1,
      limit: 1,
      offset: 0,
    })

    render(
      <LibraryPage {...defaultProps({ item: 'GOV-99' })} />,
      { wrapper: wrapper() },
    )

    await waitFor(() => {
      expect(screen.getByTestId('control-detail-page')).toBeInTheDocument()
    })

    // position.index must be null (not in filtered set), total must be filtered list length
    expect(lastDetailPageProps.position).toEqual({ index: null, total: 3 })
  })

  // ── scopeById + inScopeCount passed to LibraryList ───────────────────────

  it('builds scopeById map from scopingData and passes to LibraryList', () => {
    const scopingData = {
      organizationId: 'org-1',
      organization: { name: 'Test Org', id: 'org-1', created_at: '', updated_at: '' },
      scoped_controls: [
        { id: '1', scf_id: 'GOV-01', selected: true },
        { id: '2', scf_id: 'GOV-02', selected: false },
      ],
      evidence_tracking: {},
      metadata: { version: '1.0', total_selected: 1, total_implemented: 0 },
    }
    render(
      <LibraryPage {...defaultProps({ scopingData })} />,
      { wrapper: wrapper() },
    )
    const { scopeById } = lastLibraryListProps as {
      scopeById: Map<string, boolean>
    }
    expect(scopeById?.get('GOV-01')).toBe(true)
    expect(scopeById?.get('GOV-02')).toBe(false)
  })

  it('passes inScopeCount as count of selected=true entries', () => {
    const scopingData = {
      organizationId: 'org-1',
      organization: { name: 'Test Org', id: 'org-1', created_at: '', updated_at: '' },
      scoped_controls: [
        { id: '1', scf_id: 'GOV-01', selected: true },
        { id: '2', scf_id: 'GOV-02', selected: true },
        { id: '3', scf_id: 'GOV-03', selected: false },
      ],
      evidence_tracking: {},
      metadata: { version: '1.0', total_selected: 2, total_implemented: 0 },
    }
    render(
      <LibraryPage {...defaultProps({ scopingData })} />,
      { wrapper: wrapper() },
    )
    expect(lastLibraryListProps.inScopeCount).toBe(2)
  })

  // ── deep-link miss clears item via effect, no React warning ──────────────

  it('deep-link miss calls onItemChange(null) after effects without React update-while-rendering warning', async () => {
    // Resolved fetch returns nothing for the requested item
    setupQueryMock([makeControl('GOV-01')], 1) // does NOT contain GOV-MISSING
    mockFetchControlsPage.mockResolvedValue({
      controls: [], // empty — item truly not found
      total: 0,
      limit: 1,
      offset: 0,
    })

    const consoleError = vi.spyOn(console, 'error')
    const onItemChange = vi.fn()

    render(
      <LibraryPage {...defaultProps({ item: 'GOV-MISSING', onItemChange })} />,
      { wrapper: wrapper() },
    )

    // Wait for the one-shot fetch to complete and the effect to fire
    await waitFor(() => {
      expect(onItemChange).toHaveBeenCalledWith(null)
    })

    // Must not have triggered the React "cannot update a component while rendering" warning
    const reactUpdateWarning = consoleError.mock.calls.some(
      (args) =>
        typeof args[0] === 'string' &&
        args[0].includes('cannot update a component'),
    )
    expect(reactUpdateWarning).toBe(false)
  })

  // ── LibraryPage queries with debounced search ────────────────────────────

  it("LibraryPage's useControlsQuery is called with the debounced search value", () => {
    // useDebounce is mocked as passthrough above, so debouncedSearch === search
    // This test verifies the wiring: LibraryPage passes debouncedSearch (not raw
    // search) to useControlsQuery, converging on the same query key as LibraryList.
    render(
      <LibraryPage {...defaultProps()} />,
      { wrapper: wrapper() },
    )

    // mockUseControlsQuery should have been called with search: undefined
    // (empty string becomes undefined in the query params)
    expect(mockUseControlsQuery).toHaveBeenCalledWith(
      expect.objectContaining({ search: undefined }),
    )
  })

  // ── Bulk controls overlay ─────────────────────────────────────────────────

  it('detail renders bulk-only fields (e.g. frameworksCount) when bulkById has the item', () => {
    // Paginated list has GOV-01 with 0 frameworksCount (slim)
    setupQueryMock([makeControl('GOV-01')], 1)
    // Bulk has GOV-01 with full data (97 maps)
    const bulkControl = makeBulkControl('GOV-01')

    render(
      <LibraryPage
        {...defaultProps({ item: 'GOV-01', controls: [bulkControl] as unknown as EnrichedControl[] })}
      />,
      { wrapper: wrapper() },
    )

    expect(screen.getByTestId('control-detail-page')).toBeInTheDocument()
    // ControlDetailPage should have received the BULK control (97 frameworksCount),
    // not the slim paginated one (0 frameworksCount)
    const passedControl = lastDetailPageProps.control as { frameworksCount: number }
    expect(passedControl.frameworksCount).toBe(97)
  })

  it('detail falls back to slim control when bulk does not have the item', () => {
    // Paginated list has GOV-01 with slim data
    const slimControl = makeControl('GOV-01')
    setupQueryMock([slimControl], 1)
    // Bulk has a different control — no GOV-01
    const bulkControl = makeBulkControl('GOV-02')

    render(
      <LibraryPage
        {...defaultProps({ item: 'GOV-01', controls: [bulkControl] as unknown as EnrichedControl[] })}
      />,
      { wrapper: wrapper() },
    )

    expect(screen.getByTestId('control-detail-page')).toBeInTheDocument()
    // Should fall back to slim control (0 frameworksCount)
    const passedControl = lastDetailPageProps.control as { frameworksCount: number }
    expect(passedControl.frameworksCount).toBe(0)
  })

  it('deep-link prefers bulk (no fetch fired when bulk has the id)', async () => {
    // Filtered list does NOT have GOV-99
    setupQueryMock([makeControl('GOV-01')], 1)
    // But bulk has GOV-99 with full data
    const bulkControl = makeBulkControl('GOV-99')

    render(
      <LibraryPage
        {...defaultProps({
          item: 'GOV-99',
          controls: [bulkControl] as unknown as EnrichedControl[],
        })}
      />,
      { wrapper: wrapper() },
    )

    await waitFor(() => {
      expect(screen.getByTestId('control-detail-page')).toBeInTheDocument()
    })

    // fetchControlsPage must NOT have been called because bulk resolved the item
    expect(mockFetchControlsPage).not.toHaveBeenCalled()

    // Detail receives bulk data (97 frameworksCount)
    const passedControl = lastDetailPageProps.control as { frameworksCount: number }
    expect(passedControl.frameworksCount).toBe(97)
  })
})
