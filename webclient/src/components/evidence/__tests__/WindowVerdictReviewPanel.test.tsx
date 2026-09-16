/**
 * WindowVerdictReviewPanel — the human end of the window assessment
 * (window parity, PR3).
 *
 * The same commitments as the per-file panel, held at the window layer:
 *
 *   - gated on editor, the role the API accepts;
 *   - an unconfirmed verdict reads as a suggestion; a decided one says who
 *     decided it and how;
 *   - a correction needs a reason and at least one changed objective;
 *   - the files the AI relied on for each objective are shown, so a reviewer
 *     can check the attribution rather than take it on trust;
 *   - insufficient_sample is confirmable but its status is not the reviewer's
 *     to move here;
 *   - a verdict with no frozen version cannot be confirmed (nothing to attach
 *     the decision to) and says so instead of failing on submit.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'

import { WindowVerdictReviewPanel } from '../WindowVerdictReviewPanel'

const mockReview = vi.fn()
const mockListVersions = vi.fn()
vi.mock('../../../data/apiClient', () => ({
  reviewWindowVerdict: (...args: unknown[]) => mockReview(...args),
  listWindowAssessmentVersions: (...args: unknown[]) => mockListVersions(...args),
}))

let editor = true
vi.mock('../../../hooks/useHasOrgRole', () => ({
  useHasOrgRole: () => editor,
  useIsOrgEditor: () => editor,
}))

function finding(overrides: Record<string, unknown> = {}) {
  return {
    ao_id: 'objective_alpha',
    suggested_designation: 'gap_identified',
    rationale: 'No file in the period shows the review happening.',
    suggestion: 'Attach the monthly review record.',
    evidence_file_ids: ['file_two'],
    ...overrides,
  }
}

function window(overrides: Record<string, unknown> = {}) {
  return {
    id: 'ewa_one',
    organization_id: 'org_one',
    evidence_id: 'E-BCD-01',
    window_start: '2026-08-01T00:00:00Z',
    window_end: '2026-09-05T00:00:00Z',
    assessment_status: 'partial',
    status: 'partial',
    relevance_score: 61,
    review_status: null,
    reviewed_by_user_id: null,
    reviewed_at: null,
    review_notes: null,
    schema_version: 2,
    ao_findings: [finding()],
    file_ids: ['file_one', 'file_two', 'file_three'],
    gap_count: 1,
    cannot_assess_count: 0,
    version_number: 2,
    current_version_id: 'ver_two',
    review_decision: null,
    review_reason: null,
    verdict_reviewed_at: null,
    ...overrides,
  } as never
}

function renderPanel(overrides: Record<string, unknown> = {}, onReviewed = vi.fn()) {
  render(<WindowVerdictReviewPanel orgId="org_one" assessment={window(overrides)} onReviewed={onReviewed} />)
  return onReviewed
}

beforeEach(() => {
  vi.clearAllMocks()
  editor = true
  mockListVersions.mockResolvedValue([])
})
afterEach(() => cleanup())

describe('WindowVerdictReviewPanel gating', () => {
  it('offers the review actions to an editor', () => {
    renderPanel()
    expect(screen.getByTestId('window-verdict-confirm-btn')).toBeTruthy()
    expect(screen.getByTestId('window-verdict-override-btn')).toBeTruthy()
  })

  it('shows a viewer the suggestions but not the actions', () => {
    editor = false
    renderPanel()
    expect(screen.queryByTestId('window-verdict-confirm-btn')).toBeNull()
    expect(screen.getByText(/Editor access is needed/)).toBeTruthy()
    expect(screen.getByTestId('ao-finding-objective_alpha')).toBeTruthy()
  })

  it('draws nothing while the run has not produced a verdict', () => {
    const { container } = render(
      <WindowVerdictReviewPanel orgId="org_one" assessment={window({ status: 'processing' })} onReviewed={vi.fn()} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('cannot confirm a verdict that was never frozen, and says why', () => {
    renderPanel({ current_version_id: null, version_number: 0 })
    expect(screen.queryByTestId('window-verdict-confirm-btn')).toBeNull()
    expect(screen.getByTestId('window-verdict-no-version').textContent).toMatch(/no recorded version/)
  })
})

describe('WindowVerdictReviewPanel wording', () => {
  it('words an unconfirmed verdict as a suggestion about the period', () => {
    renderPanel()
    expect(screen.getByTestId('window-verdict-review-verdict').textContent).toBe('AI suggests: Partial')
    expect(screen.getByText(/whole collection period/)).toBeTruthy()
  })

  it('names the files each objective relied on by their place in the window', () => {
    renderPanel()
    expect(screen.getByTestId('ao-finding-attribution-objective_alpha').textContent).toBe('Based on: file 2 of 3')
  })

  it('says when an objective was not attributed to any file', () => {
    renderPanel({ ao_findings: [finding({ evidence_file_ids: [] })] })
    expect(screen.getByTestId('ao-finding-attribution-objective_alpha').textContent).toMatch(/Not attributed/)
  })

  it('only uses confirmed language once a person has decided', () => {
    renderPanel({ review_decision: 'confirmed', status: 'sufficient', verdict_reviewed_at: '2026-09-10T10:00:00Z' })
    expect(screen.getByTestId('window-verdict-review-verdict').textContent).toBe('Confirmed: Sufficient')
    expect(screen.getByTestId('window-verdict-review-decided').textContent).toContain('Confirmed')
    expect(screen.queryByTestId('window-verdict-confirm-btn')).toBeNull()
  })

  it('says a corrected verdict was corrected and shows the reason', () => {
    renderPanel({ review_decision: 'overridden', status: 'insufficient', review_reason: 'The August file is a draft.' })
    expect(screen.getByTestId('window-verdict-review-verdict').textContent).toBe('Corrected: Insufficient')
    expect(screen.getByTestId('window-verdict-review-decided').textContent).toContain('The August file is a draft.')
  })

  it('explains an insufficient sample and that a correction does not move it', () => {
    renderPanel({ status: 'insufficient_sample' })
    expect(screen.getByTestId('window-verdict-review-verdict').textContent).toBe('AI suggests: Insufficient sample')
    expect(screen.getByTestId('window-verdict-sample-note').textContent).toMatch(/Too few files/)
    expect(screen.getByTestId('window-verdict-confirm-btn')).toBeTruthy()
  })

  it('tells a reviewer a pre-objective verdict must be re-run before objective review', () => {
    renderPanel({ schema_version: 1, ao_findings: [] })
    expect(screen.getByText(/predates objective-grounded window assessment/)).toBeTruthy()
    // Confirming as-is is still allowed; correcting objective by objective is not.
    expect(screen.getByTestId('window-verdict-confirm-btn')).toBeTruthy()
    expect((screen.getByTestId('window-verdict-override-btn') as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('WindowVerdictReviewPanel confirm', () => {
  it('records a confirmation and hands the updated window back', async () => {
    const updated = window({ review_decision: 'confirmed' })
    mockReview.mockResolvedValue(updated)
    const onReviewed = renderPanel()

    fireEvent.click(screen.getByTestId('window-verdict-confirm-btn'))

    await waitFor(() => expect(onReviewed).toHaveBeenCalledWith(updated))
    expect(mockReview).toHaveBeenCalledWith('org_one', 'ewa_one', { decision: 'confirmed' })
  })

  it('shows the server’s refusal rather than pretending it landed', async () => {
    mockReview.mockRejectedValue(new Error('Segregation of duties: you uploaded every file in this window'))
    const onReviewed = renderPanel()

    fireEvent.click(screen.getByTestId('window-verdict-confirm-btn'))

    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/Segregation of duties/))
    expect(onReviewed).not.toHaveBeenCalled()
  })
})

describe('WindowVerdictReviewPanel override', () => {
  it('needs a changed objective and a reason before it can be saved', () => {
    renderPanel()
    fireEvent.click(screen.getByTestId('window-verdict-override-btn'))
    const save = screen.getByTestId('window-verdict-save-btn') as HTMLButtonElement
    expect(save.disabled).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: 'Appears satisfied' }))
    expect(save.disabled).toBe(true)
    expect(screen.getByText('1 objective changed.')).toBeTruthy()

    fireEvent.change(screen.getByTestId('window-verdict-reason'), { target: { value: 'The review is in file 2.' } })
    expect(save.disabled).toBe(false)
  })

  it('sends only the objectives that differ from the AI, with the reason', async () => {
    const updated = window({ review_decision: 'overridden', status: 'sufficient' })
    mockReview.mockResolvedValue(updated)
    const onReviewed = renderPanel({
      ao_findings: [finding(), finding({ ao_id: 'objective_bravo', suggested_designation: 'appears_satisfied' })],
    })

    fireEvent.click(screen.getByTestId('window-verdict-override-btn'))
    // Change alpha; pick bravo's own answer, which is not a disagreement.
    const alpha = screen.getByTestId('ao-finding-objective_alpha')
    fireEvent.click(alpha.querySelector('button[aria-pressed="false"]') as HTMLButtonElement)
    const bravo = screen.getByTestId('ao-finding-objective_bravo')
    fireEvent.click(bravo.querySelector('button[aria-pressed="true"]') as HTMLButtonElement)
    fireEvent.change(screen.getByTestId('window-verdict-reason'), { target: { value: 'File 2 shows it.' } })
    fireEvent.click(screen.getByTestId('window-verdict-save-btn'))

    await waitFor(() => expect(onReviewed).toHaveBeenCalledWith(updated))
    const body = mockReview.mock.calls[0][2]
    expect(body.decision).toBe('overridden')
    expect(body.reason).toBe('File 2 shows it.')
    expect(body.ao_overrides).toHaveLength(1)
    expect(body.ao_overrides[0].ao_id).toBe('objective_alpha')
  })
})

describe('WindowVerdictReviewPanel history', () => {
  it('loads the window’s own history on demand and shows corrections beside the AI original', async () => {
    mockListVersions.mockResolvedValue([
      {
        id: 'ver_two', version_number: 2, schema_version: 2, status: 'sufficient',
        model_id: 'claude-x', prompt_version: '2.1.0', assessed_at: '2026-09-05T00:00:00Z',
        review_decision: 'overridden', review_reason: 'Draft, not final.',
        ao_overrides: [{ ao_id: 'objective_alpha', ai_designation: 'gap_identified', human_designation: 'appears_satisfied', note: '' }],
      },
      {
        id: 'ver_one', version_number: 1, schema_version: 1, status: 'partial',
        model_id: 'claude-x', prompt_version: '2.0.0', assessed_at: '2026-08-05T00:00:00Z',
        review_decision: null, review_reason: null, ao_overrides: null,
      },
    ])
    renderPanel()
    fireEvent.click(screen.getByText(/Show assessment history/))

    await waitFor(() => expect(screen.getByTestId('assessment-history')).toBeTruthy())
    expect(mockListVersions).toHaveBeenCalledWith('org_one', 'ewa_one')
    expect(screen.getByText('v2')).toBeTruthy()
    expect(screen.getByText('Reason: Draft, not final.')).toBeTruthy()
    expect(screen.getByText(/pre-objective verdict/)).toBeTruthy()
  })

  it('does not draw a history it could not fetch as an empty one', async () => {
    mockListVersions.mockRejectedValue(new Error('history unavailable'))
    renderPanel()
    fireEvent.click(screen.getByText(/Show assessment history/))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('history unavailable'))
    expect(screen.queryByText('No earlier verdicts recorded.')).toBeNull()
  })
})
