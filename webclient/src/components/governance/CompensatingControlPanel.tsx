import { useState, type JSX } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import { deleteCompensatingControl } from '../../data/apiClient'
import {
  invalidateCompensatingControls,
  useCompensatingControls,
  useControlLinkedRisks,
} from '../../hooks/useCompensatingControls'
import { useConfirm } from '../../contexts/ConfirmContext'
import { useIsOrgEditor } from '../../hooks/useHasOrgRole'
import { flattenScopedControlPages, useScopedControlsQuery } from '../../hooks/useScopedControlsQuery'
import { formatAuditDate } from '../../utils/auditValue'
import type { CompensatingControl } from '../../types'
import AppLink from '../AppLink'
import CompensatingControlFormModal from './CompensatingControlFormModal'
import { EFFECTIVENESS_LABELS, acceptanceChip, withdrawMessage, type MissingAcceptance } from './compensatingText'

interface Props {
  organizationId: string
  scfId: string
}

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err))

/** A short, stable handle for a record, for its buttons' names and its row. */
const shortRef = (id: string) => id.slice(0, 8)

/** The subject of a row's buttons: its risk and short reference, not its free-text gap. */
const rowSubject = (cc: CompensatingControl) =>
  `${cc.risk_code ?? 'with no risk'} (ref ${shortRef(cc.id)})`

/* PIG R9: the compensating controls recorded against this control — the gap,
   what compensates for it, how well, and the risk it addresses — with where
   each stands now. On a material control a record needs an accepted risk with
   an approval in force; one whose acceptance has lapsed, been withdrawn or
   unlinked, or whose control became material since, is flagged. Editors record,
   edit and withdraw; a withdrawal deletes the record, so it is confirmed. */
