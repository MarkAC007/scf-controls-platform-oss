// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Review and confirm a proposed objective (admin). Confirming needs a
 * responsible team; a live or logged metric also needs a target, and a
 * milestone a completion date. The same rules the server applies, checked
 * here first so the button says why it is not ready.
 */
import { useId, useState, type JSX } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import { confirmSecurityObjective, type ConfirmBody, type ObjectiveOut } from '../../../data/securityObjectivesApi'
import { invalidateSecurityObjectives, useTeamsQuery } from '../../../hooks/useSecurityObjectives'
import ObjModal from './ObjModal'
import { errorText, numberOrNull, validateTarget } from './objectiveText'

interface Props {
  organizationId: string
  objective: ObjectiveOut
  onClose: () => void
}

const asText = (n: number | null | undefined) => (n === null || n === undefined ? '' : String(n))

export default function ConfirmObjectiveModal({ organizationId, objective, onClose }: Props): JSX.Element {
  const queryClient = useQueryClient()
  const id = useId()
  const o = objective
  const metric = o.metric
  const kind = metric?.kind
  const needsTarget = kind === 'live' || kind === 'logged'
  const milestone = kind === 'milestone'
  const teams = useTeamsQuery(organizationId)

  const [teamId, setTeamId] = useState(o.owning_team_id ?? '')
  const [target, setTarget] = useState(asText(metric?.target_value))
  const [warn, setWarn] = useState(asText(metric?.warn_value))
  const [dueDate, setDueDate] = useState(o.due_date ?? '')
  const [reviewDate, setReviewDate] = useState(o.review_date ?? '')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const t = numberOrNull(target)
  const w = numberOrNull(warn)
  const direction = metric?.direction ?? 'at_least'
  const targetProblem = needsTarget ? validateTarget(metric?.unit ?? null, direction, target, warn) : null
  const blocker = !teamId
    ? 'Choose a responsible team before confirming'
    : needsTarget && t === null
      ? 'Set a target before confirming'
      : milestone && !dueDate
        ? 'Set a completion date before confirming'
        : targetProblem

  const body: ConfirmBody = {}
  if (teamId && teamId !== o.owning_team_id) body.owning_team_id = teamId
  if (needsTarget && t !== null && t !== metric?.target_value) body.target_value = t
  if (needsTarget && w !== (metric?.warn_value ?? null)) body.warn_value = w
  if (dueDate && dueDate !== o.due_date) body.due_date = dueDate
  if (reviewDate && reviewDate !== o.review_date) body.review_date = reviewDate

  const dirty = Object.keys(body).length > 0

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      await confirmSecurityObjective(organizationId, o.id, body)
      toast.success('Objective confirmed')
      await invalidateSecurityObjectives(queryClient, organizationId)
      onClose()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  const unitMark = metric?.unit === 'percent' ? '%' : ''
  const suggested = o.reading.target_label || metric?.target_text

  return (
    <ObjModal
      title="Review and confirm"
      reference={o.ref}
      small
      busy={busy}
      dirty={dirty}
      canSubmit={blocker === null}
      submitLabel="Confirm objective"
      onSubmit={() => void submit()}
      onClose={onClose}
    >
      <dl className="obj-summary">
        <dt>Security objective</dt>
        <dd>{o.name}</dd>
        <dt>Today</dt>
        <dd>
          {o.reading.value_text || '—'}
          {o.reading.population ? ` · ${o.reading.population}` : ''}
        </dd>
        {suggested && (
          <>
            <dt>Suggested target</dt>
            <dd>{suggested}</dd>
          </>
        )}
      </dl>
      <div className="form-group">
        <label htmlFor={`${id}-team`}>Responsible team</label>
        <select
          id={`${id}-team`}
          className="form-control"
          value={teamId}
          onChange={(e) => setTeamId(e.target.value)}
          // Left enabled while the teams load so initial focus can land on it.
          data-autofocus
        >
          <option value="">Choose a team</option>
          {teamId && teams.isSuccess && !teams.data.some((tm) => tm.id === teamId) && (
            <option value={teamId}>{o.owning_team_name || 'A team no longer listed'}</option>
          )}
          {(teams.data ?? []).map((tm) => (
            <option key={tm.id} value={tm.id}>{tm.name}</option>
          ))}
        </select>
        {teams.isError && (
          <p className="deficiency-field-hint deficiency-field-hint--error">Could not load the teams: {errorText(teams.error)}</p>
        )}
      </div>
      {needsTarget && (
        <div className="obj-target-row">
          <label className="obj-target-label" htmlFor={`${id}-target`}>
            Target ({direction === 'at_most' ? 'at most' : 'at least'})
          </label>
          <input
            id={`${id}-target`}
            type="number"
            className="form-control obj-target-input"
            value={target}
            step="any"
            onChange={(e) => setTarget(e.target.value)}
          />
          <span>{unitMark}</span>
          <label htmlFor={`${id}-warn`}>{direction === 'at_most' ? 'warn above' : 'warn below'}</label>
          <input
            id={`${id}-warn`}
            type="number"
            className="form-control obj-target-input"
            value={warn}
            step="any"
            onChange={(e) => setWarn(e.target.value)}
          />
          <span>{unitMark}</span>
        </div>
      )}
      {(milestone || o.objective_type === 'achieve') && (
        <div className="form-group">
          <label htmlFor={`${id}-due`}>Complete by</label>
          <input id={`${id}-due`} type="date" className="form-control" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
        </div>
      )}
      {o.objective_type === 'maintain' && !milestone && (
        <div className="form-group">
          <label htmlFor={`${id}-review`}>Review by <small className="obj-label-note">optional</small></label>
          <input id={`${id}-review`} type="date" className="form-control" value={reviewDate} onChange={(e) => setReviewDate(e.target.value)} />
        </div>
      )}
      {blocker && <p className="deficiency-field-hint deficiency-field-hint--error">{blocker}</p>}
      {error && <div role="alert" className="gov-error">{error}</div>}
    </ObjModal>
  )
}
