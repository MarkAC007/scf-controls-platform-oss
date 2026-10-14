/**
 * DeficiencyFormModal — raise a deficiency, or edit one (PIG R8).
 *
 * A deficiency is raised against a control in scope, a risk the organisation
 * has assessed, or both; the server refuses anything else with a 422, shown
 * in the alert. What it is raised against never changes afterwards, so the
 * edit form shows it read-only. Closing a deficiency is not an edit: it has
 * its own forms (complete, with a retest note; cancel; reopen).
 *
 * Mounted only while open, so every opening starts from the record (or blank).
 *
 * It cannot be dismissed while a save is in flight: the outcome, and any
 * refusal, would land on a form that has gone. Escape and a backdrop click
 * are also ignored once anything is typed, because Escape is how a control
 * suggestion list is closed; Cancel or ✕ still close it.
 */
import { useId, useMemo, useRef, useState, type JSX } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import { createDeficiency, updateDeficiency } from '../../data/apiClient'
import {
  invalidateDeficiencies,
  isOpenDeficiency,
  useAssessedRisks,
  useOrgMembersQuery,
} from '../../hooks/useDeficiencies'
import { useDebounce } from '../../hooks/useDebounce'
import { useDialogFocus } from '../../hooks/useDialogFocus'
import { useModalDismiss } from '../../hooks/useModalDismiss'
import { flattenScopedControlPages, useScopedControlsQuery } from '../../hooks/useScopedControlsQuery'
import type {
  ActionItemPriority,
  ActionItemStatus,
  Deficiency,
  DeficiencyCreate,
  DeficiencyUpdate,
} from '../../types'
import { PRIORITIES, PRIORITY_LABELS } from './deficiencyText'

interface Props {
  organizationId: string
  /** Edit this deficiency; raise a new one when absent. */
  deficiency?: Deficiency
  /** Raise against this control, fixed: the control's own detail page. */
  fixedScfId?: string
  onClose: () => void
}

interface FormValues {
  title: string
  priority: ActionItemPriority
  status: ActionItemStatus
  ownerUserId: string
  dueDate: string
  description: string
  rootCause: string
}

const fromRecord = (d?: Deficiency): FormValues => ({
  title: d?.title ?? '',
  priority: d?.priority ?? 'medium',
  status: d?.status ?? 'open',
  ownerUserId: d?.owner_user_id ?? '',
  dueDate: d?.due_date ?? '',
  description: d?.description ?? '',
  rootCause: d?.root_cause ?? '',
})

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err))

/** The edit's PATCH body: the fields that changed, with blanks as null. */
function changedFields(initial: FormValues, values: FormValues): DeficiencyUpdate {
  const body: DeficiencyUpdate = {}
  const text = (v: string) => v.trim()
  if (text(values.title) !== text(initial.title)) body.title = text(values.title)
  if (values.priority !== initial.priority) body.priority = values.priority
  if (values.status !== initial.status) body.status = values.status
  if (values.ownerUserId !== initial.ownerUserId) body.owner_user_id = values.ownerUserId || null
  if (values.dueDate !== initial.dueDate) body.due_date = values.dueDate || null
  if (text(values.description) !== text(initial.description)) body.description = text(values.description) || null
  if (text(values.rootCause) !== text(initial.rootCause)) body.root_cause = text(values.rootCause) || null
  return body
}

