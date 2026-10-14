/**
 * #1157 — removing a control from scope asks for a reason in the in-app
 * dialog (it used to be a native prompt). Nothing is sent until a reason is
 * given; cancel sends nothing. The list itself is stubbed: only the scope
 * action wiring is under test.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ConfirmProvider, confirmClosed, findConfirm } from '../../../__tests__/confirmDialog'
import { setControlScopeOverride } from '../../../data/apiClient'
import UnifiedLibraryPage from '../UnifiedLibraryPage'

const role = vi.hoisted(() => ({ isAdmin: true }))
const listed = vi.hoisted(() => ({
  controls: [
    { scf_id: 'GOV-01', selected: true, obligation_basis: 'mcr' },
    { scf_id: 'AST-02', selected: true, obligation_basis: 'discretionary' },
  ],
}))

vi.mock('../UnifiedLibraryList', () => ({
  default: ({ onScopeAction }: { onScopeAction: (scfId: string, selected: boolean) => void }) => (
    <div>
      <button type="button" onClick={() => onScopeAction('GOV-01', true)}>remove GOV-01</button>
      <button type="button" onClick={() => onScopeAction('AST-02', true)}>remove AST-02</button>
      <button type="button" onClick={() => onScopeAction('AST-02', false)}>add AST-02</button>
    </div>
  ),
  materialityParams: () => ({}),
}))
vi.mock('../ControlDetailPage', () => ({ default: () => null }))
vi.mock('../../scoping/ScopingBulkBar', () => ({ default: () => null }))
vi.mock('../../../data/apiClient', () => ({
  batchAssignTeamToItems: vi.fn(),
  batchUpdateScopedControls: vi.fn(),
  listTeams: vi.fn().mockResolvedValue([]),
  setControlScopeOverride: vi.fn().mockResolvedValue(undefined),
}))
vi.mock('../../../data/scopingService', () => ({
  getScopedControl: vi.fn(),
  loadScopedControls: vi.fn().mockResolvedValue(null),
  updateScopedControl: vi.fn(),
}))
vi.mock('../../../hooks/useScopedControlsQuery', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../hooks/useScopedControlsQuery')>()),
  useScopedControlsQuery: () => ({ data: { pages: [{ controls: listed.controls, total: listed.controls.length }] } }),
}))
vi.mock('../../../hooks/useOrganizationSettings', () => ({ useOrganizationSettings: () => ({ data: undefined }) }))
vi.mock('../../../hooks/useHasOrgRole', () => ({ useIsOrgEditor: () => true }))
vi.mock('../../../hooks/useIsOrgAdmin', () => ({ useIsOrgAdmin: () => role.isAdmin }))
vi.mock('../../../hooks/useTeamAssignments', () => ({
  useTeamAssignments: () => ({ accountableFor: () => [], reload: vi.fn() }),
  accountableTeamLabel: () => null,
}))
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <ConfirmProvider>{children}</ConfirmProvider>
    </QueryClientProvider>
  )
  return render(
    <UnifiedLibraryPage
      item={null}
      mode="in-scope"
      onModeChange={vi.fn()}
      onItemChange={vi.fn()}
      scopingData={null}
      organizationId="org-1"
    />,
    { wrapper },
  )
}

describe('UnifiedLibraryPage — remove from scope asks for a reason (#1157)', () => {
  beforeEach(() => {
    role.isAdmin = true
    vi.mocked(setControlScopeOverride).mockClear()
  })

  it('sends nothing when the reason dialog is cancelled', async () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'remove GOV-01' }))
    const open = await findConfirm()
    expect(open.message).toBe(
      'GOV-01 is a mandatory (MCR) control. Excluding it records you as the approving admin. Why should it be removed from scope?',
    )
    open.cancel()
    await confirmClosed()
    expect(setControlScopeOverride).not.toHaveBeenCalled()
  })

  it('cannot be confirmed without a reason, and sends the trimmed reason once given', async () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'remove GOV-01' }))
    const open = await findConfirm()
    expect(screen.getByRole('button', { name: 'Remove from scope' })).toBeDisabled()
    open.type('   ')
    expect(screen.getByRole('button', { name: 'Remove from scope' })).toBeDisabled()
    open.type('  retired system  ')
    open.accept()
    await confirmClosed()
    await waitFor(() =>
      expect(setControlScopeOverride).toHaveBeenCalledWith('org-1', 'GOV-01', 'exclude', 'retired system'),
    )
  })

  it('tells a non-admin an MCR exclusion may be refused, word for word', async () => {
    role.isAdmin = false
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'remove GOV-01' }))
    expect((await findConfirm()).message).toBe(
      'GOV-01 is a mandatory (MCR) control. Only an organisation admin can exclude it, so this may be refused. Why should it be removed from scope?',
    )
  })

  it('asks the plain question for a discretionary control', async () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'remove AST-02' }))
    expect((await findConfirm()).message).toBe('Why should this control be removed from scope?')
  })

  it('adds to scope with no question', async () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'add AST-02' }))
    await waitFor(() => expect(setControlScopeOverride).toHaveBeenCalledWith('org-1', 'AST-02', 'include', undefined))
    expect(screen.queryByRole('alertdialog')).toBeNull()
  })
})
