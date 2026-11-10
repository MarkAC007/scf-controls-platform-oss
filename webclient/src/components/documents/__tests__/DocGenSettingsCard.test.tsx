/**
 * Changing the document structure asks first when it would supersede current
 * policies, standards or procedures, and says how many and how many approved.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import DocGenSettingsCard, { describeProfileOutput, describeSwitch } from '../DocGenSettingsCard'
import { ConfirmProvider } from '../../../contexts/ConfirmContext'
import {
  getDocGenSettings,
  getProfilePreview,
  listDocuments,
  updateDocGenSettings,
  type DocGenSettings,
  type DocumentSummary,
  type ProfilePreview,
} from '../../../data/documentsApi'

vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('../../../data/documentsApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../data/documentsApi')>()
  return {
    ...actual, getDocGenSettings: vi.fn(), getProfilePreview: vi.fn(),
    listDocuments: vi.fn(), updateDocGenSettings: vi.fn(),
  }
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

const scope = (key: string, title: string) => ({ key, title })
const entry = (key: string, title: string, extra: Partial<ProfilePreview['procedures']['kept'][0]> = {}) => ({
  key, title, replaced_by: [], seeded_from: [], restored: false, ...extra,
})

const PREVIEW: ProfilePreview = {
  current: scope('compact', 'Compact'),
  target: scope('essentials', 'Essentials'),
  procedures: {
    superseded: [
      entry('T-DETECT-RESPOND', 'Log Review & Incident Response', {
        replaced_by: [scope('T-INCIDENT-RESILIENCE', 'Incident Response & Recovery')],
      }),
    ],
    created: [
      entry('T-INCIDENT-RESILIENCE', 'Incident Response & Recovery', {
        seeded_from: [scope('T-DETECT-RESPOND', 'Log Review & Incident Response'), scope('T-BACKUP', 'Backup & Restore')],
      }),
      entry('T-GOVERNANCE-RISK', 'Governance, Risk & Assurance'),
    ],
    kept: [entry('T-ACCESS-MGMT', 'Access Control')],
  },
  counts: { policy: { superseded: 2, signed_off: 1 }, procedure: { superseded: 1, signed_off: 0 } },
  superseded_total: 3,
  signed_off_total: 1,
}

function renderCard(documents: DocumentSummary[], preview: ProfilePreview | null = null) {
  // No preview: the server could not be reached, and the card counts the
  // loaded library instead.
  if (preview) vi.mocked(getProfilePreview).mockResolvedValue(preview)
  else vi.mocked(getProfilePreview).mockRejectedValue(new Error('offline'))
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

describe('DocGenSettingsCard switch preview', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows what the server says the switch does before it is confirmed', async () => {
    renderCard([], PREVIEW)
    fireEvent.click(await screen.findByLabelText(/Essentials/))
    const dialog = await screen.findByRole('alertdialog')
    expect(getProfilePreview).toHaveBeenCalledWith('org-1', 'essentials')
    expect(dialog.textContent).toContain(
      '3 policies, standards and procedures were generated under Compact, 1 of them approved or published',
    )
    expect(dialog.textContent).toContain(
      '• Log Review & Incident Response: replaced by Incident Response & Recovery',
    )
    expect(updateDocGenSettings).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Switch to Essentials' }))
    await waitFor(() =>
      expect(updateDocGenSettings).toHaveBeenCalledWith('org-1', { document_profile: 'essentials' }),
    )
  })

  it('switches without asking when the preview supersedes nothing', async () => {
    renderCard(
      [doc('1', 'policy', 'approved')],
      { ...PREVIEW, superseded_total: 0, signed_off_total: 0, procedures: { superseded: [], created: [], kept: [] } },
    )
    fireEvent.click(await screen.findByLabelText(/Essentials/))
    await waitFor(() => expect(updateDocGenSettings).toHaveBeenCalled())
    expect(screen.queryByRole('alertdialog')).toBeNull()
  })
})

describe('describeSwitch', () => {
  it('lists what is replaced, what is new and seeded, and what is kept', () => {
    const text = describeSwitch(PREVIEW)
    expect(text).toContain('Procedures superseded:\n• Log Review & Incident Response: replaced by Incident Response & Recovery')
    expect(text).toContain('2 procedures are new under Essentials and start ungenerated.')
    expect(text).toContain(
      '• Incident Response & Recovery: from Log Review & Incident Response and Backup & Restore',
    )
    expect(text).not.toContain('• Governance, Risk & Assurance')
    expect(text).toContain('Procedures kept as they are: Access Control.')
    expect(text).toContain('switching back to Compact makes the superseded documents current again')
  })

  it('says so when nothing in scope replaces a superseded procedure', () => {
    const text = describeSwitch({
      ...PREVIEW,
      superseded_total: 1,
      signed_off_total: 0,
      procedures: { superseded: [entry('T-PHYSICAL', 'Physical Access')], created: [], kept: [] },
    })
    expect(text).toMatch(/^1 policy, standard or procedure was generated under Compact\./)
    expect(text).toContain('• Physical Access: nothing in scope replaces it')
    expect(text).not.toContain('Procedures kept')
  })
})

describe('describeProfileOutput', () => {
  const shaped = (bundles: number, topics: number, fold_standards: boolean) => ({
    ...profile('p', 'P'),
    generators: { policy: 'bundle', standard: fold_standards ? 'none' : 'bundle', procedure: 'topic' },
    fold_standards,
    bundles: Array.from({ length: bundles }, (_, i) => ({ key: `B-${i}`, title: `B ${i}`, domains: [] })),
    topics: Array.from({ length: topics }, (_, i) => ({ key: `T-${i}`, title: `T ${i}`, topics: [] })),
  }) as unknown as Parameters<typeof describeProfileOutput>[0]

  it.each([
    [13, 21, false, '21 procedures by operational topic'],
    [6, 12, true, '12 procedures by operational topic'],
    [1, 8, true, '8 procedures by operational topic'],
  ])('names the procedure count of a %i-policy structure', (bundles, topics, fold, phrase) => {
    expect(describeProfileOutput(shaped(bundles, topics, fold))).toContain(phrase)
  })

  it('says one per domain for the granular structure', () => {
    const granular = profile('granular', 'Granular') as unknown as Parameters<typeof describeProfileOutput>[0]
    expect(describeProfileOutput(granular)).toContain('a procedure per domain')
  })
})
