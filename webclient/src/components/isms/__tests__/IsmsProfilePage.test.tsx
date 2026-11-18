// SPDX-License-Identifier: AGPL-3.0-only
/**
 * ISMS profile page: each section saves only itself, starter rows are a
 * draft until saved, and a viewer can read but not edit.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import IsmsProfilePage from '../IsmsProfilePage'
import { getIsmsProfile, getIsmsStarters, updateIsmsSection } from '../../../data/documentsApi'
import type { IsmsProfile } from '../../../data/documentsApi'

const state = vi.hoisted(() => ({ editor: true }))

vi.mock('../../../data/documentsApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../data/documentsApi')>()
  return {
    ...actual,
    getIsmsProfile: vi.fn(),
    getIsmsStarters: vi.fn(),
    updateIsmsSection: vi.fn(),
  }
})
vi.mock('../../../hooks/useHasOrgRole', () => ({
  useIsOrgEditor: () => state.editor,
  useHasOrgRole: () => state.editor,
}))
// The register has its own records and tests; here it only has to appear.
vi.mock('../objectives/ObjectivesRegister', () => ({
  default: ({ focusObjectiveId }: { focusObjectiveId?: string | null }) => (
    <div data-testid="objectives-register" data-focus={focusObjectiveId ?? ''} />
  ),
}))
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const PROFILE: IsmsProfile = {
  scope: {
    organisation_description: '', boundaries: 'All UK operations', locations: ['London HQ'],
    services: [], interfaces: '', exclusions: [],
  },
  context_issues: [],
  interested_parties: [],
  objectives: [],
  communication_plan: [],
  audit_programme: [],
  updated_at: null,
}

beforeEach(() => {
  vi.clearAllMocks()
  // The page writes `section` to the address bar; jsdom keeps it between tests.
  window.history.replaceState(null, '', '/')
  state.editor = true
  vi.mocked(getIsmsProfile).mockResolvedValue(PROFILE)
  vi.mocked(updateIsmsSection).mockImplementation(async (_org, section, data) => ({
    ...PROFILE, [section]: data,
  }))
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <IsmsProfilePage organizationId="org-1" />
    </QueryClientProvider>
  )
}

describe('ISMS profile page', () => {
  it('saves only the section that was edited', async () => {
    renderPage()
    const boundaries = await screen.findByLabelText('Boundaries')
    expect(screen.getByRole('button', { name: 'Save scope' })).toBeDisabled()

    fireEvent.change(boundaries, { target: { value: 'UK and EU operations' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save scope' }))

    await vi.waitFor(() => expect(updateIsmsSection).toHaveBeenCalledTimes(1))
    const [, section, data] = vi.mocked(updateIsmsSection).mock.calls[0]
    expect(section).toBe('scope')
    expect(data).toMatchObject({ boundaries: 'UK and EU operations', locations: ['London HQ'] })
  })

  it('adds starter rows as an unsaved draft', async () => {
    vi.mocked(getIsmsStarters).mockResolvedValue([
      { id: 's1', what: 'Policy publication', when: 'On approval', audience: 'All staff',
        communicator: 'CISO function', method: 'Email' },
    ])
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /^Communication/ }))
    fireEvent.click(screen.getByText('Add common communications as a starting point'))

    expect(await screen.findByDisplayValue('Policy publication')).toBeInTheDocument()
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument()
    expect(updateIsmsSection).not.toHaveBeenCalled()
  })

  it('removes a row from the draft', async () => {
    vi.mocked(getIsmsProfile).mockResolvedValue({
      ...PROFILE,
      audit_programme: [{ id: 'a1', area: 'Access control', criteria: '', planned_period: '',
        auditor: '', status: 'planned', findings: '' }],
    })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /^Audit programme/ }))
    expect(screen.getByDisplayValue('Access control')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Remove audit' }))
    expect(screen.queryByDisplayValue('Access control')).not.toBeInTheDocument()
    expect(screen.getByText('No audits planned yet.')).toBeInTheDocument()
  })

  it('edits an audit’s planned date and saves it with the section', async () => {
    vi.mocked(getIsmsProfile).mockResolvedValue({
      ...PROFILE,
      audit_programme: [{ id: 'a1', area: 'Access control', criteria: '', planned_period: '2027 Q1',
        planned_date: '2027-02-15', auditor: '', status: 'planned', findings: '' }],
    })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /^Audit programme/ }))
    const date = screen.getByLabelText('Planned date')
    expect(date).toHaveAttribute('type', 'date')
    expect(date).toHaveValue('2027-02-15')
    // The free-text period stays beside it.
    expect(screen.getByLabelText('Planned period')).toHaveValue('2027 Q1')
    fireEvent.change(date, { target: { value: '2027-03-01' } })
    fireEvent.click(screen.getByRole('button', { name: /^Save audit/ }))
    await vi.waitFor(() => expect(updateIsmsSection).toHaveBeenCalledTimes(1))
    const [, section, data] = vi.mocked(updateIsmsSection).mock.calls[0]
    expect(section).toBe('audit_programme')
    expect(data).toEqual([expect.objectContaining({ id: 'a1', planned_date: '2027-03-01', planned_period: '2027 Q1' })])
    // What the server returns is shown.
    expect(await screen.findByDisplayValue('2027-03-01')).toBeInTheDocument()
  })

  it('saves a cleared planned date as null, and a new audit starts undated', async () => {
    vi.mocked(getIsmsProfile).mockResolvedValue({
      ...PROFILE,
      audit_programme: [{ id: 'a1', area: 'Access control', criteria: '', planned_period: '',
        planned_date: '2027-02-15', auditor: '', status: 'planned', findings: '' }],
    })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /^Audit programme/ }))
    fireEvent.change(screen.getByLabelText('Planned date'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: /^Save audit/ }))
    await vi.waitFor(() => expect(updateIsmsSection).toHaveBeenCalledTimes(1))
    expect(vi.mocked(updateIsmsSection).mock.calls[0][2]).toEqual([expect.objectContaining({ planned_date: null })])
  })

  it('shows the objectives register with no draft or Save bar', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /^Objectives/ }))
    expect(screen.getByTestId('objectives-register')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Save objectives/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Discard' })).not.toBeInTheDocument()
    // Other sections keep their Save bar.
    fireEvent.click(screen.getByRole('button', { name: /^Context/ }))
    expect(screen.getByRole('button', { name: 'Save context' })).toBeInTheDocument()
  })

  it('opens on the section and objective the URL names', async () => {
    window.history.replaceState(null, '', '/?tab=isms&section=objectives&objective=obj-1')
    renderPage()
    expect(await screen.findByTestId('objectives-register')).toHaveAttribute('data-focus', 'obj-1')
  })

  it('writes the chosen section to the URL without a history entry', async () => {
    window.history.replaceState(null, '', '/?tab=isms')
    const before = window.history.length
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /^Objectives/ }))
    expect(new URLSearchParams(window.location.search).get('section')).toBe('objectives')
    fireEvent.click(screen.getByRole('button', { name: /^Scope/ }))
    expect(new URLSearchParams(window.location.search).get('section')).toBe('scope')
    expect(window.history.length).toBe(before)
  })

  it('is read-only for a viewer', async () => {
    state.editor = false
    renderPage()
    expect(await screen.findByLabelText('Boundaries')).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Save scope' })).not.toBeInTheDocument()
    expect(screen.getByText(/Editing it needs the editor role/)).toBeInTheDocument()
  })
})
