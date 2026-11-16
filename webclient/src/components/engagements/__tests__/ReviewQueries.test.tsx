/**
 * ReviewQueries: the query log for one target (an evidence object or a control).
 *
 * - Lists the target's queries with status and response count.
 * - Raising sends exactly one of `scf_id` / `evidence_id`.
 * - Raising on an evidence object says the card's stage did not change.
 * - Answer, close and reopen call the right functions; a refusal is shown.
 */
import type { ReactNode } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ReviewQueries, { type ReviewQueryTarget } from '../ReviewQueries'
import { useModalDismiss } from '../../../hooks/useModalDismiss'
import type { ReviewViewer } from '../../../data/engagementReviewApi'
import {
  createEngagementQuery,
  getEngagementQuery,
  listEngagementQueries,
  respondToEngagementQuery,
  updateEngagementQueryStatus,
  type EngagementQuery,
} from '../../../data/apiClient'

vi.mock('../../../data/apiClient', () => ({
  apiFetch: vi.fn(),
  listEngagementQueries: vi.fn(),
  createEngagementQuery: vi.fn(),
  getEngagementQuery: vi.fn(),
  respondToEngagementQuery: vi.fn(),
  updateEngagementQueryStatus: vi.fn(),
}))


const ORG = 'org-1'
const ENG = 'eng-1'
const VIEWER: ReviewViewer = {
  user_id: 'aud-1', is_auditor: true, auditor_kind: 'external', org_role: null,
  can_assign_reviewer: true, can_reassign_owner: false,
}

function query(over: Partial<EngagementQuery> = {}): EngagementQuery {
  return {
    id: 'q-1', engagement_id: ENG, scf_id: null, evidence_id: 'E-IAC-07',
    raised_by_user_id: 'aud-1', raised_by_name: 'Jo Reviewer', raised_by_email: 'jo@example.test',
    title: 'Which accounts were reviewed?', body: 'Please list the systems in scope.',
    status: 'open', created_at: '2026-11-01T10:00:00Z', updated_at: null, closed_at: null,
    response_count: 0, responses: null, ...over,
  }
}

function renderQueries(target: ReviewQueryTarget, onChanged = vi.fn(), viewer: ReviewViewer = VIEWER) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  render(
    <ReviewQueries organizationId={ORG} engagementId={ENG} target={target} viewer={viewer} onChanged={onChanged} />,
    { wrapper },
  )
  return { onChanged }
}

beforeEach(() => {
  vi.mocked(listEngagementQueries).mockResolvedValue([
    query(),
    query({ id: 'q-2', title: 'Second sample', status: 'answered', response_count: 2 }),
  ])
})

afterEach(() => vi.clearAllMocks())

