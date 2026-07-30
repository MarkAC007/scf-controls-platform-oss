/**
 * CDM v2 provenance rendering (epic #709).
 *
 * These cover the two places where v1's UI actively misled a reviewer:
 * a score presented as a bare number with nothing behind it, and an empty
 * result list that meant either "you have no documents" or "your documents
 * use different words" with no way to tell which.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ScoreBreakdown } from '../CDMReviewQueue'
import { NoResultsExplanation, formatHitCount } from '../CDMControlPanel'
import type { CDMMapping, CDMQueryResponse } from '../../data/apiClient'

function mapping(overrides: Partial<CDMMapping> = {}): CDMMapping {
  return {
    id: 'm1',
    organization_id: 'o1',
    scoped_control_id: 'c1',
    cdm_document_id: 'd1',
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
    cdm_document_chunk_id: 'chunk1',
    retrieval_tier: 'postgres_fts',
    ...overrides,
  }
}

describe('ScoreBreakdown', () => {
  it('shows each component and its weight so the score can be checked', () => {
    render(<ScoreBreakdown mapping={mapping()} />)

    expect(screen.getByText('Text relevance')).toBeInTheDocument()
    expect(screen.getByText('0.333')).toBeInTheDocument()
    expect(screen.getByText('× 0.50')).toBeInTheDocument()

    expect(screen.getByText('Objective coverage')).toBeInTheDocument()
    expect(screen.getByText('0.750')).toBeInTheDocument()

    expect(screen.getByText('Term overlap')).toBeInTheDocument()
    expect(screen.getByText('0.353')).toBeInTheDocument()

    expect(screen.getByText('0.46')).toBeInTheDocument()
  })

  it('says a v1 row predates components rather than rendering zeros', () => {
    render(
      <ScoreBreakdown
        mapping={mapping({
          ts_rank_component: null,
          objective_coverage_component: null,
          term_overlap_component: null,
          score_weights: null,
        })}
      />,
    )

    expect(
      screen.getByText(/Scored before score components were recorded/i),
    ).toBeInTheDocument()
    // Zeros would read as "this passage matched nothing", which is a
    // different and false claim from "we did not record why".
    expect(screen.queryByText('0.000')).not.toBeInTheDocument()
  })
})

describe('NoResultsExplanation', () => {
  it('distinguishes an empty corpus from a terminology gap', () => {
    const { unmount } = render(
      <NoResultsExplanation reason="no_documents_ingested" />,
    )
    expect(screen.getByText(/No documents have been ingested/i)).toBeInTheDocument()
    unmount()

    render(<NoResultsExplanation reason="no_matching_passages" />)
    expect(screen.getByText(/terminology gap/i)).toBeInTheDocument()
    expect(
      screen.getByText(/no passage matching this control/i),
    ).toBeInTheDocument()
  })

  it('falls back to a plain message when the tier reports no reason', () => {
    render(<NoResultsExplanation reason={null} />)
    expect(screen.getByText('No matches.')).toBeInTheDocument()
  })
})

describe('formatHitCount', () => {
  const meta = (overrides: Partial<CDMQueryResponse>): CDMQueryResponse => ({
    hits: [],
    kb_revision: 'v2',
    retrieval_tier: 'postgres_fts',
    can_produce_mappings: true,
    candidates_shown: 10,
    candidates_total: 10,
    no_results_reason: null,
    ...overrides,
  })

  it('reveals truncation instead of implying full coverage', () => {
    expect(formatHitCount(meta({ candidates_total: 87 }), 10)).toBe(
      'showing top 10 of 87 matches',
    )
  })

  it('reports a plain count when nothing was truncated', () => {
    expect(formatHitCount(meta({ candidates_total: 3 }), 3)).toBe('3 hits')
    expect(formatHitCount(meta({ candidates_total: 1 }), 1)).toBe('1 hit')
  })

  it('reports a plain count when the tier cannot supply a total', () => {
    expect(formatHitCount(meta({ candidates_total: null }), 5)).toBe('5 hits')
  })
})
