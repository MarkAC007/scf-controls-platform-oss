// SPDX-License-Identifier: AGPL-3.0-only
/** Record an evaluation of an objective's results (clause 9.1): a dated note. */
import { useId, useState, type JSX } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import { recordObjectiveEvaluation, type ObjectiveOut } from '../../../data/securityObjectivesApi'
import { invalidateSecurityObjectives } from '../../../hooks/useSecurityObjectives'
import ObjModal from './ObjModal'
import StatusChip from './StatusChip'
import { errorText, formatDateTime } from './objectiveText'

interface Props {
  organizationId: string
  objective: ObjectiveOut
  onClose: () => void
}

export default function EvaluationModal({ organizationId, objective, onClose }: Props): JSX.Element {
  const queryClient = useQueryClient()
  const id = useId()
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const o = objective

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      await recordObjectiveEvaluation(organizationId, o.id, note.trim())
      toast.success('Evaluation recorded')
      await invalidateSecurityObjectives(queryClient, organizationId)
      onClose()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <ObjModal
      title="Record evaluation"
      reference={o.ref}
      small
      busy={busy}
      dirty={note.trim() !== ''}
      canSubmit={note.trim() !== ''}
      submitLabel="Record evaluation"
      onSubmit={() => void submit()}
      onClose={onClose}
    >
      <dl className="obj-summary">
        <dt>Security objective</dt>
        <dd>{o.name}</dd>
        <dt>How results are evaluated</dt>
        <dd>{o.evaluation_method.trim() ? o.evaluation_method : <span className="obj-gap">Not written yet</span>}</dd>
        <dt>Now</dt>
        <dd>
          <b>{o.reading.value_text || '—'}</b>{' '}
          <StatusChip status={o.reading.status} daysLeft={o.reading.days_left} />
        </dd>
        {o.last_evaluation_note && (
          <>
            <dt>
              Last evaluated {formatDateTime(o.last_evaluated_at)}
              {o.last_evaluated_by_name ? ` by ${o.last_evaluated_by_name}` : ''}
            </dt>
            <dd>{o.last_evaluation_note}</dd>
          </>
        )}
      </dl>
      <div className="form-group">
        <label htmlFor={`${id}-note`}>What the results show</label>
        <textarea
          id={`${id}-note`}
          className="form-control"
          rows={4}
          maxLength={4000}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          data-autofocus
        />
      </div>
      {error && <div role="alert" className="gov-error">{error}</div>}
    </ObjModal>
  )
}