export default function CompensatingControlPanel({ organizationId, scfId }: Props): JSX.Element {
  const queryClient = useQueryClient()
  const confirm = useConfirm()
  const canEdit = useIsOrgEditor(organizationId)
  const list = useCompensatingControls(organizationId, { scf_id: scfId })
  // The same lookup, and so the same cache, as the materiality panel above.
  const lookup = useScopedControlsQuery({ search: scfId, scope_status: 'all' }, organizationId)
  const control = flattenScopedControlPages(lookup.data?.pages).controls.find((c) => c.scf_id === scfId)
  const [recording, setRecording] = useState(false)
  // Owned here, not by the row, so a refetch cannot unmount a half-typed form.
  const [editing, setEditing] = useState<CompensatingControl | null>(null)
  // Per row: withdrawals of two records can be in flight at once.
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(new Set())
  const [rowErrors, setRowErrors] = useState<Readonly<Record<string, string>>>({})

  const rows = list.data ?? []
  // The list's reading is the server's, at the moment it was read; before any row, the scope lookup's.
  const material = rows[0]?.material ?? (control ? !!control.material : undefined)
  // The server reads no acceptance for an unlinked, unassessed or unaccepted risk alike;
  // the control's links tell them apart, so they are read only when a row needs it.
  const linked = useControlLinkedRisks(
    organizationId,
    scfId,
    rows.some((cc) => cc.risk_code && cc.risk_acceptance_state == null),
  )
  const missingAcceptance = (code: string): MissingAcceptance => {
    if (linked.isLoading || linked.error != null || !linked.linkedCodes) return 'unknown'
    if (!linked.linkedCodes.has(code)) return 'unlinked'
    return linked.risks.some((r) => r.risk_code === code) ? 'unaccepted' : 'unassessed'
  }

  const setBusy = (id: string, busy: boolean) =>
    setBusyIds((ids) => {
      const next = new Set(ids)
      if (busy) next.add(id)
      else next.delete(id)
      return next
    })
  const setRowError = (id: string, message: string | null) =>
    setRowErrors((errors) => {
      const next = { ...errors }
      if (message === null) delete next[id]
      else next[id] = message
      return next
    })

  const withdraw = async (cc: CompensatingControl) => {
    const ok = await confirm({
      title: 'Withdraw compensating control',
      message: withdrawMessage(cc.scf_id),
      confirmLabel: 'Withdraw',
      destructive: true,
    })
    if (!ok) return
    setBusy(cc.id, true)
    setRowError(cc.id, null)
    try {
      await deleteCompensatingControl(organizationId, cc.id)
      await invalidateCompensatingControls(queryClient, organizationId)
      toast.success('Compensating control withdrawn')
    } catch (err) {
      setRowError(cc.id, errorText(err))
    } finally {
      setBusy(cc.id, false)
    }
  }

  return (
    <section className="gov-panel compensating-panel" aria-label="Compensating controls">
      <header className="gov-panel-header">
        <h3>Compensating controls</h3>
        {canEdit && (
          <button type="button" className="btn-secondary btn-small" onClick={() => setRecording(true)}>
            Record compensating control
          </button>
        )}
      </header>
      {material && (
        <p className="gov-muted">
          This control is material: compensating for it needs an accepted risk linked to it, with an approval in force.
        </p>
      )}
      {list.isError && list.data && (
        <div role="alert" className="gov-error">
          Could not refresh the compensating controls: {errorText(list.error)}
        </div>
      )}
      {list.isLoading ? (
        <p className="gov-muted">Loading…</p>
      ) : !list.data ? (
        <div role="alert" className="gov-error">
          Could not load the compensating controls: {errorText(list.error)}
        </div>
      ) : rows.length === 0 ? (
        <p className="gov-muted">No compensating controls for this control.</p>
      ) : (
        <ul className="deficiency-list">
          {rows.map((cc) => {
            const chip = acceptanceChip(cc, cc.risk_code ? missingAcceptance(cc.risk_code) : undefined)
            const busy = busyIds.has(cc.id)
            const rowError = rowErrors[cc.id]
            return (
              <li key={cc.id} className="deficiency-row" data-testid={`compensating-${cc.id}`}>
                <div className="deficiency-row-head">
                  <span className={`deficiency-chip compensating-chip--${cc.effectiveness_rating}`}>
                    {EFFECTIVENESS_LABELS[cc.effectiveness_rating]} effectiveness
                  </span>
                  {chip.kind === 'none' ? (
                    <span className="deficiency-chip">{chip.text}</span>
                  ) : (
                    <span className={`risk-acceptance-badge risk-acceptance-badge--${chip.kind}`}>{chip.text}</span>
                  )}
                  {!cc.justified && <span className="deficiency-chip deficiency-chip--overdue">Not justified</span>}
                </div>
                <dl className="compensating-text">
                  <dt>Gap</dt>
                  <dd>{cc.gap_description}</dd>
                  <dt>Compensation</dt>
                  <dd>{cc.compensating_control}</dd>
                </dl>
                <div className="deficiency-row-meta">
                  {cc.risk_code && (
                    <span>
                      Risk{' '}
                      <AppLink to={{ kind: 'risk', id: cc.risk_code }} className="deficiency-subject-id">
                        {cc.risk_code}
                      </AppLink>
                    </span>
                  )}
                  <span>Recorded {formatAuditDate(cc.created_at)}</span>
                  <span className="compensating-ref">Ref {shortRef(cc.id)}</span>
                </div>
                {!cc.justified && (
                  <p className="gov-muted">
                    {cc.scf_id} is material: this compensation needs an accepted risk linked to it with an approval in
                    force.
                  </p>
                )}
                {cc.risk_reduction_notes && (
                  <details className="deficiency-row-details">
                    <summary>Risk reduction notes</summary>
                    <p>{cc.risk_reduction_notes}</p>
                  </details>
                )}
                {canEdit && (
                  <div className="deficiency-row-actions">
                    <button
                      type="button"
                      className="btn-secondary btn-small"
                      onClick={() => {
                        setRowError(cc.id, null)
                        setEditing(cc)
                      }}
                      disabled={busy}
                      aria-label={`Edit compensating control ${rowSubject(cc)}`}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="btn-danger btn-small"
                      onClick={() => void withdraw(cc)}
                      disabled={busy}
                      aria-label={`Withdraw compensating control ${rowSubject(cc)}`}
                    >
                      {busy ? 'Withdrawing…' : 'Withdraw'}
                    </button>
                  </div>
                )}
                {rowError && (
                  <div role="alert" className="gov-error">
                    {rowError}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
      {!canEdit && (
        <p className="gov-muted">Only an editor or admin can record or change a compensating control.</p>
      )}
      {recording && (
        <CompensatingControlFormModal
          organizationId={organizationId}
          scfId={scfId}
          material={material}
          onClose={() => setRecording(false)}
        />
      )}
      {editing && (
        <CompensatingControlFormModal
          key={editing.id}
          organizationId={organizationId}
          scfId={scfId}
          record={editing}
          material={material}
          onClose={() => setEditing(null)}
        />
      )}
    </section>
  )
}
