/**
 * ScopingPage.test.tsx — TDD tests for the ScopingPage container (Task 3).
 *
 * Tests the four core behaviors the task brief requires:
 *   1. list↔detail switch (selecting a control opens detail; back returns to list)
 *   2. navigateToId opens detail + consumes (onNavigationConsumed fires)
 *   3. bulk field updates issue ONE batchUpdateScopedControls request + refetch
 *   4. Scope-by-Framework modal opens
 *
 * Mocks strategy:
 *   - ScopingList, ScopingBulkBar, ScopingDetailPage — lightweight stubs
 *   - ScopeByFrameworkModal — stub that records calls
 *   - loadScopedControls — returns minimal scoping file
 *   - updateScopedControl — vi.fn(); the bulk path must NOT reach it any more
 *   - batchUpdateScopedControls — vi.fn() so we can assert the call COUNT is 1
 *   - useScopedControlsQuery / useScopedControlsStats — minimal returns
 *   - useOrganizationSettings, useTeamAssignments, useIsOrgAdmin — minimal stubs
 *   - react-hot-toast — vi.fn() stubs so we can assert toast calls
 */
import type { ReactNode } from 'react'
import { render as rtlRender, screen, fireEvent, act, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { WorkScopeProvider } from '../../../contexts/WorkScopeContext'

/**
 * Work scope (#1052) is read from context by this tree. The header owns the
 * value; every render here supplies the provider so the component sees its
 * default ('everything') rather than throwing. Passed as RTL's ``wrapper`` so
 * that ``rerender`` keeps the provider in place.
 */
function render(ui: ReactElement, options?: Parameters<typeof rtlRender>[1]) {
  return rtlRender(ui, { wrapper: WorkScopeProvider, ...options })
}

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

// ─── Stubs ────────────────────────────────────────────────────────────────────

// ── Child components stubbed so tests focus on container behavior ──────────────

vi.mock('../ScopingList', () => ({
  default: ({
    onOpenControl,
    onScopeByFramework,
    selection,
    onSelectionChange,
    bulkBar,
  }: {
    onOpenControl: (id: string) => void
    onScopeByFramework: () => void
    selection: Set<string>
    onSelectionChange: (s: Set<string>) => void
    bulkBar?: ReactNode
  }) => (
    <div data-testid="scoping-list">
      <button onClick={() => onOpenControl('SCF-ABC-1.1')}>open-control</button>
      <button onClick={() => onOpenControl('SCF-NAV-1.1')}>open-nav-control</button>
      <button onClick={onScopeByFramework}>scope-by-framework</button>
      <button
        onClick={() => {
          const next = new Set(selection)
          next.add('SCF-ABC-1.1')
          next.add('SCF-ABC-1.2')
          onSelectionChange(next)
        }}
      >
        select-two
      </button>
      <span data-testid="selection-count">{selection.size}</span>
      {/* The real list renders the bar between toolbar and rows; the stub
          renders it so the bulk-loop tests can reach the (mocked) bar. */}
      {bulkBar}
    </div>
  ),
}))

vi.mock('../ScopingDetailPage', () => ({
  default: ({
    control,
    onBack,
    onFieldChange,
    onToggleScope,
  }: {
    control: { scf_id: string }
    onBack: () => void
    onFieldChange: (field: string, value: unknown) => void
    onToggleScope: (id: string) => void
  }) => (
    <div data-testid="scoping-detail">
      <span data-testid="detail-id">{control.scf_id}</span>
      <button onClick={onBack}>back</button>
      <button onClick={() => onFieldChange('implementation_status', 'implemented')}>
        change-field
      </button>
      <button onClick={() => onToggleScope(control.scf_id)}>toggle-scope</button>
    </div>
  ),
}))

vi.mock('../ScopingBulkBar', () => ({
  default: ({
    selectedCount,
    onSetMaturity,
    onSetStatus,
    onAssignOwner,
    onClear,
    busy,
    progressText,
  }: {
    selectedCount: number
    onSetMaturity: (level: string) => void
    onSetStatus: (status: string) => void
    onAssignOwner: (owner: string) => void
    onClear: () => void
    busy?: boolean
    progressText?: string
  }) =>
    selectedCount > 0 ? (
      <div data-testid="bulk-bar">
        <span data-testid="bulk-count">{selectedCount}</span>
        <button onClick={() => onSetMaturity('L3')} disabled={busy}>
          set-maturity
        </button>
        <button onClick={() => onSetStatus('implemented')} disabled={busy}>
          set-status
        </button>
        <button onClick={() => onAssignOwner('team-1')} disabled={busy}>
          assign-owner
        </button>
        <button onClick={onClear}>clear</button>
        {progressText && <span data-testid="progress">{progressText}</span>}
      </div>
    ) : null,
}))

vi.mock('../../ScopeByFrameworkModal', () => ({
  ScopeByFrameworkModal: ({
    onClose,
    onSuccess,
  }: {
    onClose: () => void
    onSuccess: (r: { message: string }) => void
  }) => (
    <div data-testid="framework-modal">
      <button onClick={onClose}>close-modal</button>
      <button onClick={() => onSuccess({ message: 'Done' })}>modal-success</button>
    </div>
  ),
}))

// ── Data layer mocks ──────────────────────────────────────────────────────────

import type { ScopedControlsFile } from '../../../types'

const mockScopingData: ScopedControlsFile = {
  organizationId: 'org-1',
  organization: { id: 'org-1', name: 'Test Org', created_at: '', updated_at: '' },
  scoped_controls: [
    { id: 'db-1', scf_id: 'SCF-ABC-1.1', selected: true, implementation_status: 'not_started' },
    { id: 'db-2', scf_id: 'SCF-ABC-1.2', selected: true, implementation_status: 'not_started' },
    { id: 'db-3', scf_id: 'SCF-NAV-1.1', selected: false, implementation_status: 'not_started' },
  ],
  evidence_tracking: {},
  metadata: { total_controls: 3, total_selected: 2, total_implemented: 0, last_updated: '' },
}

const mockLoadScopedControls = vi.fn().mockResolvedValue(mockScopingData)
const mockUpdateScopedControl = vi.fn().mockImplementation(async (data, control) => ({
  ...data,
  scoped_controls: data.scoped_controls.map((c: { scf_id: string }) =>
    c.scf_id === control.scf_id ? { ...c, ...control } : c,
  ),
}))

vi.mock('../../../data/scopingService', () => ({
  loadScopedControls: () => mockLoadScopedControls(),
  getScopedControl: (data: { scoped_controls: Array<{ scf_id: string }> }, scf_id: string) =>
    data.scoped_controls.find((c) => c.scf_id === scf_id),
  updateScopedControl: (
    data: { scoped_controls: Array<{ scf_id: string }> },
    control: { scf_id: string },
  ) => mockUpdateScopedControl(data, control),
  getEvidenceTracking: () => null,
}))

// Mock the paginated query hooks
const mockFlatControls = [
  { scf_id: 'SCF-ABC-1.1', control_name: 'ABC Control', selected: true, implementation_status: 'not_started', control_description: '', framework_mappings: {}, evidence_requests: [], cmm_maturity: {}, business_size_guidance: {} },
  { scf_id: 'SCF-ABC-1.2', control_name: 'ABC Control 2', selected: true, implementation_status: 'not_started', control_description: '', framework_mappings: {}, evidence_requests: [], cmm_maturity: {}, business_size_guidance: {} },
  { scf_id: 'SCF-NAV-1.1', control_name: 'Nav Control', selected: false, implementation_status: 'not_started', control_description: '', framework_mappings: {}, evidence_requests: [], cmm_maturity: {}, business_size_guidance: {} },
]

const mockRefetch = vi.fn()
const mockRefetchStats = vi.fn()
const mockFetchNextPage = vi.fn()

vi.mock('../../../hooks/useScopedControlsQuery', () => ({
  useScopedControlsQuery: () => ({
    data: { pages: [{ items: mockFlatControls, total: 3 }] },
    fetchNextPage: mockFetchNextPage,
    hasNextPage: false,
    isFetchingNextPage: false,
    isLoading: false,
    isFetching: false,
    isError: false,
    refetch: mockRefetch,
  }),
  useScopedControlsStats: () => ({
    data: { in_scope: 2, total_controls: 3, implemented: 0 },
    refetch: mockRefetchStats,
  }),
  flattenScopedControlPages: (pages: Array<{ items: unknown[]; total: number }> | undefined) => ({
    controls: pages?.[0]?.items ?? [],
    total: pages?.[0]?.total ?? 0,
  }),
}))

vi.mock('../../../hooks/useTeamAssignments', () => ({
  useTeamAssignments: () => ({
    accountableFor: () => null,
    reload: vi.fn(),
  }),
  accountableTeamLabel: () => '',
}))

vi.mock('../../../hooks/useIsOrgAdmin', () => ({
  useIsOrgAdmin: () => true,
}))

vi.mock('../../../hooks/useDebounce', () => ({
  useDebounce: (v: string) => v,
}))

vi.mock('../../../hooks/useCatalogFilters', () => ({
  useCatalogFilters: () => ({
    domains: [],
    nistCsfFunctions: [],
    controlWeights: [],
    isLoading: false,
  }),
}))

const mockBatchAssignTeamToItems = vi.fn().mockResolvedValue({
  type: 'control',
  team_id: 'team-1',
  created: 2,
  updated: 0,
  demoted: 0,
  notified: 1,
})
const mockBatchUpdateScopedControls = vi.fn().mockResolvedValue({
  updated: 2,
  created: 0,
  failed: 0,
  errors: [],
  controls: [],
})
vi.mock('../../../data/apiClient', () => ({
  listTeams: vi.fn().mockResolvedValue([{ id: 'team-1', name: 'Security Operations' }]),
  listFunctions: vi.fn().mockResolvedValue([]),
  batchAssignTeamToItems: (...args: unknown[]) => mockBatchAssignTeamToItems(...args),
  batchUpdateScopedControls: (...args: unknown[]) => mockBatchUpdateScopedControls(...args),
}))

const mockToastSuccess = vi.fn()
const mockToastError = vi.fn()
vi.mock('react-hot-toast', () => ({
  toast: {
    success: (msg: string) => mockToastSuccess(msg),
    error: (msg: string) => mockToastError(msg),
  },
  default: {
    success: (msg: string) => mockToastSuccess(msg),
    error: (msg: string) => mockToastError(msg),
  },
}))

vi.mock('../../../hooks/useQueryClient', async () => {
  const actual = await vi.importActual('@tanstack/react-query')
  return actual
})

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

function renderPage(props: {
  initialSelectedId?: string
  navigateToId?: string
  onNavigationConsumed?: () => void
  scopingData?: typeof mockScopingData
}) {
  const qc = makeQueryClient()
  return render(
    <QueryClientProvider client={qc}>
      <ScopingPage
        organizationId="org-1"
        erlData={{}}
        frameworkNames={{}}
        scopingData={props.scopingData ?? mockScopingData}
        onScopingDataChange={vi.fn()}
        {...props}
      />
    </QueryClientProvider>,
  )
}

// Import component under test AFTER all mocks are set up
import ScopingPage from '../ScopingPage'

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('ScopingPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockLoadScopedControls.mockResolvedValue(mockScopingData)
    mockUpdateScopedControl.mockImplementation(async (data, control) => ({
      ...data,
      scoped_controls: data.scoped_controls.map((c: { scf_id: string }) =>
        c.scf_id === control.scf_id ? { ...c, ...control } : c,
      ),
    }))
    // Re-declared each test: the partial-failure case overrides it.
    mockBatchUpdateScopedControls.mockResolvedValue({
      updated: 2,
      created: 0,
      failed: 0,
      errors: [],
      controls: [],
    })
  })

  // ── 1. list↔detail switch ────────────────────────────────────────────────

  describe('list↔detail switch', () => {
    it('renders the list view by default', () => {
      renderPage({})
      expect(screen.getByTestId('scoping-list')).toBeInTheDocument()
      expect(screen.queryByTestId('scoping-detail')).not.toBeInTheDocument()
    })

    it('shows the detail view when a control is opened', async () => {
      renderPage({})
      fireEvent.click(screen.getByText('open-control'))
      await waitFor(() => {
        expect(screen.getByTestId('scoping-detail')).toBeInTheDocument()
      })
      expect(screen.queryByTestId('scoping-list')).not.toBeInTheDocument()
    })

    it('detail view shows the selected control id', async () => {
      renderPage({})
      fireEvent.click(screen.getByText('open-control'))
      await waitFor(() => {
        expect(screen.getByTestId('detail-id')).toHaveTextContent('SCF-ABC-1.1')
      })
    })

    it('pressing back returns to the list view', async () => {
      renderPage({})
      fireEvent.click(screen.getByText('open-control'))
      await waitFor(() => screen.getByTestId('scoping-detail'))
      fireEvent.click(screen.getByText('back'))
      await waitFor(() => {
        expect(screen.getByTestId('scoping-list')).toBeInTheDocument()
        expect(screen.queryByTestId('scoping-detail')).not.toBeInTheDocument()
      })
    })

    it('renders list (not detail) when initialSelectedId is provided — no auto-open', () => {
      // initialSelectedId is list context only; only navigateToId or a row click opens detail
      renderPage({ initialSelectedId: 'SCF-ABC-1.1' })
      expect(screen.getByTestId('scoping-list')).toBeInTheDocument()
      expect(screen.queryByTestId('scoping-detail')).not.toBeInTheDocument()
    })
  })

  // ── 2. navigateToId ───────────────────────────────────────────────────────

  describe('navigateToId', () => {
    it('opens detail for the navigation target', async () => {
      const onConsumed = vi.fn()
      renderPage({ navigateToId: 'SCF-NAV-1.1', onNavigationConsumed: onConsumed })
      await waitFor(() => {
        expect(screen.getByTestId('scoping-detail')).toBeInTheDocument()
        expect(screen.getByTestId('detail-id')).toHaveTextContent('SCF-NAV-1.1')
      })
    })

    it('calls onNavigationConsumed after navigation resolves', async () => {
      const onConsumed = vi.fn()
      renderPage({ navigateToId: 'SCF-NAV-1.1', onNavigationConsumed: onConsumed })
      await waitFor(() => {
        expect(onConsumed).toHaveBeenCalledTimes(1)
      })
    })
  })

  // ── 3. bulk field updates ────────────────────────────────────────────────

  describe('bulk field updates', () => {
    it('sets maturity through ONE batch request, not a per-control loop', async () => {
      renderPage({})
      // Select 2 controls
      fireEvent.click(screen.getByText('select-two'))
      await waitFor(() => expect(screen.getByTestId('bulk-bar')).toBeInTheDocument())

      await act(async () => {
        fireEvent.click(screen.getByText('set-maturity'))
      })

      await waitFor(() => {
        // EXACTLY one request for the whole selection. The predecessor fired
        // one per control; the count is the assertion that matters here.
        expect(mockBatchUpdateScopedControls).toHaveBeenCalledTimes(1)
      })
      // And the N+1 path is not merely quieter — it is gone.
      expect(mockUpdateScopedControl).not.toHaveBeenCalled()
    })

    it('sends one {scf_id, maturity_level} operation per selected row', async () => {
      renderPage({})
      fireEvent.click(screen.getByText('select-two'))
      await waitFor(() => screen.getByTestId('bulk-bar'))

      await act(async () => {
        fireEvent.click(screen.getByText('set-maturity'))
      })

      await waitFor(() => expect(mockBatchUpdateScopedControls).toHaveBeenCalledTimes(1))
      const [operations, orgId] = mockBatchUpdateScopedControls.mock.calls[0]
      // PIN THE FIELD: maturity_level (the org's own level), never cmm_maturity.
      expect(operations).toEqual([
        { scf_id: 'SCF-ABC-1.1', maturity_level: 'L3' },
        { scf_id: 'SCF-ABC-1.2', maturity_level: 'L3' },
      ])
      expect(orgId).toBe('org-1')
    })

    it('sends one {scf_id, implementation_status} operation per selected row', async () => {
      renderPage({})
      fireEvent.click(screen.getByText('select-two'))
      await waitFor(() => screen.getByTestId('bulk-bar'))

      await act(async () => {
        fireEvent.click(screen.getByText('set-status'))
      })

      await waitFor(() => expect(mockBatchUpdateScopedControls).toHaveBeenCalledTimes(1))
      const [operations] = mockBatchUpdateScopedControls.mock.calls[0]
      expect(operations).toEqual([
        { scf_id: 'SCF-ABC-1.1', implementation_status: 'implemented' },
        { scf_id: 'SCF-ABC-1.2', implementation_status: 'implemented' },
      ])
      // Status is an implementation judgement; it must never write `selected`.
      for (const op of operations as Array<Record<string, unknown>>) {
        expect(op).not.toHaveProperty('selected')
      }
    })

    it('assigns the owner team through ONE batch team-assignment call, not the scoped-control loop', async () => {
      renderPage({})
      fireEvent.click(screen.getByText('select-two'))
      await waitFor(() => screen.getByTestId('bulk-bar'))

      await act(async () => {
        fireEvent.click(screen.getByText('assign-owner'))
      })

      await waitFor(() => {
        expect(mockBatchAssignTeamToItems).toHaveBeenCalledTimes(1)
      })
      const [orgArg, batchArg] = mockBatchAssignTeamToItems.mock.calls[0]
      expect(orgArg).toBe('org-1')
      expect(batchArg).toEqual({
        type: 'control',
        team_id: 'team-1',
        // The scoped controls' DATABASE ids, in selection order — assignments
        // never key on scf_id.
        item_ids: ['db-1', 'db-2'],
        is_accountable: true,
      })
      // The legacy owner column is dead: no scoped-control write happens.
      expect(mockUpdateScopedControl).not.toHaveBeenCalled()
    })

    it('refetches list and stats after bulk operation', async () => {
      renderPage({})
      fireEvent.click(screen.getByText('select-two'))
      await waitFor(() => screen.getByTestId('bulk-bar'))

      await act(async () => {
        fireEvent.click(screen.getByText('set-maturity'))
      })

      await waitFor(() => {
        expect(mockRefetch).toHaveBeenCalled()
        expect(mockRefetchStats).toHaveBeenCalled()
      })
    })

    it('shows toast summary after bulk operation', async () => {
      renderPage({})
      fireEvent.click(screen.getByText('select-two'))
      await waitFor(() => screen.getByTestId('bulk-bar'))

      await act(async () => {
        fireEvent.click(screen.getByText('set-maturity'))
      })

      await waitFor(() => {
        expect(mockToastSuccess).toHaveBeenCalledWith('2 controls updated')
      })
    })

    it('clears selection after bulk operation completes', async () => {
      renderPage({})
      fireEvent.click(screen.getByText('select-two'))
      await waitFor(() => expect(screen.getByTestId('selection-count')).toHaveTextContent('2'))

      await act(async () => {
        fireEvent.click(screen.getByText('set-maturity'))
      })

      await waitFor(() => {
        expect(screen.queryByTestId('bulk-bar')).not.toBeInTheDocument()
      })
    })

    it('reports a partial failure from the batch response rather than claiming success', async () => {
      // The endpoint records per-operation failures instead of aborting, so a
      // 200 with failed > 0 must NOT produce a success toast.
      mockBatchUpdateScopedControls.mockResolvedValue({
        updated: 1,
        created: 0,
        failed: 1,
        errors: ['SCF-ABC-1.2: not found'],
        controls: [],
      })

      renderPage({})
      fireEvent.click(screen.getByText('select-two'))
      await waitFor(() => expect(screen.getByTestId('bulk-bar')).toBeInTheDocument())

      await act(async () => {
        fireEvent.click(screen.getByText('set-maturity'))
      })

      await waitFor(() => {
        expect(mockToastError).toHaveBeenCalledWith(expect.stringMatching(/1 updated.*1 failed/))
        // Still one request — a partial failure is not a retry loop.
        expect(mockBatchUpdateScopedControls).toHaveBeenCalledTimes(1)
        expect(mockToastSuccess).not.toHaveBeenCalled()
        // Selection cleared and refetch happened
        expect(screen.queryByTestId('bulk-bar')).not.toBeInTheDocument()
        expect(mockRefetch).toHaveBeenCalled()
        expect(mockRefetchStats).toHaveBeenCalled()
      })
    })

    it('shows an error toast and stops when the batch request itself rejects', async () => {
      mockBatchUpdateScopedControls.mockRejectedValue(new Error('500'))

      renderPage({})
      fireEvent.click(screen.getByText('select-two'))
      await waitFor(() => expect(screen.getByTestId('bulk-bar')).toBeInTheDocument())

      await act(async () => {
        fireEvent.click(screen.getByText('set-maturity'))
      })

      await waitFor(() => {
        expect(mockToastError).toHaveBeenCalledWith('Bulk update failed')
      })
      expect(mockToastSuccess).not.toHaveBeenCalled()
    })
  })

  // ── 4. Scope-by-Framework modal ──────────────────────────────────────────

  describe('Scope-by-Framework modal', () => {
    it('opens the modal when scope-by-framework is clicked', () => {
      renderPage({})
      fireEvent.click(screen.getByText('scope-by-framework'))
      expect(screen.getByTestId('framework-modal')).toBeInTheDocument()
    })

    it('closes the modal on close callback', () => {
      renderPage({})
      fireEvent.click(screen.getByText('scope-by-framework'))
      expect(screen.getByTestId('framework-modal')).toBeInTheDocument()
      fireEvent.click(screen.getByText('close-modal'))
      expect(screen.queryByTestId('framework-modal')).not.toBeInTheDocument()
    })

    it('refetches after modal success', async () => {
      renderPage({})
      fireEvent.click(screen.getByText('scope-by-framework'))

      await act(async () => {
        fireEvent.click(screen.getByText('modal-success'))
      })

      await waitFor(() => {
        expect(mockRefetch).toHaveBeenCalled()
        expect(mockRefetchStats).toHaveBeenCalled()
      })
    })

    it('closes the modal after success', async () => {
      renderPage({})
      fireEvent.click(screen.getByText('scope-by-framework'))

      await act(async () => {
        fireEvent.click(screen.getByText('modal-success'))
      })

      await waitFor(() => {
        expect(screen.queryByTestId('framework-modal')).not.toBeInTheDocument()
      })
    })
  })
})
