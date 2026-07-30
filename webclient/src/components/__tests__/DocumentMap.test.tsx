/**
 * Document Map rendering.
 *
 * The assertions that matter here are the ones protecting the view's
 * credibility rather than its layout: a suggested placement must never be
 * presentable as a confirmed one, a stage with no data must be absent rather
 * than shown as a confident zero, and the grid must hold catalogue order so
 * a domain is in the same place in month fourteen as it was in month one.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DocumentMap from '../DocumentMap'
import { fetchDocumentMap } from '../../data/apiClient'
import type {
  CDMDocumentMapDomain,
  CDMDocumentMapResponse,
} from '../../data/apiClient'

vi.mock('../../data/apiClient', () => ({
  fetchDocumentMap: vi.fn(),
}))

const mockFetch = vi.mocked(fetchDocumentMap)

function domain(overrides: Partial<CDMDocumentMapDomain> = {}): CDMDocumentMapDomain {
  return {
    domain: 'GOV',
    name: 'Cybersecurity & Data Protection Governance',
    display_order: 1,
    scoped_control_counts: { total: 41, selected: 33 },
    state: 'covered',
    totals: {
      documents: 1,
      confirmed_documents: 1,
      controls_with_accepted_mapping: 7,
      controls_with_proposed_mapping: 19,
    },
    documents: [
      {
        cdm_document_id: 'doc-a',
        filename: 'information-security-policy.pdf',
        intent_source: 'confirmed',
        claimed_by_model: true,
        rank: 1,
        mapping_counts: { proposed: 12, accepted: 7, dismissed: 2, stale: 0 },
      },
    ],
    ...overrides,
  }
}

const claimedDomain = domain({
  domain: 'CRY',
  name: 'Cryptographic Protections',
  display_order: 2,
  state: 'claimed',
  scoped_control_counts: { total: 14, selected: 11 },
  totals: {
    documents: 1,
    confirmed_documents: 0,
    controls_with_accepted_mapping: 0,
    controls_with_proposed_mapping: 4,
  },
  documents: [
    {
      cdm_document_id: 'doc-b',
      filename: 'encryption-standard.docx',
      intent_source: 'model',
      claimed_by_model: true,
      rank: 1,
      mapping_counts: { proposed: 4, accepted: 0, dismissed: 0, stale: 0 },
    },
  ],
})

/**
 * GOV as it looks when a person confirmed a domain that the placement pass
 * never proposed for this document. Served suggested-first, and the confirmed
 * document carries no rank — so a naive sort would bury exactly the entry that
 * matters most.
 */
const confirmedNotClaimedDomain = domain({
  totals: {
    documents: 2,
    confirmed_documents: 1,
    controls_with_accepted_mapping: 7,
    controls_with_proposed_mapping: 19,
  },
  documents: [
    {
      cdm_document_id: 'doc-suggested',
      filename: 'board-charter.docx',
      intent_source: 'model',
      claimed_by_model: true,
      rank: 1,
      mapping_counts: { proposed: 4, accepted: 0, dismissed: 0, stale: 0 },
    },
    {
      cdm_document_id: 'doc-review',
      filename: 'information-security-policy.pdf',
      intent_source: 'confirmed',
      claimed_by_model: false,
      rank: null,
      mapping_counts: { proposed: 0, accepted: 7, dismissed: 0, stale: 0 },
    },
  ],
})

const gapDomain = domain({
  domain: 'CPL',
  name: 'Compliance',
  display_order: 3,
  state: 'gap',
  scoped_control_counts: { total: 26, selected: 21 },
  totals: {
    documents: 0,
    confirmed_documents: 0,
    controls_with_accepted_mapping: 0,
    controls_with_proposed_mapping: 0,
  },
  documents: [],
})

const outOfScopeDomain = domain({
  domain: 'EMB',
  name: 'Embedded Technology',
  display_order: 4,
  state: 'out_of_scope',
  scoped_control_counts: { total: 12, selected: 0 },
  totals: {
    documents: 0,
    confirmed_documents: 0,
    controls_with_accepted_mapping: 0,
    controls_with_proposed_mapping: 0,
  },
  documents: [],
})

function response(overrides: Partial<CDMDocumentMapResponse> = {}): CDMDocumentMapResponse {
  return {
    generated_at: '2026-07-29T11:38:00Z',
    coverage_summary: {
      total_domains: 4,
      covered: 1,
      claimed: 1,
      gap: 1,
      documents_total: 3,
      documents_orphaned: 1,
      documents_awaiting_classification: 0,
    },
    domains: [domain(), claimedDomain, gapDomain, outOfScopeDomain],
    orphan_documents: [
      {
        cdm_document_id: 'doc-c',
        filename: 'office-floor-plan.pdf',
        ingest_status: 'indexed',
        intent_state: 'unclassified',
        mapping_counts: { proposed: 0, accepted: 0, dismissed: 3, stale: 0 },
      },
    ],
    ...overrides,
  }
}

