/**
 * The document map: a control search names the documents it lands in, with
 * the generated copy openable and the rest marked not generated, and the map
 * says which controls a topic structure leaves without a procedure.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'

import DocumentMap from '../DocumentMap'
import {
  getDocumentMap,
  listDocuments,
  type DocumentMap as DocumentMapData,
  type DocumentSummary,
} from '../../../data/documentsApi'

vi.mock('../../../data/documentsApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../data/documentsApi')>()
  return { ...actual, getDocumentMap: vi.fn(), listDocuments: vi.fn() }
})

const MAP: DocumentMapData = {
  profile: 'essentials',
  profile_display_name: 'Essentials',
  fold_standards: true,
  domains: [
    { identifier: 'GOV', name: 'Cybersecurity & Data Protection Governance', control_count: 1 },
    { identifier: 'IAC', name: 'Identification & Authentication', control_count: 2 },
  ],
  controls: [
    { scf_id: 'GOV-02', name: 'Publishing Policies', domain: 'GOV' },
    { scf_id: 'IAC-08', name: 'Role-Based Access Control', domain: 'IAC' },
    { scf_id: 'IAC-21', name: 'Least Privilege', domain: 'IAC' },
  ],
  generators: {
    policy: {
      display_name: 'Policy', mode: 'bundle',
      scopes: [{ key: 'M-ISMS', kind: 'bundle', title: 'Information Security Manual', domains: ['GOV', 'IAC'], controls: ['GOV-02', 'IAC-08', 'IAC-21'] }],
    },
    standard: { display_name: 'Standard', mode: 'none', scopes: [] },
    procedure: {
      display_name: 'Procedure', mode: 'topic',
      scopes: [{ key: 'T-ACCESS', kind: 'topic', title: 'Access Management Procedure', domains: ['IAC'], controls: ['IAC-08'] }],
    },
  },
}

const POLICY_DOC = {
  id: 'doc-1', generator_name: 'policy', document_type: 'policy', domain_id: 'M-ISMS',
  title: 'Information Security Manual', lifecycle_status: 'draft', superseded: false,
} as unknown as DocumentSummary

function renderMap(onOpenDocument = vi.fn()) {
  vi.mocked(getDocumentMap).mockResolvedValue(MAP)
  vi.mocked(listDocuments).mockResolvedValue([POLICY_DOC])
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <DocumentMap organizationId="org-1" onOpenDocument={onOpenDocument} />
    </QueryClientProvider>,
  )
  return onOpenDocument
}

describe('DocumentMap', () => {
  it('names the structure and the controls a topic procedure leaves uncovered', async () => {
    renderMap()
    expect(await screen.findByRole('heading', { name: 'Essentials' })).toBeTruthy()
    expect(screen.getByText(/Standards are written into the policies/)).toBeTruthy()
    expect(screen.getByText(/2 in-scope controls have no procedure/)).toBeTruthy()
  })

  it('answers which documents a control is in', async () => {
    const onOpen = renderMap()
    const search = await screen.findByLabelText('Find a control')
    fireEvent.change(search, { target: { value: 'iac-08' } })
    const result = screen.getByText('IAC-08').closest('li') as HTMLElement
    // In the generated manual (openable) and a procedure not generated yet.
    const manual = within(result).getByRole('button', { name: /Information Security Manual/ })
    expect(within(result).getByText('Access Management Procedure')).toBeTruthy()
    expect(within(result).getByText('Not generated yet')).toBeTruthy()
    expect(within(result).getByText('Folded into the policy')).toBeTruthy()
    fireEvent.click(manual)
    expect(onOpen).toHaveBeenCalledWith('doc-1')
  })

  it('says when a control has no procedure under a topic structure', async () => {
    renderMap()
    fireEvent.change(await screen.findByLabelText('Find a control'), { target: { value: 'least' } })
    const result = screen.getByText('IAC-21').closest('li') as HTMLElement
    expect(within(result).getByText('Not covered by a topic procedure')).toBeTruthy()
  })
})
