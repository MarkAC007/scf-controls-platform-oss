/**
 * ReviewBoard: five review-stage columns of evidence-object cards, server-side
 * filters, the no-evidence strip, and moving a card (menu or drag) under the
 * card's own allowed_transitions, with an optimistic update and rollback.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ReviewBoard, { type ReviewBoardProps } from '../ReviewBoard'
import {
  changeReviewStatus,
  getReviewBoard,
  type EvidenceCard,
  type EvidenceReviewBoard,
} from '../../../data/engagementReviewApi'

vi.mock('../../../data/engagementReviewApi', async () => {
  const actual = await vi.importActual<typeof import('../../../data/engagementReviewApi')>(
    '../../../data/engagementReviewApi')
  return {
    ...actual,
    getReviewBoard: vi.fn(),
    changeReviewStatus: vi.fn(),
  }
})

vi.mock('../ReviewCardPanel', () => ({
  default: (p: { evidenceId: string; pendingMove?: string | null; onPendingMoveConsumed?: () => void }) => (
    <div data-testid="card-panel">
      Panel for {p.evidenceId}
      <span data-testid="pending-move">{p.pendingMove ?? 'none'}</span>
      <button type="button" onClick={p.onPendingMoveConsumed}>consume pending move</button>
    </div>
  ),
}))

vi.mock('../ReviewQueries', () => ({
  default: (p: { target: { scfId?: string; evidenceId?: string } }) => (
    <div data-testid="queries">Queries for {p.target.scfId ?? p.target.evidenceId}</div>
  ),
}))

const getBoard = vi.mocked(getReviewBoard)
const change = vi.mocked(changeReviewStatus)

function makeCard(over: Partial<EvidenceCard>): EvidenceCard {
  return {
    review_id: 'rev-x',
    evidence_id: 'E-X-01',
    title: 'X',
    status: 'open',
    controls: [],
    clauses: [],
    file_count: 1,
    in_window_count: 1,
    ai_verdict: 'not_assessed',
    open_query_count: 0,
    reviewer: null,
    owner: {
      tier: 'explicit', label: 'Someone', team_id: null, team_name: null, is_tracked: true,
      tracking_id: null, assigned_user_id: null, user_ids: [],
    },
    allowed_transitions: [],
    note_required: {},
    updated_at: null,
    ...over,
  }
}

const IAC07 = makeCard({
  review_id: 'rev-iac07',
  evidence_id: 'E-IAC-07',
  title: 'Account Management Compliance Reviews',
  status: 'open',
  controls: [
    { scf_id: 'IAC-08', title: 'Account Management', clauses: ['2-BP1', '4-BP1'] },
    { scf_id: 'IAC-12', title: 'Periodic Review', clauses: ['2-BP1'] },
  ],
  clauses: ['2-BP1', '4-BP1', '4-BP3', '5.0', '5-BP1', '5-BP2'],
  file_count: 2,
  in_window_count: 0,
  ai_verdict: 'partial',
  open_query_count: 2,
  reviewer: { user_id: 'u-jr', name: 'Jane Rogers' },
  owner: {
    tier: 'accountable_team', label: 'IT Operations', team_id: 'team-it', team_name: 'IT Operations', is_tracked: true,
    tracking_id: 't-1', assigned_user_id: null, user_ids: [],
  },
  allowed_transitions: ['in_progress', 'action_required'],
  note_required: { action_required: true },
})

const MNT03 = makeCard({
  review_id: 'rev-mnt03',
  evidence_id: 'E-MNT-03',
  title: 'Patch Management',
  status: 'submitted',
  file_count: 0,
  in_window_count: 0,
  ai_verdict: 'sufficient',
  allowed_transitions: [],
})

const AST01 = makeCard({
  review_id: 'rev-ast01',
  evidence_id: 'E-AST-01',
  title: 'IT Asset Management',
  status: 'accepted',
  ai_verdict: 'sufficient',
})

function makeBoard(cards: EvidenceCard[] = [IAC07, MNT03, AST01]): EvidenceReviewBoard {
  const counts = { open: 0, in_progress: 0, action_required: 0, submitted: 0, accepted: 0 }
  for (const c of cards) counts[c.status] += 1
  return {
    engagement: {
      id: 'eng-1', name: 'CE 2026', status: 'active', start_date: null, end_date: null, frameworks: ['ce'],
    },
    framework: 'ce',
    totals: { evidence: 3, controls: 9 },
    counts,
    cards,
    no_evidence_controls: [
      { scf_id: 'IAC-08.1', title: 'Automated System Account Management', clauses: ['2-BP6'], open_query_count: 1 },
    ],
    filter_options: {
      clauses: ['2-BP1', '4-BP1'],
      scf_ids: ['IAC-08', 'IAC-12'],
      reviewers: [{ user_id: 'u-jr', name: 'Jane Rogers' }],
    },
    viewer: {
      user_id: 'u-jr', is_auditor: true, auditor_kind: 'external', org_role: null,
      can_assign_reviewer: true, can_reassign_owner: false,
    },
  }
}

function renderBoard(over: Partial<ReviewBoardProps> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const props: ReviewBoardProps = {
    organizationId: 'org-1',
    engagementId: 'eng-1',
    framework: 'ce',
    frameworkLabel: 'CE 3.3',
    focus: null,
    onFocusConsumed: vi.fn(),
    onTotals: vi.fn(),
    ...over,
  }
  render(
    <QueryClientProvider client={client}>
      <ReviewBoard {...props} />
    </QueryClientProvider>,
  )
  return props
}

const column = (name: string) => screen.getByRole('region', { name })
const cardIn = (col: string, id: string) =>
  within(column(col)).queryByRole('article', { name: (accessibleName) => accessibleName.startsWith(id) })

function apiError(status: number, message: string) {
  return Object.assign(new Error(message), { status })
}

beforeEach(() => {
  getBoard.mockResolvedValue(makeBoard())
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('ReviewBoard', () => {
  it('renders five columns in order with counts and hints, and reports totals', async () => {
    const props = renderBoard()
    await screen.findByRole('region', { name: 'Open' })
    const names = ['Open', 'In progress', 'Action required', 'Submitted', 'Accepted']
    for (const n of names) expect(column(n)).toBeInTheDocument()
    const cols = screen.getAllByRole('region').filter(r => names.includes(r.getAttribute('aria-label') ?? ''))
    expect(cols.map(c => c.getAttribute('aria-label'))).toEqual(names)
    expect(within(column('Open')).getByLabelText('1 cards')).toHaveTextContent('1')
    expect(within(column('In progress')).getByLabelText('0 cards')).toHaveTextContent('0')
    expect(within(column('In progress')).getByText('No evidence objects in this stage.')).toBeInTheDocument()
    expect(within(column('Open')).getByText('Not yet tested by the auditor')).toBeInTheDocument()
    expect(props.onTotals).toHaveBeenCalledWith({ evidence: 3, controls: 9 })
  })

  it('shows the evidence object on the card: id, title, chips, counts, verdict, reviewer, owner', async () => {
    renderBoard()
    await screen.findByRole('region', { name: 'Open' })
    const card = cardIn('Open', 'E-IAC-07')!
    const c = within(card)
    expect(c.getByText('E-IAC-07')).toBeInTheDocument()
    expect(c.getByText('Account Management Compliance Reviews')).toBeInTheDocument()
    expect(c.getByText('IAC-08')).toBeInTheDocument()
    expect(c.getByText('IAC-12')).toBeInTheDocument()
    expect(c.getByText('2-BP1')).toBeInTheDocument()
    expect(c.getByText('+2')).toBeInTheDocument() // six clauses collapse to four + 2
    expect(c.getByText(/2 files · 0 in window/)).toHaveClass('is-warn')
    expect(c.getByText('2 open queries')).toBeInTheDocument()
    expect(c.getByText('AI: partial')).toBeInTheDocument()
    expect(c.getByText('JR')).toBeInTheDocument()
    expect(c.getByText('Reviewer: Jane Rogers')).toBeInTheDocument()
    expect(c.getByText('Owner: IT Operations')).toBeInTheDocument()

    const mnt = within(cardIn('Submitted', 'E-MNT-03')!)
    expect(mnt.getByText('No files')).toHaveClass('is-warn')
    expect(mnt.getByText('Unassigned')).toBeInTheDocument()
  })

  it('sends filters to the API, debounces search, and offers Clear filters', async () => {
    const user = userEvent.setup()
    renderBoard()
    await screen.findByRole('region', { name: 'Open' })
    expect(getBoard).toHaveBeenLastCalledWith('org-1', 'eng-1', 'ce', {})
    expect(screen.queryByRole('button', { name: 'Clear filters' })).not.toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Clause'), '2-BP1')
    await waitFor(() => expect(getBoard).toHaveBeenLastCalledWith('org-1', 'eng-1', 'ce', { clause: '2-BP1' }))

    await user.selectOptions(screen.getByLabelText('SCF control'), 'IAC-12')
    await user.selectOptions(screen.getByLabelText('AI verdict'), 'partial')
    await user.selectOptions(screen.getByLabelText('Reviewer'), 'none')
    await user.click(screen.getByLabelText('Has open queries'))
    await user.type(screen.getByLabelText('Search evidence, control or clause'), 'account')
    await waitFor(() => expect(getBoard).toHaveBeenLastCalledWith('org-1', 'eng-1', 'ce', {
      clause: '2-BP1', scfId: 'IAC-12', aiVerdict: 'partial', reviewer: 'none', hasOpenQueries: true, search: 'account',
    }))
    // Debounced: no request per keystroke.
    expect(getBoard.mock.calls.some(c => (c[3] as { search?: string }).search === 'acc')).toBe(false)
    expect(screen.getByText(/3 of 3 evidence objects match/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Clear filters' }))
    await waitFor(() => expect(getBoard).toHaveBeenLastCalledWith('org-1', 'eng-1', 'ce', {}))
  })

  it('"My cards" sends mine', async () => {
    const user = userEvent.setup()
    renderBoard()
    await screen.findByRole('region', { name: 'Open' })
    const toggle = screen.getByRole('button', { name: 'My cards' })
    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
    await waitFor(() => expect(getBoard).toHaveBeenLastCalledWith('org-1', 'eng-1', 'ce', { mine: true }))
  })

  it('the Move menu lists only allowed_transitions and is keyboard driven', async () => {
    const user = userEvent.setup()
    renderBoard()
    await screen.findByRole('region', { name: 'Open' })
    const btn = screen.getByRole('button', { name: 'Move E-IAC-07 to…' })
    await user.click(btn)
    const menu = screen.getByRole('menu')
    const items = within(menu).getAllByRole('menuitem')
    expect(items.map(i => i.textContent)).toEqual(['In progress', 'Action required · needs a note'])
    expect(items[0]).toHaveFocus()
    await user.keyboard('{ArrowDown}')
    expect(items[1]).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(btn).toHaveFocus()

    // No permitted move: say who has the next move instead of a control.
    const mnt = within(cardIn('Submitted', 'E-MNT-03')!)
    expect(mnt.queryByRole('button', { name: /Move E-MNT-03/ })).not.toBeInTheDocument()
    expect(mnt.getByText('Next move: the auditor')).toBeInTheDocument()
  })

  it('a permitted move calls changeReviewStatus and moves the card before the request resolves', async () => {
    const user = userEvent.setup()
    change.mockReturnValue(new Promise(() => {}))
    renderBoard()
    await screen.findByRole('region', { name: 'Open' })
    await user.click(screen.getByRole('button', { name: 'Move E-IAC-07 to…' }))
    await user.keyboard('{Enter}')
    expect(change).toHaveBeenCalledWith('org-1', 'eng-1', 'E-IAC-07', 'ce', { to_status: 'in_progress', from_status: 'open' })
    await waitFor(() => expect(cardIn('In progress', 'E-IAC-07')).toBeInTheDocument())
    expect(cardIn('Open', 'E-IAC-07')).not.toBeInTheDocument()
    expect(within(column('In progress')).getByLabelText('1 cards')).toBeInTheDocument()
  })

  it('a 409 rolls the card back and shows the server message', async () => {
    const user = userEvent.setup()
    change.mockRejectedValue(apiError(409, 'Card is already In progress.'))
    renderBoard()
    await screen.findByRole('region', { name: 'Open' })
    await user.click(screen.getByRole('button', { name: 'Move E-IAC-07 to…' }))
    await user.click(screen.getByRole('menuitem', { name: 'In progress' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Someone else has already moved it: Card is already In progress.')
    expect(cardIn('Open', 'E-IAC-07')).toBeInTheDocument()
    expect(cardIn('In progress', 'E-IAC-07')).not.toBeInTheDocument()
  })

  it('a 403 rolls the card back and shows the server message', async () => {
    const user = userEvent.setup()
    change.mockRejectedValue(apiError(403, 'Only the reviewing auditor may move this card.'))
    renderBoard()
    await screen.findByRole('region', { name: 'Open' })
    await user.click(screen.getByRole('button', { name: 'Move E-IAC-07 to…' }))
    await user.click(screen.getByRole('menuitem', { name: 'In progress' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('You are not permitted to make this move: Only the reviewing auditor may move this card.')
    expect(cardIn('Open', 'E-IAC-07')).toBeInTheDocument()
  })

  it('a move that needs a note opens the panel instead of calling the API', async () => {
    const user = userEvent.setup()
    renderBoard()
    await screen.findByRole('region', { name: 'Open' })
    await user.click(screen.getByRole('button', { name: 'Move E-IAC-07 to…' }))
    await user.click(screen.getByRole('menuitem', { name: /Action required/ }))
    expect(change).not.toHaveBeenCalled()
    expect(screen.getByTestId('card-panel')).toHaveTextContent('Panel for E-IAC-07')
    expect(cardIn('Open', 'E-IAC-07')).toHaveAttribute('aria-current', 'true')
  })

  it('drag and drop: a permitted column accepts, a refused column does nothing', async () => {
    change.mockReturnValue(new Promise(() => {}))
    renderBoard()
    await screen.findByRole('region', { name: 'Open' })
    const card = cardIn('Open', 'E-IAC-07')!
    const dt = { setData: vi.fn(), getData: () => 'E-IAC-07', effectAllowed: '', dropEffect: '' }

    fireEvent.dragStart(card, { dataTransfer: dt })
    expect(column('In progress')).toHaveClass('is-drop-target')
    expect(column('Accepted')).toHaveClass('is-drop-refused')
    fireEvent.dragOver(column('Accepted'), { dataTransfer: dt })
    fireEvent.drop(column('Accepted'), { dataTransfer: dt })
    fireEvent.dragEnd(card, { dataTransfer: dt })
    expect(change).not.toHaveBeenCalled()
    expect(cardIn('Open', 'E-IAC-07')).toBeInTheDocument()

    fireEvent.dragStart(card, { dataTransfer: dt })
    fireEvent.dragOver(column('In progress'), { dataTransfer: dt })
    fireEvent.drop(column('In progress'), { dataTransfer: dt })
    await waitFor(() =>
      expect(change).toHaveBeenCalledWith('org-1', 'eng-1', 'E-IAC-07', 'ce', { to_status: 'in_progress', from_status: 'open' }))
    await waitFor(() => expect(cardIn('In progress', 'E-IAC-07')).toBeInTheDocument())
  })

  it('lists no-evidence controls and opens a control-level query dialog', async () => {
    const user = userEvent.setup()
    renderBoard()
    const strip = await screen.findByRole('region', { name: 'No evidence — resolve by query' })
    const item = within(strip).getByRole('button', { name: /IAC-08\.1/ })
    expect(item).toHaveTextContent('Automated System Account Management')
    expect(item).toHaveTextContent('2-BP6')
    expect(item).toHaveTextContent('1 open query')
    await user.click(item)
    const dialog = screen.getByRole('dialog', { name: /IAC-08\.1/ })
    expect(within(dialog).getByTestId('queries')).toHaveTextContent('Queries for IAC-08.1')
    expect(dialog).toHaveTextContent(
      'It counts as Accepted once at least one query here is Closed and none is Open or Answered.',
    )
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('focus.reviewId opens the matching card and calls onFocusConsumed', async () => {
    const props = renderBoard({ focus: { reviewId: 'rev-mnt03' } })
    expect(await screen.findByTestId('card-panel')).toHaveTextContent('Panel for E-MNT-03')
    expect(props.onFocusConsumed).toHaveBeenCalledTimes(1)
    expect(cardIn('Submitted', 'E-MNT-03')).toHaveAttribute('aria-current', 'true')
  })

  it('focus on a card not on this tab says so', async () => {
    const props = renderBoard({ focus: { evidenceId: 'E-NOPE-01' } })
    expect(await screen.findByText('E-NOPE-01 is not on the CE 3.3 tab.')).toBeInTheDocument()
    expect(props.onFocusConsumed).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId('card-panel')).not.toBeInTheDocument()
  })

  it('a 403 or any other failed move fetches the board again after the rollback (L3)', async () => {
    const user = userEvent.setup()
    change.mockRejectedValue(apiError(422, 'Unprocessable move.'))
    renderBoard()
    await screen.findByRole('region', { name: 'Open' })
    const before = getBoard.mock.calls.length
    await user.click(screen.getByRole('button', { name: 'Move E-IAC-07 to…' }))
    await user.click(screen.getByRole('menuitem', { name: 'In progress' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Unprocessable move.')
    await waitFor(() => expect(getBoard.mock.calls.length).toBeGreaterThan(before))
  })

  it('a menu move that needs a note hands the move to the panel, which clears it once taken (L2)', async () => {
    const user = userEvent.setup()
    renderBoard()
    await screen.findByRole('region', { name: 'Open' })
    await user.click(screen.getByRole('button', { name: 'Move E-IAC-07 to…' }))
    await user.click(screen.getByRole('menuitem', { name: /Action required/ }))
    expect(screen.getByTestId('pending-move')).toHaveTextContent('action_required')
    await user.click(screen.getByRole('button', { name: 'consume pending move' }))
    expect(screen.getByTestId('pending-move')).toHaveTextContent('none')
  })

  it('a drag that needs a note hands the move to the panel (L2)', async () => {
    renderBoard()
    await screen.findByRole('region', { name: 'Open' })
    const card = cardIn('Open', 'E-IAC-07')!
    const dt = { setData: vi.fn(), getData: () => 'E-IAC-07', effectAllowed: '', dropEffect: '' }
    fireEvent.dragStart(card, { dataTransfer: dt })
    fireEvent.dragOver(column('Action required'), { dataTransfer: dt })
    fireEvent.drop(column('Action required'), { dataTransfer: dt })
    expect(change).not.toHaveBeenCalled()
    expect(screen.getByTestId('pending-move')).toHaveTextContent('action_required')
  })

  it('a focus missing from this tab is handed to onFocusMissed, which can keep it (M2)', async () => {
    const onFocusMissed = vi.fn(() => true)
    const props = renderBoard({ focus: { evidenceId: 'E-NOPE-01' }, onFocusMissed })
    await waitFor(() => expect(onFocusMissed).toHaveBeenCalledTimes(1))
    expect(props.onFocusConsumed).not.toHaveBeenCalled()
    expect(screen.queryByText(/is not on the CE 3.3 tab/)).not.toBeInTheDocument()
  })

  it('says the focus is missing once onFocusMissed has no tab left to try (M2)', async () => {
    const onFocusMissed = vi.fn(() => false)
    const props = renderBoard({ focus: { evidenceId: 'E-NOPE-01' }, onFocusMissed })
    expect(await screen.findByText('E-NOPE-01 is not on the CE 3.3 tab.')).toBeInTheDocument()
    expect(onFocusMissed).toHaveBeenCalledTimes(1)
    expect(props.onFocusConsumed).toHaveBeenCalledTimes(1)
  })

  it('shows what is loading, and an error with retry', async () => {
    getBoard.mockReset()
    getBoard.mockRejectedValueOnce(apiError(500, 'Database unavailable'))
    getBoard.mockResolvedValue(makeBoard())
    const user = userEvent.setup()
    renderBoard()
    expect(screen.getByText(/Loading the CE 3.3 review board/)).toBeInTheDocument()
    expect(await screen.findByText(/could not be loaded: Database unavailable/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('region', { name: 'Open' })).toBeInTheDocument()
  })
})
