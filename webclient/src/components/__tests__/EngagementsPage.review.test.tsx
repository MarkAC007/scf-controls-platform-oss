// SPDX-License-Identifier: AGPL-3.0-only
/**
 * EngagementsPage: the way into the review board and dashboard, and granting
 * an internal auditor.
 */
import { useState } from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ConfirmProvider } from '../../__tests__/confirmDialog'
import EngagementsPage from '../EngagementsPage'
import {
  getOrgMemberSummaries, listOrgAuditors, inviteOrgAuditor, grantEngagementToOrgAuditor, grantEngagementAuditor, listEngagementAuditors, listEngagements,
  type AuditEngagement,
} from '../../data/apiClient'
import { fetchFrameworks } from '../../data/catalogApi'

// The viewer's role in the organisation, switchable per test. The suites above
// the role-gating block run as 'admin'; that block (#1273) sets its own.
const role = vi.hoisted(() => ({ rank: 'admin' as 'viewer' | 'editor' | 'admin' | 'unknown' }))
vi.mock('../../hooks/useHasOrgRole', () => {
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

vi.mock('../../data/apiClient', () => ({
  listEngagements: vi.fn(),
  createEngagement: vi.fn(),
  deleteEngagement: vi.fn(),
  getEngagementScope: vi.fn(),
  getEngagementPresentation: vi.fn(),
  listEngagementAuditors: vi.fn(),
  grantEngagementAuditor: vi.fn(),
  revokeEngagementAuditor: vi.fn(),
  listEngagementQueries: vi.fn(),
  createEngagementQuery: vi.fn(),
  getEngagementQuery: vi.fn(),
  respondToEngagementQuery: vi.fn(),
  updateEngagementQueryStatus: vi.fn(),
  getEvidenceFile: vi.fn(),
  getOrgMemberSummaries: vi.fn(),
  listOrgAuditors: vi.fn(),
  inviteOrgAuditor: vi.fn(),
  grantEngagementToOrgAuditor: vi.fn(),
}))
vi.mock('../../data/catalogApi', () => ({ fetchFrameworks: vi.fn() }))
vi.mock('../engagements/EngagementReviewWorkspace', () => ({
  default: function Workspace(props: { engagement: AuditEngagement; initialView: string; initialFocus: unknown; onBack: () => void }) {
    // Like the real workspace, the focus is read only on mount.
    const [focus] = useState(props.initialFocus)
    return (
      <div data-testid="workspace">
        {props.engagement.id}|{props.initialView}|{JSON.stringify(focus)}
        <button type="button" onClick={props.onBack}>back</button>
      </div>
    )
  },
}))

const ORG_ID = 'org-1'
const ENGAGEMENT: AuditEngagement = {
  id: 'eng-1', organization_id: ORG_ID, name: 'Cyber Essentials 2026', frameworks: ['cyber_essentials'],
  status: 'active', start_date: '2026-01-01', end_date: '2026-12-31', created_by_user_id: null,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', scope_count: 2,
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listEngagements).mockResolvedValue([ENGAGEMENT])
  vi.mocked(fetchFrameworks).mockResolvedValue([])
  vi.mocked(listEngagementAuditors).mockResolvedValue([])
})

function renderPage(props: Partial<React.ComponentProps<typeof EngagementsPage>> = {}) {
  return render(<EngagementsPage organizationId={ORG_ID} {...props} />, { wrapper: ConfirmProvider })
}

