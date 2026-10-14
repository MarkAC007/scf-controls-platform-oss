import { useState, type JSX } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import { createPostureSnapshot } from '../../data/apiClient'
import { invalidatePostureSnapshots, usePostureSnapshots } from '../../hooks/useDueCare'
import { useConfirm } from '../../contexts/ConfirmContext'
import { useIsOrgEditor } from '../../hooks/useHasOrgRole'
import type { PostureSnapshot } from '../../types'
import { formatServerDay } from '../../utils/auditValue'

interface Props {
  organizationId: string
  /** Opens Generated Documents, where the Governance Oversight Report is generated. */
  onOpenDocuments?: () => void
}

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err))

/** YYYY-MM. The server sends the month so; an older server sent its first day, which reads the same. */
const monthOf = (period: string) => period.slice(0, 7)

function monthLabel(period: string): string {
  const [year, month] = monthOf(period).split('-').map(Number)
  return new Date(year, month - 1, 1).toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
}

/** The month the server is in. It takes a snapshot for its UTC month, whatever the reader's clock says. */
function currentMonth(): string {
  const d = new Date()
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`
}

const statusLabel = (status: string) => {
  const words = status.replace(/_/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

function implemented(s: PostureSnapshot): string {
  const n = s.counts.status.implemented ?? 0
  return s.counts.in_scope ? `${n} (${Math.round((n / s.counts.in_scope) * 100)}%)` : `${n}`
}

/* PIG R10: the organisation's posture as it stood each month — taken by the
   schedule on the first, or by an editor sooner — so a reader can see what
   moved. A snapshot is a record: nothing changes it once taken, and a month
   has one. The Governance Oversight Report sets these figures against the
   snapshot a quarter ago; it is generated with the other documents. */
export default function PostureHistoryPanel({ organizationId, onOpenDocuments }: Props): JSX.Element {
  const queryClient = useQueryClient()
  const confirm = useConfirm()
  const canEdit = useIsOrgEditor(organizationId)
  const snapshots = usePostureSnapshots(organizationId)
  const [taking, setTaking] = useState(false)
  const [takeError, setTakeError] = useState<string | null>(null)

  const rows = snapshots.data ?? []
  const latest = rows[0]
  const month = currentMonth()
  const monthTaken = rows.some((s) => monthOf(s.period) === month)

  const take = async () => {
    const ok = await confirm({
      title: 'Take the posture snapshot',
      message: `Take the posture snapshot for ${monthLabel(month)} now? A month has one snapshot: it cannot be replaced or removed, and the schedule will not take another this month.`,
      confirmLabel: 'Take snapshot',
    })
    if (!ok) return
    setTaking(true)
    setTakeError(null)
    try {
      await createPostureSnapshot(organizationId)
      toast.success('Posture snapshot taken')
      await invalidatePostureSnapshots(queryClient, organizationId)
    } catch (err) {
      setTakeError(errorText(err))
      // A refusal is often a month taken unseen (409): read the list again so the offer goes.
      await invalidatePostureSnapshots(queryClient, organizationId)
    } finally {
      setTaking(false)
    }
  }

  return (
    <div className="dashboard-card posture-history">
      <div className="dashboard-card-header posture-history-header">
        <h3>Posture snapshots</h3>
        {canEdit && snapshots.data && !monthTaken && (
          <button type="button" className="btn-secondary btn-small" onClick={() => void take()} disabled={taking}>
            {taking ? 'Taking…' : "Take this month's snapshot"}
          </button>
        )}
      </div>
      {takeError && (
        <div role="alert" className="gov-error">
          {takeError}
        </div>
      )}
      {snapshots.isError && snapshots.data && (
        <div role="alert" className="gov-error">
          Could not refresh the posture snapshots: {errorText(snapshots.error)}
        </div>
      )}
      {snapshots.isLoading ? (
        <p className="dashboard-card-empty">Loading…</p>
      ) : !snapshots.data ? (
        <div role="alert" className="gov-error">
          Could not load the posture snapshots: {errorText(snapshots.error)}
        </div>
      ) : !latest ? (
        <p className="dashboard-card-empty">
          No posture snapshot yet. The schedule takes one on the first of each month.
        </p>
      ) : (
        <>
          <div className="posture-latest" role="group" aria-label={`Latest: ${monthLabel(latest.period)}`}>
            <p className="dashboard-card-note">
              <strong className="posture-latest-month">{monthLabel(latest.period)}</strong>
              {` · taken ${formatServerDay(latest.taken_at)} ${latest.trigger === 'scheduled' ? 'by the monthly schedule' : 'on request'}`}
            </p>
            <dl className="compensating-text posture-latest-figures">
              <dt>In scope</dt>
              <dd>
                <ul className="posture-status-list">
                  {/* JSONB keeps no key order, so the statuses are put in one here. */}
                  {Object.entries(latest.counts.status)
                    .sort(([a], [b]) => a.localeCompare(b))
                    .map(([status, n]) => (
                      <li key={status}>{`${statusLabel(status)}: ${n}`}</li>
                    ))}
                </ul>
              </dd>
              <dt>Basis</dt>
              <dd>{`MCR ${latest.counts.basis.mcr} · DSR ${latest.counts.basis.dsr} · none ${latest.counts.basis.none}`}</dd>
              <dt>Material</dt>
              <dd>{`${latest.counts.materiality.material} of ${latest.counts.in_scope}`}</dd>
              <dt>Deficiencies</dt>
              <dd>
                {`${latest.counts.deficiencies.open} open · ${latest.counts.deficiencies.material_open} material · ${latest.counts.deficiencies.overdue} overdue · ${latest.counts.deficiencies.without_due_date} without a due date`}
              </dd>
            </dl>
          </div>
          {/* Focusable, so the keyboard can scroll it where the columns overflow a phone. */}
          <div className="posture-table-wrap" tabIndex={0} role="region" aria-label="Posture by month, scrollable">
            <table className="posture-table" aria-label="Posture by month">
              <thead>
                <tr>
                  <th scope="col">Month</th>
                  <th scope="col">In scope</th>
                  <th scope="col">Implemented</th>
                  <th scope="col">Material</th>
                  <th scope="col">Open deficiencies</th>
                  <th scope="col">Overdue</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={s.id}>
                    <td>{monthLabel(s.period)}</td>
                    <td>{s.counts.in_scope}</td>
                    <td>{implemented(s)}</td>
                    <td>{s.counts.materiality.material}</td>
                    <td>{`${s.counts.deficiencies.open} (${s.counts.deficiencies.material_open} material)`}</td>
                    <td>{s.counts.deficiencies.overdue}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <p className="dashboard-card-note posture-report-note">
        The Governance Oversight Report sets these figures against the snapshot a quarter ago, with the red flags. It
        is generated under Generated Documents, in Reports and Registers.
      </p>
      {onOpenDocuments && (
        <button type="button" className="dashboard-card-link" onClick={onOpenDocuments}>
          Open Generated Documents
        </button>
      )}
    </div>
  )
}
