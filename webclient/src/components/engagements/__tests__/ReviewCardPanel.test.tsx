/**
 * ReviewCardPanel: the detail panel for one evidence object under review.
 *
 * - Only the moves the server allows are rendered; a note-required move waits
 *   for a note and sends it.
 * - The organisation's response form appears only in Action required with
 *   `submitted` allowed, and sends linked files or a reason for none.
 * - The Internal discussion tab, its count and its list exist only when the
 *   counts response carries an `internal` key.
 * - Replies, edit and delete follow the server's flags.
 * - Reviewer and owner pickers follow the viewer's flags.
 * - Artifacts open through the engagement-scoped link; control ids are plain
 *   text without navigation; history renders in the server's order.
 */
import type { ReactNode } from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ReviewCardPanel from '../ReviewCardPanel'
import {
  assignReviewer,
  changeReviewStatus,
  createReviewComment,
  getReviewArtifactLink,
  getReviewCard,
  getReviewCommentCounts,
  getReviewPeople,
  listReviewComments,
  listReviewEvents,
  type EvidenceCardDetail,
  type ReviewComment,
  type ReviewEvent,
  type ReviewViewer,
} from '../../../data/engagementReviewApi'
import {
  clearAccountableTeam,
  createOrUpdateEvidenceTracking,
  listEngagementQueries,
  listTeams,
  setAccountableTeam,
} from '../../../data/apiClient'

vi.mock('../../../data/apiClient', () => ({
  apiFetch: vi.fn(),
  createOrUpdateEvidenceTracking: vi.fn(),
  listTeams: vi.fn(),
  setAccountableTeam: vi.fn(),
  clearAccountableTeam: vi.fn(),
  listEngagementQueries: vi.fn(),
  createEngagementQuery: vi.fn(),
  getEngagementQuery: vi.fn(),
  respondToEngagementQuery: vi.fn(),
  updateEngagementQueryStatus: vi.fn(),
}))

vi.mock('../../../data/engagementReviewApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../data/engagementReviewApi')>()
  return {
    ...actual,
    getReviewCard: vi.fn(),
    changeReviewStatus: vi.fn(),
    assignReviewer: vi.fn(),
    listReviewEvents: vi.fn(),
    getReviewPeople: vi.fn(),
    getReviewArtifactLink: vi.fn(),
    listReviewComments: vi.fn(),
    getReviewCommentCounts: vi.fn(),
    createReviewComment: vi.fn(),
    updateReviewComment: vi.fn(),
    deleteReviewComment: vi.fn(),
    getReviewCommentHistory: vi.fn(),
  }
})

vi.mock('../../evidence/EvidenceFileUpload', () => ({
  EvidenceFileUpload: ({ onUploadComplete }: { onUploadComplete: () => void }) => (
    <button type="button" onClick={onUploadComplete}>Simulate upload</button>
  ),
}))

const ORG = 'org-1'
const ENG = 'eng-1'
const FW = 'cyber_essentials_3_3'

const AUDITOR: ReviewViewer = {
  user_id: 'aud-1', is_auditor: true, auditor_kind: 'external', org_role: null,
  can_assign_reviewer: true, can_reassign_owner: false,
}
const ORG_EDITOR: ReviewViewer = {
  user_id: 'u-1', is_auditor: false, auditor_kind: null, org_role: 'editor',
  can_assign_reviewer: false, can_reassign_owner: false,
}
const ORG_ADMIN: ReviewViewer = {
  user_id: 'u-9', is_auditor: false, auditor_kind: null, org_role: 'admin',
  can_assign_reviewer: true, can_reassign_owner: true,
}
const TEAMS = [
  { id: 'team-it', name: 'IT Operations' },
  { id: 'team-sec', name: 'Security' },
]

