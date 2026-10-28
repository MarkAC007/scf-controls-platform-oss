/**
 * The generate panel under a bundled document profile.
 *
 * What a policy, standard or procedure covers comes from the scopes endpoint:
 * bundles of domains for policies, operational topics for procedures, and
 * nothing for a standard the profile folds into its policies. These tests pin
 * the three things a user would notice going wrong: the right pickers appear,
 * the folded standard explains itself instead of vanishing, and the requests
 * sent carry scope keys.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import GeneratePanel from '../GeneratePanel'
import { describeProfileOutput } from '../DocGenSettingsCard'
import {
  generateDocuments,
  getDocGenSettings,
  getGenerationStatus,
  listDocumentScopes,
  listGenerators,
} from '../../../data/documentsApi'
import type { DocGenSettings, DocumentProfile, GeneratorInfo } from '../../../data/documentsApi'

vi.mock('../../../data/documentsApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../data/documentsApi')>()
  return {
    ...actual,
    listGenerators: vi.fn(),
    getDocGenSettings: vi.fn(),
    listDocumentScopes: vi.fn(),
    getGenerationStatus: vi.fn(),
    generateDocuments: vi.fn(),
  }
})

const SETTINGS: DocGenSettings = {
  enabled: true,
  derivative_generators_enabled: true,
  licence_acknowledged: true,
  licence_acknowledged_at: null,
  licence_acknowledged_by_email: null,
  licence_text_version: null,
  daily_generation_limit: 20,
  platform_disabled: false,
  acknowledgement_text: '',
  document_profile: 'compact',
}

const tier2 = (name: string, display: string): GeneratorInfo => ({
  name,
  display_name: display,
  tier: 2,
  document_type: name,
  is_derivative: true,
  domain_scoped: true,
  description: `${display} description`,
})

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getDocGenSettings).mockResolvedValue(SETTINGS)
  vi.mocked(getGenerationStatus).mockResolvedValue({ status: 'idle' })
  vi.mocked(generateDocuments).mockResolvedValue({ task_id: 't', queued: 2 })
  vi.mocked(listGenerators).mockResolvedValue([
    tier2('policy', 'Domain Policy'),
    tier2('procedure', 'Domain Procedure'),
    tier2('standard', 'Domain Standard'),
  ])
  vi.mocked(listDocumentScopes).mockResolvedValue({
    profile: 'compact',
    fold_standards: true,
    generators: {
      policy: {
        mode: 'bundle',
        scopes: [
          { key: 'G-GRC', kind: 'bundle', title: 'Governance, Risk & Compliance', domains: ['GOV', 'RSK'], control_count: 44 },
          { key: 'G-TECHOPS', kind: 'bundle', title: 'Technology Operations', domains: ['AST', 'NET'], control_count: 147 },
        ],
      },
      procedure: {
        mode: 'topic',
        scopes: [
          { key: 'T-INCIDENT', kind: 'topic', title: 'Incident Response', domains: ['IRO'], control_count: 13 },
        ],
      },
      standard: { mode: 'none', scopes: [] },
    },
  })
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <GeneratePanel organizationId="org-1" onClose={() => {}} />
    </QueryClientProvider>
  )
}

describe('generate panel under a bundled profile', () => {
  it('explains a folded standard rather than hiding it', async () => {
    renderPanel()
    await screen.findByText('Domain Standard')
    expect(screen.getByText(/each policy carries its own requirements/)).toBeInTheDocument()
    const standard = screen.getByText('Domain Standard').closest('label')!.querySelector('input')!
    expect(standard).toBeDisabled()
  })

  it('offers policy areas for policies and topics for procedures', async () => {
    renderPanel()
    fireEvent.click(await screen.findByText('Domain Policy'))
    expect(await screen.findByText('Policy areas')).toBeInTheDocument()
    expect(screen.getByText('Technology Operations')).toBeInTheDocument()
    expect(screen.queryByText('Procedure topics')).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('Domain Procedure'))
    expect(await screen.findByText('Procedure topics')).toBeInTheDocument()
    expect(screen.getByText('Incident Response')).toBeInTheDocument()
  })

  it('sends one request per selected scope, keyed by the scope', async () => {
    renderPanel()
    fireEvent.click(await screen.findByText('Domain Policy'))
    fireEvent.click(await screen.findByText('Technology Operations'))
    fireEvent.click(screen.getByText('Domain Procedure'))
    fireEvent.click(await screen.findByText('Incident Response'))

    expect(screen.getByText('2 documents selected')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }))
    await vi.waitFor(() => expect(generateDocuments).toHaveBeenCalled())
    expect(vi.mocked(generateDocuments).mock.calls[0][1]).toEqual([
      { generator: 'policy', domain_id: 'G-TECHOPS' },
      { generator: 'procedure', domain_id: 'T-INCIDENT' },
    ])
  })
})

describe('describeProfileOutput', () => {
  const profile = (over: Partial<DocumentProfile>): DocumentProfile => ({
    name: 'x', display_name: 'X', description: '', recommended_for: '',
    generators: {}, fold_standards: false, bundles: [], ...over,
  })

  it('describes the granular profile per domain', () => {
    expect(describeProfileOutput(profile({
      generators: { policy: 'domain', standard: 'domain', procedure: 'domain' },
    }))).toBe('a policy per scoped domain, a standard per domain, a procedure per domain')
  })

  it('counts bundled policies and says when requirements are folded in', () => {
    const bundles = Array.from({ length: 6 }, (_, i) => ({ key: `G-${i}`, title: '', domains: [] }))
    expect(describeProfileOutput(profile({
      generators: { policy: 'bundle', standard: 'none', procedure: 'topic' },
      fold_standards: true,
      bundles,
    }))).toBe('6 policies, procedures by operational topic; requirements are built into each policy')
  })

  it('names a single bundle a policy manual', () => {
    expect(describeProfileOutput(profile({
      generators: { policy: 'bundle', standard: 'none', procedure: 'topic' },
      bundles: [{ key: 'M-ISMS', title: '', domains: [] }],
    }))).toBe('one policy manual, procedures by operational topic')
  })
})
