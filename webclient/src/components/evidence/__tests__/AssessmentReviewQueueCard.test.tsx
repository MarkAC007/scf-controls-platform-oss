/**
 * AssessmentReviewQueueCard — making unconfirmed AI output findable (#881 WS3).
 *
 * Before this card, the only way to discover that the model had said something
 * nobody had checked was to open the file that happened to carry it. The queue
 * exists so the question "what has the AI claimed that no person has stood
 * behind?" has an answer on one screen.
 *
 * The two failures worth guarding: drawing an unreachable queue as an empty
 * one (which would report "all reviewed" when nothing was read at all), and
 * re-sorting the server's priority order on the client.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'

import { AssessmentReviewQueueCard } from '../AssessmentReviewQueueCard'

const mockGetQueue = vi.fn()
vi.mock('../../../data/apiClient', () => ({
  getAssessmentReviewQueue: (...args: unknown[]) => mockGetQueue(...args),
}))

let editor = true
vi.mock('../../../hooks/useHasOrgRole', () => ({
  useHasOrgRole: () => editor,
  useIsOrgEditor: () => editor,
}))

// The flag decides which layer the queue lists by default. Mocked as a
// mutable so one file can exercise both settings of a compile-time constant.
let perWindow = false
vi.mock('../../../data/featureFlags', () => ({
  get PER_WINDOW_REVIEW_ENABLED() {
    return perWindow
  },
}))

function item(overrides: Record<string, unknown> = {}) {
  return {
    file_id: 'file_one',
    evidence_id: 'evidence_one',
    filename: 'access-review.pdf',
    status: 'partial',
    review_decision: null,
    gap_count: 2,
    cannot_assess_count: 1,
    relevance_score: 55,
    assessed_at: '2026-08-30T00:00:00Z',
    ...overrides,
  }
}

function windowItem(overrides: Record<string, unknown> = {}) {
  return {
    kind: 'window',
    file_id: null,
    filename: null,
    window_assessment_id: 'ewa_one',
    evidence_id: 'E-BCD-01',
    window_start: '2026-08-01T00:00:00Z',
    window_end: '2026-09-05T00:00:00Z',
    frequency_used: 'monthly',
    file_count: 3,
    status: 'partial',
    review_decision: null,
    gap_count: 2,
    cannot_assess_count: 0,
    relevance_score: 61,
    assessed_at: '2026-09-05T00:00:00Z',
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  editor = true
  perWindow = false
})
afterEach(() => cleanup())

describe('AssessmentReviewQueueCard', () => {
  it('lists what is awaiting confirmation, worded as suggestions', async () => {
    mockGetQueue.mockResolvedValue({ items: [item()], total: 1 })
    render(<AssessmentReviewQueueCard orgId="org_one" />)

    await waitFor(() => expect(screen.getByText('access-review.pdf')).toBeTruthy())
    expect(screen.getByText('AI suggests: Partial')).toBeTruthy()
    expect(screen.getByText('2 gaps')).toBeTruthy()
    expect(screen.getByText('1 unreadable')).toBeTruthy()
    expect(mockGetQueue).toHaveBeenCalledWith('org_one', { status: 'awaiting', tier: 'file', limit: 10 })
  })

  it('keeps the server’s priority order rather than sorting again', async () => {
    // Worst-first is the server's decision. A client that quietly disagreed
    // with it about priority would be worse than no queue.
    mockGetQueue.mockResolvedValue({
      items: [
        item({ file_id: 'f_worst', filename: 'worst.pdf', gap_count: 5 }),
        item({ file_id: 'f_middle', filename: 'middle.pdf', gap_count: 3 }),
        item({ file_id: 'f_least', filename: 'least.pdf', gap_count: 0, cannot_assess_count: 0 }),
      ],
      total: 3,
    })
    render(<AssessmentReviewQueueCard orgId="org_one" />)

    await waitFor(() => expect(screen.getByText('worst.pdf')).toBeTruthy())
    const rendered = Array.from(
      document.querySelectorAll('.assessment-queue-filename'),
    ).map((el) => el.textContent)
    expect(rendered).toEqual(['worst.pdf', 'middle.pdf', 'least.pdf'])
  })

  it('says the queue is empty only when the server said so', async () => {
    mockGetQueue.mockResolvedValue({ items: [], total: 0 })
    render(<AssessmentReviewQueueCard orgId="org_one" />)

    await waitFor(() =>
      expect(screen.getByText('Every AI verdict has been reviewed.')).toBeTruthy(),
    )
  })

  it('does not draw an unreachable queue as an empty one', async () => {
    mockGetQueue.mockRejectedValue(new Error('queue unavailable'))
    render(<AssessmentReviewQueueCard orgId="org_one" />)

    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('queue unavailable'),
    )
    expect(screen.queryByText('Every AI verdict has been reviewed.')).toBeNull()
  })

  it('discloses how many are waiting when the page does not show them all', async () => {
    mockGetQueue.mockResolvedValue({ items: [item()], total: 42 })
    render(<AssessmentReviewQueueCard orgId="org_one" />)

    await waitFor(() => expect(screen.getByText(/42 awaiting/)).toBeTruthy())
    expect(screen.getByText(/showing 1/)).toBeTruthy()
  })

  it('tells a viewer why they cannot act on the list they can see', async () => {
    editor = false
    mockGetQueue.mockResolvedValue({ items: [item()], total: 1 })
    render(<AssessmentReviewQueueCard orgId="org_one" />)

    await waitFor(() => expect(screen.getByText('access-review.pdf')).toBeTruthy())
    expect(screen.getByText(/Editor access is needed/)).toBeTruthy()
  })

  it('opens the evidence a row points at when the parent can navigate', async () => {
    const onOpen = vi.fn()
    mockGetQueue.mockResolvedValue({ items: [item()], total: 1 })
    render(<AssessmentReviewQueueCard orgId="org_one" onOpenEvidence={onOpen} />)

    await waitFor(() => expect(screen.getByText('access-review.pdf')).toBeTruthy())
    fireEvent.click(screen.getByText('access-review.pdf'))
    expect(onOpen).toHaveBeenCalledWith('evidence_one', 'file_one')
  })
})

describe('AssessmentReviewQueueCard window tier (window parity)', () => {
  it('lists window verdicts when the per-window review flag is on', async () => {
    perWindow = true
    mockGetQueue.mockResolvedValue({ items: [windowItem()], total: 1 })
    render(<AssessmentReviewQueueCard orgId="org_one" />)

    await waitFor(() => expect(screen.getByTestId('assessment-queue-window')).toBeTruthy())
    expect(mockGetQueue).toHaveBeenCalledWith('org_one', { status: 'awaiting', tier: 'window', limit: 10 })
    expect(screen.getByText('AI suggests: Partial')).toBeTruthy()
    expect(screen.getByText('2 gaps')).toBeTruthy()
    expect(screen.getByTestId('assessment-queue-window').textContent).toMatch(/3 files/)
    expect(screen.getByText(/E-BCD-01/)).toBeTruthy()
  })

  it('keeps listing per-file verdicts while the flag is off', async () => {
    mockGetQueue.mockResolvedValue({ items: [item()], total: 1 })
    render(<AssessmentReviewQueueCard orgId="org_one" />)
    await waitFor(() => expect(screen.getByText('access-review.pdf')).toBeTruthy())
    expect(mockGetQueue.mock.calls[0][1].tier).toBe('file')
  })

  it('lets a caller pick the tier explicitly', async () => {
    mockGetQueue.mockResolvedValue({ items: [windowItem()], total: 1 })
    render(<AssessmentReviewQueueCard orgId="org_one" tier="window" />)
    await waitFor(() => expect(screen.getByTestId('assessment-queue-window')).toBeTruthy())
    expect(mockGetQueue.mock.calls[0][1].tier).toBe('window')
  })

  it('opens the evidence with no file for a window entry — the period is the subject', async () => {
    const onOpen = vi.fn()
    mockGetQueue.mockResolvedValue({ items: [windowItem()], total: 1 })
    render(<AssessmentReviewQueueCard orgId="org_one" tier="window" onOpenEvidence={onOpen} />)

    await waitFor(() => expect(screen.getByTestId('assessment-queue-window')).toBeTruthy())
    fireEvent.click(screen.getByRole('button'))
    expect(onOpen).toHaveBeenCalledWith('E-BCD-01', null)
  })

  it('words an insufficient sample as a suggestion too', async () => {
    mockGetQueue.mockResolvedValue({ items: [windowItem({ status: 'insufficient_sample', gap_count: 0 })], total: 1 })
    render(<AssessmentReviewQueueCard orgId="org_one" tier="window" />)
    await waitFor(() => expect(screen.getByText('AI suggests: Insufficient sample')).toBeTruthy())
  })
})
