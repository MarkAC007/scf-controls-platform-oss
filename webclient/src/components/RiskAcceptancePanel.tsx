import { useEffect, useState, type JSX } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import { acceptRisk, withdrawRiskAcceptance } from '../data/apiClient'
import { useConfirm } from '../contexts/ConfirmContext'
import { useIsOrgAdmin } from '../hooks/useIsOrgAdmin'
import { JOURNEY_QUERY_ROOT } from '../hooks/useJourney'
import { formatAuditDate } from '../utils/auditValue'
import type { RiskAssessment, RiskAssessmentUpdate, UserSimple } from '../types'

/** An approval this close to its expiry is flagged as expiring. */
export const EXPIRING_SOON_DAYS = 30

export interface AcceptanceDisplay {
  kind: 'approved' | 'expiring' | 'expired' | 'unapproved'
  /** Days from today to the expiry date; negative once it has passed. */
  daysLeft: number | null
}

const pad = (n: number) => String(n).padStart(2, '0')
const localIsoDate = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
/** The next calendar day, by local date arithmetic: a DST night is not 24 hours. */
const nextDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1)

function daysUntil(isoDate: string, today: Date): number {
  const [y, m, d] = isoDate.split('-').map(Number)
  const start = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())
  return Math.round((Date.UTC(y, m - 1, d) - start) / 86_400_000)
}

/**
 * How to show an accepted risk's approval, or null when it is not accepted.
 *
 * The server's acceptance_state was computed when the register loaded; a page
 * left open past the expiry date still says 'approved', so the date wins.
 */
export function acceptanceDisplay(assessment: RiskAssessment, today: Date = new Date()): AcceptanceDisplay | null {
  if (assessment.treatment_status !== 'accepted') return null
  if (assessment.acceptance_state === 'unapproved' || !assessment.accepted_at || !assessment.accepted_by) {
    return { kind: 'unapproved', daysLeft: null }
  }
  if (!assessment.acceptance_expires_on) return { kind: 'expired', daysLeft: null }
  const daysLeft = daysUntil(assessment.acceptance_expires_on, today)
  if (assessment.acceptance_state === 'expired' || daysLeft < 0) return { kind: 'expired', daysLeft }
  if (daysLeft <= EXPIRING_SOON_DAYS) return { kind: 'expiring', daysLeft }
  return { kind: 'approved', daysLeft }
}

function badgeText(display: AcceptanceDisplay, expiresOn: string | null | undefined): string {
  switch (display.kind) {
    case 'unapproved':
      return 'Accepted · not approved'
    case 'expired':
      return 'Acceptance expired'
    case 'expiring':
      return display.daysLeft === 0
        ? 'Approved · expires today'
        : `Approved · expires in ${display.daysLeft} day${display.daysLeft === 1 ? '' : 's'}`
    default:
      return `Approved · expires ${expiresOn ? formatAuditDate(expiresOn) : ''}`
  }
}

/**
 * Whether the risk carries an approval still in force: the backend's
 * approval_in_force, from the browser's date.
 */
export function approvalInForce(assessment: RiskAssessment, today: Date = new Date()): boolean {
  const kind = acceptanceDisplay(assessment, today)?.kind
  return kind === 'approved' || kind === 'expiring'
}

/** The confirmation before a write the server answers by withdrawing an approval. */
export const withdrawApprovalMessage = (riskCode: string) =>
  `This withdraws the admin approval of ${riskCode}'s acceptance. Continue?`

const SCORE_FIELDS = ['likelihood', 'impact', 'residual_likelihood', 'residual_impact'] as const

/**
 * What an ordinary save will do to an approval in force, before it is sent
 * (the backend's after_ordinary_write): 'withdraw' it, 'keep' the status
 * Accepted because only an admin may withdraw, or nothing (null).
 */
export function saveEffectOnApproval(
  assessment: RiskAssessment,
  field: keyof RiskAssessmentUpdate,
  value: unknown,
  isAdmin: boolean,
): 'withdraw' | 'keep' | null {
  if (assessment.treatment_status !== 'accepted' || !approvalInForce(assessment)) return null
  if (field === 'treatment_status') {
    if (value === 'accepted') return null
    return isAdmin ? 'withdraw' : 'keep'
  }
  if ((SCORE_FIELDS as readonly string[]).includes(field)) {
    return (assessment as unknown as Record<string, unknown>)[field] === value ? null : 'withdraw'
  }
  return null
}

