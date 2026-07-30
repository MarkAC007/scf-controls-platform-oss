/**
 * CDM review queue — proposal-card semantics (#722).
 *
 * The queue used to ask the reviewer to decide per citation, which meant a
 * document with eight passages about one control produced eight decisions that
 * were all the same decision. These tests hold the property that replaced it:
 * one card per (control, document) proposal, with the citations demoted to
 * provenance the reviewer can open rather than work to get through.
 *
 * The assertions worth protecting are the honesty ones — a heuristic score must
 * never be dressed up as a model's judgment, a proposal that was dismissed and
 * came back must say so, and a stale decision (409) must resync the queue
 * instead of telling the reviewer their click failed.
 *
 * Placeholder SCF ids here are deliberately not of the real ``AAA-NN`` shape,
 * matching CDMProvenance.test.tsx.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../data/apiClient', () => ({
  listCdmControlProposals: vi.fn(),
  acceptCdmControlProposal: vi.fn(),
  dismissCdmControlProposal: vi.fn(),
  triggerCdmComputeMappings: vi.fn(),
  getCdmComputeMappingsStatus: vi.fn(),
}))

vi.mock('react-hot-toast', () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}))

import { toast } from 'react-hot-toast'
import CDMReviewQueue from '../CDMReviewQueue'
import {
  listCdmControlProposals,
  acceptCdmControlProposal,
  dismissCdmControlProposal,
} from '../../data/apiClient'
import type {
  CDMControlProposal,
  CDMControlProposalListResponse,
  CDMMapping,
} from '../../data/apiClient'

function citation(overrides: Partial<CDMMapping> = {}): CDMMapping {
  return {
    id: 'cit-1',
    organization_id: 'org-1',
    scoped_control_id: 'ctl-1',
    cdm_document_id: 'doc-1',
    section: '4.2 Supplier Onboarding',
    byte_offset_start: 58,
    byte_offset_end: 180,
    relevance_score: 0.462255,
    status: 'proposed',
    kb_revision: 'v2',
    accepted_by_user_id: null,
    accepted_at: null,
    dismiss_reason: null,
    dismissed_by_user_id: null,
    dismissed_at: null,
    excerpt: 'All third-party suppliers must complete a security risk assessment.',
    review_notes: null,
    last_reviewed_at: null,
    last_reviewed_by_user_id: null,
    created_at: '2026-07-28T00:00:00Z',
    scf_id: 'TESTCONTROL',
    original_filename: 'supplier-policy.pdf',
    ts_rank_component: 0.333333,
    objective_coverage_component: 0.75,
    term_overlap_component: 0.353,
    score_weights: { ts_rank: 0.5, objective_coverage: 0.3, term_overlap: 0.2 },
    match_type: 'exact',
    matched_objective_text: 'A risk assessment is conducted prior to acquisition.',
    cdm_document_chunk_id: 'chunk-1',
    retrieval_tier: 'postgres_fts',
    ...overrides,
  }
}

function proposal(overrides: Partial<CDMControlProposal> = {}): CDMControlProposal {
  return {
    id: 'prop-1',
    organization_id: 'org-1',
    scoped_control_id: 'ctl-1',
    cdm_document_id: 'doc-1',
    status: 'proposed',
    consolidated_score: 0.874,
    rationale: null,
    citation_count: 2,
    recompute_provider: null,
    recompute_model_id: null,
    kb_revision: 'v2',
    accepted_at: null,
    accepted_by_user_id: null,
    dismissed_at: null,
    dismissed_by_user_id: null,
    dismiss_reason: null,
    created_at: '2026-07-28T00:00:00Z',
    updated_at: '2026-07-28T00:00:00Z',
    scf_id: 'TESTCONTROL',
    control_name: 'Third-Party Risk Assessments',
    original_filename: 'supplier-policy.pdf',
    citations: [
      citation(),
      citation({
        id: 'cit-2',
        section: '4.3 Ongoing Monitoring',
        relevance_score: 0.31,
        excerpt: 'Supplier risk ratings are refreshed annually by the security team.',
      }),
    ],
    ...overrides,
  }
}

function listResponse(
  proposals: CDMControlProposal[],
  overrides: Partial<CDMControlProposalListResponse> = {},
): CDMControlProposalListResponse {
  return {
    proposals,
    total: proposals.length,
    offset: 0,
    limit: 25,
    ...overrides,
  }
}

/** One-proposal list with the given fields overridden. */
function oneProposal(
  overrides: Partial<CDMControlProposal>,
): CDMControlProposalListResponse {
  return listResponse([proposal(overrides)])
}

const mockList = vi.mocked(listCdmControlProposals)
const mockAccept = vi.mocked(acceptCdmControlProposal)
const mockDismiss = vi.mocked(dismissCdmControlProposal)

