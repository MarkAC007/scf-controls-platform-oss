/**
 * The library's arithmetic and its grammar.
 *
 * Both are credibility problems rather than cosmetic ones. A card that folds
 * retiring sections into the section count tells a compliance officer their
 * Statement of Applicability is 69 sections long when 30 of those are ghosts
 * awaiting deletion — and the reader, one click away, says 39. A banner that
 * reads "1 of your documents need a decision" undermines the same surface in a
 * smaller way. The assertions below pin the count semantics (operative only,
 * retiring reported separately and only when the backend actually reports it)
 * and subject-verb agreement at 1 and at N.
 *
 * The URL cases cover the other half: the workspace records where it is in the
 * query string, so a reload or a pasted link lands back on the same document
 * in the same mode. Deliberately parameter sync and not a router — see
 * `data/appUrl.ts`, which owns the vocabulary all URL-aware screens share.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DocumentsPage from '../DocumentsPage'
import { getDocGenSettings, listDocuments } from '../../../data/documentsApi'
import type { DocGenSettings, DocumentSummary } from '../../../data/documentsApi'

vi.mock('../../../data/documentsApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../data/documentsApi')>()
  return { ...actual, listDocuments: vi.fn(), getDocGenSettings: vi.fn() }
})

// The reader, the editor and the generate panel each fetch their own data.
// None of that is under test here, and mounting them for real would make these
// cases depend on three other surfaces' contracts.
vi.mock('../DocumentReader', () => ({
  default: ({ documentId }: { documentId: string }) => (
    <div data-testid="reader">reader:{documentId}</div>
  ),
}))
vi.mock('../DocumentEditor', () => ({
  default: ({ documentId }: { documentId: string }) => (
    <div data-testid="editor">editor:{documentId}</div>
  ),
}))
vi.mock('../GeneratePanel', () => ({ default: () => <div data-testid="generate" /> }))

const mockList = vi.mocked(listDocuments)
const mockSettings = vi.mocked(getDocGenSettings)

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
}

function doc(overrides: Partial<DocumentSummary> = {}): DocumentSummary {
  return {
    id: 'doc-1',
    generator_name: 'soa',
    document_type: 'soa',
    domain_id: '',
    title: 'Statement of Applicability',
    lifecycle_status: 'draft',
    tier: 1,
    is_derivative: false,
    generation_version: 3,
    catalog_version: '2025.2',
    section_count: 39,
    conflict_count: 0,
    edited_count: 0,
    pending_retirement_count: 0,
    updated_at: '2026-08-20T10:00:00Z',
    ...overrides,
  }
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>
      <DocumentsPage organizationId="org-1" />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockSettings.mockResolvedValue(SETTINGS)
  window.history.replaceState({}, '', '/')
})

afterEach(() => {
  window.history.replaceState({}, '', '/')
})

describe('library card section counts', () => {
  it('counts one section in the singular', async () => {
    mockList.mockResolvedValue([doc({ section_count: 1 })])
    renderPage()
    expect(await screen.findByText('1 section')).toBeInTheDocument()
    expect(screen.queryByText('1 sections')).not.toBeInTheDocument()
  })

  it('counts many sections in the plural', async () => {
    mockList.mockResolvedValue([doc({ section_count: 39 })])
    renderPage()
    expect(await screen.findByText('39 sections')).toBeInTheDocument()
  })

  it('reports retiring sections separately, in OutlineCount’s words', async () => {
    mockList.mockResolvedValue([
      doc({ section_count: 39, pending_retirement_count: 30 }),
    ])
    renderPage()
    // The operative count is the count. The 30 are a separate, secondary tally
    // — never added in, which is the defect this test exists for.
    expect(await screen.findByText('39 sections')).toBeInTheDocument()
    expect(screen.getByText('+30 retiring')).toBeInTheDocument()
    expect(screen.queryByText('69 sections')).not.toBeInTheDocument()
  })

  it('says nothing about retirement when nothing is retiring', async () => {
    mockList.mockResolvedValue([
      doc({ section_count: 39, pending_retirement_count: 0 }),
    ])
    renderPage()
    await screen.findByText('39 sections')
    expect(screen.queryByText(/retiring/)).not.toBeInTheDocument()
  })

  it('treats a missing pending_retirement_count as not reported, not as zero', async () => {
    // An older backend omits the field. Rendering it as a number would print
    // "+undefined retiring" on every card in the library.
    const older = doc({ section_count: 39 })
    delete (older as Partial<DocumentSummary>).pending_retirement_count
    mockList.mockResolvedValue([older])
    renderPage()
    await screen.findByText('39 sections')
    expect(screen.queryByText(/retiring/)).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain('undefined')
  })
})

describe('count-bearing sentences agree in number', () => {
  it('uses a singular verb for one conflicted section', async () => {
    mockList.mockResolvedValue([doc({ conflict_count: 1 })])
    renderPage()
    expect(
      await screen.findByText(/1 section across your documents needs your decision\./)
    ).toBeInTheDocument()
    expect(screen.getByText('1 section needs a decision')).toBeInTheDocument()
  })

  it('uses a plural verb for several conflicted sections', async () => {
    mockList.mockResolvedValue([
      doc({ id: 'doc-1', conflict_count: 2 }),
      doc({ id: 'doc-2', title: 'Access Control Policy', conflict_count: 1 }),
    ])
    renderPage()
    expect(
      await screen.findByText(/3 sections across your documents need your decision\./)
    ).toBeInTheDocument()
    expect(screen.getByText('2 sections need a decision')).toBeInTheDocument()
  })

  it('leaves the number-neutral edited tally alone', async () => {
    mockList.mockResolvedValue([doc({ edited_count: 1 })])
    renderPage()
    expect(await screen.findByText('1 edited')).toBeInTheDocument()
  })
})

describe('URL parameter sync', () => {
  it('opens the document named by ?doc on mount', async () => {
    window.history.replaceState({}, '', '/?tab=documents&doc=doc-42&mode=reader')
    mockList.mockResolvedValue([])
    renderPage()
    expect(await screen.findByTestId('reader')).toHaveTextContent('reader:doc-42')
  })

  it('opens the editor when ?mode=editor', async () => {
    window.history.replaceState({}, '', '/?tab=documents&doc=doc-42&mode=editor')
    mockList.mockResolvedValue([])
    renderPage()
    expect(await screen.findByTestId('editor')).toHaveTextContent('editor:doc-42')
  })

  it('ignores ?mode without a ?doc rather than opening the editor on nothing', async () => {
    window.history.replaceState({}, '', '/?tab=documents&mode=editor')
    mockList.mockResolvedValue([doc()])
    renderPage()
    // Both the type group and the card carry this title, so the card's own
    // heading is the unambiguous target.
    await screen.findByRole('heading', { level: 3, name: 'Statement of Applicability' })
    expect(screen.queryByTestId('editor')).not.toBeInTheDocument()
    expect(screen.queryByTestId('reader')).not.toBeInTheDocument()
  })

  it('writes ?doc and ?mode when a document is opened', async () => {
    window.history.replaceState({}, '', '/?tab=documents&doc=doc-7&mode=reader')
    mockList.mockResolvedValue([])
    renderPage()
    await screen.findByTestId('reader')
    await waitFor(() => {
      const params = new URLSearchParams(window.location.search)
      expect(params.get('doc')).toBe('doc-7')
      expect(params.get('mode')).toBe('reader')
    })
  })

  it('follows Back to the library when the URL loses its ?doc', async () => {
    window.history.replaceState({}, '', '/?tab=documents&doc=doc-7&mode=reader')
    mockList.mockResolvedValue([doc()])
    renderPage()
    await screen.findByTestId('reader')

    window.history.replaceState({}, '', '/?tab=documents')
    window.dispatchEvent(new PopStateEvent('popstate'))

    await waitFor(() => expect(screen.queryByTestId('reader')).not.toBeInTheDocument())
    expect(
      await screen.findByRole('heading', { level: 3, name: 'Statement of Applicability' })
    ).toBeInTheDocument()
  })

  it('clears ?doc and ?mode when the reader is closed', async () => {
    window.history.replaceState({}, '', '/?tab=documents&doc=doc-7&mode=reader')
    mockList.mockResolvedValue([doc()])
    renderPage()
    await screen.findByTestId('reader')

    window.history.replaceState({}, '', '/?tab=documents')
    window.dispatchEvent(new PopStateEvent('popstate'))

    await waitFor(() => {
      const params = new URLSearchParams(window.location.search)
      expect(params.get('doc')).toBeNull()
      expect(params.get('mode')).toBeNull()
      // The tab parameter belongs to App.tsx and must survive untouched.
      expect(params.get('tab')).toBe('documents')
    })
  })
})
