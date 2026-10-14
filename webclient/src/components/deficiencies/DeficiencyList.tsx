/**
 * DeficiencyList — deficiencies as rows, with what an editor may do to each
 * (PIG R8). Shared by the Deficiencies page and a control's governance tab.
 *
 * Editing and completing open a form, which the page or panel mounts: a form
 * owned here would be lost, half typed, whenever a refetch fails or drops its
 * row from the filter and the list unmounts. Cancelling and reopening are
 * single writes, confirmed first: a cancelled deficiency stops counting, and
 * a reopened one loses its retest note and completion date. Nothing is
 * deleted. A refused write shows on its own row until the row changes. The
 * rules live in `deficiencyMove`, shared with the board (#1155).
 */
import type { JSX } from 'react'
import { isOpenDeficiency, isOverdue, localIsoDate } from '../../hooks/useDeficiencies'
import { formatAuditDate } from '../../utils/auditValue'
import type { Deficiency, UserSimple } from '../../types'
import AppLink from '../AppLink'
import { useDeficiencyMove } from './deficiencyMove'
import { PRIORITY_LABELS, STATUS_LABELS, ownerLabel, sourceLabel } from './deficiencyText'

interface Props {
  organizationId: string
  deficiencies: Deficiency[]
  /** Undefined while the members are not known. */
  users: UserSimple[] | undefined
  canEdit: boolean
  /** Name the control each row is raised against; off on that control's own page. */
  showControl?: boolean
  /** Link a capability test finding to its test; off on that test's own row. */
  showTest?: boolean
  onEdit: (d: Deficiency) => void
  onComplete: (d: Deficiency) => void
}

export default function DeficiencyList({
  organizationId,
  deficiencies,
  users,
  canEdit,
  showControl = true,
  showTest = true,
  onEdit,
  onComplete,
}: Props): JSX.Element {
  const { move, isBusy, errorFor, clearError } = useDeficiencyMove(organizationId, deficiencies, onComplete)
  const today = localIsoDate()

  const openForm = (d: Deficiency, open: (d: Deficiency) => void) => {
    clearError(d)
    open(d)
  }

  return (
    <ul className="deficiency-list">
        {deficiencies.map((d) => {
          const open = isOpenDeficiency(d)
          const overdue = isOverdue(d, today)
          const busy = isBusy(d)
          return (
            <li key={d.id} className="deficiency-row" data-testid={`deficiency-${d.id}`}>
              <div className="deficiency-row-head">
                <span className="deficiency-row-title">{d.title}</span>
                <span className={`deficiency-chip deficiency-chip--${d.status}`}>{STATUS_LABELS[d.status]}</span>
                <span className={`deficiency-chip deficiency-chip--${d.priority}`}>{PRIORITY_LABELS[d.priority]}</span>
                {overdue && <span className="deficiency-chip deficiency-chip--overdue">Overdue</span>}
              </div>
              <div className="deficiency-row-meta">
                {d.scf_id && showControl && (
                  <span>
                    Control{' '}
                    <AppLink to={{ kind: 'control', id: d.scf_id }} className="deficiency-subject-id">
                      {d.scf_id}
                    </AppLink>
                  </span>
                )}
                {d.risk_code && (
                  <span>
                    Risk{' '}
                    <AppLink to={{ kind: 'risk', id: d.risk_code }} className="deficiency-subject-id">
                      {d.risk_code}
                    </AppLink>
                  </span>
                )}
                <span>Owner: {ownerLabel(d, users)}</span>
                <span>Due: {d.due_date ? formatAuditDate(d.due_date) : 'none'}</span>
                {d.completed_date && <span>Completed {formatAuditDate(d.completed_date)}</span>}
                {d.capability_test_id && showTest ? (
                  <AppLink
                    to={{ kind: 'capability-test', id: d.capability_test_id }}
                    aria-label={`${sourceLabel(d)} "${d.title}": open its test`}
                  >
                    {sourceLabel(d)}
                  </AppLink>
                ) : (
                  <span>{sourceLabel(d)}</span>
                )}
              </div>
              {(d.description || d.root_cause || d.retest_note) && (
                <details className="deficiency-row-details">
                  <summary>Details</summary>
                  {d.description && <p><strong>Description.</strong> {d.description}</p>}
                  {d.root_cause && <p><strong>Root cause.</strong> {d.root_cause}</p>}
                  {d.retest_note && <p><strong>Retest note.</strong> {d.retest_note}</p>}
                </details>
              )}
              {canEdit && (
                <div className="deficiency-row-actions">
                  <button
                    type="button"
                    className="btn-secondary btn-small"
                    onClick={() => openForm(d, onEdit)}
                    disabled={busy}
                    aria-label={`Edit "${d.title}"`}
                  >
                    Edit
                  </button>
                  {open ? (
                    <>
                      <button
                        type="button"
                        className="btn-primary btn-small"
                        onClick={() => openForm(d, onComplete)}
                        disabled={busy}
                        aria-label={`Complete "${d.title}"`}
                      >
                        Complete
                      </button>
                      <button
                        type="button"
                        className="btn-danger btn-small"
                        onClick={() => void move(d, 'cancelled')}
                        disabled={busy}
                        aria-label={`Cancel deficiency "${d.title}"`}
                      >
                        Cancel deficiency
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      className="btn-secondary btn-small"
                      onClick={() => void move(d, 'open')}
                      disabled={busy}
                      aria-label={`Reopen "${d.title}"`}
                    >
                      Reopen
                    </button>
                  )}
                </div>
              )}
              {errorFor(d) && (
                <div role="alert" className="gov-error">
                  {errorFor(d)}
                </div>
              )}
            </li>
          )
        })}
    </ul>
  )
}