function conflict(): Error & { status: number } {
  const err = new Error("Proposal is in state 'accepted', not 'proposed'") as Error & {
    status: number
  }
  err.status = 409
  return err
}

beforeEach(() => {
  vi.clearAllMocks()
  mockList.mockResolvedValue(listResponse([proposal()]))
})

function renderQueue() {
  return render(<CDMReviewQueue organizationId="org-1" />)
}

async function card() {
  return (await screen.findByText('TESTCONTROL')).closest('li') as HTMLElement
}

describe('CDMReviewQueue proposal cards', () => {
  it('renders one card per proposal, not one per citation', async () => {
    mockList.mockResolvedValue(
      listResponse([
        proposal(),
        proposal({
          id: 'prop-2',
          scf_id: 'OTHERCONTROL',
          control_name: 'Cybersecurity Governance Program',
          consolidated_score: 0.55,
          citation_count: 5,
        }),
      ]),
    )
    renderQueue()

    // Two proposals carrying seven citations between them must still be two
    // decisions — the whole point of #722.
    const cards = await screen.findAllByRole('listitem')
    expect(cards).toHaveLength(2)
    expect(screen.getByText('Third-Party Risk Assessments')).toBeInTheDocument()
    expect(screen.getByText('Cybersecurity Governance Program')).toBeInTheDocument()
  })

  it('shows the control, document, status and consolidated score on the card', async () => {
    renderQueue()
    const el = await card()

    expect(within(el).getByText('Third-Party Risk Assessments')).toBeInTheDocument()
    expect(within(el).getByText('supplier-policy.pdf')).toBeInTheDocument()
    expect(within(el).getByText('Proposed')).toBeInTheDocument()
    expect(within(el).getByText(/0\.87/)).toBeInTheDocument()
    expect(within(el).getByText(/2 citations/)).toBeInTheDocument()
  })

  it('labels a heuristic score as such and claims no rationale', async () => {
    mockList.mockResolvedValue(
      oneProposal({ recompute_provider: null, rationale: null }),
    )
    renderQueue()
    const el = await card()

    expect(within(el).getByText(/heuristic score/i)).toBeInTheDocument()
    expect(within(el).queryByText(/recomputed/i)).not.toBeInTheDocument()
  })

  it('shows the consolidated judgment when a model produced the score', async () => {
    mockList.mockResolvedValue(
      oneProposal({
        recompute_provider: 'claude',
        recompute_model_id: 'claude-sonnet-5',
        rationale: 'Both passages assign supplier risk assessment to the security team.',
      }),
    )
    renderQueue()
    const el = await card()

    expect(
      within(el).getByText(/Both passages assign supplier risk assessment/),
    ).toBeInTheDocument()
    expect(within(el).getByText(/recomputed/i)).toBeInTheDocument()
    expect(within(el).queryByText(/heuristic score/i)).not.toBeInTheDocument()
  })

  it('keeps citations collapsed until asked, then shows each with its breakdown', async () => {
    renderQueue()
    const el = await card()

    expect(within(el).queryByText('4.2 Supplier Onboarding')).not.toBeInTheDocument()

    fireEvent.click(within(el).getByRole('button', { name: /2 citations/i }))

    expect(within(el).getByText('4.2 Supplier Onboarding')).toBeInTheDocument()
    expect(within(el).getByText('4.3 Ongoing Monitoring')).toBeInTheDocument()
    expect(
      within(el).getByText(/All third-party suppliers must complete/),
    ).toBeInTheDocument()
    expect(
      within(el).getByText(/Supplier risk ratings are refreshed annually/),
    ).toBeInTheDocument()
    // ScoreBreakdown, once per citation.
    expect(within(el).getAllByText('Text relevance')).toHaveLength(2)
  })

  it('says a proposal was previously dismissed when it has been resurrected', async () => {
    mockList.mockResolvedValue(
      oneProposal({ status: 'proposed', dismiss_reason: 'Wrong document version' }),
    )
    renderQueue()
    const el = await card()

    expect(within(el).getByText(/previously dismissed/i)).toBeInTheDocument()
    expect(within(el).getByText(/Wrong document version/)).toBeInTheDocument()
  })
})

