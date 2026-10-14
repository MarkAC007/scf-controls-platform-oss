/**
 * The live Control Library list (PIG R6): the material filter and the
 * weighting order reach the paginated query, and a material row says so.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({ calls: [] as Record<string, unknown>[], controls: [] as Record<string, unknown>[] }))

vi.mock('react-window', () => ({
  FixedSizeList: ({ itemCount, children: Row }: {
    itemCount: number
    children: React.ComponentType<{ index: number; style: React.CSSProperties }>
  }) => <div>{Array.from({ length: itemCount }, (_, i) => <Row key={i} index={i} style={{}} />)}</div>,
}))
vi.mock('../../../hooks/useDebounce', () => ({ useDebounce: (value: unknown) => value }))
vi.mock('../../../hooks/useScopedControlsQuery', () => ({
  useScopedControlsQuery: (filters: Record<string, unknown>) => {
    state.calls.push(filters)
    return { isLoading: false, isError: false, data: { pages: [] }, hasNextPage: false }
  },
  flattenScopedControlPages: () => ({ controls: state.controls, total: state.controls.length }),
  useScopedControlsStats: () => ({ data: undefined }),
}))
vi.mock('../../../hooks/useCatalogFilters', () => ({
  useCatalogFilters: () => ({ domains: [], nistCsfFunctions: [], controlWeights: [] }),
}))
vi.mock('../../../contexts/WorkScopeContext', () => ({ useWorkScope: () => ({ isMyTeams: false }) }))
vi.mock('../../../data/apiClient', () => ({ listTeams: vi.fn() }))

import UnifiedLibraryList, { type UnifiedLibraryFilters } from '../UnifiedLibraryList'

const FILTERS: UnifiedLibraryFilters = {
  domain: 'all', csf: 'all', weight: 'all', framework: 'all', scope: 'all', basis: 'all',
  material: 'all', sort: 'scf_id',
}

function renderList(filters: Partial<UnifiedLibraryFilters> = {}, onFiltersChange = vi.fn()) {
  render(
    <UnifiedLibraryList
      organizationId="org-one"
      mode="in-scope"
      filters={{ ...FILTERS, ...filters }}
      onFiltersChange={onFiltersChange}
      search=""
      onSearchChange={vi.fn()}
      onOpenControl={vi.fn()}
      onScopeAction={vi.fn()}
      canEdit={false}
      selection={new Set()}
      onSelectionChange={vi.fn()}
      initialScrollOffset={0}
      onScrollOffsetChange={vi.fn()}
    />,
  )
  return { onFiltersChange }
}

const lastQuery = () => state.calls[state.calls.length - 1]

describe('UnifiedLibraryList materiality (PIG R6)', () => {
  beforeEach(() => {
    state.calls = []
    state.controls = []
  })

  it('applies no material filter and the default order unless asked', () => {
    renderList()
    expect(lastQuery().material).toBeUndefined()
    expect(lastQuery().sort).toBeUndefined()
  })

  it('sends material=true, material=false and the weighting order to the query', () => {
    renderList({ material: 'material', sort: 'weighting' })
    expect(lastQuery()).toMatchObject({ material: true, sort: 'weighting' })
    state.calls = []
    renderList({ material: 'not_material' })
    expect(lastQuery().material).toBe(false)
  })

  it('changes the filter and the order from the sidebar', () => {
    const { onFiltersChange } = renderList()
    fireEvent.change(screen.getByRole('combobox', { name: 'Materiality' }), { target: { value: 'material' } })
    expect(onFiltersChange).toHaveBeenLastCalledWith({ ...FILTERS, material: 'material' })
    fireEvent.change(screen.getByRole('combobox', { name: 'Order' }), { target: { value: 'weighting' } })
    expect(onFiltersChange).toHaveBeenLastCalledWith({ ...FILTERS, sort: 'weighting' })
  })

  it('marks a material row and leaves the rest unmarked', () => {
    state.controls = [
      { scf_id: 'GOV-01', control_name: 'Programme', control_description: '', selected: true, material: true, material_source: 'weighting' },
      { scf_id: 'GOV-02', control_name: 'Publishing', control_description: '', selected: true, material: false, material_source: 'weighting' },
    ]
    renderList()
    expect(screen.getAllByText('Material', { selector: 'span' })).toHaveLength(1)
  })
})