function renderMap() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <DocumentMap organizationId="org-under-test" onOpenDocuments={() => {}} />
    </QueryClientProvider>
  )
}

/** The map tile for a domain, found by its accessible name. */
function tile(code: string) {
  // False positive: `code` is a literal SCF domain code supplied by this test
  // file (e.g. 'GOV', 'IAO'). It is test-local, never user input, and this file
  // is not shipped in any build output.
  // nosemgrep: javascript.lang.security.audit.detect-non-literal-regexp.detect-non-literal-regexp
  return screen.getByRole('button', { name: new RegExp(`^${code},`) })
}

beforeEach(() => {
  mockFetch.mockReset()
})

describe('DocumentMap', () => {
  it('separates a confirmed domain from a suggested one on more than colour', async () => {
    mockFetch.mockResolvedValue(response())
    renderMap()

    await waitFor(() => expect(tile('GOV')).toBeInTheDocument())

    const covered = tile('GOV')
    expect(within(covered).getByText('Confirmed')).toBeInTheDocument()
    expect(covered.querySelector('.dm-strip-continuous')).not.toBeNull()
    expect(covered.querySelector('.dm-record-solid')).not.toBeNull()
    expect(covered.querySelector('.dm-glyph-check')).not.toBeNull()

    const claimed = tile('CRY')
    expect(within(claimed).getByText('Suggested')).toBeInTheDocument()
    expect(claimed.querySelector('.dm-strip-segmented')).not.toBeNull()
    expect(claimed.querySelector('.dm-record-dashed')).not.toBeNull()
    expect(claimed.querySelector('.dm-glyph-ring')).not.toBeNull()
  })

  it('omits a depth step that has nothing to show rather than printing zero', async () => {
    mockFetch.mockResolvedValue(response())
    renderMap()

    await waitFor(() => expect(tile('GOV')).toBeInTheDocument())

    expect(tile('GOV').querySelector('.dm-pip-conf')).not.toBeNull()
    // Nothing confirmed in CRY yet — the step is absent, not a zero.
    expect(tile('CRY').querySelector('.dm-pip-conf')).toBeNull()
    expect(within(tile('CRY')).queryByText('CONF')).not.toBeInTheDocument()
  })

  it('gives a gap its own tile and keeps out-of-scope legible', async () => {
    mockFetch.mockResolvedValue(response())
    renderMap()

    await waitFor(() => expect(tile('CPL')).toBeInTheDocument())

    const gap = within(tile('CPL'))
    expect(gap.getByText('Gap')).toBeInTheDocument()
    expect(gap.getByText(/21 controls scoped/)).toBeInTheDocument()
    expect(tile('CPL').querySelector('.dm-record')).toBeNull()

    const oos = within(tile('EMB'))
    expect(oos.getByText('Not in scope')).toBeInTheDocument()
    expect(oos.getByText('No controls scoped')).toBeInTheDocument()
  })

  it('keeps catalogue order regardless of coverage state', async () => {
    mockFetch.mockResolvedValue(
      response({
        // Served out of order — the grid must still lay them out by display_order.
        domains: [gapDomain, outOfScopeDomain, domain(), claimedDomain],
      })
    )
    renderMap()

    await waitFor(() => expect(tile('GOV')).toBeInTheDocument())

    const codes = Array.from(document.querySelectorAll('.dm-grid .dm-code')).map(
      (el) => el.textContent
    )
    expect(codes).toEqual(['GOV', 'CRY', 'CPL', 'EMB'])
  })

  it('reads the adoption summary with both the encouraging and the record count', async () => {
    mockFetch.mockResolvedValue(response())
    const { container } = renderMap()

    await waitFor(() => expect(tile('GOV')).toBeInTheDocument())

    const summary = container.querySelector('.dm-summary')?.textContent ?? ''
    expect(summary).toContain('2 of 3')
    expect(summary).toContain('in-scope domains have documentation')
    expect(summary).toContain('1 confirmed')
  })

  it('counts only confirmed domains in the covered KPI, encouraging in the sub-label', async () => {
    mockFetch.mockResolvedValue(response())
    const { container } = renderMap()

    await waitFor(() => expect(tile('GOV')).toBeInTheDocument())

    const card = Array.from(container.querySelectorAll('.dm-kpi')).find((el) =>
      el.textContent?.includes('Domains covered')
    )
    expect(card).toBeDefined()
    // One covered, one claimed, one gap — the headline number is the exportable one.
    expect(card!.querySelector('.kpi-value')?.textContent).toBe('1/3')
    expect(card!.querySelector('.dm-kpi-sub')?.textContent).toBe(
      '1 more suggested · 1 gap remaining'
    )
  })

  it('drops the suggested clause from the covered KPI when nothing is claimed', async () => {
    mockFetch.mockResolvedValue(
      response({
        coverage_summary: { ...response().coverage_summary, claimed: 0 },
        domains: [domain(), gapDomain, outOfScopeDomain],
      })
    )
    const { container } = renderMap()

    await waitFor(() => expect(tile('GOV')).toBeInTheDocument())

    const card = Array.from(container.querySelectorAll('.dm-kpi')).find((el) =>
      el.textContent?.includes('Domains covered')
    )
    expect(card!.querySelector('.dm-kpi-sub')?.textContent).toBe('1 gap remaining')
  })

  it('puts confirmed documents ahead of suggested ones on the tile', async () => {
    mockFetch.mockResolvedValue(
      response({ domains: [confirmedNotClaimedDomain, claimedDomain, gapDomain, outOfScopeDomain] })
    )
    renderMap()

    await waitFor(() => expect(tile('GOV')).toBeInTheDocument())

    const names = Array.from(tile('GOV').querySelectorAll('.dm-doc-chip-name')).map(
      (el) => el.textContent
    )
    expect(names).toEqual(['information-security-policy.pdf', 'board-charter.docx'])
  })

  it('says where a placement came from when a person added it during review', async () => {
    mockFetch.mockResolvedValue(
      response({ domains: [confirmedNotClaimedDomain, claimedDomain, gapDomain, outOfScopeDomain] })
    )
    renderMap()

    await waitFor(() => expect(tile('GOV')).toBeInTheDocument())
    fireEvent.click(tile('GOV'))

    const panel = screen.getByRole('dialog')
    const rows = Array.from(panel.querySelectorAll('.dm-doc-row'))
    // Confirmed first here too, despite being served second and carrying no rank.
    expect(rows[0].querySelector('.dm-doc-name')?.textContent).toBe(
      'information-security-policy.pdf'
    )
    expect(rows[0].querySelector('.dm-doc-origin')?.textContent).toBe('Placed during review')
    // A routine placement says nothing extra.
    expect(rows[1].querySelector('.dm-doc-origin')).toBeNull()
  })

  it('leaves the origin note off a confirmed document that was placed at upload', async () => {
    mockFetch.mockResolvedValue(response())
    renderMap()

    await waitFor(() => expect(tile('GOV')).toBeInTheDocument())
    fireEvent.click(tile('GOV'))

    const panel = screen.getByRole('dialog')
    expect(within(panel).getByText('information-security-policy.pdf')).toBeInTheDocument()
    expect(within(panel).queryByText('Placed during review')).not.toBeInTheDocument()
  })

  it('lists unmapped documents in the rail and never as a tile', async () => {
    mockFetch.mockResolvedValue(response())
    renderMap()

    await waitFor(() => expect(tile('GOV')).toBeInTheDocument())

    const rail = screen.getByRole('complementary', { name: 'Unmapped documents' })
    expect(within(rail).getByText('office-floor-plan.pdf')).toBeInTheDocument()
    expect(within(rail).getByText('No domain proposed')).toBeInTheDocument()

    const codes = Array.from(document.querySelectorAll('.dm-grid .dm-code')).map(
      (el) => el.textContent
    )
    expect(codes).toHaveLength(4)
  })

  it('notes documents awaiting classification when any are outstanding', async () => {
    mockFetch.mockResolvedValue(
      response({
        coverage_summary: {
          ...response().coverage_summary,
          documents_awaiting_classification: 2,
        },
      })
    )
    renderMap()

    await waitFor(() =>
      expect(screen.getByText(/2 documents awaiting classification/)).toBeInTheDocument()
    )
  })

  it('renders the whole map on day one with a single upload call to action', async () => {
    mockFetch.mockResolvedValue(
      response({
        coverage_summary: {
          total_domains: 4,
          covered: 0,
          claimed: 0,
          gap: 3,
          documents_total: 0,
          documents_orphaned: 0,
          documents_awaiting_classification: 0,
        },
        domains: [
          { ...domain(), state: 'gap', documents: [], totals: gapDomain.totals },
          { ...claimedDomain, state: 'gap', documents: [], totals: gapDomain.totals },
          gapDomain,
          outOfScopeDomain,
        ],
        orphan_documents: [],
      })
    )
    const { container } = renderMap()

    await waitFor(() => expect(tile('GOV')).toBeInTheDocument())

    expect(
      screen.getByRole('button', { name: 'Upload your first document' })
    ).toBeInTheDocument()
    expect(container.querySelector('.dm-summary')?.textContent).toContain('0 of 3')
    // The map is complete before a single upload — every domain still has a tile.
    expect(document.querySelectorAll('.dm-grid .dm-tile')).toHaveLength(4)
  })

  it('shows a skeleton grid while loading and an error when the fetch fails', async () => {
    mockFetch.mockReturnValue(new Promise(() => {}))
    const { container, unmount } = renderMap()
    expect(container.querySelectorAll('.dm-tile-skeleton').length).toBeGreaterThan(0)
    unmount()

    mockFetch.mockRejectedValue(new Error('backend unavailable'))
    renderMap()
    await waitFor(() =>
      expect(screen.getByText(/Failed to load the document map/)).toBeInTheDocument()
    )
  })
})
