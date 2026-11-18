// SPDX-License-Identifier: AGPL-3.0-only
/**
 * The objectives register's table: the contract's nine columns, in order.
 * Empty cells say what is missing, in the warning colour. Row actions sit
 * under the objective's name so the nine columns stay exactly nine.
 */
import { useEffect, useRef, type JSX, type MutableRefObject } from 'react'
import type { ObjectiveOut } from '../../../data/securityObjectivesApi'
import ClauseChips from './ClauseChips'
import StatusChip from './StatusChip'
import { completeBy, isClosed } from './objectiveText'

export interface RowError {
  id: string
  message: string
}

interface Props {
  items: ObjectiveOut[]
  canEdit: boolean
  isAdmin: boolean
  focusObjectiveId?: string | null
  rowError: RowError | null
  /**
   * The objective already scrolled to. Owned by the register so it survives
   * the table remounting while the list reloads (e.g. "Show archived").
   */
  scrolledTo: MutableRefObject<string | null>
  onEdit: (o: ObjectiveOut) => void
  onConfirm: (o: ObjectiveOut) => void
  onLog: (o: ObjectiveOut) => void
  onEvaluate: (o: ObjectiveOut) => void
  onArchive: (o: ObjectiveOut) => void
  onRestore: (o: ObjectiveOut) => void
}

const COLUMNS: [string, string][] = [
  ['ref', 'Ref'],
  ['name', 'Security objective'],
  ['actions', 'What will be done'],
  ['resources', 'Resources required'],
  ['team', 'Responsible team'],
  ['complete', 'Complete by'],
  ['evaluation', 'How results are evaluated'],
  ['metric', 'Supporting ISMS metric'],
  ['now', 'Now'],
]

const Gap = ({ children }: { children: string }) => <span className="obj-gap">{children}</span>

function TextCell({ text }: { text: string }): JSX.Element {
  return <td>{text.trim() ? text : <Gap>Not written yet</Gap>}</td>
}

export default function ObjectivesTable({
  items, canEdit, isAdmin, focusObjectiveId, rowError, scrolledTo,
  onEdit, onConfirm, onLog, onEvaluate, onArchive, onRestore,
}: Props): JSX.Element {
  const rowRefs = useRef<Record<string, HTMLTableRowElement | null>>({})
  useEffect(() => {
    // Once per linked objective: adding a row or showing archived ones must not jump back.
    if (!focusObjectiveId || scrolledTo.current === focusObjectiveId) return
    const row = rowRefs.current[focusObjectiveId]
    if (!row) return
    scrolledTo.current = focusObjectiveId
    // Optional call: jsdom has no scrollIntoView.
    row.scrollIntoView?.({ block: 'center' })
  }, [focusObjectiveId, items.length, scrolledTo])

  return (
    <div className="obj-table-wrap">
      <table className="obj-table">
        <colgroup>
          {COLUMNS.map(([key]) => <col key={key} className={`obj-col-${key}`} />)}
        </colgroup>
        <thead>
          <tr>
            {COLUMNS.map(([key, label]) => <th key={key} scope="col">{label}</th>)}
          </tr>
        </thead>
        <tbody>
          {items.map((o) => {
            const proposed = o.lifecycle_status === 'proposed'
            // Same rule on the server: archive and restore of a confirmed objective are admin-only.
            const canArchive = isAdmin || proposed
            const archived = o.archived_at !== null
            const kind = o.metric?.kind
            const noMetric = !o.metric || kind === 'unset'
            const when = completeBy(o)
            const classes = [
              'obj-row',
              proposed ? 'obj-row--proposed' : '',
              archived ? 'obj-row--archived' : '',
              o.id === focusObjectiveId ? 'obj-row--focus' : '',
            ].filter(Boolean).join(' ')
            return (
              <tr
                key={o.id}
                ref={(el) => { rowRefs.current[o.id] = el }}
                className={classes}
                data-testid={`objective-${o.id}`}
              >
                <td className="obj-cell-ref"><span className="obj-ref">{o.ref}</span></td>
                <td className="obj-cell-name">
                  <strong>{o.name}</strong>
                  {proposed && <span className="obj-tag">Proposed</span>}
                  {archived && <span className="obj-tag">Archived</span>}
                  <ClauseChips clauses={o.iso_clauses} />
                  {canEdit && (
                    <div className="obj-row-actions">
                      {!archived && (
                        <>
                          <button type="button" className="btn-link" aria-label={`Edit ${o.ref}`} onClick={() => onEdit(o)}>Edit</button>
                          {(kind === 'logged' || kind === 'milestone') && !isClosed(o) && (
                            <button type="button" className="btn-link" aria-label={`Log value ${o.ref}`} onClick={() => onLog(o)}>Log value</button>
                          )}
                          <button type="button" className="btn-link" aria-label={`Record evaluation ${o.ref}`} onClick={() => onEvaluate(o)}>Record evaluation</button>
                          {isAdmin && proposed && (
                            <button type="button" className="btn-link" aria-label={`Review and confirm ${o.ref}`} onClick={() => onConfirm(o)}>Review and confirm</button>
                          )}
                          {canArchive && (
                            <button type="button" className="btn-link" aria-label={`Archive ${o.ref}`} onClick={() => onArchive(o)}>Archive</button>
                          )}
                        </>
                      )}
                      {archived && canArchive && (
                        <button type="button" className="btn-link" aria-label={`Restore ${o.ref}`} onClick={() => onRestore(o)}>Restore</button>
                      )}
                    </div>
                  )}
                  {rowError?.id === o.id && (
                    <p className="deficiency-field-hint deficiency-field-hint--error" role="alert">{rowError.message}</p>
                  )}
                </td>
                <TextCell text={o.planned_actions} />
                <TextCell text={o.resources} />
                <td>
                  {o.owning_team_name ? (
                    <>
                      {o.owning_team_name}
                      {o.owner_name && <span className="obj-cell-sub">{o.owner_name}</span>}
                    </>
                  ) : o.owner_text ? (
                    <>
                      <Gap>{`“${o.owner_text}” (text)`}</Gap>
                      {canEdit && !archived && (
                        <button type="button" className="btn-link obj-cell-sub" aria-label={`Choose a team ${o.ref}`} onClick={() => onEdit(o)}>Choose a team</button>
                      )}
                    </>
                  ) : (
                    <Gap>No team yet</Gap>
                  )}
                </td>
                <td>
                  {when.gap ? <Gap>{when.main}</Gap> : when.main}
                  {when.sub && <span className="obj-cell-sub">{when.sub}</span>}
                </td>
                <TextCell text={o.evaluation_method} />
                <td>
                  {noMetric ? (
                    <>
                      <Gap>No metric</Gap>
                      {canEdit && !archived && (
                        <button type="button" className="btn-link obj-cell-sub" aria-label={`Choose a metric ${o.ref}`} onClick={() => onEdit(o)}>Choose a metric</button>
                      )}
                    </>
                  ) : (
                    <>
                      <strong>{o.metric!.name}</strong>
                      {o.metric!.description && <span className="obj-cell-sub">{o.metric!.description}</span>}
                    </>
                  )}
                </td>
                <td className="obj-cell-now">
                  <b className="obj-now-value">{o.reading.value_text || '—'}</b>
                  <StatusChip status={o.reading.status} daysLeft={o.reading.days_left} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
