/**
 * MaturityTargetsSettings — Settings → Maturity targets (PIG R5).
 *
 * What these tests defend:
 *   - each catalogue domain shows its saved target, matched case-insensitively;
 *   - Save sends the WHOLE map { CODE: level } with "No target" rows omitted,
 *     because the PATCH replaces the stored map rather than merging into it;
 *   - that map is the one stored at save time with only this admin's changed
 *     rows applied, so another admin's concurrent edit is not wiped;
 *   - a stored code the active catalogue no longer lists is kept, not dropped;
 *   - the API's 422 detail is shown, never swallowed;
 *   - a settings read that failed offers no Save, which would wipe every target;
 *   - below admin the selects are read-only and no Save is offered.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import MaturityTargetsSettings from '../MaturityTargetsSettings'

const state = vi.hoisted(() => ({
  admin: true,
  settings: undefined as Record<string, unknown> | undefined,
  settingsError: null as Error | null,
  domainsError: false,
}))
const api = vi.hoisted(() => ({ update: vi.fn(), fetch: vi.fn() }))

vi.mock('../../data/apiClient', () => ({
  updateOrganizationSettings: api.update,
  fetchOrganizationSettings: api.fetch,
}))
vi.mock('../../hooks/useOrganizationSettings', () => ({
  useOrganizationSettings: () => ({
    data: state.settings,
    isLoading: false,
    isError: !!state.settingsError,
    error: state.settingsError,
  }),
}))
vi.mock('../../hooks/useCatalogFilters', () => ({
  domainFilterLabel: (identifier: string, name: string) => `${identifier} - ${name}`,
  useCatalogDomains: () => ({
    data: state.domainsError ? undefined : [
      { identifier: 'GOV', name: 'Cybersecurity & Data Protection Governance' },
      { identifier: 'IAC', name: 'Identification & Authentication' },
      { identifier: 'RSK', name: 'Risk Management' },
    ],
    isLoading: false,
    isError: state.domainsError,
    error: state.domainsError ? new Error('Catalogue unavailable') : null,
  }),
}))
vi.mock('../../hooks/useIsOrgAdmin', () => ({ useIsOrgAdmin: () => state.admin }))
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  render(
    <QueryClientProvider client={client}>
      <MaturityTargetsSettings organizationId="org-1" />
    </QueryClientProvider>,
  )
  return { invalidate }
}

describe('MaturityTargetsSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state.admin = true
    state.settingsError = null
    state.domainsError = false
    state.settings = { maturity_targets: { gov: 'L3', RSK: 'L2' } }
    api.update.mockResolvedValue({})
    api.fetch.mockImplementation(async () => state.settings)
  })

  it("shows each domain's saved target, matching codes case-insensitively", () => {
    renderCard()
    expect(screen.getByLabelText('GOV target')).toHaveValue('L3')
    expect(screen.getByLabelText('IAC target')).toHaveValue('')
    expect(screen.getByLabelText('RSK target')).toHaveValue('L2')
    expect(screen.getByText('2 of 3 domains have a target.')).toBeInTheDocument()
  })

  it('keeps Save inert until a target changes', () => {
    renderCard()
    expect(screen.getByRole('button', { name: 'Save maturity targets' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('IAC target'), { target: { value: 'L1' } })
    expect(screen.getByRole('button', { name: 'Save maturity targets' })).toBeEnabled()
  })

  it('sends the whole map with cleared domains omitted, then refreshes settings and controls', async () => {
    const { invalidate } = renderCard()
    fireEvent.change(screen.getByLabelText('IAC target'), { target: { value: 'L4' } })
    fireEvent.change(screen.getByLabelText('RSK target'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save maturity targets' }))
    await waitFor(() => expect(api.update).toHaveBeenCalledWith('org-1', {
      maturity_targets: { GOV: 'L3', IAC: 'L4' },
    }))
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['organization-settings', 'org-1'] }))
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['scoped-controls', 'org-1'] })
  })

  it('sends an empty map when every target is cleared', async () => {
    renderCard()
    fireEvent.change(screen.getByLabelText('GOV target'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('RSK target'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save maturity targets' }))
    await waitFor(() => expect(api.update).toHaveBeenCalledWith('org-1', { maturity_targets: {} }))
  })

  it('keeps a stored code the active catalogue no longer lists', async () => {
    state.settings = { maturity_targets: { GOV: 'L3', OLD: 'L1' } }
    renderCard()
    expect(screen.getByLabelText('OLD target')).toHaveValue('L1')
    expect(screen.getByText(/not in the current catalogue/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('IAC target'), { target: { value: 'L2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save maturity targets' }))
    await waitFor(() => expect(api.update).toHaveBeenCalledWith('org-1', {
      maturity_targets: { GOV: 'L3', IAC: 'L2', OLD: 'L1' },
    }))
  })

  it("applies only this admin's changes to the map as stored at save time", async () => {
    renderCard()
    fireEvent.change(screen.getByLabelText('RSK target'), { target: { value: 'L4' } })
    // Another admin set IAC and cleared GOV after this page loaded.
    state.settings = { maturity_targets: { IAC: 'L5', RSK: 'L2' } }
    fireEvent.click(screen.getByRole('button', { name: 'Save maturity targets' }))
    await waitFor(() => expect(api.update).toHaveBeenCalledWith('org-1', {
      maturity_targets: { IAC: 'L5', RSK: 'L4' },
    }))
    expect(api.fetch).toHaveBeenCalledWith('org-1')
  })

  it('saves nothing when the fresh read before saving fails', async () => {
    api.fetch.mockRejectedValue(new Error('Settings unavailable'))
    renderCard()
    fireEvent.change(screen.getByLabelText('IAC target'), { target: { value: 'L2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save maturity targets' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Settings unavailable')
    expect(api.update).not.toHaveBeenCalled()
  })

  it('shows the API refusal message', async () => {
    api.update.mockRejectedValue(new Error('Not SCF domain codes in this catalogue: OLD'))
    renderCard()
    fireEvent.change(screen.getByLabelText('IAC target'), { target: { value: 'L2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save maturity targets' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Not SCF domain codes in this catalogue: OLD')
  })

  it('offers no editor when the settings could not be read', () => {
    state.settings = undefined
    state.settingsError = new Error('Internal server error')
    renderCard()
    expect(screen.getByRole('alert')).toHaveTextContent('Internal server error')
    expect(screen.queryByRole('button', { name: 'Save maturity targets' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('GOV target')).not.toBeInTheDocument()
  })

  it('offers no editor when the catalogue domains could not be read', () => {
    state.domainsError = true
    renderCard()
    expect(screen.getByRole('alert')).toHaveTextContent('Catalogue unavailable')
    expect(screen.queryByRole('button', { name: 'Save maturity targets' })).not.toBeInTheDocument()
  })

  it('is read-only below admin', () => {
    state.admin = false
    renderCard()
    expect(screen.getByLabelText('GOV target')).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Save maturity targets' })).not.toBeInTheDocument()
    expect(screen.getByText(/Only an organisation administrator can change maturity targets/)).toBeInTheDocument()
  })
})