describe('close and reopen are the auditor\'s', () => {
  const ORG_MEMBER: ReviewViewer = {
    user_id: 'org-9', is_auditor: false, auditor_kind: null, org_role: 'editor',
    can_assign_reviewer: false, can_reassign_owner: true,
  }

  it('offers an org member no Close query or Reopen query', async () => {
    vi.mocked(getEngagementQuery).mockResolvedValue(query())
    const user = userEvent.setup()
    renderQueries({ evidenceId: 'E-IAC-07' }, vi.fn(), ORG_MEMBER)
    await user.click(await screen.findByRole('button', { name: /Which accounts were reviewed\?/ }))
    expect(await screen.findByRole('button', { name: 'Post answer' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Close query' })).not.toBeInTheDocument()
  })

  it('offers an org member no Reopen query on a closed query', async () => {
    const closed = query({ id: 'q-3', title: 'Settled one', status: 'closed', closed_at: '2026-11-02T10:00:00Z' })
    vi.mocked(listEngagementQueries).mockResolvedValue([closed])
    vi.mocked(getEngagementQuery).mockResolvedValue(closed)
    const user = userEvent.setup()
    renderQueries({ evidenceId: 'E-IAC-07' }, vi.fn(), ORG_MEMBER)
    await user.click(await screen.findByRole('button', { name: /Settled one/ }))
    expect(await screen.findByText('Please list the systems in scope.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reopen query' })).not.toBeInTheDocument()
  })
})

function DialogHost({ onClose }: { onClose: () => void }) {
  useModalDismiss(true, onClose)
  return (
    <div role="dialog" aria-modal="true" aria-label="Control">
      <ReviewQueries organizationId={ORG} engagementId={ENG} target={{ scfId: 'IAC-08.1' }} viewer={VIEWER} inDialog />
    </div>
  )
}

describe('ReviewQueries', () => {
  it('inside a dialog, Escape closes the new-query form and keeps the dialog open (L1)', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><DialogHost onClose={onClose} /></QueryClientProvider>)
    await user.click(await screen.findByRole('button', { name: 'Raise a query' }))
    await user.type(screen.getByLabelText('Title'), 'Draft')
    await user.keyboard('{Escape}')
    expect(screen.queryByLabelText('Title')).not.toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
    // With the form closed, Escape belongs to the dialog again.
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("lists the target's queries with status and response count", async () => {
    renderQueries({ evidenceId: 'E-IAC-07' })
    expect(await screen.findByText('Which accounts were reviewed?')).toBeInTheDocument()
    expect(screen.getByText('Second sample')).toBeInTheDocument()
    expect(screen.getByText('Answered')).toBeInTheDocument()
    expect(screen.getByText('2 responses')).toBeInTheDocument()
    expect(listEngagementQueries).toHaveBeenCalledWith(ORG, ENG, { evidenceId: 'E-IAC-07' })
  })

  it('lists by control for a control target', async () => {
    renderQueries({ scfId: 'IAC-08.1' })
    await screen.findByText('Which accounts were reviewed?')
    expect(listEngagementQueries).toHaveBeenCalledWith(ORG, ENG, { scfId: 'IAC-08.1' })
  })

  it('explains on an evidence object that a query does not move the card, and the lifecycle', async () => {
    renderQueries({ evidenceId: 'E-IAC-07' })
    await screen.findByText('Which accounts were reviewed?')
    expect(screen.getByText(/It does not move the card: set Action required if the organisation must act\./))
      .toBeInTheDocument()
    expect(screen.getByText(/Open → Answered when someone answers → Closed when settled\./)).toBeInTheDocument()
  })

  it('explains the lifecycle for a control, without the card wording', async () => {
    renderQueries({ scfId: 'IAC-08.1' })
    await screen.findByText('Which accounts were reviewed?')
    expect(screen.getByText(/A query is a tracked question about this control\./)).toBeInTheDocument()
    expect(screen.queryByText(/does not move the card/)).not.toBeInTheDocument()
  })

  it('raises on an evidence object with only evidence_id, then prompts about the stage', async () => {
    const user = userEvent.setup()
    vi.mocked(createEngagementQuery).mockResolvedValue(query())
    const { onChanged } = renderQueries({ evidenceId: 'E-IAC-07' })
    await user.click(await screen.findByRole('button', { name: 'Raise a query' }))
    await user.type(screen.getByLabelText('Title'), 'Missing month')
    await user.type(screen.getByLabelText('Question'), 'Where is August?')
    await user.click(screen.getByRole('button', { name: 'Raise query' }))

    await waitFor(() => expect(createEngagementQuery).toHaveBeenCalledTimes(1))
    const payload = vi.mocked(createEngagementQuery).mock.calls[0][2]
    expect(payload).toEqual({ evidence_id: 'E-IAC-07', title: 'Missing month', body: 'Where is August?' })
    expect('scf_id' in payload).toBe(false)
    expect(await screen.findByText(/This did not change the card's stage\. Set it to Action required/)).toBeInTheDocument()
    expect(onChanged).toHaveBeenCalled()
  })

  it('raises on a control with only scf_id and no stage prompt', async () => {
    const user = userEvent.setup()
    vi.mocked(createEngagementQuery).mockResolvedValue(query({ scf_id: 'IAC-08.1', evidence_id: null }))
    renderQueries({ scfId: 'IAC-08.1' })
    await user.click(await screen.findByRole('button', { name: 'Raise a query' }))
    await user.type(screen.getByLabelText('Title'), 'Directory services')
    await user.type(screen.getByLabelText('Question'), 'How are accounts synchronised?')
    await user.click(screen.getByRole('button', { name: 'Raise query' }))

    await waitFor(() => expect(createEngagementQuery).toHaveBeenCalledTimes(1))
    const payload = vi.mocked(createEngagementQuery).mock.calls[0][2]
    expect(payload).toEqual({ scf_id: 'IAC-08.1', title: 'Directory services', body: 'How are accounts synchronised?' })
    expect('evidence_id' in payload).toBe(false)
    expect(screen.queryByText(/did not change the card's stage/)).not.toBeInTheDocument()
  })

  it('answers and closes an open query', async () => {
    const user = userEvent.setup()
    vi.mocked(getEngagementQuery).mockResolvedValue(query({
      responses: [
        { id: 'r-1', user_id: 'u-1', name: 'Sam Owner', email: 'sam@example.test', content: 'Earlier answer', created_at: '2026-11-02T10:00:00Z' },
        // An external auditor's response: no email, and here no name either.
        { id: 'r-2', user_id: 'aud-9', name: null, email: null, content: 'Auditor follow-up', created_at: '2026-11-02T11:00:00Z' },
      ],
      response_count: 2,
    }))
    vi.mocked(respondToEngagementQuery).mockResolvedValue(query({ status: 'answered' }))
    vi.mocked(updateEngagementQueryStatus).mockResolvedValue(query({ status: 'closed' }))
    renderQueries({ evidenceId: 'E-IAC-07' })

    await user.click(await screen.findByRole('button', { name: /Which accounts were reviewed\?/ }))
    expect(await screen.findByText('Please list the systems in scope.')).toBeInTheDocument()
    // Display names only; never an email address or a user id.
    expect(screen.getByText(/Raised by Jo Reviewer/)).toBeInTheDocument()
    expect(screen.getByText(/Sam Owner ·/)).toBeInTheDocument()
    expect(screen.getByText(/Unnamed user ·/)).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/@example\.test/)
    expect(document.body.textContent).not.toMatch(/aud-9|u-1|aud-1/)

    await user.type(screen.getByLabelText('Answer'), 'AD and Okta')
    await user.click(screen.getByRole('button', { name: 'Post answer' }))
    await waitFor(() => expect(respondToEngagementQuery).toHaveBeenCalledWith(ORG, ENG, 'q-1', 'AD and Okta'))

    await user.click(screen.getByRole('button', { name: 'Close query' }))
    await waitFor(() => expect(updateEngagementQueryStatus).toHaveBeenCalledWith(ORG, ENG, 'q-1', 'closed'))
  })

  it('reopens a closed query', async () => {
    const user = userEvent.setup()
    vi.mocked(getEngagementQuery).mockResolvedValue(query({ status: 'closed', closed_at: '2026-11-03T10:00:00Z', responses: [] }))
    vi.mocked(updateEngagementQueryStatus).mockResolvedValue(query({ status: 'open' }))
    renderQueries({ evidenceId: 'E-IAC-07' })
    await user.click(await screen.findByRole('button', { name: /Which accounts were reviewed\?/ }))
    await user.click(await screen.findByRole('button', { name: 'Reopen query' }))
    await waitFor(() => expect(updateEngagementQueryStatus).toHaveBeenCalledWith(ORG, ENG, 'q-1', 'open'))
    expect(screen.queryByLabelText('Answer')).not.toBeInTheDocument()
  })

  it("shows the server's refusal", async () => {
    const user = userEvent.setup()
    vi.mocked(getEngagementQuery).mockResolvedValue(query({ responses: [] }))
    vi.mocked(updateEngagementQueryStatus).mockRejectedValue(
      Object.assign(new Error("Cannot move query from 'closed' to 'closed'."), { status: 409 }),
    )
    renderQueries({ evidenceId: 'E-IAC-07' })
    await user.click(await screen.findByRole('button', { name: /Which accounts were reviewed\?/ }))
    await user.click(await screen.findByRole('button', { name: 'Close query' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("Cannot move query from 'closed' to 'closed'.")
  })
})
