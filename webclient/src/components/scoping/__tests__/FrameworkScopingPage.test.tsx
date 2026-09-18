import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import FrameworkScopingPage from '../FrameworkScopingPage'

const role = vi.hoisted(() => ({ editor: false, admin: false }))
const api = vi.hoisted(() => ({
  fetchSummary: vi.fn(),
  preview: vi.fn(),
  add: vi.fn(),
  remove: vi.fn(),
  reset: vi.fn(),
}))

vi.mock('../../../hooks/useHasOrgRole', () => ({
  useIsOrgEditor: () => role.editor,
}))
vi.mock('../../../hooks/useIsOrgAdmin', () => ({
  useIsOrgAdmin: () => role.admin,
}))
vi.mock('../../../data/apiClient', () => ({
  fetchFrameworkScopeSummary: api.fetchSummary,
  previewFrameworkScopeChange: api.preview,
  bulkScopeByFramework: api.add,
  bulkUnscopeByFramework: api.remove,
  resetAllScope: api.reset,
}))
vi.mock('../../FrameworkLogo', () => ({
  FrameworkLogo: ({ frameworkName }: { frameworkName: string }) => <span>{frameworkName} logo</span>,
}))
vi.mock('react-hot-toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

const summary = {
  total: 2,
  selected_count: 1,
  frameworks: [
    {
      id: 'iso_27001_2022',
      name: 'ISO 27001:2022',
      family: 'international',
      mapped_control_count: 10,
      in_scope_count: 8,
      missing_count: 2,
      coverage_percentage: 80,
      expected_additions: 0,
      active: true,
      partial: true,
      source: 'bulk_scope',
      selected_at: '2026-09-17T10:00:00Z',
      selected_by: '00000000-0000-0000-0000-000000000001',
    },
    {
      id: 'soc2',
      name: 'SOC 2',
      family: 'industry',
      mapped_control_count: 6,
      in_scope_count: 1,
      missing_count: 5,
      coverage_percentage: 16.7,
      expected_additions: 5,
      active: false,
      partial: false,
    },
  ],
}

function renderPage(props: Partial<Parameters<typeof FrameworkScopingPage>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <FrameworkScopingPage organizationId="org-one" {...props} />
    </QueryClientProvider>,
  )
}

describe('FrameworkScopingPage roles and exact preview', () => {
  beforeEach(() => {
    role.editor = false
    role.admin = false
    vi.clearAllMocks()
    api.fetchSummary.mockResolvedValue(summary)
    api.preview.mockResolvedValue({
      operation: 'add',
      frameworks: ['soc2'],
      mapped_controls: ['ctl-a', 'ctl-b'],
      new_controls: ['ctl-a'],
      already_covered: ['ctl-b'],
      shared_with_active_frameworks: [],
      individual_inclusions: [],
      explicitly_excluded: ['ctl-excluded'],
      controls_leaving_scope: [],
    })
    api.add.mockResolvedValue({ message: 'added' })
    api.reset.mockResolvedValue({ message: 'reset', removed: 8 })
  })

  it('gives viewers read-only selected, partial, and unselected framework state', async () => {
    renderPage()
    expect((await screen.findAllByText('ISO 27001:2022')).length).toBeGreaterThan(1)
    expect(screen.getAllByText('Partial').length).toBeGreaterThan(1)
    expect(screen.getByText('Not selected')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove framework' })).not.toBeInTheDocument()
    expect(screen.queryByText('Danger zone')).not.toBeInTheDocument()
  })

  it('lets editors inspect the server-authoritative exact effect before adding', async () => {
    role.editor = true
    const onChanged = vi.fn()
    renderPage({ onChanged })
    fireEvent.click(await screen.findByRole('button', { name: 'Add' }))

    expect(await screen.findByText('Exact change preview')).toBeInTheDocument()
    expect(api.preview).toHaveBeenCalledWith('org-one', 'add', ['soc2'])
    expect(screen.getByText('New controls entering scope')).toBeInTheDocument()
    expect(screen.getByText('Blocked by an explicit exclusion')).toBeInTheDocument()
    // Headline rows stay visible at zero because zero is a real answer for them.
    expect(screen.getByText('Already in scope via another active framework')).toBeInTheDocument()
    // An add can never empty a control out of scope, so that row is not rendered at all
    // rather than shown as a permanent zero.
    expect(screen.queryByText('Controls leaving scope')).not.toBeInTheDocument()
    // Nothing is individually included in this fixture, so the row stays out of the way.
    expect(screen.queryByText(/individually included/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Confirm exact change' }))
    await waitFor(() => expect(api.add).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
  })

  it('shows destructive reset only to admins', async () => {
    role.editor = true
    role.admin = true
    vi.spyOn(window, 'prompt').mockReturnValue('REMOVE ALL')
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Reset scope' }))
    await waitFor(() => expect(api.reset).toHaveBeenCalledWith('org-one'))
  })
})