describe('CDMReviewQueue decisions', () => {
  it('accepts at the proposal level and reloads the queue', async () => {
    mockAccept.mockResolvedValue({
      proposal_id: 'prop-1',
      status: 'accepted',
      accepted_at: '2026-07-30T00:00:00Z',
      accepted_by_user_id: 'u1',
      citations_accepted: 2,
    })
    renderQueue()
    const el = await card()

    fireEvent.click(within(el).getByRole('button', { name: 'Accept' }))

    await waitFor(() => expect(mockAccept).toHaveBeenCalledWith('org-1', 'prop-1'))
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2))
  })

  it('dismisses with the inline reason', async () => {
    mockDismiss.mockResolvedValue({
      proposal_id: 'prop-1',
      status: 'dismissed',
      reason: 'Aspirational, not implemented',
      dismissed_at: '2026-07-30T00:00:00Z',
      dismissed_by_user_id: 'u1',
      citations_dismissed: 2,
    })
    renderQueue()
    const el = await card()

    fireEvent.click(within(el).getByRole('button', { name: 'Dismiss' }))
    fireEvent.change(within(el).getByPlaceholderText(/Reason/i), {
      target: { value: 'Aspirational, not implemented' },
    })
    fireEvent.click(within(el).getByRole('button', { name: 'Confirm' }))

    await waitFor(() =>
      expect(mockDismiss).toHaveBeenCalledWith(
        'org-1',
        'prop-1',
        'Aspirational, not implemented',
      ),
    )
  })

  it('resyncs rather than erroring when someone else already decided (409)', async () => {
    mockAccept.mockRejectedValue(conflict())
    renderQueue()
    const el = await card()

    fireEvent.click(within(el).getByRole('button', { name: 'Accept' }))

    // A 409 means the queue is stale, not that the reviewer did anything
    // wrong. Refetch and stay quiet about it.
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2))
    expect(vi.mocked(toast.error)).not.toHaveBeenCalled()
  })

  it('surfaces a genuine failure loudly', async () => {
    mockAccept.mockRejectedValue(new Error('Backend exploded'))
    renderQueue()
    const el = await card()

    fireEvent.click(within(el).getByRole('button', { name: 'Accept' }))

    await waitFor(() =>
      expect(vi.mocked(toast.error)).toHaveBeenCalledWith('Backend exploded'),
    )
  })
})

describe('CDMReviewQueue bulk actions', () => {
  it('loops the per-proposal endpoint and carries on past a 409', async () => {
    mockList.mockResolvedValue(
      listResponse([
        proposal(),
        proposal({ id: 'prop-2', scf_id: 'OTHERCONTROL', control_name: 'Governance' }),
      ]),
    )
    mockAccept.mockRejectedValueOnce(conflict()).mockResolvedValueOnce({
      proposal_id: 'prop-2',
      status: 'accepted',
      accepted_at: '2026-07-30T00:00:00Z',
      accepted_by_user_id: 'u1',
      citations_accepted: 1,
    })
    renderQueue()
    await card()

    fireEvent.click(screen.getByRole('checkbox', { name: /Select all/i }))
    fireEvent.click(screen.getByRole('button', { name: /^Accept 2$/ }))

    // One conflict must not abandon the rest of the selection.
    await waitFor(() => expect(mockAccept).toHaveBeenCalledTimes(2))
    await waitFor(() =>
      expect(vi.mocked(toast.success)).toHaveBeenCalledWith(
        expect.stringContaining('1 already actioned'),
      ),
    )
  })
})

describe('CDMReviewQueue filters and empty state', () => {
  it('asks for the selected status', async () => {
    renderQueue()
    await card()

    fireEvent.click(screen.getByRole('tab', { name: 'Accepted' }))

    await waitFor(() =>
      expect(mockList).toHaveBeenLastCalledWith('org-1', {
        status: 'accepted',
        limit: 25,
        offset: 0,
      }),
    )
  })

  it('offers all four review states', async () => {
    renderQueue()
    await card()

    for (const label of ['Proposed', 'Accepted', 'Dismissed', 'Stale']) {
      expect(screen.getByRole('tab', { name: label })).toBeInTheDocument()
    }
  })

  it('points an empty proposed queue at Run mapping', async () => {
    mockList.mockResolvedValue(listResponse([]))
    renderQueue()

    expect(await screen.findByText(/No proposals to review/i)).toBeInTheDocument()
    expect(
      screen.getByText(/Run Mapping to \(re\)build the review queue/i),
    ).toBeInTheDocument()
  })

  it('does not blame consolidation for an empty accepted list', async () => {
    mockList.mockResolvedValue(listResponse([]))
    renderQueue()
    await screen.findByText(/No proposals to review/i)

    fireEvent.click(screen.getByRole('tab', { name: 'Accepted' }))

    await waitFor(() =>
      expect(
        screen.queryByText(/Run Mapping to \(re\)build the review queue/i),
      ).not.toBeInTheDocument(),
    )
  })

  it('counts proposals, not citations, in the pagination line', async () => {
    mockList.mockResolvedValue(listResponse([proposal()], { total: 40 }))
    renderQueue()

    expect(await screen.findByText(/Page 1 of 2 — 40 total/)).toBeInTheDocument()
  })
})
