/**
 * Risk acceptance (PIG R7, backend #1138): an admin accepts a risk with a
 * rationale and an expiry, renews it or withdraws it; everyone sees who
 * approved it, until when, and whether it has lapsed.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ConfirmProvider, answerConfirm } from '../../__tests__/confirmDialog'
import RiskAcceptancePanel, {
  RiskAcceptanceBadge,
  acceptanceDisplay,
  saveEffectOnApproval,
  saveOutcome,
} from '../RiskAcceptancePanel'
import { acceptRisk, withdrawRiskAcceptance } from '../../data/apiClient'
import type { RiskAssessment, UserSimple } from '../../types'

vi.mock('../../data/apiClient', () => ({
  acceptRisk: vi.fn(),
  withdrawRiskAcceptance: vi.fn(),
}))

const adminState = vi.hoisted(() => ({ isAdmin: true, user: { db_id: 'user-1' } as { db_id: string } | null }))
vi.mock('../../hooks/useIsOrgAdmin', () => ({
  useIsOrgAdmin: () => adminState.isAdmin,
  default: () => adminState.isAdmin,
}))
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: adminState.user }),
}))
vi.mock('react-hot-toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
  default: { success: vi.fn(), error: vi.fn() },
}))

const mockAccept = vi.mocked(acceptRisk)
const mockWithdraw = vi.mocked(withdrawRiskAcceptance)

const users: UserSimple[] = [
  { id: 'user-1', display_name: 'Ada Admin', email: 'ada@example.com' },
  { id: 'user-2', email: 'owner@example.com' },
]

const analysed = {
  id: 'ra-1',
  organization_id: 'org-1',
  risk_code: 'R-GV-1',
  treatment_status: 'analysed',
  likelihood: 3,
  impact: 3,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  accepted_by: null,
  accepted_at: null,
  acceptance_rationale: null,
  acceptance_expires_on: null,
  acceptance_state: null,
} as RiskAssessment

const approved = {
  ...analysed,
  treatment_status: 'accepted',
  accepted_by: 'user-1',
  accepted_at: '2026-10-01T09:30:00',
  acceptance_rationale: 'Residual risk sits inside appetite',
  acceptance_expires_on: '2099-03-31',
  acceptance_state: 'approved',
} as RiskAssessment

let client: QueryClient

function renderPanel(props: Partial<React.ComponentProps<typeof RiskAcceptancePanel>> = {}) {
  const onChange = vi.fn()
  const utils = render(
    <QueryClientProvider client={client}>
      <RiskAcceptancePanel
        organizationId="org-1"
        assessment={analysed}
        users={users}
        onChange={onChange}
        {...props}
      />
    </QueryClientProvider>,
    // A wrapper, so the rerenders below keep the provider.
    { wrapper: ConfirmProvider },
  )
  return { ...utils, onChange }
}

beforeEach(() => {
  vi.clearAllMocks()
  adminState.isAdmin = true
  adminState.user = { db_id: 'user-1' }
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})

describe('acceptanceDisplay', () => {
  const today = new Date(2026, 9, 13)

  it('is null for a risk that is not accepted', () => {
    expect(acceptanceDisplay(analysed, today)).toBeNull()
  })

  it('reads an approval in force, and one inside 30 days of its expiry as expiring', () => {
    expect(acceptanceDisplay(approved, today)).toEqual({ kind: 'approved', daysLeft: expect.any(Number) })
    expect(acceptanceDisplay({ ...approved, acceptance_expires_on: '2026-10-20' }, today))
      .toEqual({ kind: 'expiring', daysLeft: 7 })
    expect(acceptanceDisplay({ ...approved, acceptance_expires_on: '2026-10-13' }, today))
      .toEqual({ kind: 'expiring', daysLeft: 0 })
  })

  it('reads a passed expiry as expired even when the loaded state still says approved', () => {
    expect(acceptanceDisplay({ ...approved, acceptance_expires_on: '2026-10-12' }, today))
      .toEqual({ kind: 'expired', daysLeft: -1 })
  })

  it('trusts the server when it says expired or unapproved', () => {
    expect(acceptanceDisplay({ ...approved, acceptance_state: 'expired' }, today)?.kind).toBe('expired')
    expect(acceptanceDisplay({ ...approved, acceptance_state: 'unapproved' }, today)?.kind).toBe('unapproved')
  })

  it('reads an accepted risk with no approver as unapproved', () => {
    const noApprover = { ...analysed, treatment_status: 'accepted', acceptance_state: 'unapproved' } as RiskAssessment
    expect(acceptanceDisplay(noApprover, today)).toEqual({ kind: 'unapproved', daysLeft: null })
  })
})

describe('RiskAcceptanceBadge', () => {
  it('renders nothing for a risk that is not accepted', () => {
    const { container } = render(<RiskAcceptanceBadge assessment={analysed} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('says approved, expired or not approved', () => {
    const { rerender } = render(<RiskAcceptanceBadge assessment={approved} />)
    expect(screen.getByText(/^Approved · expires/)).toBeInTheDocument()
    rerender(<RiskAcceptanceBadge assessment={{ ...approved, acceptance_expires_on: '2020-01-01', acceptance_state: 'expired' }} />)
    expect(screen.getByText('Acceptance expired')).toBeInTheDocument()
    rerender(<RiskAcceptanceBadge assessment={{ ...approved, accepted_by: null, accepted_at: null, acceptance_state: 'unapproved' }} />)
    expect(screen.getByText('Accepted · not approved')).toBeInTheDocument()
  })
})

describe('RiskAcceptancePanel — accept', () => {
  it('sends exactly the rationale and expiry, then hands back the server record and refreshes risk queries', async () => {
    const saved = { ...approved, acceptance_rationale: 'Residual risk sits inside appetite' }
    mockAccept.mockResolvedValue(saved)
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const { onChange } = renderPanel()

    fireEvent.click(screen.getByRole('button', { name: 'Accept risk' }))
    const submit = screen.getByRole('button', { name: 'Record acceptance' })
    expect(submit).toBeDisabled()

    fireEvent.change(screen.getByLabelText('Rationale'), { target: { value: '  Residual risk sits inside appetite  ' } })
    expect(submit).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Expires on'), { target: { value: '2099-03-31' } })
    expect(submit).toBeEnabled()
    fireEvent.click(submit)

    await waitFor(() => expect(onChange).toHaveBeenCalledWith(saved))
    expect(mockAccept).toHaveBeenCalledTimes(1)
    expect(mockAccept).toHaveBeenCalledWith(
      'R-GV-1',
      { rationale: 'Residual risk sits inside appetite', expires_on: '2099-03-31' },
      'org-1',
    )
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['risk-assessments', 'org-1'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['journey', 'org-1'] })
    expect(screen.queryByLabelText('Rationale')).toBeNull()
  })

  it('keeps the submit disabled for a blank rationale or an expiry that is not after today', () => {
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Accept risk' }))
    const submit = screen.getByRole('button', { name: 'Record acceptance' })
    fireEvent.change(screen.getByLabelText('Rationale'), { target: { value: '   ' } })
    fireEvent.change(screen.getByLabelText('Expires on'), { target: { value: '2099-03-31' } })
    expect(submit).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Rationale'), { target: { value: 'Inside appetite' } })
    fireEvent.change(screen.getByLabelText('Expires on'), { target: { value: '2020-01-01' } })
    expect(submit).toBeDisabled()
  })

  it('shows the API detail in an alert and keeps the form open when the server refuses', async () => {
    mockAccept.mockRejectedValue(new Error('Accepting a risk needs an expiry date after today'))
    const { onChange } = renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Accept risk' }))
    fireEvent.change(screen.getByLabelText('Rationale'), { target: { value: 'Inside appetite' } })
    fireEvent.change(screen.getByLabelText('Expires on'), { target: { value: '2099-03-31' } })
    fireEvent.click(screen.getByRole('button', { name: 'Record acceptance' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Accepting a risk needs an expiry date after today')
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Rationale')).toHaveValue('Inside appetite')
  })

  it('holds the submit while the page still has an autosave pending', () => {
    renderPanel({ autosavePending: true })
    fireEvent.click(screen.getByRole('button', { name: 'Accept risk' }))
    fireEvent.change(screen.getByLabelText('Rationale'), { target: { value: 'Inside appetite' } })
    fireEvent.change(screen.getByLabelText('Expires on'), { target: { value: '2099-03-31' } })
    expect(screen.getByRole('button', { name: 'Record acceptance' })).toBeDisabled()
  })

  it('drops an unsent rationale when the page moves to another risk', () => {
    const { rerender, onChange } = renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Accept risk' }))
    fireEvent.change(screen.getByLabelText('Rationale'), { target: { value: 'Written for R-GV-1' } })
    rerender(
      <QueryClientProvider client={client}>
        <RiskAcceptancePanel
          organizationId="org-1"
          assessment={{ ...analysed, id: 'ra-2', risk_code: 'R-GV-2' }}
          users={users}
          onChange={onChange}
        />
      </QueryClientProvider>,
    )
    expect(screen.queryByLabelText('Rationale')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Accept risk' }))
    expect(screen.getByLabelText('Rationale')).toHaveValue('')
  })
})

describe('RiskAcceptancePanel — an approval on record', () => {
  it('shows who approved it, when it expires and why', () => {
    renderPanel({ assessment: approved })
    expect(screen.getByText(/Approved by/)).toHaveTextContent('Approved by Ada Admin')
    expect(screen.getByText('Residual risk sits inside appetite')).toBeInTheDocument()
    expect(screen.getByText(/Expires on/)).toHaveTextContent('2099')
    expect(screen.getByText(/Changing any likelihood or impact score, inherent or residual, withdraws this approval/)).toBeInTheDocument()
  })

  it('names a former member when the approver is not in the member list', () => {
    renderPanel({ assessment: { ...approved, accepted_by: 'user-gone' } })
    expect(screen.getByText(/Approved by/)).toHaveTextContent('Approved by a former member')
  })

  it('renews with a blank rationale and expiry, never the previous decision', async () => {
    mockAccept.mockResolvedValue({ ...approved, acceptance_rationale: 'Renewed for another year' })
    const { onChange } = renderPanel({ assessment: approved })
    fireEvent.click(screen.getByRole('button', { name: 'Renew acceptance' }))
    expect(screen.getByLabelText('Rationale')).toHaveValue('')
    expect(screen.getByLabelText('Expires on')).toHaveValue('')
    fireEvent.change(screen.getByLabelText('Rationale'), { target: { value: 'Renewed for another year' } })
    fireEvent.change(screen.getByLabelText('Expires on'), { target: { value: '2099-12-31' } })
    fireEvent.click(screen.getByRole('button', { name: 'Record renewal' }))
    await waitFor(() => expect(onChange).toHaveBeenCalled())
    expect(mockAccept).toHaveBeenCalledWith(
      'R-GV-1',
      { rationale: 'Renewed for another year', expires_on: '2099-12-31' },
      'org-1',
    )
  })

  it('tells the reader an expired acceptance lapsed and offers renewal', () => {
    renderPanel({ assessment: { ...approved, acceptance_expires_on: '2020-01-31', acceptance_state: 'expired' } })
    expect(screen.getByText(/lapsed after/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Renew acceptance' })).toBeInTheDocument()
  })

  it('offers an admin approval of an acceptance nobody approved', () => {
    renderPanel({
      assessment: { ...approved, accepted_by: null, accepted_at: null, acceptance_rationale: null, acceptance_expires_on: null, acceptance_state: 'unapproved' },
    })
    expect(screen.getByText(/no admin has approved/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Approve acceptance' }))
    expect(screen.getByRole('button', { name: 'Record approval' })).toBeInTheDocument()
  })

  it('withdraws after a confirmation and hands back the server record', async () => {
    const back = { ...analysed }
    mockWithdraw.mockResolvedValue(back)
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const { onChange } = renderPanel({ assessment: approved })
    fireEvent.click(screen.getByRole('button', { name: 'Withdraw acceptance' }))
    expect(await answerConfirm(true)).toBe(
      'Withdraw the acceptance of R-GV-1? It returns to Analysed and needs a new treatment decision.',
    )
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(back))
    expect(mockWithdraw).toHaveBeenCalledWith('R-GV-1', 'org-1')
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['risk-assessments', 'org-1'] })
  })

  it('does not withdraw when the confirmation is declined', async () => {
    renderPanel({ assessment: approved })
    fireEvent.click(screen.getByRole('button', { name: 'Withdraw acceptance' }))
    await answerConfirm(false)
    expect(mockWithdraw).not.toHaveBeenCalled()
  })

  it('shows the API detail when a withdrawal is refused', async () => {
    mockWithdraw.mockRejectedValue(new Error("'R-GV-1' is not accepted"))
    renderPanel({ assessment: approved })
    fireEvent.click(screen.getByRole('button', { name: 'Withdraw acceptance' }))
    await answerConfirm(true)
    expect(await screen.findByRole('alert')).toHaveTextContent("'R-GV-1' is not accepted")
  })
})

describe('RiskAcceptancePanel — role gate', () => {
  it('shows a non-admin the record but no actions', () => {
    adminState.isAdmin = false
    renderPanel({ assessment: approved })
    expect(screen.getByText(/Approved by/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /renew|withdraw|accept/i })).toBeNull()
    expect(screen.getByText(/Only an organisation admin can accept a risk/)).toBeInTheDocument()
  })

  // API-key mode: useIsOrgAdmin counts it as admin and there is no signed-in
  // user. An org key acts for its creator, so the decision is offered and the
  // server's 403 for a key with no user behind it is the check.
  it('offers the decisions in API-key mode, with no signed-in user', () => {
    adminState.user = null
    renderPanel({ assessment: approved })
    expect(screen.getByRole('button', { name: 'Renew acceptance' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Withdraw acceptance' })).toBeEnabled()
    expect(screen.queryByText(/Only an organisation admin/)).toBeNull()
  })

  it("shows the server's 403 detail when a key with no user behind it accepts", async () => {
    adminState.user = null
    const detail = "Accepting a risk is recorded against an approving admin; use an admin's own credentials"
    mockAccept.mockRejectedValue(new Error(detail))
    const { onChange } = renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Accept risk' }))
    fireEvent.change(screen.getByLabelText('Rationale'), { target: { value: 'Inside appetite' } })
    fireEvent.change(screen.getByLabelText('Expires on'), { target: { value: '2099-03-31' } })
    fireEvent.click(screen.getByRole('button', { name: 'Record acceptance' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(detail)
    expect(mockAccept).toHaveBeenCalledWith('R-GV-1', { rationale: 'Inside appetite', expires_on: '2099-03-31' }, 'org-1')
    expect(onChange).not.toHaveBeenCalled()
  })

  it("shows the server's 403 detail when a key with no user behind it withdraws", async () => {
    adminState.user = null
    const detail = "Withdrawing a risk acceptance is recorded against an admin; use an admin's own credentials"
    mockWithdraw.mockRejectedValue(new Error(detail))
    renderPanel({ assessment: approved })
    fireEvent.click(screen.getByRole('button', { name: 'Withdraw acceptance' }))
    await answerConfirm(true)
    expect(await screen.findByRole('alert')).toHaveTextContent(detail)
  })
})

describe('RiskAcceptancePanel — expiry boundary on a fixed clock', () => {
  afterEach(() => vi.useRealTimers())

  const openAccept = () => {
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Accept risk' }))
    fireEvent.change(screen.getByLabelText('Rationale'), { target: { value: 'Inside appetite' } })
  }

  it('refuses today and allows tomorrow', () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 9, 13, 12, 0))
    openAccept()
    const expires = screen.getByLabelText('Expires on')
    const submit = screen.getByRole('button', { name: 'Record acceptance' })
    expect(expires).toHaveAttribute('min', '2026-10-14')
    fireEvent.change(expires, { target: { value: '2026-10-13' } })
    expect(submit).toBeDisabled()
    fireEvent.change(expires, { target: { value: '2026-10-14' } })
    expect(submit).toBeEnabled()
  })

  it('takes tomorrow from the calendar, not 24 hours, across a clock change', () => {
    // Late on the eve of the UK spring change: 24 hours on is the day after
    // tomorrow in Europe/London. Under a zone with no DST both agree.
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 2, 28, 23, 30))
    openAccept()
    expect(screen.getByLabelText('Expires on')).toHaveAttribute('min', '2026-03-29')
  })
})

describe('RiskAcceptancePanel — review fixes', () => {
  it('clears a refused decision\'s error on Cancel', async () => {
    mockAccept.mockRejectedValue(new Error('Accepting a risk needs an expiry date after today'))
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Accept risk' }))
    fireEvent.change(screen.getByLabelText('Rationale'), { target: { value: 'Inside appetite' } })
    fireEvent.change(screen.getByLabelText('Expires on'), { target: { value: '2099-03-31' } })
    fireEvent.click(screen.getByRole('button', { name: 'Record acceptance' }))
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('re-reads the risk when a decision is refused as stale (400), not when forbidden (403)', async () => {
    const onRefresh = vi.fn()
    mockWithdraw.mockRejectedValueOnce(Object.assign(new Error("'R-GV-1' is not accepted"), { status: 400 }))
    const { unmount } = renderPanel({ assessment: approved, onRefresh })
    fireEvent.click(screen.getByRole('button', { name: 'Withdraw acceptance' }))
    await answerConfirm(true)
    expect(await screen.findByRole('alert')).toHaveTextContent("'R-GV-1' is not accepted")
    expect(onRefresh).toHaveBeenCalledWith('R-GV-1')
    unmount()

    onRefresh.mockClear()
    mockWithdraw.mockRejectedValueOnce(Object.assign(new Error('Forbidden'), { status: 403 }))
    renderPanel({ assessment: approved, onRefresh })
    fireEvent.click(screen.getByRole('button', { name: 'Withdraw acceptance' }))
    await answerConfirm(true)
    await screen.findByRole('alert')
    expect(onRefresh).not.toHaveBeenCalled()
  })

  it('closes an open decision form when the record changes underneath it', () => {
    const { rerender, onChange } = renderPanel({ assessment: approved })
    fireEvent.click(screen.getByRole('button', { name: 'Renew acceptance' }))
    fireEvent.change(screen.getByLabelText('Rationale'), { target: { value: 'Renewal reason' } })
    rerender(
      <QueryClientProvider client={client}>
        <RiskAcceptancePanel
          organizationId="org-1"
          assessment={{ ...approved, accepted_at: null, accepted_by: null, acceptance_state: 'unapproved' } as RiskAssessment}
          users={users}
          onChange={onChange}
        />
      </QueryClientProvider>,
    )
    expect(screen.queryByLabelText('Rationale')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Approve acceptance' }))
    expect(screen.getByLabelText('Rationale')).toHaveValue('')
  })
})

describe('saveEffectOnApproval', () => {
  it('withdraws on a score change, inherent or residual', () => {
    expect(saveEffectOnApproval(approved, 'likelihood', 4, false)).toBe('withdraw')
    expect(saveEffectOnApproval(approved, 'residual_impact', 2, false)).toBe('withdraw')
    expect(saveEffectOnApproval(approved, 'likelihood', 3, false)).toBeNull()
  })

  it('withdraws on an admin status change and keeps the status for anyone else', () => {
    expect(saveEffectOnApproval(approved, 'treatment_status', 'treating', true)).toBe('withdraw')
    expect(saveEffectOnApproval(approved, 'treatment_status', 'treating', false)).toBe('keep')
    expect(saveEffectOnApproval(approved, 'treatment_status', 'accepted', false)).toBeNull()
  })

  it('does nothing without an approval in force, or for other fields', () => {
    const expired = { ...approved, acceptance_expires_on: '2020-01-01' } as RiskAssessment
    expect(saveEffectOnApproval(expired, 'likelihood', 4, true)).toBeNull()
    expect(saveEffectOnApproval(analysed, 'likelihood', 4, true)).toBeNull()
    expect(saveEffectOnApproval(approved, 'notes', 'x', false)).toBeNull()
  })
})

describe('saveOutcome', () => {
  it('reports a status the server kept as Accepted', () => {
    expect(saveOutcome(approved, { treatment_status: 'treating' }, approved)).toEqual({
      ok: false,
      message: 'Status kept as Accepted: only an admin can withdraw an approved acceptance. Other changes saved.',
    })
  })

  it('reports an approval withdrawn by a re-score, and an acceptance withdrawn by a status change', () => {
    const cleared = { ...approved, accepted_at: null, accepted_by: null } as RiskAssessment
    expect(saveOutcome(approved, { treatment_status: 'accepted', likelihood: 4 }, cleared).message)
      .toBe('Saved. Approval withdrawn: the scores changed.')
    expect(saveOutcome(approved, { treatment_status: 'treating' }, { ...cleared, treatment_status: 'treating' } as RiskAssessment).message)
      .toBe('Saved. Acceptance withdrawn: the risk is no longer accepted.')
  })

  it('says saved when nothing about the acceptance changed', () => {
    expect(saveOutcome(analysed, { notes: 'x' }, analysed)).toEqual({ ok: true, message: 'Risk assessment saved' })
  })
})