function makeCard(over: Partial<EvidenceCardDetail> = {}): EvidenceCardDetail {
  return {
    review_id: 'rev-1',
    evidence_id: 'E-IAC-07',
    title: 'Account Management Compliance Reviews',
    status: 'in_progress',
    controls: [{ scf_id: 'IAC-08', title: 'Account Management', clauses: ['2-BP1', '4-BP3'] }],
    clauses: ['2-BP1', '4-BP3'],
    file_count: 1,
    in_window_count: 1,
    ai_verdict: 'partial',
    open_query_count: 0,
    reviewer: { user_id: 'aud-1', name: 'Jo Reviewer' },
    owner: {
      tier: 'explicit', label: 'Sam Owner', team_id: null, team_name: null, is_tracked: true,
      tracking_id: 't-1', assigned_user_id: 'u-1', user_ids: ['u-1'],
    },
    allowed_transitions: ['action_required', 'accepted'],
    note_required: { action_required: true, accepted: false },
    updated_at: '2026-11-01T10:00:00Z',
    artifacts: [{
      id: 'f-1', filename: 'access-review-q3.pdf', uploaded_at: '2026-10-01T09:00:00Z',
      effective_start: '2026-07-01', effective_end: '2026-09-30', in_window: true,
      window_basis: 'asserted_period', ai_status: 'partial',
    }],
    decision_note: null,
    last_decision_at: null,
    last_decision_by: null,
    description: 'Evidence that account reviews happen on schedule.',
    ...over,
  }
}

function comment(over: Partial<ReviewComment> = {}): ReviewComment {
  return {
    id: 'c-1', parent_comment_id: null, author: { user_id: 'aud-1', name: 'Jo Reviewer' },
    content: 'Please confirm the review date.', visibility: 'shared', mentions: [],
    is_edited: false, edited_at: null, is_deleted: false, created_at: '2026-11-02T10:00:00Z',
    can_edit: false, can_delete: false, ...over,
  }
}

function renderPanel(props: Partial<Parameters<typeof ReviewCardPanel>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  const onClose = vi.fn()
  render(
    <ReviewCardPanel
      organizationId={ORG}
      engagementId={ENG}
      framework={FW}
      frameworkLabel="CE 3.3"
      evidenceId="E-IAC-07"
      viewer={AUDITOR}
      onClose={onClose}
      {...props}
    />,
    { wrapper },
  )
  return { onClose }
}

beforeEach(() => {
  vi.mocked(getReviewCard).mockResolvedValue(makeCard())
  vi.mocked(listTeams).mockResolvedValue(TEAMS as never)
  vi.mocked(listReviewEvents).mockResolvedValue([])
  vi.mocked(getReviewPeople).mockResolvedValue({
    auditors: [{ user_id: 'aud-1', name: 'Jo Reviewer', kind: 'external' }, { user_id: 'aud-2', name: 'Kim Auditor', kind: 'internal' }],
    members: [{ user_id: 'u-1', name: 'Sam Owner', role: 'editor' }, { user_id: 'u-2', name: 'Pat Member', role: 'viewer' }],
  })
  vi.mocked(getReviewCommentCounts).mockResolvedValue({ shared: 0 })
  vi.mocked(listReviewComments).mockResolvedValue([])
  vi.mocked(listEngagementQueries).mockResolvedValue([])
})

afterEach(() => {
  vi.clearAllMocks()
  vi.restoreAllMocks()
})