export default function DeficiencyFormModal({ organizationId, deficiency, fixedScfId, onClose }: Props): JSX.Element {
  const queryClient = useQueryClient()
  const dialogRef = useRef<HTMLDivElement>(null)
  useDialogFocus(dialogRef)
  const id = useId()

  const editing = deficiency !== undefined
  const initial = useMemo(() => fromRecord(deficiency), [deficiency])
  const [values, setValues] = useState<FormValues>(initial)
  const [scfId, setScfId] = useState(fixedScfId ?? '')
  const [riskCode, setRiskCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const members = useOrgMembersQuery(organizationId)
  const risks = useAssessedRisks(organizationId, !editing)
  const controlSearch = useDebounce(scfId.trim(), 250)
  const searching = !editing && !fixedScfId && controlSearch.length >= 2
  const controls = useScopedControlsQuery({ search: controlSearch, scope_status: 'in_scope' }, organizationId, searching)
  const controlOptions = flattenScopedControlPages(controls.data?.pages).controls

  const set = <K extends keyof FormValues>(key: K, value: FormValues[K]) =>
    setValues((v) => ({ ...v, [key]: value }))

  const patch = editing ? changedFields(initial, values) : {}
  const hasSubject = scfId.trim() !== '' || riskCode !== ''
  const canSubmit =
    values.title.trim() !== '' && (editing ? Object.keys(patch).length > 0 : hasSubject) && !busy
  const dirty = editing
    ? Object.keys(patch).length > 0
    : (Object.keys(initial) as (keyof FormValues)[]).some((k) => values[k] !== initial[k]) ||
      scfId !== (fixedScfId ?? '') ||
      riskCode !== ''

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
        await updateDeficiency(organizationId, deficiency.id, patch)
        toast.success('Deficiency updated')
      } else {
        const body: DeficiencyCreate = { title: values.title.trim(), priority: values.priority }
        if (scfId.trim()) body.scf_id = scfId.trim().toUpperCase()
        if (riskCode) body.risk_code = riskCode
        if (values.ownerUserId) body.owner_user_id = values.ownerUserId
        if (values.dueDate) body.due_date = values.dueDate
        if (values.description.trim()) body.description = values.description.trim()
        if (values.rootCause.trim()) body.root_cause = values.rootCause.trim()
        await createDeficiency(organizationId, body)
        toast.success('Deficiency raised')
      }
      await invalidateDeficiencies(queryClient, organizationId)
      onClose()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  const heading = editing ? 'Edit deficiency' : 'Raise deficiency'
  // Editing a closed deficiency changes its details, never its status.
  const statusEditable = editing && isOpenDeficiency(deficiency)

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
              <label htmlFor={`${id}-title`}>Title</label>
              <input
                id={`${id}-title`}
                type="text"
                className="form-control"
                value={values.title}
                maxLength={255}
                onChange={(e) => set('title', e.target.value)}
                data-autofocus
              />
            </div>

            {editing || fixedScfId ? (
              <div className="form-group">
                <span className="deficiency-form-label">Raised against</span>
                <p className="deficiency-subject">
                  {(editing ? deficiency.scf_id : fixedScfId) && (
                    <span className="deficiency-subject-id">{editing ? deficiency.scf_id : fixedScfId}</span>
                  )}
                  {editing && deficiency.risk_code && <span className="deficiency-subject-id">{deficiency.risk_code}</span>}
                  {editing && !deficiency.scf_id && !deficiency.risk_code && <span className="gov-muted">No control or risk</span>}
                </p>
              </div>
            ) : (
              <div className="form-group">
                <label htmlFor={`${id}-control`}>Control</label>
                <input
                  id={`${id}-control`}
                  type="text"
                  className="form-control"
                  value={scfId}
                  list={`${id}-controls`}
                  placeholder="SCF id of a control in scope"
                  autoComplete="off"
                  onChange={(e) => setScfId(e.target.value)}
                />
                <datalist id={`${id}-controls`}>
                  {controlOptions.map((c) => (
                    <option key={c.scf_id} value={c.scf_id}>
                      {c.control_name}
                    </option>
                  ))}
                </datalist>
                {searching && controls.isFetching && <p className="deficiency-field-hint">Searching controls…</p>}
                {searching && controls.isError && (
                  <p className="deficiency-field-hint deficiency-field-hint--error">
                    Could not search the controls: {errorText(controls.error)}
                  </p>
                )}
              </div>
            )}

            {!editing && (
              <div className="form-group">
                <label htmlFor={`${id}-risk`}>Risk</label>
                <select
                  id={`${id}-risk`}
                  className="form-control"
                  value={riskCode}
                  onChange={(e) => setRiskCode(e.target.value)}
                  disabled={!risks.isSuccess}
                >
                  <option value="">None</option>
                  {(risks.data ?? []).map((r) => (
                    <option key={r.risk_code} value={r.risk_code}>
                      {r.risk_code}
                    </option>
                  ))}
                </select>
                {risks.isLoading && <p className="deficiency-field-hint">Loading the assessed risks…</p>}
                {risks.isError && (
                  <p className="deficiency-field-hint deficiency-field-hint--error">
                    Could not load the assessed risks: {errorText(risks.error)}
                  </p>
                )}
                {!fixedScfId && <p className="deficiency-field-hint">Raise it against a control, a risk, or both.</p>}
              </div>
            )}

            <div className="deficiency-form-row">
              <div className="form-group">
                <label htmlFor={`${id}-priority`}>Priority</label>
                <select
                  id={`${id}-priority`}
                  className="form-control"
                  value={values.priority}
                  onChange={(e) => set('priority', e.target.value as ActionItemPriority)}
                >
                  {PRIORITIES.map((p) => (
                    <option key={p} value={p}>
                      {PRIORITY_LABELS[p]}
                    </option>
                  ))}
                </select>
              </div>
              {statusEditable && (
                <div className="form-group">
                  <label htmlFor={`${id}-status`}>Status</label>
                  <select
                    id={`${id}-status`}
                    className="form-control"
                    value={values.status}
                    onChange={(e) => set('status', e.target.value as ActionItemStatus)}
                  >
                    <option value="open">Open</option>
                    <option value="in_progress">In progress</option>
                  </select>
                </div>
              )}
            </div>

            <div className="deficiency-form-row">
              <div className="form-group">
                <label htmlFor={`${id}-owner`}>Owner</label>
                <select
                  id={`${id}-owner`}
                  className="form-control"
                  value={values.ownerUserId}
                  onChange={(e) => set('ownerUserId', e.target.value)}
                  disabled={!members.isSuccess}
                >
                  <option value="">Unassigned</option>
                  {/* A member who has since left still shows as the owner. */}
                  {values.ownerUserId && members.isSuccess && !members.data.some((u) => u.id === values.ownerUserId) && (
                    <option value={values.ownerUserId}>{deficiency?.owner_name || 'A former member'}</option>
                  )}
                  {(members.data ?? []).map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.display_name || u.email}
                    </option>
                  ))}
                </select>
                {members.isLoading && <p className="deficiency-field-hint">Loading the members…</p>}
                {members.isError && (
                  <p className="deficiency-field-hint deficiency-field-hint--error">
                    Could not load the members: {errorText(members.error)}
                  </p>
                )}
              </div>
              <div className="form-group">
                <label htmlFor={`${id}-due`}>Due date</label>
                <input
                  id={`${id}-due`}
                  type="date"
                  className="form-control"
                  value={values.dueDate}
                  onChange={(e) => set('dueDate', e.target.value)}
                />
              </div>
            </div>

            <div className="form-group">
              <label htmlFor={`${id}-description`}>Description</label>
              <textarea
                id={`${id}-description`}
                className="form-control"
                rows={3}
                value={values.description}
                onChange={(e) => set('description', e.target.value)}
              />
            </div>
            <div className="form-group">
              <label htmlFor={`${id}-root-cause`}>Root cause</label>
              <textarea
                id={`${id}-root-cause`}
                className="form-control"
                rows={2}
                value={values.rootCause}
                placeholder="Why the deficiency arose"
                onChange={(e) => set('rootCause', e.target.value)}
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
              {busy ? 'Saving…' : editing ? 'Save' : 'Raise deficiency'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
