/**
 * DocumentReader breadcrumb + pager bar tests.
 *
 * Task 7 (Phase 4): adds a breadcrumb "‹ Generated Documents / <name>" and a
 * "k of N documents" pager above the existing masthead.
 *
 * TDD: these tests are written first and drive the implementation.
 *
 * Coverage:
 *   - Breadcrumb renders "Generated Documents" back link and doc name
 *   - Pager shows "k of N documents" position text
 *   - Prev/next buttons fire onPrev/onNext
 *   - Pager bounds: prev disabled at first, next disabled at last
 *   - Keyboard ArrowLeft/ArrowRight/Esc navigation
 *   - Keyboard suppressed in input/textarea/contentEditable targets
 *   - Position null: no position text, both buttons disabled
 *   - Position index null: "— of N" with both buttons disabled
 */
import { fireEvent, render, screen, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi, afterEach } from 'vitest'

// ─── Mock heavy sub-components ────────────────────────────────────────────────

vi.mock('../OutlineCount', () => ({
  default: () => <span data-testid="outline-count" />,
}))

vi.mock('../SectionDecision', () => ({
  default: () => <div data-testid="section-decision" />,
}))

vi.mock('../SectionDiff', () => ({
  default: () => <div data-testid="section-diff" />,
}))

vi.mock('../useResolveSection', () => ({
  useResolveSection: () => ({ mutate: vi.fn(), isPending: false }),
}))

// DOMPurify in jsdom — keep it simple
vi.mock('dompurify', () => ({
  default: { sanitize: (html: string) => html },
}))

// documentsApi — return minimal doc + no preview/history
vi.mock('../../../data/documentsApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../data/documentsApi')>()
  return {
    ...actual,
    getDocument: vi.fn().mockResolvedValue({
      id: 'doc-1',
      title: 'Information Security Policy',
      lifecycle_status: 'draft',
      generation_version: 2,
      catalog_version: '2025.4',
      section_count: 12,
      pending_retirement_count: 0,
      is_stale: false,
      stale_reason: null,
      sections: [],
    }),
    previewDocument: vi.fn().mockResolvedValue({ html: '<p>body</p>' }),
    getDocumentHistory: vi.fn().mockResolvedValue({ transitions: [], versions: [] }),
    downloadDocument: vi.fn(),
  }
})

// ─── Import after mocks ───────────────────────────────────────────────────────

import DocumentReader, { type DocumentReaderProps } from '../DocumentReader'

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeProps(overrides: Partial<DocumentReaderProps> = {}): DocumentReaderProps {
  return {
    organizationId: 'org-1',
    documentId: 'doc-1',
    documentTitle: 'Information Security Policy',
    position: { index: 1, total: 9 },
    onPrev: vi.fn(),
    onNext: vi.fn(),
    onBack: vi.fn(),
    onEdit: vi.fn(),
    ...overrides,
  }
}

function renderReader(props?: Partial<DocumentReaderProps>) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>
      <DocumentReader {...makeProps(props)} />
    </QueryClientProvider>,
  )
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('DocumentReader breadcrumb', () => {
  afterEach(() => vi.restoreAllMocks())

  it('renders a "Generated Documents" back link', () => {
    renderReader()
    expect(
      screen.getByRole('button', { name: /generated documents/i }),
    ).toBeInTheDocument()
  })

  it('renders the document name in the breadcrumb', () => {
    renderReader()
    // The breadcrumb shows the doc name (title prop comes via documentTitle or
    // after the query resolves — the title prop is the fast path before the
    // query comes back)
    expect(screen.getByText('Information Security Policy')).toBeInTheDocument()
  })

  it('calls onBack when the back link is clicked', () => {
    const onBack = vi.fn()
    renderReader({ onBack })
    fireEvent.click(screen.getByRole('button', { name: /generated documents/i }))
    expect(onBack).toHaveBeenCalledOnce()
  })
})