/** The message for a saved risk, from what was asked against what came back. */
export function saveOutcome(
  before: RiskAssessment | undefined,
  requested: RiskAssessmentUpdate,
  after: RiskAssessment,
): { ok: boolean; message: string } {
  if (
    before?.treatment_status === 'accepted' &&
    requested.treatment_status &&
    requested.treatment_status !== 'accepted' &&
    after.treatment_status === 'accepted'
  ) {
    return {
      ok: false,
      message: 'Status kept as Accepted: only an admin can withdraw an approved acceptance. Other changes saved.',
    }
  }
  if (before?.accepted_at && !after.accepted_at) {
    return after.treatment_status === 'accepted'
      ? { ok: true, message: 'Saved. Approval withdrawn: the scores changed.' }
      : { ok: true, message: 'Saved. Acceptance withdrawn: the risk is no longer accepted.' }
  }
  return { ok: true, message: 'Risk assessment saved' }
}

/** The approval/expiry chip for an accepted risk; nothing otherwise. */
export function RiskAcceptanceBadge({ assessment }: { assessment: RiskAssessment }): JSX.Element | null {
  const display = acceptanceDisplay(assessment)
  if (!display) return null
  return (
    <span className={`risk-acceptance-badge risk-acceptance-badge--${display.kind}`}>
      {badgeText(display, assessment.acceptance_expires_on)}
    </span>
  )
}

interface Props {
  organizationId: string
  assessment: RiskAssessment
  users: UserSimple[]
  /**
   * The detail page autosaves its whole form, treatment status included. A
   * save still pending from before a decision would overwrite it, so the
   * decision waits for it.
   */
  autosavePending?: boolean
  /** The server's record after a decision. */
  onChange: (updated: RiskAssessment) => void
  /** Re-read the risk: a refused decision may mean the record here is stale. */
  onRefresh?: (riskCode: string) => void
}

/* PIG R7: accepting a risk is a decision an admin answers for. It is recorded
   with the approver (the caller, never a payload field), a rationale and an
   expiry after today; renewing replaces it, withdrawing returns the risk to
   Analysed. Everyone sees the record; only an admin acts on it. */
