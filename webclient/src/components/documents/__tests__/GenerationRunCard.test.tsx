/**
 * The run card: what a running batch and a finished one show, and that a
 * finished document's row opens it.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'

import GenerationRunCard from '../GenerationRunCard'
import { getGenerationStatus, type GenerationStatus } from '../../../data/documentsApi'

vi.mock('../../../data/documentsApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../data/documentsApi')>()
  return { ...actual, getGenerationStatus: vi.fn() }
})

const ITEMS: GenerationStatus['items'] = [
  { generator: 'policy', display_name: 'Policy', domain_id: 'B-ACCESS', state: 'created', document_id: 'doc-1', title: 'Access Control Policy' },
  { generator: 'policy', display_name: 'Policy', domain_id: 'B-CRYPTO', state: 'running' },
  { generator: 'procedure', display_name: 'Procedure', domain_id: 'T-INCIDENT', state: 'pending' },
]

function renderCard(status: GenerationStatus, onOpenDocument = vi.fn()) {
  vi.mocked(getGenerationStatus).mockResolvedValue(status)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <GenerationRunCard organizationId="org-1" onOpenDocument={onOpenDocument} />
    </QueryClientProvider>,
  )
  return onOpenDocument
}

describe('GenerationRunCard', () => {
  it('renders nothing when no run has happened', async () => {
    renderCard({ status: 'idle' })
    await new Promise((r) => setTimeout(r, 0))
    expect(screen.queryByLabelText('Document generation status')).toBeNull()
  })

  it('shows the current document, its stage and every queued document', async () => {
    renderCard({
      status: 'running', total: 3, completed: 1, current_index: 1, stage: 'generating',
      detail: 'Writing chapter 2 of 4: Cryptographic Protections', items: ITEMS,
      generated: 1, skipped: 0, failed: 0, started_at: new Date(Date.now() - 30_000).toISOString(),
    })
    expect(await screen.findByText('Generating documents')).toBeTruthy()
    expect(screen.getByText('1 of 3 documents')).toBeTruthy()
    expect(screen.getByText('Writing chapter 2 of 4: Cryptographic Protections')).toBeTruthy()
    expect(screen.getByText('33%')).toBeTruthy()
    expect(screen.getAllByRole('listitem').map((li) => li.textContent)).toEqual(
      expect.arrayContaining(['Check', 'Load data', 'Write', 'Merge edits', 'Save']),
    )
    expect(screen.getByText('Access Control Policy')).toBeTruthy()
    expect(screen.queryByLabelText('Dismiss run summary')).toBeNull()
  })

  it('summarises a finished run, opens a document and can be dismissed', async () => {
    const onOpen = renderCard({
      status: 'completed_with_errors', total: 2, completed: 2, generated: 1, skipped: 0, failed: 1,
      started_at: '2026-10-28T12:00:00Z', finished_at: '2026-10-28T12:01:05Z',
      items: [ITEMS![0], { ...ITEMS![1], state: 'failed', error: 'No controls in scope' }],
    })
    expect(await screen.findByText('Finished with refusals')).toBeTruthy()
    expect(screen.getByText('1m 05s total')).toBeTruthy()
    expect(screen.getByText('1 refused')).toBeTruthy()
    expect(screen.getByText('No controls in scope')).toBeTruthy()
    fireEvent.click(screen.getByText('Access Control Policy'))
    expect(onOpen).toHaveBeenCalledWith('doc-1')
    fireEvent.click(screen.getByLabelText('Dismiss run summary'))
    expect(screen.queryByLabelText('Document generation status')).toBeNull()
  })
})

describe('GenerationRunCard with a status from an older worker', () => {
  it('names the document from the message line', async () => {
    renderCard({
      status: 'running', total: 9, completed: 1, stage: 'generating',
      message: 'policy (M-ISMS): Writing chapter 10 of 30: Data Classification & Handling',
    })
    expect(await screen.findByText('Policy')).toBeTruthy()
    expect(screen.getByText('M-ISMS')).toBeTruthy()
    expect(screen.getByText('Writing chapter 10 of 30: Data Classification & Handling')).toBeTruthy()
  })
})