describe('stage buttons', () => {
  it('renders only the moves the server allows', async () => {
    renderPanel()
    expect(await screen.findByRole('button', { name: 'Accept evidence' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Action required' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start testing' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Move back to Open' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Mark as submitted' })).not.toBeInTheDocument()
  })

  it('says whose move it is when none is allowed', async () => {
    vi.mocked(getReviewCard).mockResolvedValue(makeCard({ status: 'action_required', allowed_transitions: [], note_required: {} }))
    renderPanel()
    expect(await screen.findByText(/next move is the organisation's: Sam Owner/)).toBeInTheDocument()
  })

  it('blocks a note-required move until a note is typed, then sends it', async () => {
    const user = userEvent.setup()
    vi.mocked(changeReviewStatus).mockResolvedValue(makeCard({ status: 'action_required' }))
    renderPanel()
    await user.click(await screen.findByRole('button', { name: 'Action required' }))

    const note = screen.getByLabelText(/What does the organisation need to do\?/)
    const form = note.closest('form') as HTMLFormElement
    await user.click(within(form).getByRole('button', { name: 'Action required' }))
    expect(screen.getByRole('alert')).toHaveTextContent('A note is required for this move.')
    expect(changeReviewStatus).not.toHaveBeenCalled()

    await user.type(note, 'Upload the Q3 review sign-off')
    await user.click(within(form).getByRole('button', { name: 'Action required' }))
    await waitFor(() => expect(changeReviewStatus).toHaveBeenCalledWith(
      ORG, ENG, 'E-IAC-07', FW, { to_status: 'action_required', from_status: 'in_progress', note: 'Upload the Q3 review sign-off' },
    ))
  })

  it('sends the stage the card was seen in with a plain move (stale-move guard)', async () => {
    const user = userEvent.setup()
    vi.mocked(changeReviewStatus).mockResolvedValue(makeCard({ status: 'accepted' }))
    renderPanel()
    await user.click(await screen.findByRole('button', { name: 'Accept evidence' }))
    await waitFor(() => expect(changeReviewStatus).toHaveBeenCalledWith(
      ORG, ENG, 'E-IAC-07', FW, { to_status: 'accepted', from_status: 'in_progress' },
    ))
  })

  it('opens the note form for a move handed over by the board, then clears it (L2)', async () => {
    const onPendingMoveConsumed = vi.fn()
    renderPanel({ pendingMove: 'action_required', onPendingMoveConsumed })
    expect(await screen.findByLabelText(/What does the organisation need to do\?/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Action required', expanded: true })).toBeInTheDocument()
    await waitFor(() => expect(onPendingMoveConsumed).toHaveBeenCalledTimes(1))
    expect(changeReviewStatus).not.toHaveBeenCalled()
  })

  it("shows the server's refusal", async () => {
    const user = userEvent.setup()
    const err = Object.assign(new Error('Card is no longer In progress.'), { status: 409 })
    vi.mocked(changeReviewStatus).mockRejectedValue(err)
    renderPanel()
    await user.click(await screen.findByRole('button', { name: 'Accept evidence' }))
    expect(await screen.findByText('Card is no longer In progress.')).toBeInTheDocument()
    // 409: the card is fetched again.
    await waitFor(() => expect(vi.mocked(getReviewCard).mock.calls.length).toBeGreaterThan(1))
  })
})

describe('organisation response form', () => {
  const actionCard = () => makeCard({
    status: 'action_required',
    allowed_transitions: ['submitted'],
    note_required: { submitted: true },
    decision_note: 'The Q3 sign-off is missing.',
  })

  it('does not appear outside Action required', async () => {
    vi.mocked(getReviewCard).mockResolvedValue(makeCard({ status: 'in_progress', allowed_transitions: ['submitted'] }))
    renderPanel({ viewer: ORG_EDITOR })
    await screen.findByRole('button', { name: 'Mark as submitted' })
    expect(screen.queryByRole('form', { name: 'Respond to the auditor' })).not.toBeInTheDocument()
  })

  it('does not appear when submitted is not allowed', async () => {
    vi.mocked(getReviewCard).mockResolvedValue(makeCard({ status: 'action_required', allowed_transitions: [] }))
    renderPanel()
    await screen.findByText(/next move is the organisation's/)
    expect(screen.queryByRole('form', { name: 'Respond to the auditor' })).not.toBeInTheDocument()
  })

  it('submits the ticked files', async () => {
    const user = userEvent.setup()
    vi.mocked(getReviewCard).mockResolvedValue(actionCard())
    vi.mocked(changeReviewStatus).mockResolvedValue(actionCard())
    renderPanel({ viewer: ORG_EDITOR })
    const form = await screen.findByRole('form', { name: 'Respond to the auditor' })
    expect(within(form).getByText('The Q3 sign-off is missing.')).toBeInTheDocument()
    await user.type(within(form).getByLabelText(/Your response/), 'Sign-off attached')
    await user.click(within(form).getByRole('checkbox', { name: /access-review-q3\.pdf/ }))
    await user.click(within(form).getByRole('button', { name: 'Submit response' }))
    await waitFor(() => expect(changeReviewStatus).toHaveBeenCalledWith(ORG, ENG, 'E-IAC-07', FW, {
      to_status: 'submitted', from_status: 'action_required', note: 'Sign-off attached', artifact_file_ids: ['f-1'],
    }))
  })

  it('submits a reason when no file is linked', async () => {
    const user = userEvent.setup()
    vi.mocked(getReviewCard).mockResolvedValue(actionCard())
    vi.mocked(changeReviewStatus).mockResolvedValue(actionCard())
    renderPanel({ viewer: ORG_EDITOR })
    const form = await screen.findByRole('form', { name: 'Respond to the auditor' })
    await user.type(within(form).getByLabelText(/Your response/), 'See explanation')

    // Neither a file nor a reason: blocked.
    await user.click(within(form).getByRole('button', { name: 'Submit response' }))
    expect(within(form).getByRole('alert')).toHaveTextContent(/Link at least one file/)
    expect(changeReviewStatus).not.toHaveBeenCalled()

    await user.click(within(form).getByRole('checkbox', { name: 'Link no file' }))
    await user.type(within(form).getByLabelText(/Why is no file linked\?/), 'The review runs in January')
    await user.click(within(form).getByRole('button', { name: 'Submit response' }))
    await waitFor(() => expect(changeReviewStatus).toHaveBeenCalledWith(ORG, ENG, 'E-IAC-07', FW, {
      to_status: 'submitted', from_status: 'action_required', note: 'See explanation', no_artifact_reason: 'The review runs in January',
    }))
  })

  it('pre-ticks a newly uploaded file', async () => {
    const user = userEvent.setup()
    const before = actionCard()
    const after = actionCard()
    after.artifacts = [...before.artifacts, { ...before.artifacts[0], id: 'f-2', filename: 'signoff.pdf' }]
    vi.mocked(getReviewCard).mockResolvedValueOnce(before).mockResolvedValue(after)
    renderPanel({ viewer: ORG_EDITOR })
    const form = await screen.findByRole('form', { name: 'Respond to the auditor' })
    await user.click(within(form).getByRole('button', { name: 'Upload a new file' }))
    await user.click(within(form).getByRole('button', { name: 'Simulate upload' }))
    expect(await within(form).findByRole('checkbox', { name: /signoff\.pdf/ })).toBeChecked()
    expect(within(form).getByRole('checkbox', { name: /access-review-q3\.pdf/ })).not.toBeChecked()
  })
})

describe('discussion', () => {
  it('has no Internal tab and never asks for internal notes when counts has no internal key', async () => {
    vi.mocked(getReviewCommentCounts).mockResolvedValue({ shared: 2 })
    renderPanel()
    const shared = await screen.findByRole('tab', { name: /Shared/ })
    await waitFor(() => expect(shared).toHaveTextContent('2'))
    expect(screen.queryByRole('tab', { name: /Internal/ })).not.toBeInTheDocument()
    expect(screen.queryByText(/not visible to the auditor/)).not.toBeInTheDocument()
    await waitFor(() => expect(listReviewComments).toHaveBeenCalled())
    for (const call of vi.mocked(listReviewComments).mock.calls) {
      expect(call[3]).toBe('shared')
    }
  })

  it('shows an Internal tab with its count when counts has the key', async () => {
    const user = userEvent.setup()
    vi.mocked(getReviewCommentCounts).mockResolvedValue({ shared: 1, internal: 3 })
    renderPanel({ viewer: ORG_EDITOR })
    const internal = await screen.findByRole('tab', { name: /Internal/ })
    expect(internal).toHaveTextContent('3')
    await user.click(internal)
    await waitFor(() => expect(listReviewComments).toHaveBeenCalledWith(ORG, ENG, 'E-IAC-07', 'internal'))
    expect(screen.getAllByText(/not visible to the auditor/).length).toBeGreaterThan(0)
  })

  it('posts a reply with parent_comment_id', async () => {
    const user = userEvent.setup()
    vi.mocked(listReviewComments).mockResolvedValue([comment()])
    vi.mocked(createReviewComment).mockResolvedValue(comment({ id: 'c-2', parent_comment_id: 'c-1' }))
    renderPanel()
    await screen.findByText('Please confirm the review date.')
    await user.click(screen.getByRole('button', { name: 'Reply' }))
    const box = screen.getByLabelText(/^Reply/)
    await user.type(box, 'Confirmed: 30 September')
    await user.click(screen.getByRole('button', { name: 'Post reply' }))
    await waitFor(() => expect(createReviewComment).toHaveBeenCalledWith(ORG, ENG, 'E-IAC-07', {
      content: 'Confirmed: 30 September', visibility: 'shared', parent_comment_id: 'c-1',
    }))
  })

  it('offers edit and delete only as the server allows', async () => {
    vi.mocked(listReviewComments).mockResolvedValue([
      comment({ id: 'c-1', content: 'Mine', can_edit: true, can_delete: true }),
      comment({ id: 'c-2', content: 'Theirs', can_edit: false, can_delete: false }),
    ])
    renderPanel()
    const mine = (await screen.findByText('Mine')).closest('li') as HTMLElement
    const theirs = screen.getByText('Theirs').closest('li') as HTMLElement
    expect(within(mine).getByRole('button', { name: 'Edit' })).toBeInTheDocument()
    expect(within(mine).getByRole('button', { name: 'Delete' })).toBeInTheDocument()
    expect(within(theirs).queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument()
    expect(within(theirs).queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument()
  })

  it('keeps a deleted comment in place', async () => {
    vi.mocked(listReviewComments).mockResolvedValue([comment({ content: '', is_deleted: true })])
    renderPanel()
    expect(await screen.findByText('This comment was deleted.')).toBeInTheDocument()
  })
})

describe('owner and reviewer', () => {
  it('shows the reviewer select only with can_assign_reviewer', async () => {
    const user = userEvent.setup()
    vi.mocked(assignReviewer).mockResolvedValue(makeCard())
    renderPanel({ viewer: AUDITOR })
    const select = await screen.findByLabelText('Reviewer')
    await user.selectOptions(select, 'aud-2')
    await waitFor(() => expect(assignReviewer).toHaveBeenCalledWith(ORG, ENG, 'E-IAC-07', FW, 'aud-2'))
    expect(screen.queryByLabelText('Assign to')).not.toBeInTheDocument()
  })

  it('offers the accountable-team select only with can_reassign_owner, never a person picker', async () => {
    renderPanel({ viewer: ORG_EDITOR })
    await screen.findByRole('heading', { name: 'Owner and reviewer' })
    expect(screen.queryByLabelText('Accountable team')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Assign to')).not.toBeInTheDocument()
  })

  it('sets the accountable team on the tracking row through the team-assignment route', async () => {
    const user = userEvent.setup()
    vi.mocked(setAccountableTeam).mockResolvedValue({} as never)
    renderPanel({ viewer: ORG_ADMIN })
    const select = await screen.findByLabelText('Accountable team')
    await waitFor(() => expect(select).toBeEnabled())
    await user.selectOptions(select, 'team-sec')
    await waitFor(() => expect(setAccountableTeam).toHaveBeenCalledWith(
      ORG, { type: 'evidence', itemId: 't-1', teamId: 'team-sec' },
    ))
    expect(createOrUpdateEvidenceTracking).not.toHaveBeenCalled()
  })

  it('creates the tracking row first for untracked evidence, sending only the evidence id', async () => {
    const user = userEvent.setup()
    vi.mocked(getReviewCard).mockResolvedValue(makeCard({
      owner: { tier: 'org_admin', label: 'Unowned → org admins', team_id: null, team_name: null, is_tracked: false, tracking_id: null, assigned_user_id: null, user_ids: [] },
    }))
    vi.mocked(createOrUpdateEvidenceTracking).mockResolvedValue({ id: 't-new' } as never)
    vi.mocked(setAccountableTeam).mockResolvedValue({} as never)
    renderPanel({ viewer: ORG_ADMIN })
    const select = await screen.findByLabelText('Accountable team')
    await waitFor(() => expect(select).toBeEnabled())
    await user.selectOptions(select, 'team-it')
    await waitFor(() => expect(setAccountableTeam).toHaveBeenCalledWith(
      ORG, { type: 'evidence', itemId: 't-new', teamId: 'team-it' },
    ))
    expect(createOrUpdateEvidenceTracking).toHaveBeenCalledWith({ evidence_id: 'E-IAC-07' }, ORG)
  })

  it('binds the select to the accountable team and stands it down on "No accountable team"', async () => {
    const user = userEvent.setup()
    vi.mocked(getReviewCard).mockResolvedValue(makeCard({
      owner: { tier: 'accountable_team', label: 'IT Operations', team_id: 'team-it', team_name: 'IT Operations', is_tracked: true, tracking_id: 't-1', assigned_user_id: null, user_ids: ['u-1'] },
    }))
    vi.mocked(clearAccountableTeam).mockResolvedValue({} as never)
    renderPanel({ viewer: ORG_ADMIN })
    const select = await screen.findByLabelText('Accountable team')
    await waitFor(() => expect(select).toHaveValue('team-it'))
    await user.selectOptions(select, '')
    await waitFor(() => expect(clearAccountableTeam).toHaveBeenCalledWith(
      ORG, { type: 'evidence', itemId: 't-1', teamId: 'team-it' },
    ))
  })

  it('lets a person stamped before the team rule be cleared, and only cleared', async () => {
    const user = userEvent.setup()
    vi.mocked(createOrUpdateEvidenceTracking).mockResolvedValue({ id: 't-1' } as never)
    renderPanel({ viewer: ORG_ADMIN })
    await user.click(await screen.findByRole('button', { name: /Clear Sam Owner/ }))
    await waitFor(() => expect(createOrUpdateEvidenceTracking).toHaveBeenCalledWith(
      { evidence_id: 'E-IAC-07', assigned_user_id: null }, ORG,
    ))
  })

  it('shows an unowned card as falling to org admins', async () => {
    vi.mocked(getReviewCard).mockResolvedValue(makeCard({
      owner: { tier: 'org_admin', label: 'Unowned → org admins', team_id: null, team_name: null, is_tracked: false, tracking_id: null, assigned_user_id: null, user_ids: [] },
    }))
    renderPanel()
    expect(await screen.findByText('Unowned → org admins')).toBeInTheDocument()
  })
})

describe('artifacts, controls and history', () => {
  it('opens an artifact through getReviewArtifactLink', async () => {
    const user = userEvent.setup()
    const tab = { opener: {}, location: { href: '' }, close: vi.fn() }
    const open = vi.spyOn(window, 'open').mockReturnValue(tab as unknown as Window)
    vi.mocked(getReviewArtifactLink).mockResolvedValue({ download_url: '/files/signed/abc', filename: 'access-review-q3.pdf' })
    renderPanel()
    await user.click(await screen.findByRole('button', { name: 'access-review-q3.pdf' }))
    expect(open).toHaveBeenCalledWith('about:blank', '_blank')
    await waitFor(() => expect(getReviewArtifactLink).toHaveBeenCalledWith(ORG, ENG, 'E-IAC-07', 'f-1'))
    await waitFor(() => expect(tab.location.href).toBe(`${window.location.origin}/files/signed/abc`))
    expect(tab.opener).toBeNull()
  })

  it('says when no link is available', async () => {
    const user = userEvent.setup()
    const tab = { opener: {}, location: { href: '' }, close: vi.fn() }
    vi.spyOn(window, 'open').mockReturnValue(tab as unknown as Window)
    vi.mocked(getReviewArtifactLink).mockResolvedValue({ download_url: null, filename: 'access-review-q3.pdf' })
    renderPanel()
    await user.click(await screen.findByRole('button', { name: 'access-review-q3.pdf' }))
    expect(await screen.findByText('No download link is available for access-review-q3.pdf.')).toBeInTheDocument()
    expect(tab.close).toHaveBeenCalled()
  })

  it('says when the organisation holds no artifact', async () => {
    vi.mocked(getReviewCard).mockResolvedValue(makeCard({ artifacts: [], file_count: 0, in_window_count: 0 }))
    renderPanel()
    expect(await screen.findByText('The organisation holds no artifact for this evidence object.')).toBeInTheDocument()
  })

  it('renders control ids as plain text without onNavigateToControl', async () => {
    renderPanel()
    const id = await screen.findByText('IAC-08')
    expect(id.tagName).toBe('SPAN')
    expect(screen.queryByRole('button', { name: 'IAC-08' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Evidence workspace/ })).not.toBeInTheDocument()
  })

  it('makes control ids clickable with onNavigateToControl', async () => {
    const user = userEvent.setup()
    const nav = vi.fn()
    renderPanel({ onNavigateToControl: nav })
    await user.click(await screen.findByRole('button', { name: 'IAC-08' }))
    expect(nav).toHaveBeenCalledWith('IAC-08', 'in-scope')
  })

  it('renders events in order', async () => {
    const events: ReviewEvent[] = [
      { id: 'e1', event_type: 'transition', from_status: 'open', to_status: 'in_progress', actor: { user_id: 'aud-1', name: 'Jo Reviewer' }, actor_kind: 'auditor', note: null, created_at: '2026-10-01T09:00:00Z' },
      { id: 'e2', event_type: 'transition', from_status: 'in_progress', to_status: 'action_required', actor: { user_id: 'aud-1', name: 'Jo Reviewer' }, actor_kind: 'auditor', note: 'Q3 sign-off missing', created_at: '2026-10-02T09:00:00Z' },
      { id: 'e3', event_type: 'transition', from_status: 'action_required', to_status: 'submitted', actor: { user_id: 'u-1', name: 'Sam Owner' }, actor_kind: 'org', note: 'Attached', created_at: '2026-10-03T09:00:00Z' },
    ]
    vi.mocked(listReviewEvents).mockResolvedValue(events)
    renderPanel()
    const list = (await screen.findByText('Q3 sign-off missing')).closest('ol') as HTMLElement
    const items = within(list).getAllByRole('listitem')
    expect(items).toHaveLength(3)
    expect(items[0]).toHaveTextContent(/Open\s*→\s*to\s*In progress/)
    expect(items[1]).toHaveTextContent('Q3 sign-off missing')
    expect(items[2]).toHaveTextContent('Sam Owner')
    expect(items[2]).toHaveTextContent('Organisation')
  })

  it('closes on Escape', async () => {
    const user = userEvent.setup()
    const { onClose } = renderPanel()
    await screen.findByRole('button', { name: 'Accept evidence' })
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
  })

  it('keeps the other sections when the card fails to load', async () => {
    vi.mocked(getReviewCard).mockRejectedValue(Object.assign(new Error('Evidence object not found'), { status: 404 }))
    renderPanel()
    expect(await screen.findByText('Evidence object not found')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Discussion' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'History' })).toBeInTheDocument()
  })
})
