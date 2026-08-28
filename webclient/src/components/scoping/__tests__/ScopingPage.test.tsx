/**
 * ScopingPage.test.tsx — TDD tests for the ScopingPage container (Task 3).
 *
 * Tests the four core behaviors the task brief requires:
 *   1. list↔detail switch (selecting a control opens detail; back returns to list)
 *   2. navigateToId opens detail + consumes (onNavigationConsumed fires)
 *   3. bulk loop calls updateScopedControl n times + refetches
 *   4. Scope-by-Framework modal opens
 *
 * Mocks strategy:
 *   - ScopingList, ScopingBulkBar, ScopingDetailPage — lightweight stubs
 *   - ScopeByFrameworkModal — stub that records calls
 *   - loadScopedControls — returns minimal scoping file
 *   - updateScopedControl — vi.fn() so we can count calls
 *   - useScopedControlsQuery / useScopedControlsStats — minimal returns
 *   - useOrganizationSettings, useTeamAssignments, useIsOrgAdmin — minimal stubs
 *   - react-hot-toast — vi.fn() stubs so we can assert toast calls
 */
import type { ReactNode } from 'react'
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

// ─── Stubs ────────────────────────────────────────────────────────────────────

globalThis.ResizeObserver = vi.fn().mockImplementation(() => ({
  observe: vi.fn(),
  unobserve: vi.fn(),
  disconnect: vi.fn(),
}))

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
    onSetApplicable,
    onSetNA,
    onAssignOwner,
    onClear,
    busy,
    progressText,
  }: {
    selectedCount: number
    onSetApplicable: () => void
    onSetNA: () => void
    onAssignOwner: (owner: string) => void
    onClear: () => void
    busy?: boolean
    progressText?: string
  }) =>
    selectedCount > 0 ? (
      <div data-testid="bulk-bar">
        <span data-testid="bulk-count">{selectedCount}</span>
        <button onClick={onSetApplicable} disabled={busy}>
          set-applicable
        </button>
        <button onClick={onSetNA} disabled={busy}>
          set-na
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
vi.mock('../../../data/apiClient', () => ({
  listTeams: vi.fn().mockResolvedValue([{ id: 'team-1', name: 'Security Operations' }]),
  listFunctions: vi.fn().mockResolvedValue([]),
  batchAssignTeamToItems: (...args: unknown[]) => mockBatchAssignTeamToItems(...args),
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

  // ── 3. bulk loop ─────────────────────────────────────────────────────────

  describe('bulk actions loop', () => {
    it('calls updateScopedControl for each selected control (set applicable)', async () => {
      renderPage({})
      // Select 2 controls
      fireEvent.click(screen.getByText('select-two'))
      await waitFor(() => expect(screen.getByTestId('bulk-bar')).toBeInTheDocument())

      await act(async () => {
        fireEvent.click(screen.getByText('set-applicable'))
      })

      await waitFor(() => {
        // Called once per selected control (2)
        expect(mockUpdateScopedControl).toHaveBeenCalledTimes(2)
      })
    })

    it('calls updateScopedControl with selected=true for set applicable', async () => {
      renderPage({})
      fireEvent.click(screen.getByText('select-two'))
      await waitFor(() => screen.getByTestId('bulk-bar'))

      await act(async () => {
        fireEvent.click(screen.getByText('set-applicable'))
      })

      await waitFor(() => {
        const calls = mockUpdateScopedControl.mock.calls
        expect(calls.length).toBeGreaterThan(0)
        expect(calls[0][1]).toMatchObject({ selected: true })
      })
    })

    it('calls updateScopedControl with selected=false for set N/A', async () => {
      renderPage({})
      fireEvent.click(screen.getByText('select-two'))
      await waitFor(() => screen.getByTestId('bulk-bar'))

      await act(async () => {
        fireEvent.click(screen.getByText('set-na'))
      })

      await waitFor(() => {
        const calls = mockUpdateScopedControl.mock.calls
        expect(calls.length).toBeGreaterThan(0)
        expect(calls[0][1]).toMatchObject({ selected: false })
      })
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
        fireEvent.click(screen.getByText('set-applicable'))
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
        fireEvent.click(screen.getByText('set-applicable'))
      })

      await waitFor(() => {
        expect(mockToastSuccess).toHaveBeenCalled()
      })
    })

    it('clears selection after bulk operation completes', async () => {
      renderPage({})
      fireEvent.click(screen.getByText('select-two'))
      await waitFor(() => expect(screen.getByTestId('selection-count')).toHaveTextContent('2'))

      await act(async () => {
        fireEvent.click(screen.getByText('set-applicable'))
      })

      await waitFor(() => {
        expect(screen.queryByTestId('bulk-bar')).not.toBeInTheDocument()
      })
    })

    it('handles partial failure: continues after error, shows error toast, and refetches', async () => {
      // Make updateScopedControl reject for the first control, resolve for the second
      mockUpdateScopedControl
        .mockRejectedValueOnce(new Error('Update failed'))
        .mockResolvedValueOnce({
          ...mockScopingData,
          scoped_controls: mockScopingData.scoped_controls.map((c) =>
            c.scf_id === 'SCF-ABC-1.2' ? { ...c, selected: true } : c,
          ),
        })

      renderPage({})
      fireEvent.click(screen.getByText('select-two'))
      await waitFor(() => expect(screen.getByTestId('bulk-bar')).toBeInTheDocument())

      await act(async () => {
        fireEvent.click(screen.getByText('set-applicable'))
      })

      await waitFor(() => {
        // Error toast should fire with message matching "1 updated · 1 failed"
        expect(mockToastError).toHaveBeenCalledWith(expect.stringMatching(/1 updated.*1 failed/))
        // updateScopedControl called twice (once per control, despite first failure)
        expect(mockUpdateScopedControl).toHaveBeenCalledTimes(2)
        // Selection cleared and refetch happened
        expect(screen.queryByTestId('bulk-bar')).not.toBeInTheDocument()
        expect(mockRefetch).toHaveBeenCalled()
        expect(mockRefetchStats).toHaveBeenCalled()
      })
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
