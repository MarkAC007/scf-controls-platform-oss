/**
 * WindowReviewPanel — component tests (M4 #574 PR3, ISC-39 acceptance).
 *
 * Covers:
 *   - loading skeleton renders
 *   - empty state renders when no EWA exists for the evidence_id
 *   - error state renders on fetch failure with retry button
 *   - happy path renders metadata + status badge + 3 action buttons
 *   - clicking Approve calls reviewWindowAssessment with correct args
 *   - submit error path renders error message
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { WindowReviewPanel } from '../WindowReviewPanel'
import type { EvidenceWindowAssessment } from '../../../types'

// Mock the apiClient module — we never want to hit real fetch under test.
vi.mock('../../../data/apiClient', () => ({
  listWindowAssessments: vi.fn(),
  reviewWindowAssessment: vi.fn(),
}))

import {
  listWindowAssessments,
  reviewWindowAssessment,
} from '../../../data/apiClient'

const sampleEwa: EvidenceWindowAssessment = {
  id: 'ewa-1',
  organization_id: 'org-1',
  evidence_id: 'E-BCM-11',
  window_start: '2026-04-01T00:00:00Z',
  window_end: '2026-05-01T00:00:00Z',
  assessment_status: 'sufficient',
  relevance_score: 87.4,
  review_status: null,
  reviewed_by_user_id: null,
  reviewed_at: null,
  review_notes: null,
}

beforeEach(() => {
  vi.clearAllMocks()
  cleanup()
})

describe('WindowReviewPanel', () => {
  it('renders the loading skeleton while fetching', () => {
    vi.mocked(listWindowAssessments).mockReturnValueOnce(
      new Promise(() => {
        /* never resolves — keeps panel in loading state */
      }),
    )
    render(<WindowReviewPanel orgId="org-1" evidenceId="E-BCM-11" />)
    expect(screen.getByTestId('window-review-panel-loading')).toBeInTheDocument()
  })

  it('renders the empty state when no EWA rows exist', async () => {
    vi.mocked(listWindowAssessments).mockResolvedValueOnce([])
    render(<WindowReviewPanel orgId="org-1" evidenceId="E-BCM-11" />)
    await waitFor(() =>
      expect(screen.getByTestId('window-review-panel-empty')).toBeInTheDocument(),
    )
    expect(screen.getByText(/no window assessment exists/i)).toBeInTheDocument()
  })

  it('renders the error state on fetch failure with a retry button', async () => {
    vi.mocked(listWindowAssessments).mockRejectedValueOnce(new Error('boom'))
    render(<WindowReviewPanel orgId="org-1" evidenceId="E-BCM-11" />)
    await waitFor(() =>
      expect(screen.getByTestId('window-review-panel-error')).toBeInTheDocument(),
    )
    expect(screen.getByText(/boom/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })

  it('renders the latest EWA with status badge + action buttons', async () => {
    vi.mocked(listWindowAssessments).mockResolvedValueOnce([sampleEwa])
    render(<WindowReviewPanel orgId="org-1" evidenceId="E-BCM-11" />)
    await waitFor(() =>
      expect(screen.getByTestId('window-review-panel')).toBeInTheDocument(),
    )
    expect(screen.getByTestId('window-review-status-badge')).toHaveTextContent(
      /not reviewed/i,
    )
    expect(screen.getByTestId('window-review-approve-btn')).toBeInTheDocument()
    expect(screen.getByTestId('window-review-reject-btn')).toBeInTheDocument()
    expect(screen.getByTestId('window-review-revision-btn')).toBeInTheDocument()
    // Reset button only renders when status !== not_reviewed
    expect(screen.queryByTestId('window-review-reset-btn')).not.toBeInTheDocument()
  })

  it('calls reviewWindowAssessment with the right args when Approve is clicked', async () => {
    vi.mocked(listWindowAssessments).mockResolvedValueOnce([sampleEwa])
    vi.mocked(reviewWindowAssessment).mockResolvedValueOnce({
      ...sampleEwa,
      review_status: 'approved',
      reviewed_at: '2026-05-09T20:00:00Z',
    })

    render(<WindowReviewPanel orgId="org-1" evidenceId="E-BCM-11" />)
    await waitFor(() =>
      expect(screen.getByTestId('window-review-approve-btn')).toBeInTheDocument(),
    )

    const textarea = screen.getByTestId('window-review-notes-textarea')
    fireEvent.change(textarea, { target: { value: 'Looks good' } })
    fireEvent.click(screen.getByTestId('window-review-approve-btn'))

    await waitFor(() => {
      expect(reviewWindowAssessment).toHaveBeenCalledWith('org-1', 'ewa-1', {
        review_status: 'approved',
        review_notes: 'Looks good',
      })
    })

    // After successful review, the badge updates and the Reset button appears.
    await waitFor(() =>
      expect(screen.getByTestId('window-review-status-badge')).toHaveTextContent(
        /approved/i,
      ),
    )
    expect(screen.getByTestId('window-review-reset-btn')).toBeInTheDocument()
  })

  it('renders the submit error when reviewWindowAssessment rejects', async () => {
    vi.mocked(listWindowAssessments).mockResolvedValueOnce([sampleEwa])
    vi.mocked(reviewWindowAssessment).mockRejectedValueOnce(
      new Error('400: invalid review_status'),
    )

    render(<WindowReviewPanel orgId="org-1" evidenceId="E-BCM-11" />)
    await waitFor(() =>
      expect(screen.getByTestId('window-review-approve-btn')).toBeInTheDocument(),
    )

    fireEvent.click(screen.getByTestId('window-review-approve-btn'))

    await waitFor(() =>
      expect(screen.getByTestId('window-review-submit-error')).toHaveTextContent(
        /invalid review_status/,
      ),
    )
  })
})
