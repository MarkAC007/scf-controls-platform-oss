/**
 * FindingFields — one finding of a capability test, as typed (PIG R11).
 *
 * A finding is recorded as a deficiency, so it takes a deficiency's fields:
 * optionally a control in scope and a risk the organisation has assessed (the
 * server refuses any other with a 422), an owner, a due date, a description
 * and its root cause. Unlike a deficiency raised by hand it needs neither a
 * control nor a risk: the test is what it was found in.
 *
 * Several can sit in one form, so with a `legend` each field's accessible name
 * starts with it ("Finding 2 Title"). Everything is read-only while the form
 * saves.
 */
import { useEffect, useId, useRef, type JSX } from 'react'
import { useAssessedRisks, useOrgMembersQuery } from '../../hooks/useDeficiencies'
import { useDebounce } from '../../hooks/useDebounce'
import { flattenScopedControlPages, useScopedControlsQuery } from '../../hooks/useScopedControlsQuery'
import type { ActionItemPriority, CapabilityTestFinding } from '../../types'
import { PRIORITIES, PRIORITY_LABELS } from '../deficiencies/deficiencyText'

export interface FindingDraft {
  title: string
  priority: ActionItemPriority
  scfId: string
  riskCode: string
  ownerUserId: string
  dueDate: string
  description: string
  rootCause: string
}

export const emptyFinding = (): FindingDraft => ({
  title: '',
  priority: 'medium',
  scfId: '',
  riskCode: '',
  ownerUserId: '',
  dueDate: '',
  description: '',
  rootCause: '',
})

/** Anything typed, so discarding it needs a confirmation. */
export const findingTouched = (f: FindingDraft): boolean => {
  const blank = emptyFinding()
  return (Object.keys(blank) as (keyof FindingDraft)[]).some((k) => f[k] !== blank[k])
}

/** The finding as the server takes it: trimmed, with blanks left out. */
export function findingBody(f: FindingDraft): CapabilityTestFinding {
  const body: CapabilityTestFinding = { title: f.title.trim(), priority: f.priority }
  if (f.scfId.trim()) body.scf_id = f.scfId.trim().toUpperCase()
  if (f.riskCode) body.risk_code = f.riskCode
  if (f.ownerUserId) body.owner_user_id = f.ownerUserId
  if (f.dueDate) body.due_date = f.dueDate
  if (f.description.trim()) body.description = f.description.trim()
  if (f.rootCause.trim()) body.root_cause = f.rootCause.trim()
  return body
}

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err))

interface Props {
  organizationId: string
  value: FindingDraft
  onChange: (next: FindingDraft) => void
  busy: boolean
  /** Names this finding among several, e.g. "Finding 2". */
  legend?: string
  /** Offer to drop this finding from the form; needs a legend. */
  onRemove?: () => void
  /** Focus the title when this finding appears. */
  autoFocus?: boolean
}

export default function FindingFields({ organizationId, value, onChange, busy, legend, onRemove, autoFocus }: Props): JSX.Element {
  const id = useId()
  const titleRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (autoFocus) titleRef.current?.focus()
    // Only as it appears: later renders must not pull focus back.
  }, [])
  const members = useOrgMembersQuery(organizationId)
  const risks = useAssessedRisks(organizationId)
  const controlSearch = useDebounce(value.scfId.trim(), 250)
  const searching = controlSearch.length >= 2
  const controls = useScopedControlsQuery({ search: controlSearch, scope_status: 'in_scope' }, organizationId, searching)
  const controlOptions = flattenScopedControlPages(controls.data?.pages).controls

  const set = <K extends keyof FindingDraft>(key: K, v: FindingDraft[K]) => onChange({ ...value, [key]: v })
  // With a legend, each name reads "<legend> <label>" so several findings stay distinguishable.
  const named = (field: string) =>
    legend ? { 'aria-labelledby': `${id}-legend ${id}-${field}-label` } : {}

  const fields = (
    <>
      <div className="form-group">
        <label id={`${id}-title-label`} htmlFor={`${id}-title`}>Title</label>
        <input
          ref={titleRef}
          id={`${id}-title`}
          type="text"
          className="form-control"
          value={value.title}
          maxLength={255}
          readOnly={busy}
          aria-required="true"
          onChange={(e) => set('title', e.target.value)}
          {...named('title')}
          {...(autoFocus ? { 'data-autofocus': true } : {})}
        />
      </div>

      <div className="deficiency-form-row">
        <div className="form-group">
          <label id={`${id}-control-label`} htmlFor={`${id}-control`}>Control</label>
          <input
            id={`${id}-control`}
            type="text"
            className="form-control"
            value={value.scfId}
            list={`${id}-controls`}
            placeholder="SCF id of a control in scope"
            autoComplete="off"
            readOnly={busy}
            onChange={(e) => set('scfId', e.target.value)}
            {...named('control')}
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
        <div className="form-group">
          <label id={`${id}-risk-label`} htmlFor={`${id}-risk`}>Risk</label>
          <select
            id={`${id}-risk`}
            className="form-control"
            value={value.riskCode}
            onChange={(e) => set('riskCode', e.target.value)}
            disabled={busy || !risks.isSuccess}
            {...named('risk')}
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
        </div>
      </div>

      <div className="deficiency-form-row">
        <div className="form-group">
          <label id={`${id}-priority-label`} htmlFor={`${id}-priority`}>Priority</label>
          <select
            id={`${id}-priority`}
            className="form-control"
            value={value.priority}
            onChange={(e) => set('priority', e.target.value as ActionItemPriority)}
            disabled={busy}
            {...named('priority')}
          >
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {PRIORITY_LABELS[p]}
              </option>
            ))}
          </select>
        </div>
        <div className="form-group">
          <label id={`${id}-owner-label`} htmlFor={`${id}-owner`}>Owner</label>
          <select
            id={`${id}-owner`}
            className="form-control"
            value={value.ownerUserId}
            onChange={(e) => set('ownerUserId', e.target.value)}
            disabled={busy || !members.isSuccess}
            {...named('owner')}
          >
            <option value="">Unassigned</option>
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
      </div>

      <div className="form-group">
        <label id={`${id}-due-label`} htmlFor={`${id}-due`}>Due date</label>
        <input
          id={`${id}-due`}
          type="date"
          className="form-control"
          value={value.dueDate}
          readOnly={busy}
          onChange={(e) => set('dueDate', e.target.value)}
          {...named('due')}
        />
      </div>
      <div className="form-group">
        <label id={`${id}-description-label`} htmlFor={`${id}-description`}>Description</label>
        <textarea
          id={`${id}-description`}
          className="form-control"
          rows={2}
          value={value.description}
          readOnly={busy}
          onChange={(e) => set('description', e.target.value)}
          {...named('description')}
        />
      </div>
      <div className="form-group">
        <label id={`${id}-root-cause-label`} htmlFor={`${id}-root-cause`}>Root cause</label>
        <textarea
          id={`${id}-root-cause`}
          className="form-control"
          rows={2}
          value={value.rootCause}
          placeholder="Why the finding arose"
          readOnly={busy}
          onChange={(e) => set('rootCause', e.target.value)}
          {...named('root-cause')}
        />
      </div>
    </>
  )

  if (!legend) return fields
  return (
    <fieldset className="capability-finding">
      <legend id={`${id}-legend`}>{legend}</legend>
      {onRemove && (
        <button type="button" className="btn-secondary btn-small" onClick={onRemove} disabled={busy}>
          Remove {legend.toLowerCase()}
        </button>
      )}
      {fields}
    </fieldset>
  )
}
