/**
 * EvidenceFilePreviewModal — the AI panel tells the truth about itself (#881).
 *
 * The panel's job is not only to show a verdict but to stop the reader drawing
 * a conclusion the assessment does not support:
 *
 *   - a failed request is a failed request, not an "Insufficient" verdict and
 *     not a serene "No AI assessment yet";
 *   - a truncated read is disclosed once, at the top, because it qualifies
 *     every finding under it;
 *   - a verdict nobody can attribute to a model and prompt version is one
 *     nobody can reproduce, so the provenance renders even when it is empty.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'

import { EvidenceFilePreviewModal } from '../EvidenceFilePreviewModal'
import { makeEvidenceFile } from './evidenceFileFixture'

const mockUseAssessmentPolling = vi.fn()
vi.mock('../../../hooks/useAssessmentPolling', () => ({
  useAssessmentPolling: (...args: unknown[]) => mockUseAssessmentPolling(...args),
}))

vi.mock('../PreparerAssertionPanel', () => ({
  PreparerAssertionPanel: () => <div data-testid="preparer-assertion-panel" />,
}))

// #881 WS3: the confirmation panel now sits inside the AI panel. It has its own
// suite; stubbing it here keeps these tests about what the AI *said* rather
// than about who has confirmed it.
vi.mock('../AssessmentReviewPanel', () => ({
  AssessmentReviewPanel: () => <div data-testid="assessment-review-panel" />,
}))

function assessment(overrides: Record<string, unknown> = {}) {
  return {
    id: 'a-1',
    evidence_file_id: 'file-1',
    organization_id: 'org-1',
    evidence_id: 'ERL-001',
    status: 'partial',
    relevance_score: 62,
    findings: [],
    summary: 'Covers part of the control.',
    model_id: 'claude-sonnet-4-6',
    prompt_hash: 'abc',
    prompt_version: '1.2.0',
    control_context_hash: 'def',
    framework_version: '2025.1',
    input_token_count: 1,
    output_token_count: 1,
    cost_cents: 3,
    processing_time_ms: 10,
    assessment_source: 'on_demand',
    requested_by_user_id: 'u-1',
    assessed_at: '2026-09-01T12:00:00Z',
    created_at: '2026-09-01T12:00:00Z',
    truncated: false,
    truncated_at_chars: null,
    cached: false,
    ...overrides,
  }
}

function renderModal(hookState: Record<string, unknown>) {
  mockUseAssessmentPolling.mockReturnValue({
    assessment: null,
    loading: false,
    triggering: false,
    trigger: vi.fn(),
    requestError: null,
    retry: vi.fn(),
    ...hookState,
  })
  render(
    <EvidenceFilePreviewModal
      file={makeEvidenceFile()}
      orgId="org-1"
      evidenceId="ERL-001"
      onClose={vi.fn()}
      onDownload={vi.fn()}
      onDelete={vi.fn()}
      isDeleting={false}
    />,
  )
}

describe('AI panel — failed requests', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => cleanup())

  it('says the request failed instead of "No AI assessment yet"', async () => {
    renderModal({ requestError: { kind: 'load', message: 'Internal Server Error' } })

    await waitFor(() =>
      expect(screen.getByText('Could not load the AI assessment for this file.')).toBeTruthy(),
    )
    expect(screen.getByText('Internal Server Error')).toBeTruthy()
    // The load-bearing negative: an outage must not read as a clean slate.
    expect(screen.queryByText('No AI assessment yet')).toBeNull()
  })

  it('states plainly that this is not a finding about the evidence', () => {
    renderModal({ requestError: { kind: 'poll', message: 'network down' } })
    expect(
      screen.getByText(/not a finding about this file/),
    ).toBeTruthy()
  })

  it('offers to start the run again when it was the trigger that failed', () => {
    renderModal({ requestError: { kind: 'trigger', message: 'unavailable' } })
    expect(screen.getByText('Try again')).toBeTruthy()
    expect(screen.queryByText('Retry')).toBeNull()
  })
})

describe('AI panel — truncation disclosure', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => cleanup())

  it('renders the backend\'s own disclosure sentence, with its real figure', () => {
    renderModal({
      assessment: assessment({
        truncated: true,
        truncated_at_chars: 50000,
        findings: [{
          category: 'quality',
          level: 'info',
          message:
            'Only the first 50,000 characters of this document were assessed; the rest '
            + "was truncated to stay within the model's context budget.",
          suggestion: 'Split large documents into control-specific extracts.',
          truncated: true,
          truncated_at_chars: 50000,
        }],
      }),
    })

    expect(screen.getByTestId('ai-assessment-truncation')).toBeTruthy()
    expect(screen.getByText(/Only the first 50,000 characters/)).toBeTruthy()
    expect(screen.getByText(/Split large documents/)).toBeTruthy()
  })

  it('says it once — the disclosure is not repeated in the findings list', () => {
    renderModal({
      assessment: assessment({
        truncated: true,
        truncated_at_chars: 50000,
        findings: [
          {
            category: 'quality',
            level: 'info',
            message: 'Only the first 50,000 characters of this document were assessed.',
            truncated: true,
            truncated_at_chars: 50000,
          },
          {
            category: 'relevance',
            level: 'partial',
            message: 'The policy does not name an owner.',
          },
        ],
      }),
    })

    expect(screen.getAllByText(/Only the first 50,000 characters/)).toHaveLength(1)
    // The real findings still render — filtering removed the disclosure only.
    expect(screen.getByText('The policy does not name an owner.')).toBeTruthy()
  })

  it('falls back to its own wording for a row stored before the disclosure existed', () => {
    renderModal({ assessment: assessment({ truncated: true, truncated_at_chars: 50000 }) })
    expect(screen.getByText(/Only the first 50,000 characters/)).toBeTruthy()
  })

  it('invents no figure when it does not have one', () => {
    renderModal({ assessment: assessment({ truncated: true, truncated_at_chars: null }) })
    expect(screen.getByText(/truncated before analysis/)).toBeTruthy()
  })

  it('stays quiet when the whole document was read', () => {
    renderModal({ assessment: assessment({ truncated: false }) })
    expect(screen.queryByTestId('ai-assessment-truncation')).toBeNull()
  })
})

describe('AI panel — provenance and unassessable', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => cleanup())

  it('names the model and prompt version behind the verdict', () => {
    renderModal({ assessment: assessment() })
    const provenance = screen.getByTestId('ai-assessment-provenance')
    expect(provenance.textContent).toContain('claude-sonnet-4-6')
    expect(provenance.textContent).toContain('1.2.0')
  })

  it('admits when a verdict cannot be attributed rather than leaving a blank', () => {
    renderModal({ assessment: assessment({ model_id: null, prompt_version: null, assessed_at: null }) })
    const provenance = screen.getByTestId('ai-assessment-provenance')
    expect(provenance.textContent).toContain('not recorded')
  })

  it('explains that unassessable is not a judgement on the evidence', () => {
    renderModal({
      assessment: assessment({ status: 'unassessable', relevance_score: null }),
    })
    expect(screen.getByTestId('ai-assessment-verdict-chip').textContent).toBe(
      'AI suggests: Unassessable',
    )
    expect(screen.getByText(/not a judgement on the evidence/)).toBeTruthy()
  })

  it('renders no score for an unassessable file rather than falling back to zero', () => {
    renderModal({
      assessment: assessment({ status: 'unassessable', relevance_score: null }),
    })
    expect(screen.queryByText('0/100')).toBeNull()
  })
})
