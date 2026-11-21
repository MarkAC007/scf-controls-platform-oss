// SPDX-License-Identifier: AGPL-3.0-only
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
  // Used by the nested WindowVerdictReviewPanel (window parity).
  reviewWindowVerdict: vi.fn(),
  listWindowAssessmentVersions: vi.fn(),
}))

// The nested verdict panel reads the org role; without this the render dies
// in AuthContext rather than testing anything.
// The viewer's role in the organisation, switchable per test. The suites above
// the role-gating block run as 'admin'; that block (#1273) sets its own.
const role = vi.hoisted(() => ({ rank: 'admin' as 'viewer' | 'editor' | 'admin' | 'unknown' }))
vi.mock('../../../hooks/useHasOrgRole', () => {
  const order: Record<string, number> = { viewer: 0, editor: 1, admin: 2 }
  // 'unknown' is the window before the membership lookup returns: nothing is allowed yet.
  const has = (_org: unknown, min: 'viewer' | 'editor' | 'admin') => order[role.rank] >= order[min]
  return {
    useHasOrgRole: has,
    useIsOrgEditor: (org: unknown) => has(org, 'editor'),
    useOrgRoleState: (org: unknown, min: 'viewer' | 'editor' | 'admin') => ({
      allowed: has(org, min),
      resolved: role.rank !== 'unknown',
    }),
  }
})

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

  it('mounts the verdict confirmation beneath the AI context for a terminal window (window parity)', async () => {
    vi.mocked(listWindowAssessments).mockResolvedValueOnce([
      {
        ...sampleEwa,
        status: 'partial',
        summary: 'Two of three months are evidenced.',
        schema_version: 2,
        ao_findings: [
          { ao_id: 'objective_alpha', suggested_designation: 'gap_identified', rationale: 'r', suggestion: 's', evidence_file_ids: [] },
        ],
        file_ids: ['f1', 'f2'],
        current_version_id: 'ver_one',
        version_number: 1,
        review_decision: null,
      },
    ])
    render(<WindowReviewPanel orgId="org-1" evidenceId="E-BCM-11" />)
    await waitFor(() => expect(screen.getByTestId('window-verdict-review')).toBeInTheDocument())
    // The status badge says who stands behind the verdict.
    expect(screen.getByTestId('window-review-status-assessment-badge')).toHaveTextContent('AI suggests: Partial')
    expect(screen.getByTestId('window-verdict-confirm-btn')).toBeInTheDocument()
    // The acceptance buttons are still there: the two verbs coexist.
    expect(screen.getByTestId('window-review-approve-btn')).toBeInTheDocument()
  })

  it('shows the unassessable reason and labels the status', async () => {
    vi.mocked(listWindowAssessments).mockResolvedValueOnce([
      {
        ...sampleEwa,
        status: 'unassessable',
        unassessable_reason: 'No assessment objective could be evaluated from the files in this window.',
        schema_version: 2,
        ao_findings: [],
        current_version_id: 'ver_one',
        version_number: 1,
        review_decision: null,
      },
    ])
    render(<WindowReviewPanel orgId="org-1" evidenceId="E-BCM-11" />)
    await waitFor(() => expect(screen.getByTestId('window-review-unassessable-reason')).toBeInTheDocument())
    expect(screen.getByTestId('window-review-status-assessment-badge')).toHaveTextContent('AI suggests: Unassessable')
  })
})

// ── Role gating (#1273) ─────────────────────────────────────────────────────
import { afterEach as afterEachRoleTest } from 'vitest'

describe('WindowReviewPanel — who may review a window', () => {
  afterEachRoleTest(() => { role.rank = 'admin' })

  it('shows a viewer the assessment without Approve, Reject, Request revision or an editable note', async () => {
    role.rank = 'viewer'
    vi.mocked(listWindowAssessments).mockResolvedValueOnce([sampleEwa])
    render(<WindowReviewPanel orgId="org-1" evidenceId="E-BCM-11" />)
    await waitFor(() => expect(screen.getByTestId('window-review-panel')).toBeInTheDocument())

    expect(screen.getByTestId('window-review-status-badge')).toHaveTextContent(/not reviewed/i)
    expect(screen.queryByTestId('window-review-approve-btn')).not.toBeInTheDocument()
    expect(screen.queryByTestId('window-review-reject-btn')).not.toBeInTheDocument()
    expect(screen.queryByTestId('window-review-revision-btn')).not.toBeInTheDocument()
    expect(screen.getByTestId('window-review-notes-textarea')).toBeDisabled()
    expect(screen.getByTestId('window-review-readonly')).toHaveTextContent('Editor access is needed')
  })

  it('shows neither the review actions nor the editor-access line while the role is not yet known', async () => {
    role.rank = 'unknown'
    vi.mocked(listWindowAssessments).mockResolvedValueOnce([sampleEwa])
    render(<WindowReviewPanel orgId="org-1" evidenceId="E-BCM-11" />)
    await waitFor(() => expect(screen.getByTestId('window-review-panel')).toBeInTheDocument())

    expect(screen.queryByTestId('window-review-approve-btn')).not.toBeInTheDocument()
    expect(screen.queryByTestId('window-review-reject-btn')).not.toBeInTheDocument()
    expect(screen.getByTestId('window-review-notes-textarea')).toBeDisabled()
    expect(screen.queryByTestId('window-review-readonly')).not.toBeInTheDocument()
  })

  it.each(['editor', 'admin'] as const)('offers an %s the review actions', async (rank) => {
    role.rank = rank
    vi.mocked(listWindowAssessments).mockResolvedValueOnce([sampleEwa])
    render(<WindowReviewPanel orgId="org-1" evidenceId="E-BCM-11" />)
    await waitFor(() => expect(screen.getByTestId('window-review-panel')).toBeInTheDocument())

    expect(screen.getByTestId('window-review-approve-btn')).toBeEnabled()
    expect(screen.getByTestId('window-review-reject-btn')).toBeEnabled()
    expect(screen.getByTestId('window-review-notes-textarea')).toBeEnabled()
    expect(screen.queryByTestId('window-review-readonly')).not.toBeInTheDocument()
  })
})