describe('EngagementsPage review workspace entry', () => {
  it('opens the review board for an engagement and returns to the list', async () => {
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Review board' }))
    expect(screen.getByTestId('workspace')).toHaveTextContent('eng-1|board|null')
    await userEvent.click(screen.getByRole('button', { name: 'back' }))
    expect(await screen.findByRole('button', { name: 'Review board' })).toBeInTheDocument()
  })

  it('opens the dashboard for an engagement', async () => {
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Dashboard' }))
    expect(screen.getByTestId('workspace')).toHaveTextContent('eng-1|dashboard|null')
  })

  it('opens the card a notification asked for, once', async () => {
    const consumed = vi.fn()
    renderPage({
      openReview: { engagementId: 'eng-1', view: 'board', focus: { reviewId: 'r-1' } },
      onOpenReviewConsumed: consumed,
    })
    expect(await screen.findByTestId('workspace')).toHaveTextContent('eng-1|board|{"reviewId":"r-1"}')
    expect(consumed).toHaveBeenCalledTimes(1)
  })

  it('clears a request for an engagement that is not in this organisation', async () => {
    const consumed = vi.fn()
    renderPage({ openReview: { engagementId: 'elsewhere' }, onOpenReviewConsumed: consumed })
    await waitFor(() => expect(consumed).toHaveBeenCalledTimes(1))
    expect(screen.queryByTestId('workspace')).not.toBeInTheDocument()
  })

  it('says so, dismissibly, when a request names an engagement not in the list (L6)', async () => {
    renderPage({ openReview: { engagementId: 'elsewhere' }, onOpenReviewConsumed: vi.fn() })
    const notice = await screen.findByText(/That engagement is not in your list/)
    expect(notice.closest('.erb-banner')).toHaveAttribute('role', 'status')
    await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByText(/That engagement is not in your list/)).not.toBeInTheDocument()
  })

  it('a notification for the engagement already open reaches it as a new focus (M1)', async () => {
    const consumed = vi.fn()
    const { rerender } = renderPage({
      openReview: { engagementId: 'eng-1', view: 'board', focus: { reviewId: 'r-1' } },
      onOpenReviewConsumed: consumed,
    })
    expect(await screen.findByTestId('workspace')).toHaveTextContent('{"reviewId":"r-1"}')
    rerender(
      <EngagementsPage
        organizationId={ORG_ID}
        openReview={{ engagementId: 'eng-1', view: 'board', focus: { reviewId: 'r-2' } }}
        onOpenReviewConsumed={consumed}
      />,
    )
    await waitFor(() => expect(screen.getByTestId('workspace')).toHaveTextContent('{"reviewId":"r-2"}'))
  })
})

describe('EngagementsPage auditors drawer', () => {
  it('assigns an external auditor picked from the organisation\'s auditor list', async () => {
    vi.mocked(grantEngagementToOrgAuditor).mockResolvedValue({} as never)
    vi.mocked(listOrgAuditors).mockResolvedValue([
      { id: 'aud-1', user_id: 'user-7', email: 'lead@audit-firm.example', display_name: 'Lee Auditor',
        firm: 'Audit Firm', status: 'active', active_engagements: 0 },
      { id: 'aud-2', user_id: 'user-8', email: 'gone@audit-firm.example', display_name: 'Gone Auditor',
        firm: null, status: 'disabled', active_engagements: 0 },
    ] as never)
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Auditors' }))
    const dialog = await screen.findByRole('dialog')
    const option = await within(dialog).findByRole('option', { name: 'Lee Auditor · Audit Firm' })
    expect(within(dialog).queryByRole('option', { name: /Gone Auditor/ })).toBeNull()  // disabled: not assignable
    await userEvent.selectOptions(within(dialog).getByRole('combobox'), option)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Grant' }))
    await waitFor(() => expect(grantEngagementToOrgAuditor).toHaveBeenCalledWith(ORG_ID, 'eng-1', 'aud-1'))
    expect(grantEngagementAuditor).not.toHaveBeenCalled()
    expect(getOrgMemberSummaries).not.toHaveBeenCalled()
  })

  it('invites a new external auditor from the drawer and selects them', async () => {
    vi.mocked(listOrgAuditors).mockResolvedValue([] as never)
    vi.mocked(inviteOrgAuditor).mockResolvedValue({
      id: 'aud-9', user_id: 'user-9', email: 'new@audit-firm.example', display_name: null, firm: null,
      status: 'invited', active_engagements: 0, idp_temporary_password: null,
    } as never)
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Auditors' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.selectOptions(within(dialog).getByRole('combobox'),
      await within(dialog).findByRole('option', { name: 'Invite a new auditor…' }))
    await userEvent.type(within(dialog).getByRole('textbox', { name: 'Auditor email' }), 'new@audit-firm.example')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Send invite' }))
    await waitFor(() => expect(inviteOrgAuditor).toHaveBeenCalledWith(ORG_ID, { email: 'new@audit-firm.example', display_name: undefined, firm: undefined }))
    expect(await within(dialog).findByRole('option', { name: /new@audit-firm.example \(invited/ })).toBeInTheDocument()
    expect((within(dialog).getByRole('combobox') as HTMLSelectElement).value).toBe('aud-9')
  })

  it('grants an internal auditor picked from the organisation members', async () => {
    vi.mocked(grantEngagementAuditor).mockResolvedValue({} as never)
    vi.mocked(getOrgMemberSummaries).mockResolvedValue([
      { id: 'm1', organization_id: ORG_ID, user_id: 'user-3', role: 'editor', member_type: 'internal',
        user: { id: 'user-3', email: 'sam@acme.example', display_name: 'Sam Reid' } },
    ] as never)
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Auditors' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('radio', { name: /Internal auditor/ }))
    await userEvent.selectOptions(await within(dialog).findByRole('combobox'), await within(dialog).findByRole('option', { name: 'Sam Reid (editor)' }))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Grant' }))
    await waitFor(() => expect(grantEngagementAuditor).toHaveBeenCalledWith(ORG_ID, 'eng-1', 'user-3', 'internal'))
  })

  it('labels each grant as internal or external', async () => {
    vi.mocked(listEngagementAuditors).mockResolvedValue([
      { id: 'g1', user_id: 'u1', email: 'a@firm.example', status: 'active', kind: 'external' },
      { id: 'g2', user_id: 'u2', email: 'b@acme.example', status: 'active', kind: 'internal' },
    ] as never)
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Auditors' }))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('External')).toBeInTheDocument()
    expect(within(dialog).getByText('Internal')).toBeInTheDocument()
  })
})

