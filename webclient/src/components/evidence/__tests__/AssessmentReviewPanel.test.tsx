/**
 * AssessmentReviewPanel — the human end of the assessment (#881 WS3).
 *
 * What these tests defend:
 *
 *   - the panel is gated on *editor*, the role the API actually accepts. A
 *     viewer sees the suggestions and is told why they cannot act; gating on
 *     admin would hide a control from people the platform already authorises;
 *   - a correction cannot be filed without saying what is wrong with the
 *     original — the reason is the audit trail, and an empty one is worse than
 *     no override;
 *   - picking the AI's own answer is not a disagreement and must not be
 *     recorded as one;
 *   - a disagreement, once recorded, still shows what it disagreed with. A
 *     correction that hid the original would leave no way to see that a human
 *     and the model differed.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'

import { AssessmentReviewPanel } from '../AssessmentReviewPanel'

const mockReview = vi.fn()
const mockListVersions = vi.fn()
vi.mock('../../../data/apiClient', () => ({
  reviewAssessment: (...args: unknown[]) => mockReview(...args),
  listAssessmentVersions: (...args: unknown[]) => mockListVersions(...args),
}))

// useIsOrgEditor reads AuthContext; without this the render dies with
// "useAuth must be used within AuthProvider" rather than testing anything.
let editor = true
vi.mock('../../../hooks/useHasOrgRole', () => ({
  useHasOrgRole: () => editor,
  useIsOrgEditor: () => editor,
}))

function finding(overrides: Record<string, unknown> = {}) {
  return {
    ao_id: 'objective_alpha',
    suggested_designation: 'gap_identified',
    rationale: 'The document does not state a review cadence.',
    suggestion: 'Add the cadence to section two.',
    ...overrides,
  }
}

function assessment(overrides: Record<string, unknown> = {}) {
  return {
    id: 'assessment_one',
    status: 'partial',
    ao_findings: [finding()],
    review_decision: null,
    reviewed_at: null,
    version_number: 1,
    ...overrides,
  } as never
}

function renderPanel(overrides: Record<string, unknown> = {}, onReviewed = vi.fn()) {
  render(
    <AssessmentReviewPanel
      orgId="org_one"
      evidenceId="evidence_one"
      fileId="file_one"
      assessment={assessment(overrides)}
      onReviewed={onReviewed}
    />,
  )
  return onReviewed
}

beforeEach(() => {
  vi.clearAllMocks()
  editor = true
  mockListVersions.mockResolvedValue([])
})
afterEach(() => cleanup())

describe('AssessmentReviewPanel gating', () => {
  it('offers the review actions to an editor', () => {
    renderPanel()
    expect(screen.getByText('Confirm AI assessment')).toBeTruthy()
    expect(screen.getByText('Correct designations')).toBeTruthy()
  })

  it('shows a viewer the suggestions but not the actions', () => {
    editor = false
    renderPanel()
    expect(screen.queryByText('Confirm AI assessment')).toBeNull()
    expect(screen.getByText(/Editor access is needed/)).toBeTruthy()
    // The findings themselves are still readable — read access is not the
    // thing being withheld.
    expect(screen.getByTestId('ao-finding-objective_alpha')).toBeTruthy()
  })

  it('draws nothing at all while the run has not produced a verdict', () => {
    const { container } = render(
      <AssessmentReviewPanel
        orgId="org_one"
        evidenceId="evidence_one"
        fileId="file_one"
        assessment={assessment({ status: 'processing' })}
        onReviewed={vi.fn()}
      />,
    )
    expect(container.firstChild).toBeNull()
  })
})

describe('AssessmentReviewPanel wording', () => {
  it('words an unconfirmed verdict as a suggestion', () => {
    renderPanel()
    expect(screen.getByTestId('assessment-review-verdict').textContent).toBe(
      'AI suggests: Partial',
    )
    expect(screen.getByText(/advisory until someone confirms or corrects them/)).toBeTruthy()
  })

  it('only uses confirmed language once a person has decided', () => {
    renderPanel({ review_decision: 'confirmed', status: 'sufficient' })
    expect(screen.getByTestId('assessment-review-verdict').textContent).toBe(
      'Confirmed: Sufficient',
    )
    expect(screen.getByTestId('assessment-review-decided').textContent).toContain('Confirmed')
    // A decided assessment offers no second decision.
    expect(screen.queryByText('Confirm AI assessment')).toBeNull()
  })

  it('says a corrected verdict was corrected, not confirmed', () => {
    renderPanel({ review_decision: 'overridden', status: 'insufficient' })
    expect(screen.getByTestId('assessment-review-verdict').textContent).toBe(
      'Corrected: Insufficient',
    )
    expect(screen.getByText(/A reviewer corrected this assessment/)).toBeTruthy()
  })
})

describe('AssessmentReviewPanel confirm', () => {
  it('records a confirmation and hands the updated record back', async () => {
    const updated = { id: 'assessment_one', review_decision: 'confirmed' }
    mockReview.mockResolvedValue(updated)
    const onReviewed = renderPanel()

    fireEvent.click(screen.getByText('Confirm AI assessment'))

    await waitFor(() => expect(mockReview).toHaveBeenCalled())
    expect(mockReview).toHaveBeenCalledWith('org_one', 'evidence_one', 'file_one', {
      decision: 'confirmed',
    })
    await waitFor(() => expect(onReviewed).toHaveBeenCalledWith(updated))
  })

  it('surfaces a refused confirmation instead of appearing to succeed', async () => {
    mockReview.mockRejectedValue(new Error('You cannot review evidence you uploaded alone.'))
    const onReviewed = renderPanel()

    fireEvent.click(screen.getByText('Confirm AI assessment'))

    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('you uploaded alone'),
    )
    expect(onReviewed).not.toHaveBeenCalled()
  })
})

describe('AssessmentReviewPanel correction', () => {
  it('will not file a correction without a changed designation and a reason', () => {
    renderPanel()
    fireEvent.click(screen.getByText('Correct designations'))

    const save = screen.getByText('Save correction') as HTMLButtonElement
    expect(save.disabled).toBe(true)
    expect(screen.getByText(/Change at least one objective above/)).toBeTruthy()

    // A reason on its own is not enough — there is nothing to apply it to.
    fireEvent.change(screen.getByLabelText(/Why are you changing this/), {
      target: { value: 'The cadence is in the appendix.' },
    })
    expect((screen.getByText('Save correction') as HTMLButtonElement).disabled).toBe(true)

    // Nor is a change on its own.
    fireEvent.change(screen.getByLabelText(/Why are you changing this/), {
      target: { value: '' },
    })
    fireEvent.click(screen.getByText('Appears satisfied'))
    expect((screen.getByText('Save correction') as HTMLButtonElement).disabled).toBe(true)
  })

  it('sends only the objectives the reviewer actually changed', async () => {
    mockReview.mockResolvedValue({ id: 'assessment_one', review_decision: 'overridden' })
    renderPanel()

    fireEvent.click(screen.getByText('Correct designations'))
    fireEvent.click(screen.getByText('Appears satisfied'))
    fireEvent.change(screen.getByLabelText(/Why are you changing this/), {
      target: { value: 'The cadence is stated in the appendix.' },
    })
    fireEvent.click(screen.getByText('Save correction'))

    await waitFor(() => expect(mockReview).toHaveBeenCalled())
    expect(mockReview).toHaveBeenCalledWith('org_one', 'evidence_one', 'file_one', {
      decision: 'overridden',
      reason: 'The cadence is stated in the appendix.',
      ao_overrides: [{ ao_id: 'objective_alpha', human_designation: 'appears_satisfied' }],
    })
  })

  it('does not record agreeing with the AI as a disagreement', () => {
    renderPanel()
    fireEvent.click(screen.getByText('Correct designations'))

    fireEvent.click(screen.getByText('Appears satisfied'))
    expect(screen.getByText(/1 objective changed/)).toBeTruthy()

    // Selecting the AI's own answer again is a no-op, not an override row.
    // Scoped to the picker: "Gap identified" also appears struck through as
    // the designation being corrected.
    const picker = screen.getByRole('group', { name: /Designation for objective_alpha/ })
    fireEvent.click(
      Array.from(picker.querySelectorAll('button')).find(
        (b) => b.textContent === 'Gap identified',
      ) as HTMLButtonElement,
    )
    expect(screen.getByText(/Change at least one objective above/)).toBeTruthy()
  })

  it('shows the AI designation struck through beside the reviewer’s', () => {
    renderPanel()
    fireEvent.click(screen.getByText('Correct designations'))
    fireEvent.click(screen.getByText('Appears satisfied'))

    const row = screen.getByTestId('ao-finding-objective_alpha')
    const superseded = row.querySelector('s')
    expect(superseded?.textContent).toBe('Gap identified')
    expect(row.textContent).toContain('reviewer')
  })

  it('renders a persisted disagreement without needing the picker open', () => {
    renderPanel({
      review_decision: 'overridden',
      ao_findings: [
        finding({
          suggested_designation: 'appears_satisfied',
          overridden_by_human: true,
          override_note: 'The appendix is out of date.',
        }),
      ],
    })
    const row = screen.getByTestId('ao-finding-objective_alpha')
    expect(row.querySelector('s')).toBeTruthy()
    expect(row.textContent).toContain('The appendix is out of date.')
  })
})

describe('AssessmentReviewPanel history', () => {
  it('shows earlier verdicts and what a reviewer changed', async () => {
    mockListVersions.mockResolvedValue([
      {
        id: 'version_one',
        version_number: 1,
        status: 'insufficient',
        review_decision: 'overridden',
        review_reason: 'The cadence is in the appendix.',
        model_id: 'a-model',
        prompt_version: '2.0.0',
        schema_version: 2,
        assessed_at: '2026-08-01T00:00:00Z',
        ao_overrides: [
          {
            ao_id: 'objective_alpha',
            ai_designation: 'gap_identified',
            human_designation: 'appears_satisfied',
          },
        ],
      },
    ])
    renderPanel()

    fireEvent.click(screen.getByText(/Show assessment history/))

    await waitFor(() => expect(screen.getByTestId('assessment-history')).toBeTruthy())
    const history = screen.getByTestId('assessment-history')
    expect(history.textContent).toContain('v1')
    expect(history.textContent).toContain('Corrected: Insufficient')
    expect(history.textContent).toContain('The cadence is in the appendix.')
    expect(history.querySelector('s')?.textContent).toBe('Gap identified')
  })

  it('does not draw an unreachable history as an empty one', async () => {
    mockListVersions.mockRejectedValue(new Error('history unavailable'))
    renderPanel()

    fireEvent.click(screen.getByText(/Show assessment history/))

    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('history unavailable'),
    )
    expect(screen.queryByText('No earlier verdicts recorded.')).toBeNull()
  })
})
