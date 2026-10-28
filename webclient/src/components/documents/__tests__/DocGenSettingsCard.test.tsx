/**
 * Changing the document structure asks first when it would supersede current
 * policies, standards or procedures, and says how many and how many approved.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import DocGenSettingsCard from '../DocGenSettingsCard'
import { ConfirmProvider } from '../../../contexts/ConfirmContext'
import {
  getDocGenSettings,
  listDocuments,
  updateDocGenSettings,
  type DocGenSettings,
  type DocumentSummary,
} from '../../../data/documentsApi'

vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('../../../data/documentsApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../data/documentsApi')>()
  return { ...actual, getDocGenSettings: vi.fn(), listDocuments: vi.fn(), updateDocGenSettings: vi.fn() }
})

const profile = (name: string, display_name: string) => ({
  name, display_name, description: '', recommended_for: '',
  generators: { policy: 'domain', standard: 'domain', procedure: 'domain' }, fold_standards: false, bundles: [],
})

const SETTINGS = {
  enabled: true, derivative_generators_enabled: true, licence_acknowledged: true,
  licence_acknowledged_at: null, licence_acknowledged_by_email: null, licence_text_version: null,
  daily_generation_limit: 10, platform_disabled: false, acknowledgement_text: '',
  document_profile: 'granular',
  profiles: [profile('granular', 'Granular'), profile('essentials', 'Essentials')],
} as unknown as DocGenSettings

const doc = (id: string, generator_name: string, lifecycle_status: string, superseded = false) =>
  ({ id, generator_name, lifecycle_status, superseded }) as unknown as DocumentSummary

function renderCard(documents: DocumentSummary[]) {
  vi.mocked(getDocGenSettings).mockResolvedValue(SETTINGS)
  vi.mocked(listDocuments).mockResolvedValue(documents)
  vi.mocked(updateDocGenSettings).mockResolvedValue({ ...SETTINGS, document_profile: 'essentials' })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <ConfirmProvider>
        <DocGenSettingsCard organizationId="org-1" />
      </ConfirmProvider>
    </QueryClientProvider>,
  )
}

describe('DocGenSettingsCard structure change', () => {
  beforeEach(() => vi.clearAllMocks())

  it('confirms before superseding current documents and cancels cleanly', async () => {
    renderCard([
      doc('1', 'policy', 'approved'), doc('2', 'procedure', 'draft'),
      doc('3', 'soa', 'approved'), doc('4', 'policy', 'draft', true),
    ])
    await waitFor(() => expect(listDocuments).toHaveBeenCalled())
    fireEvent.click(await screen.findByLabelText(/Essentials/))
    const dialog = await screen.findByRole('alertdialog')
    // The SoA and an already-superseded policy are not counted.
    expect(dialog.textContent).toMatch(/2 policies, standards and procedures were generated under Granular, 1 of them approved or published/)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(updateDocGenSettings).not.toHaveBeenCalled()
  })

  it('switches once confirmed', async () => {
    renderCard([doc('1', 'policy', 'draft')])
    await waitFor(() => expect(listDocuments).toHaveBeenCalled())
    fireEvent.click(await screen.findByLabelText(/Essentials/))
    fireEvent.click(await screen.findByRole('button', { name: 'Switch to Essentials' }))
    await waitFor(() =>
      expect(updateDocGenSettings).toHaveBeenCalledWith('org-1', { document_profile: 'essentials' }),
    )
  })

  it('switches without asking when nothing would be superseded', async () => {
    renderCard([doc('3', 'soa', 'approved')])
    await waitFor(() => expect(listDocuments).toHaveBeenCalled())
    fireEvent.click(await screen.findByLabelText(/Essentials/))
    await waitFor(() => expect(updateDocGenSettings).toHaveBeenCalled())
    expect(screen.queryByRole('alertdialog')).toBeNull()
  })
})
