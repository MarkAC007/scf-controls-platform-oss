/**
 * CompensatingControlFormModal — record a compensating control for a control,
 * or edit one (PIG R9).
 *
 * The risk it names must be one linked to the control (the catalogue's
 * mapping, or a custom risk linked to it) that the organisation has assessed;
 * the picker offers only those. Compensating for a material control is a
 * decision to live with the gap, so there the risk is required and must be
 * accepted with an approval in force: other choices are shown but disabled.
 * Like the server, an edit checks the risk only when it changes it, so a
 * record that names none can still be corrected without naming one.
 * The server checks all of this again, and a refusal shows in the alert.
 * What control it is for never changes.
 *
 * Mounted only while open, so every opening starts from the record (or blank).
 * It cannot be dismissed while a save is in flight, and Escape or a backdrop
 * click is ignored once anything is typed; Cancel or ✕ still close it.
 */
import { useId, useMemo, useRef, useState, type JSX } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import { createCompensatingControl, updateCompensatingControl } from '../../data/apiClient'
import { invalidateCompensatingControls, useControlLinkedRisks } from '../../hooks/useCompensatingControls'
import { useDialogFocus } from '../../hooks/useDialogFocus'
import { useModalDismiss } from '../../hooks/useModalDismiss'
import type {
  CompensatingControl,
  CompensatingControlCreate,
  CompensatingControlUpdate,
  EffectivenessRating,
  RiskAssessment,
} from '../../types'
import { acceptanceDisplay, approvalInForce } from '../RiskAcceptancePanel'
import { EFFECTIVENESS_LABELS, EFFECTIVENESS_RATINGS } from './compensatingText'

interface Props {
  organizationId: string
  scfId: string
  /** Edit this record; record a new one when absent. */
  record?: CompensatingControl
  /** Whether the control is material; undefined while unknown, when the server alone decides. */
  material: boolean | undefined
  onClose: () => void
}

interface FormValues {
  gap: string
  compensation: string
  effectiveness: EffectivenessRating
  riskCode: string
  notes: string
}

const fromRecord = (r?: CompensatingControl): FormValues => ({
  gap: r?.gap_description ?? '',
  compensation: r?.compensating_control ?? '',
  effectiveness: r?.effectiveness_rating ?? 'partial',
  riskCode: r?.risk_code ?? '',
  notes: r?.risk_reduction_notes ?? '',
})

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err))

/** The edit's PATCH body: the fields that changed, with a cleared risk or notes as null. */
function changedFields(initial: FormValues, values: FormValues): CompensatingControlUpdate {
  const body: CompensatingControlUpdate = {}
  const text = (v: string) => v.trim()
  if (text(values.gap) !== text(initial.gap)) body.gap_description = text(values.gap)
  if (text(values.compensation) !== text(initial.compensation)) body.compensating_control = text(values.compensation)
  if (values.effectiveness !== initial.effectiveness) body.effectiveness_rating = values.effectiveness
  if (values.riskCode !== initial.riskCode) body.risk_code = values.riskCode || null
  if (text(values.notes) !== text(initial.notes)) body.risk_reduction_notes = text(values.notes) || null
  return body
}

/** How a risk's acceptance reads in the picker. */
function acceptanceLabel(risk: RiskAssessment): string {
  switch (acceptanceDisplay(risk)?.kind) {
    case 'approved':
    case 'expiring':
      return 'accepted, approved'
    case 'expired':
      return 'acceptance expired'
    case 'unapproved':
      return 'accepted, not approved'
    default:
      return 'not accepted'
  }
}