describe('DocumentReader pager', () => {
  afterEach(() => vi.restoreAllMocks())

  it('shows "2 of 9 documents" position text (1-based)', () => {
    renderReader({ position: { index: 1, total: 9 } })
    expect(screen.getByText('2 of 9 documents')).toBeInTheDocument()
  })

  it('shows "1 of 1 documents" for a single-document list', () => {
    renderReader({ position: { index: 0, total: 1 } })
    expect(screen.getByText('1 of 1 documents')).toBeInTheDocument()
  })

  it('shows "— of N documents" when index is null (doc not in filtered set)', () => {
    renderReader({ position: { index: null, total: 9 } })
    expect(screen.getByText('— of 9 documents')).toBeInTheDocument()
  })

  it('renders no position text when position is null (total unknown)', () => {
    renderReader({ position: null })
    expect(screen.queryByText(/of \d+ documents/)).not.toBeInTheDocument()
  })

  it('fires onPrev when the prev button is clicked', () => {
    const onPrev = vi.fn()
    renderReader({ onPrev, position: { index: 1, total: 9 } })
    fireEvent.click(screen.getByRole('button', { name: /previous document/i }))
    expect(onPrev).toHaveBeenCalledOnce()
  })

  it('fires onNext when the next button is clicked', () => {
    const onNext = vi.fn()
    renderReader({ onNext, position: { index: 1, total: 9 } })
    fireEvent.click(screen.getByRole('button', { name: /next document/i }))
    expect(onNext).toHaveBeenCalledOnce()
  })

  it('disables prev at the first document', () => {
    renderReader({ position: { index: 0, total: 9 } })
    expect(screen.getByRole('button', { name: /previous document/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /next document/i })).not.toBeDisabled()
  })

  it('disables next at the last document', () => {
    renderReader({ position: { index: 8, total: 9 } })
    expect(screen.getByRole('button', { name: /previous document/i })).not.toBeDisabled()
    expect(screen.getByRole('button', { name: /next document/i })).toBeDisabled()
  })

  it('disables both buttons when position is null', () => {
    renderReader({ position: null })
    expect(screen.getByRole('button', { name: /previous document/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /next document/i })).toBeDisabled()
  })

  it('disables both buttons when index is null (not in filtered set)', () => {
    renderReader({ position: { index: null, total: 9 } })
    expect(screen.getByRole('button', { name: /previous document/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /next document/i })).toBeDisabled()
  })
})

describe('DocumentReader keyboard navigation', () => {
  afterEach(() => vi.restoreAllMocks())

  it('ArrowLeft fires onPrev', () => {
    const onPrev = vi.fn()
    renderReader({ onPrev, position: { index: 1, total: 9 } })
    act(() => { fireEvent.keyDown(window, { key: 'ArrowLeft' }) })
    expect(onPrev).toHaveBeenCalledOnce()
  })

  it('ArrowRight fires onNext', () => {
    const onNext = vi.fn()
    renderReader({ onNext, position: { index: 1, total: 9 } })
    act(() => { fireEvent.keyDown(window, { key: 'ArrowRight' }) })
    expect(onNext).toHaveBeenCalledOnce()
  })

  it('Escape fires onBack', () => {
    const onBack = vi.fn()
    renderReader({ onBack })
    act(() => { fireEvent.keyDown(window, { key: 'Escape' }) })
    expect(onBack).toHaveBeenCalledOnce()
  })

  it('ArrowRight does NOT fire onNext when focus is in an <input>', () => {
    const onNext = vi.fn()
    renderReader({ onNext, position: { index: 1, total: 9 } })
    const input = document.createElement('input')
    document.body.appendChild(input)
    input.focus()
    act(() => { fireEvent.keyDown(input, { key: 'ArrowRight' }) })
    // The handler is on window; jsdom routes document key events through window
    // but the target will be the input, so isSuppressed returns true.
    expect(onNext).not.toHaveBeenCalled()
    input.remove()
  })

  it('ArrowRight does NOT fire onNext when focus is in a <textarea>', () => {
    const onNext = vi.fn()
    renderReader({ onNext, position: { index: 1, total: 9 } })
    const ta = document.createElement('textarea')
    document.body.appendChild(ta)
    ta.focus()
    act(() => { fireEvent.keyDown(ta, { key: 'ArrowRight' }) })
    expect(onNext).not.toHaveBeenCalled()
    ta.remove()
  })
})
