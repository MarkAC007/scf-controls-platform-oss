/**
 * EngagementDashboard: every number comes from the review-dashboard response.
 *
 * The fixture uses unusual counts (7 of 23, 13 open queries, …) so a value
 * hard-coded in the component, or one recomputed in the browser, would fail.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import EngagementDashboard from '../EngagementDashboard'
import type { ReviewFocus, ReviewTotals } from '../EngagementReviewWorkspace'
import { getReviewDashboard, type ReviewDashboard } from '../../../data/engagementReviewApi'

vi.mock('../../../data/engagementReviewApi', async () => {
  const actual = await vi.importActual<typeof import('../../../data/engagementReviewApi')>(
    '../../../data/engagementReviewApi',
  )
  return { ...actual, getReviewDashboard: vi.fn() }
})

const mockedGet = vi.mocked(getReviewDashboard)

const DASHBOARD: ReviewDashboard = {
  engagement: {
    id: 'eng-1', name: 'CE 2026', status: 'active',
    start_date: '2026-03-04', end_date: null, frameworks: ['ce_3_3'], created_at: '2026-01-01T00:00:00Z',
  },
  framework: 'ce_3_3',
  evidence: {
    total: 23,
    by_status: { open: 5, in_progress: 4, action_required: 3, submitted: 4, accepted: 7 },
  },
  controls: {
    total: 29,
    by_status: { open: 6, in_progress: 8, action_required: 2, submitted: 2, accepted: 11 },
  },
  clauses: {
    total: 17,
    by_status: { open: 1, in_progress: 2, action_required: 4, submitted: 4, accepted: 6 },
  },
  clause_rollup: [
    { clause: '1.0', group: '1', status: 'accepted', control_count: 2, evidence_count: 4 },
    { clause: '2-BP1', group: '2', status: 'action_required', control_count: 3, evidence_count: 6 },
    { clause: '2-BP2', group: '2', status: 'submitted', control_count: 1, evidence_count: 1 },
  ],
  groups: [
    { key: '1', label: '1', clauses_total: 1, clauses_accepted: 1 },
    { key: '2', label: '2', clauses_total: 2, clauses_accepted: 0 },
  ],
  action_required: [
    {
      evidence_id: 'E-IAC-07', title: 'Account Management Compliance Reviews',
      held_controls: ['IAC-08'], held_clauses: ['2-BP1', '4-BP1'], open_query_count: 2,
    },
  ],
  no_evidence_controls: [
    { scf_id: 'IAC-08.1', title: 'Automated System Account Management', clauses: ['2-BP6'], open_query_count: 1 },
  ],
  open_queries: { total: 13, evidence: 9, control: 4 },
  timeline: [
    { key: 'window_start', label: 'Audit window opens', date: '2026-03-04', done: true },
    { key: 'testing_started', label: 'Testing started', date: '2026-03-09T10:00:00Z', done: true },
    { key: 'all_evidence_tested', label: 'All evidence tested', date: null, done: false },
    { key: 'all_clauses_accepted', label: 'All clauses accepted', date: null, done: false },
    { key: 'window_end', label: 'Audit window closes', date: null, done: false },
  ],
}

const ZERO: ReviewDashboard = {
  ...DASHBOARD,
  evidence: { total: 0, by_status: { open: 0, in_progress: 0, action_required: 0, submitted: 0, accepted: 0 } },
  controls: { total: 0, by_status: { open: 0, in_progress: 0, action_required: 0, submitted: 0, accepted: 0 } },
  clauses: { total: 0, by_status: { open: 0, in_progress: 0, action_required: 0, submitted: 0, accepted: 0 } },
  clause_rollup: [],
  groups: [],
  action_required: [],
  no_evidence_controls: [],
  open_queries: { total: 0, evidence: 0, control: 0 },
}

function renderDashboard(overrides: { onOpenOnBoard?: (focus: ReviewFocus) => void; onTotals?: (totals: ReviewTotals) => void } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onOpenOnBoard = overrides.onOpenOnBoard ?? vi.fn<(focus: ReviewFocus) => void>()
  const onTotals = overrides.onTotals ?? vi.fn<(totals: ReviewTotals) => void>()
  const utils = render(
    <QueryClientProvider client={client}>
      <EngagementDashboard
        organizationId="org-1" engagementId="eng-1" framework="ce_3_3" frameworkLabel="CE 3.3"
        onOpenOnBoard={onOpenOnBoard} onTotals={onTotals}
      />
    </QueryClientProvider>,
  )
  return { ...utils, onOpenOnBoard, onTotals }
}

function tile(label: string) {
  const el = screen.getByText(label, { selector: '.erd-tile-label' }).closest('.erd-tile')
  if (!el) throw new Error(`tile ${label} not found`)
  return el as HTMLElement
}

describe('EngagementDashboard', () => {
  beforeEach(() => {
    mockedGet.mockReset()
  })

  it('loads the dashboard for the tab framework and reports the header totals', async () => {
    mockedGet.mockResolvedValue(DASHBOARD)
    const { onTotals } = renderDashboard()
    expect(screen.getByRole('status')).toHaveTextContent(/loading/i)
    await screen.findByText('Testing progress')
    expect(mockedGet).toHaveBeenCalledWith('org-1', 'eng-1', 'ce_3_3')
    expect(onTotals).toHaveBeenCalledWith({ evidence: 23, controls: 29 })
  })

  it('shows each tile with the number from the response', async () => {
    mockedGet.mockResolvedValue(DASHBOARD)
    renderDashboard()
    await screen.findByText('Testing progress')

    expect(within(tile('Evidence accepted')).getByText('7 / 23')).toBeInTheDocument()
    // 6 of 17 clauses = 35%
    expect(within(tile('Clauses accepted')).getByText('35%')).toBeInTheDocument()
    expect(within(tile('Clauses accepted')).getByText('6 of 17 CE 3.3 clauses')).toBeInTheDocument()
    expect(within(tile('Controls accepted')).getByText('11 / 29')).toBeInTheDocument()
    expect(within(tile('Action required')).getByText('3')).toBeInTheDocument()
    expect(within(tile('Open queries')).getByText('13')).toBeInTheDocument()
    expect(within(tile('Open queries')).getByText('9 on evidence · 4 on controls')).toBeInTheDocument()
  })

  it('splits each bar into the five stage counts', async () => {
    mockedGet.mockResolvedValue(DASHBOARD)
    renderDashboard()
    await screen.findByText('Testing progress')

    const expected = {
      evidence: DASHBOARD.evidence,
      controls: DASHBOARD.controls,
      clauses: DASHBOARD.clauses,
    }
    for (const [id, block] of Object.entries(expected)) {
      const row = screen.getByTestId(`erd-bar-${id}`)
      const bar = within(row).getByRole('img')
      expect(bar.getAttribute('aria-label')).toContain(
        `Open ${block.by_status.open}, In progress ${block.by_status.in_progress}, `
        + `Action required ${block.by_status.action_required}, Submitted ${block.by_status.submitted}, `
        + `Accepted ${block.by_status.accepted}`,
      )
      for (const stage of ['open', 'in_progress', 'action_required', 'submitted', 'accepted'] as const) {
        const item = row.querySelector(`.erd-legend-item[data-stage="${stage}"]`)
        expect(item).toHaveTextContent(String(block.by_status[stage]))
      }
      // Segments carry the same counts, in board order.
      const segs = Array.from(bar.querySelectorAll('.erd-bar-seg')).map(s => s.textContent)
      expect(segs).toEqual([
        block.by_status.open, block.by_status.in_progress, block.by_status.action_required,
        block.by_status.submitted, block.by_status.accepted,
      ].map(String))
    }
    expect(within(screen.getByTestId('erd-bar-evidence')).getByText('23 objects')).toBeInTheDocument()
  })

  it('groups clause tiles under their section and shows each tile stage', async () => {
    mockedGet.mockResolvedValue(DASHBOARD)
    renderDashboard()
    await screen.findByText('Testing progress')

    const g1 = screen.getByTestId('erd-group-1')
    expect(within(g1).getByText('Section 1')).toBeInTheDocument()
    expect(within(g1).getByText('1 of 1 clauses accepted')).toBeInTheDocument()
    expect(within(g1).getAllByRole('listitem')).toHaveLength(1)

    const g2 = screen.getByTestId('erd-group-2')
    expect(within(g2).getByText('0 of 2 clauses accepted')).toBeInTheDocument()
    const tiles = within(g2).getAllByRole('listitem')
    expect(tiles.map(t => t.getAttribute('data-stage'))).toEqual(['action_required', 'submitted'])
    expect(tiles[0]).toHaveTextContent('2-BP1')
    expect(tiles[0]).toHaveTextContent('Action required')
    expect(tiles[0]).toHaveTextContent('3 ctrl · 6 ev')
    expect(tiles[0]).toHaveAttribute('title', '2-BP1: Action required, 3 controls, 6 evidence objects')
    expect(within(g1).queryByText('2-BP1')).not.toBeInTheDocument()
  })

  it('opens an action-required evidence object on the board', async () => {
    mockedGet.mockResolvedValue(DASHBOARD)
    const { onOpenOnBoard } = renderDashboard()
    await screen.findByText('Testing progress')

    const row = screen.getByRole('button', { name: /E-IAC-07/ })
    expect(row).toHaveTextContent('Holds SCF IAC-08 · CE 3.3 2-BP1, 4-BP1 · 2 open queries')
    await userEvent.click(row)
    expect(onOpenOnBoard).toHaveBeenCalledWith({ evidenceId: 'E-IAC-07' })
  })

  it('opens a control with no evidence object on the board', async () => {
    mockedGet.mockResolvedValue(DASHBOARD)
    const { onOpenOnBoard } = renderDashboard()
    await screen.findByText('Testing progress')

    const row = screen.getByRole('button', { name: /IAC-08\.1/ })
    expect(row).toHaveTextContent('CE 3.3 2-BP6 · 1 open query')
    expect(screen.getByText(/stays Open until the auditor resolves it with a control-level query/)).toBeInTheDocument()
    await userEvent.click(row)
    expect(onOpenOnBoard).toHaveBeenCalledWith({ controlScfId: 'IAC-08.1' })
  })

  it('shows the timeline dates, "not yet" and "not set"', async () => {
    mockedGet.mockResolvedValue(DASHBOARD)
    renderDashboard()
    await screen.findByText('Testing progress')

    const start = screen.getByTestId('erd-milestone-window_start')
    expect(start).toHaveClass('is-done')
    expect(start).toHaveTextContent(new Date(2026, 2, 4).toLocaleDateString(undefined, {
      day: 'numeric', month: 'short', year: 'numeric',
    }))
    const evidence = screen.getByTestId('erd-milestone-all_evidence_tested')
    expect(evidence).not.toHaveClass('is-done')
    expect(evidence).toHaveTextContent('not yet · 7 of 23 accepted so far')
    expect(screen.getByTestId('erd-milestone-all_clauses_accepted')).toHaveTextContent('not yet · 6 of 17 so far')
    expect(screen.getByTestId('erd-milestone-window_end')).toHaveTextContent('not set')
  })

  it('renders a zero-total response without NaN', async () => {
    mockedGet.mockResolvedValue(ZERO)
    const { container, onTotals } = renderDashboard()
    await screen.findByText('Testing progress')

    expect(container.textContent).not.toMatch(/NaN|Infinity/)
    expect(screen.getByText(/Nothing is in scope for CE 3.3/)).toBeInTheDocument()
    expect(screen.getByText('Nothing to show: no objects for this framework.')).toBeInTheDocument()
    expect(screen.queryAllByRole('img')).toHaveLength(0)
    expect(screen.getByText('Nothing is waiting on the organisation.')).toBeInTheDocument()
    expect(screen.getByText('Every in-scope control has an evidence object.')).toBeInTheDocument()
    expect(onTotals).toHaveBeenCalledWith({ evidence: 0, controls: 0 })
  })

  it('shows the server error message and retries', async () => {
    mockedGet.mockRejectedValueOnce(Object.assign(new Error('Engagement not found'), { status: 404 }))
    mockedGet.mockResolvedValueOnce(DASHBOARD)
    renderDashboard()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Engagement not found')
    await userEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.getByText('Testing progress')).toBeInTheDocument())
    expect(mockedGet).toHaveBeenCalledTimes(2)
  })
})
