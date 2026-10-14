import { useState, type JSX } from 'react'
import { useIsOrgEditor } from '../../hooks/useHasOrgRole'
import { useControlValidations } from '../../hooks/useDueCare'
import { useOrgMembersQuery } from '../../hooks/useDeficiencies'
import { userLabel } from '../../data/userDisplay'
import { formatAuditDate, formatServerDay } from '../../utils/auditValue'
import ValidationFormModal from './ValidationFormModal'
import { VALIDATION_STATE_LABELS, validationStateHint } from './validationText'

interface Props {
  organizationId: string
  scfId: string
}

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err))

/* PIG R10: the control's validation schedule — its cadence, when it was last
   validated, by whom and with what note, and when the next is due — and a way
   for an editor to record that it has been validated. The server computes the
   state against today, so the schedule is read afresh on every visit. */
export default function ValidationPanel({ organizationId, scfId }: Props): JSX.Element {
  const canEdit = useIsOrgEditor(organizationId)
  const schedule = useControlValidations(organizationId, { scf_id: scfId })
  const members = useOrgMembersQuery(organizationId)
  const [recording, setRecording] = useState(false)

  const row = schedule.data?.controls.find((c) => c.scf_id === scfId.toUpperCase())
  // Who validated it, once the members are read. An API key acts as an account
  // that is not a member, as does someone who has since left.
  const by = row?.last_validated_by && members.data ? members.data.find((m) => m.id === row.last_validated_by) : undefined
  const byText = !row?.last_validated_by
    ? ''
    : members.data
      ? ` by ${by ? userLabel(by) : 'a non-member account'}`
      : members.isError
        ? ' by an account that could not be named (the member list did not load)'
        : ''

  return (
    <section className="gov-panel validation-panel" aria-label="Validation">
      <header className="gov-panel-header">
        <h3>Validation</h3>
        {canEdit && row && (
          <button type="button" className="btn-secondary btn-small" onClick={() => setRecording(true)}>
            Record validation
          </button>
        )}
      </header>
      {schedule.isError && schedule.data && (
        <div role="alert" className="gov-error">
          Could not refresh the validation schedule: {errorText(schedule.error)}
        </div>
      )}
      {schedule.isLoading ? (
        <p className="gov-muted">Loading…</p>
      ) : !schedule.data ? (
        <div role="alert" className="gov-error">
          Could not load the validation schedule: {errorText(schedule.error)}
        </div>
      ) : !row ? (
        <p className="gov-muted">This control is not in scope, so it has no validation schedule.</p>
      ) : (
        <>
          <div className="validation-state">
            <span className={`deficiency-chip validation-chip--${row.validation_state}`}>
              {VALIDATION_STATE_LABELS[row.validation_state]}
            </span>
            <span className="gov-muted">{validationStateHint(row)}</span>
          </div>
          <dl className="compensating-text validation-text">
            <dt>Cadence</dt>
            <dd>{row.validation_cadence ?? 'None set'}</dd>
            {row.last_validated_at && (
              <>
                <dt>Last validated</dt>
                <dd>
                  {formatServerDay(row.last_validated_at)}
                  {byText}
                </dd>
              </>
            )}
            <dt>Next due</dt>
            <dd>{row.next_validation_due ? formatAuditDate(row.next_validation_due) : 'No date'}</dd>
            {row.last_validation_note && (
              <>
                <dt>Note</dt>
                <dd>{row.last_validation_note}</dd>
              </>
            )}
          </dl>
        </>
      )}
      {!canEdit && row && !schedule.isError && (
        <p className="gov-muted">Only an editor or admin can record a validation.</p>
      )}
      {recording && row && (
        <ValidationFormModal
          organizationId={organizationId}
          scfId={row.scf_id}
          lastValidatedAt={row.last_validated_at}
          onClose={() => setRecording(false)}
        />
      )}
    </section>
  )
}
