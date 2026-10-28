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
vi.mock('../../../hooks/useHasOrgRole', () => ({ useIsOrgEditor: () => state.editor }))
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
      objectives: [{ id: 'o1', objective: 'Patch critical vulnerabilities within 14 days',
        measure: '', target: '', owner: '', resources: '', due_date: null, evaluation: '',
        status: 'planned' }],
    })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /^Objectives/ }))
    expect(screen.getByDisplayValue(/Patch critical/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Remove objective' }))
    expect(screen.queryByDisplayValue(/Patch critical/)).not.toBeInTheDocument()
    expect(screen.getByText('No objectives recorded yet.')).toBeInTheDocument()
  })

  it('is read-only for a viewer', async () => {
    state.editor = false
    renderPage()
    expect(await screen.findByLabelText('Boundaries')).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Save scope' })).not.toBeInTheDocument()
    expect(screen.getByText(/Editing it needs the editor role/)).toBeInTheDocument()
  })
})