export default function RiskAcceptancePanel({
  organizationId,
  assessment,
  users,
  autosavePending = false,
  onChange,
  onRefresh,
}: Props): JSX.Element {
  const queryClient = useQueryClient()
  const confirm = useConfirm()
  // An org API key acts for the user who created it, so API-key mode counts
  // as admin here. The server records the caller as approver and refuses a
  // caller with no user behind it; that 403 shows in the alert below.
  const canDecide = useIsOrgAdmin(organizationId)
  const [editing, setEditing] = useState(false)
  const [rationale, setRationale] = useState('')
  const [expiresOn, setExpiresOn] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const riskCode = assessment.risk_code

  // A rationale is written for one risk's decision; never carry it to another.
  useEffect(() => {
    setEditing(false)
    setRationale('')
    setExpiresOn('')
    setError(null)
  }, [riskCode, organizationId])

  const display = acceptanceDisplay(assessment)
  const displayKind = display?.kind ?? 'none'

  // A form opened for one decision is never submitted as another: when the
  // record changes underneath it (a link change, a refresh), it closes.
  useEffect(() => {
    setEditing(false)
    setRationale('')
    setExpiresOn('')
  }, [displayKind])

  const accepted = assessment.treatment_status === 'accepted'
  const now = new Date()
  const today = localIsoDate(now)
  const tomorrow = localIsoDate(nextDay(now))

  const decision =
    !display ? { open: 'Accept risk', submit: 'Record acceptance', done: 'accepted' }
    : display.kind === 'unapproved' ? { open: 'Approve acceptance', submit: 'Record approval', done: 'approved' }
    : { open: 'Renew acceptance', submit: 'Record renewal', done: 'renewed' }

  const approverName = (() => {
    if (!assessment.accepted_by) return null
    const approver = users.find((u) => u.id === assessment.accepted_by)
    return approver ? approver.display_name || approver.email : 'a former member'
  })()

  const valid = rationale.trim() !== '' && expiresOn > today

  const startDecision = () => {
    // Every decision starts blank: a renewal is a new decision, not the old one re-sent.
    setRationale('')
    setExpiresOn('')
    setError(null)
    setEditing(true)
  }

  const afterDecision = async (updated: RiskAssessment) => {
    onChange(updated)
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['risk-assessments', organizationId] }),
      // The journey gate counts accepted risks with no approval in force.
      queryClient.invalidateQueries({ queryKey: [JOURNEY_QUERY_ROOT, organizationId] }),
    ])
  }

  // A 400 or 404 can mean the record shown here is no longer the server's
  // (another admin withdrew it, say): re-read it so the panel stops offering it.
  const refreshIfStale = (err: unknown) => {
    const status = (err as { status?: number } | null)?.status
    if (status === 400 || status === 404) onRefresh?.(riskCode)
  }

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const updated = await acceptRisk(riskCode, { rationale: rationale.trim(), expires_on: expiresOn }, organizationId)
      setEditing(false)
      setRationale('')
      setExpiresOn('')
      await afterDecision(updated)
      toast.success(`${riskCode} acceptance ${decision.done}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not record the acceptance')
      refreshIfStale(err)
    } finally {
      setBusy(false)
    }
  }

  const withdraw = async () => {
    const ok = await confirm({
      title: 'Withdraw acceptance',
      message: `Withdraw the acceptance of ${riskCode}? It returns to Analysed and needs a new treatment decision.`,
      confirmLabel: 'Withdraw acceptance',
      destructive: true,
    })
    if (!ok) return
    setBusy(true)
    setError(null)
    try {
      const updated = await withdrawRiskAcceptance(riskCode, organizationId)
      setEditing(false)
      await afterDecision(updated)
      toast.success(`${riskCode} acceptance withdrawn`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not withdraw the acceptance')
      refreshIfStale(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="gov-panel risk-detail-page-section risk-acceptance-panel" aria-label="Risk acceptance">
      <header className="gov-panel-header">
        <h3>Risk acceptance</h3>
        {canDecide && !editing && (
          <div className="risk-acceptance-actions">
            <button type="button" className="btn-secondary btn-small" onClick={startDecision} disabled={busy}>
              {decision.open}
            </button>
            {accepted && (
              <button
                type="button"
                className="btn-danger btn-small"
                onClick={() => void withdraw()}
                disabled={busy || autosavePending}
              >
                Withdraw acceptance
              </button>
            )}
          </div>
        )}
      </header>

      {!display ? (
        <p className="gov-muted">
          This risk is not accepted.
          {canDecide && ' Accepting it records you as the approver, with a rationale and an expiry date.'}
        </p>
      ) : (
        <div className="gov-panel-body">
          <RiskAcceptanceBadge assessment={assessment} />
          {display.kind === 'unapproved' ? (
            <p className="gov-note">
              This risk is marked accepted, but no admin has approved it. An admin records the approval with a
              rationale and an expiry date.
            </p>
          ) : (
            <>
              <p className="gov-muted">
                Approved by <strong>{approverName}</strong>
                {assessment.accepted_at && <> on {formatAuditDate(assessment.accepted_at)}</>}
              </p>
              {assessment.acceptance_expires_on && (
                <p className="gov-muted">Expires on {formatAuditDate(assessment.acceptance_expires_on)}</p>
              )}
              {assessment.acceptance_rationale && (
                <p className="risk-acceptance-rationale">{assessment.acceptance_rationale}</p>
              )}
              {display.kind === 'expired' ? (
                <p className="gov-note">
                  This acceptance lapsed after{' '}
                  {assessment.acceptance_expires_on ? formatAuditDate(assessment.acceptance_expires_on) : 'its expiry'}.
                  Renew it with a new rationale and expiry, or withdraw it.
                </p>
              ) : (
                <p className="gov-muted">
                  Changing any likelihood or impact score, inherent or residual, withdraws this approval.
                </p>
              )}
            </>
          )}
        </div>
      )}

      {!canDecide && (
        <p className="gov-muted">Only an organisation admin can accept a risk or withdraw an acceptance.</p>
      )}

      {error && <div role="alert" className="gov-error">{error}</div>}

      {editing && (
        <form
          className="gov-form"
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          <label>
            Rationale
            <textarea
              className="form-control"
              rows={3}
              value={rationale}
              onChange={(event) => setRationale(event.target.value)}
              placeholder="Why the organisation accepts this risk"
            />
          </label>
          <label>
            Expires on
            <input
              type="date"
              className="form-control"
              min={tomorrow}
              value={expiresOn}
              onChange={(event) => setExpiresOn(event.target.value)}
            />
          </label>
          {autosavePending && <p className="gov-muted">Waiting for this page's changes to save…</p>}
          <div className="gov-form-actions">
            <button
              type="button"
              className="btn-secondary btn-small"
              onClick={() => {
                setEditing(false)
                setError(null)
              }}
              disabled={busy}
            >
              Cancel
            </button>
            <button type="submit" className="btn-primary btn-small" disabled={busy || !valid || autosavePending}>
              {busy ? 'Saving…' : decision.submit}
            </button>
          </div>
        </form>
      )}
    </section>
  )
}