// ── Role gating (#1273) ─────────────────────────────────────────────────────
import { afterEach as afterEachRoleTest } from 'vitest'

describe('EngagementsPage — what each role is offered', () => {
  afterEachRoleTest(() => { role.rank = 'admin' })
  const DRAFT: AuditEngagement = { ...ENGAGEMENT, id: 'eng-2', name: 'Draft engagement', status: 'draft' }

  it('offers a viewer neither "+ New Engagement" nor Delete, and still lists the engagements', async () => {
    role.rank = 'viewer'
    vi.mocked(listEngagements).mockResolvedValue([ENGAGEMENT, DRAFT])
    renderPage()
    expect(await screen.findByText('Draft engagement')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '+ New Engagement' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Review board' })).toHaveLength(2)
  })

  it('offers an editor "+ New Engagement" but not Delete, which the API keeps for admins', async () => {
    role.rank = 'editor'
    vi.mocked(listEngagements).mockResolvedValue([ENGAGEMENT, DRAFT])
    renderPage()
    expect(await screen.findByText('Draft engagement')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '+ New Engagement' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument()
  })

  it('offers an admin both, Delete on a draft only', async () => {
    role.rank = 'admin'
    vi.mocked(listEngagements).mockResolvedValue([ENGAGEMENT, DRAFT])
    renderPage()
    expect(await screen.findByText('Draft engagement')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '+ New Engagement' })).toBeEnabled()
    expect(screen.getAllByRole('button', { name: 'Delete' })).toHaveLength(1)
  })

  it('names no missing role and offers no New Engagement while the role is not yet known', async () => {
    role.rank = 'unknown'
    vi.mocked(listEngagements).mockResolvedValue([])
    renderPage()
    expect(await screen.findByText('No engagements yet')).toBeInTheDocument()
    expect(screen.queryByText(/needs the editor role/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '+ New Engagement' })).not.toBeInTheDocument()
  })

  it('does not invite a viewer to create the first engagement', async () => {
    role.rank = 'viewer'
    vi.mocked(listEngagements).mockResolvedValue([])
    renderPage()
    expect(await screen.findByText('Creating an engagement needs the editor role.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '+ New Engagement' })).not.toBeInTheDocument()
  })
})
