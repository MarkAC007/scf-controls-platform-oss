// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Log a value for an objective measured by hand: a number for the period, or
 * done / not done for a milestone. Results are never changed afterwards; a
 * correction is a newer entry.
 */
import { useId, useState, type JSX } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import { logMetricResult, type ObjectiveOut, type ResultCreate } from '../../../data/securityObjectivesApi'
import { invalidateSecurityObjectives } from '../../../hooks/useSecurityObjectives'
import ObjModal from './ObjModal'
import { errorText, formatDate, numberOrNull } from './objectiveText'

interface Props {
  organizationId: string
  objective: ObjectiveOut
  onClose: () => void
  /** The period to log for; defaults to the reading's current period (the Tasks page names its task's). */
  periodStart?: string
  /** Called once a value is stored, before `onClose`. */
  onLogged?: () => void
}

export default function LogValueModal({ organizationId, objective, onClose, periodStart: period, onLogged }: Props): JSX.Element {
  const queryClient = useQueryClient()
  const id = useId()
  const o = objective
  const metric = o.metric
  const milestone = metric?.kind === 'milestone'
  const [value, setValue] = useState('')
  const [done, setDone] = useState(o.reading.value === 1)
  const [periodStart, setPeriodStart] = useState(period ?? o.reading.current_period_start ?? '')
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const n = numberOrNull(value)
  let problem: string | null = null
  if (!milestone) {
    if (value.trim() === '') problem = 'Enter the value for the period.'
    else if (n === null) problem = 'The value must be a number.'
    else if (metric?.unit === 'percent' && (n < 0 || n > 100)) problem = 'A percentage must be between 0 and 100.'
    else if (metric?.unit === 'count' && n < 0) problem = 'A count cannot be below 0.'
  }
  const dirty = value.trim() !== '' || note.trim() !== '' || (milestone && done !== (o.reading.value === 1))
  // A milestone entry that repeats the current state with no note adds nothing; results are never deleted.
  const milestoneUnchanged = milestone && o.reading.value !== null && (done ? 1 : 0) === o.reading.value && note.trim() === ''

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const body: ResultCreate = { value: milestone ? (done ? 1 : 0) : n! }
      if (!milestone && periodStart) body.period_start = periodStart
      if (note.trim()) body.note = note.trim()
      await logMetricResult(organizationId, o.id, body)
      toast.success('Value logged')
      await invalidateSecurityObjectives(queryClient, organizationId)
      onLogged?.()
      onClose()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <ObjModal
      title="Log a value"
      reference={o.ref}
      small
      busy={busy}
      dirty={dirty}
      canSubmit={problem === null && !milestoneUnchanged}
      submitLabel="Log value"
      onSubmit={() => void submit()}
      onClose={onClose}
    >
      <dl className="obj-summary">
        <dt>Supporting ISMS metric</dt>
        <dd>{metric?.name || o.name}</dd>
        {(o.reading.target_label || metric?.target_text) && (
          <>
            <dt>Target</dt>
            <dd>{o.reading.target_label || metric?.target_text}</dd>
          </>
        )}
        {o.reading.measured_on && (
          <>
            <dt>Last entry</dt>
            <dd>
              {o.reading.value_text} · Last logged {formatDate(o.reading.measured_on)}
              {o.reading.logged_by_name ? ` by ${o.reading.logged_by_name}` : ''}
            </dd>
          </>
        )}
      </dl>
      {milestone ? (
        <div className="obj-field">
          <span className="deficiency-form-label">Milestone</span>
          <div className="view-toggle" role="group" aria-label="Milestone">
            <button type="button" className={!done ? 'active' : ''} aria-pressed={!done} onClick={() => setDone(false)}>
              Not done
            </button>
            <button type="button" className={done ? 'active' : ''} aria-pressed={done} onClick={() => setDone(true)}>
              Done
            </button>
          </div>
        </div>
      ) : (
        <div className="deficiency-form-row">
          <div className="form-group">
            <label htmlFor={`${id}-value`}>Value{metric?.unit === 'percent' ? ' (%)' : ''}</label>
            <input
              id={`${id}-value`}
              type="number"
              className="form-control"
              value={value}
              min={0}
              max={metric?.unit === 'percent' ? 100 : undefined}
              step="any"
              onChange={(e) => setValue(e.target.value)}
              data-autofocus
            />
          </div>
          <div className="form-group">
            <label htmlFor={`${id}-period`}>Period starting</label>
            <input
              id={`${id}-period`}
              type="date"
              className="form-control"
              value={periodStart}
              onChange={(e) => setPeriodStart(e.target.value)}
            />
          </div>
        </div>
      )}
      {!milestone && value.trim() !== '' && problem && (
        <p className="deficiency-field-hint deficiency-field-hint--error">{problem}</p>
      )}
      <div className="form-group">
        <label htmlFor={`${id}-note`}>Note <small className="obj-label-note">optional</small></label>
        <textarea
          id={`${id}-note`}
          className="form-control"
          rows={2}
          maxLength={4000}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </div>
      {error && <div role="alert" className="gov-error">{error}</div>}
    </ObjModal>
  )
}