export default function CompensatingControlFormModal({
  organizationId,
  scfId,
  record,
  material,
  onClose,
}: Props): JSX.Element {
  const queryClient = useQueryClient()
  const dialogRef = useRef<HTMLDivElement>(null)
  useDialogFocus(dialogRef)
  const id = useId()

  const editing = record !== undefined
  const initial = useMemo(() => fromRecord(record), [record])
  const [values, setValues] = useState<FormValues>(initial)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const linked = useControlLinkedRisks(organizationId, scfId)

  const set = <K extends keyof FormValues>(key: K, value: FormValues[K]) =>
    setValues((v) => ({ ...v, [key]: value }))

  const riskRequired = material === true
  const patch = editing ? changedFields(initial, values) : {}
  // Mirrors the server, which re-checks the risk on an edit only when the edit changes it.
  const keepsRisk = editing && values.riskCode === initial.riskCode
  const riskOk = !riskRequired || values.riskCode !== '' || keepsRisk
  const complete = values.gap.trim() !== '' && values.compensation.trim() !== '' && riskOk
  const canSubmit = complete && (!editing || Object.keys(patch).length > 0) && !busy
  const dirty = editing
    ? Object.keys(patch).length > 0
    : (Object.keys(initial) as (keyof FormValues)[]).some((k) => values[k] !== initial[k])

  const dismiss = () => {
    if (!busy) onClose()
  }
  const dismissIfClean = () => {
    if (!dirty) dismiss()
  }
  // Stays registered while busy or dirty, so Escape cannot reach the page behind.
  useModalDismiss(true, dismissIfClean)

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      if (editing) {
        await updateCompensatingControl(organizationId, record.id, patch)
        toast.success('Compensating control updated')
      } else {
        const body: CompensatingControlCreate = {
          scf_id: scfId,
          gap_description: values.gap.trim(),
          compensating_control: values.compensation.trim(),
          effectiveness_rating: values.effectiveness,
        }
        if (values.riskCode) body.risk_code = values.riskCode
        if (values.notes.trim()) body.risk_reduction_notes = values.notes.trim()
        await createCompensatingControl(organizationId, body)
        toast.success('Compensating control recorded')
      }
      await invalidateCompensatingControls(queryClient, organizationId)
      onClose()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  const heading = editing ? 'Edit compensating control' : 'Record compensating control'
  // A risk the record names that is no longer linked stays the visible choice; when a
  // read of the links failed, whether it is still linked is unknown, and it says so.
  const unlisted = values.riskCode && !linked.isLoading && !linked.risks.some((r) => r.risk_code === values.riskCode)
    ? values.riskCode
    : null
  const unlistedLabel = linked.error == null ? 'no longer linked to this control' : 'current choice (links could not be checked)'

  const noneAssessed = !linked.isLoading && !linked.error && linked.risks.length === 0
  const noneInForce = riskRequired && !linked.isLoading && !linked.error && !linked.risks.some((r) => approvalInForce(r))
  const keepsNoRisk = riskRequired && keepsRisk && values.riskCode === ''
  const riskHints = [
    `${id}-risk-hint`,
    linked.isLoading && `${id}-risk-loading`,
    noneAssessed && `${id}-risk-none`,
    noneInForce && `${id}-risk-no-approval`,
    keepsNoRisk && `${id}-risk-kept`,
    linked.error != null && `${id}-risk-error`,
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className="modal-overlay" onClick={dismissIfClean}>
      <div
        ref={dialogRef}
        className="modal-content deficiency-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-heading`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 id={`${id}-heading`}>{heading}</h2>
          <button type="button" className="modal-close" onClick={dismiss} disabled={busy} aria-label="Close">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (canSubmit) void submit()
          }}
        >
          <div className="modal-body">
            <div className="form-group">
              <span className="deficiency-form-label">Control</span>
              <p className="deficiency-subject">
                <span className="deficiency-subject-id">{scfId}</span>
              </p>
            </div>

            <div className="form-group">
              <label htmlFor={`${id}-gap`}>Gap</label>
              <textarea
                id={`${id}-gap`}
                className="form-control"
                rows={2}
                value={values.gap}
                placeholder="What the control does not meet"
                onChange={(e) => set('gap', e.target.value)}
                data-autofocus
              />
            </div>
            <div className="form-group">
              <label htmlFor={`${id}-compensation`}>Compensating control</label>
              <textarea
                id={`${id}-compensation`}
                className="form-control"
                rows={3}
                value={values.compensation}
                placeholder="What compensates for the gap"
                onChange={(e) => set('compensation', e.target.value)}
              />
            </div>

            <div className="deficiency-form-row">
              <div className="form-group">
                <label htmlFor={`${id}-effectiveness`}>Effectiveness</label>
                <select
                  id={`${id}-effectiveness`}
                  className="form-control"
                  value={values.effectiveness}
                  onChange={(e) => set('effectiveness', e.target.value as EffectivenessRating)}
                >
                  {EFFECTIVENESS_RATINGS.map((r) => (
                    <option key={r} value={r}>
                      {EFFECTIVENESS_LABELS[r]}
                    </option>
                  ))}
                </select>
              </div>
              <div className="form-group">
                <label htmlFor={`${id}-risk`}>
                  Risk{riskRequired && <span className="compensating-required"> (required)</span>}
                </label>
                <select
                  id={`${id}-risk`}
                  className="form-control"
                  value={values.riskCode}
                  onChange={(e) => set('riskCode', e.target.value)}
                  disabled={linked.isLoading}
                  aria-required={riskRequired || undefined}
                  aria-describedby={riskHints}
                >
                  <option value="">{riskRequired ? 'Choose an accepted risk' : 'None'}</option>
                  {unlisted && <option value={unlisted}>{`${unlisted} · ${unlistedLabel}`}</option>}
                  {linked.risks.map((r) => (
                    <option
                      key={r.risk_code}
                      value={r.risk_code}
                      disabled={riskRequired && !approvalInForce(r) && r.risk_code !== values.riskCode}
                    >
                      {`${r.risk_code} · ${acceptanceLabel(r)}`}
                    </option>
                  ))}
                </select>
                <p id={`${id}-risk-hint`} className="deficiency-field-hint">
                  {riskRequired
                    ? 'This control is material: name a risk linked to it that is accepted with an approval in force.'
                    : 'The risk this compensation addresses: one linked to this control that has been assessed.'}
                </p>
                {linked.isLoading && (
                  <p id={`${id}-risk-loading`} className="deficiency-field-hint">
                    Loading the risks linked to this control…
                  </p>
                )}
                {noneAssessed && (
                  <p id={`${id}-risk-none`} className="deficiency-field-hint">
                    No assessed risk is linked to this control.
                  </p>
                )}
                {noneInForce && (
                  <p id={`${id}-risk-no-approval`} className="deficiency-field-hint deficiency-field-hint--error">
                    None of the risks linked to this control is accepted with an approval in force. Accept one in the
                    Risk Register, or link one to this control, first.
                  </p>
                )}
                {keepsNoRisk && (
                  <p id={`${id}-risk-kept`} className="deficiency-field-hint">
                    Saving keeps this record not justified until an accepted risk is named.
                  </p>
                )}
                {linked.error != null && (
                  <p id={`${id}-risk-error`} className="deficiency-field-hint deficiency-field-hint--error">
                    Could not load the risks linked to this control: {errorText(linked.error)}
                  </p>
                )}
              </div>
            </div>

            <div className="form-group">
              <label htmlFor={`${id}-notes`}>Risk reduction notes</label>
              <textarea
                id={`${id}-notes`}
                className="form-control"
                rows={2}
                value={values.notes}
                placeholder="How much of the risk the compensation removes"
                onChange={(e) => set('notes', e.target.value)}
              />
            </div>

            {error && (
              <div role="alert" className="gov-error">
                {error}
              </div>
            )}
          </div>
          <div className="modal-footer">
            <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={!canSubmit}>
              {busy ? 'Saving…' : editing ? 'Save' : 'Record'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
