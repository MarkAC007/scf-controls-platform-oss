// SPDX-License-Identifier: AGPL-3.0-only
/**
 * ObjectiveFormModal — create or edit a security objective (ISO/IEC 27001
 * clause 6.2) with its supporting ISMS metric.
 *
 * Left: the objective. Right: how it is measured. Picking a measure fills
 * the suggested target, the clauses and, through the wording endpoint, the
 * "How results are evaluated" text and the metric's name; a field the
 * person has typed in is never overwritten.
 *
 * Create saves as proposed; an admin may tick "Confirm now", which confirms
 * straight after the create. If that confirm is refused the objective stays
 * saved and the next submit retries the confirm only, never a second create.
 *
 * Edit sends only what changed: a PATCH for the objective's fields (skipped
 * when nothing changed there) and a PUT for the metric when it changed. Once
 * an objective is confirmed only an admin can change its metric.
 */
import { useEffect, useId, useMemo, useState, type JSX } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import {
  confirmSecurityObjective,
  createSecurityObjective,
  setObjectiveMetric,
  updateSecurityObjective,
  type Lifecycle,
  type LoggedFrequency,
  type MeasureOut,
  type MetricDirection,
  type MetricSpec,
  type ObjectiveCreate,
  type ObjectiveOut,
  type ObjectiveType,
  type ObjectiveUpdate,
  type WordingRequest,
} from '../../../data/securityObjectivesApi'
import { useDebounce } from '../../../hooks/useDebounce'
import { useOrgMembersQuery } from '../../../hooks/useDeficiencies'
import { useHasOrgRole } from '../../../hooks/useHasOrgRole'
import {
  invalidateSecurityObjectives,
  useIsmsMeasures,
  useMetricWording,
  useTeamsQuery,
} from '../../../hooks/useSecurityObjectives'
import ClauseChips from './ClauseChips'
import MeasurePicker, { type MeasureMode } from './MeasurePicker'
import ObjModal from './ObjModal'
import { errorText, formatDateTime, numberOrNull, validateTarget } from './objectiveText'

interface Props {
  organizationId: string
  objective?: ObjectiveOut
  onClose: () => void
}

interface FormValues {
  name: string
  plannedActions: string
  resources: string
  teamId: string
  ownerUserId: string
  type: ObjectiveType
  dueDate: string
  reviewDate: string
  clauses: string[]
  lifecycle: Lifecycle
  mode: MeasureMode
  measureKey: string
  params: Record<string, unknown>
  metricName: string
  metricDescription: string
  unit: '' | 'percent' | 'count'
  direction: MetricDirection
  target: string
  warn: string
  frequency: '' | LoggedFrequency
  evaluationMethod: string
}

const MAX_CLAUSES = 20
const MAX_CLAUSE_LENGTH = 20
const asText = (n: number | null | undefined) => (n === null || n === undefined ? '' : String(n))

function fromRecord(o?: ObjectiveOut): FormValues {
  const m = o?.metric
  const mode: MeasureMode = !m || m.kind === 'unset' ? 'none' : m.kind
  return {
    name: o?.name ?? '',
    plannedActions: o?.planned_actions ?? '',
    resources: o?.resources ?? '',
    teamId: o?.owning_team_id ?? '',
    ownerUserId: o?.owner_user_id ?? '',
    type: o?.objective_type ?? 'maintain',
    dueDate: o?.due_date ?? '',
    reviewDate: o?.review_date ?? '',
    clauses: o?.iso_clauses ?? [],
    lifecycle: o?.lifecycle_status ?? 'proposed',
    mode,
    measureKey: mode === 'live' ? m?.measure_key ?? '' : '',
    params: mode === 'live' ? m?.params ?? {} : {},
    metricName: mode === 'none' ? '' : m?.name ?? '',
    metricDescription: mode === 'logged' ? m?.description ?? '' : '',
    unit: mode === 'none' ? '' : m?.unit ?? '',
    direction: m?.direction ?? 'at_least',
    target: mode === 'none' ? '' : asText(m?.target_value),
    warn: mode === 'none' ? '' : asText(m?.warn_value),
    frequency: m?.frequency && m.frequency !== 'nightly' ? m.frequency : '',
    evaluationMethod: o?.evaluation_method ?? '',
  }
}

const METRIC_FIELDS: (keyof FormValues)[] = [
  'mode', 'measureKey', 'params', 'metricName', 'metricDescription', 'unit', 'direction', 'target', 'warn', 'frequency',
]
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

function metricSpec(v: FormValues): MetricSpec | null {
  if (v.mode === 'none') return null
  const spec: MetricSpec = { kind: v.mode, evaluation_method: v.evaluationMethod.trim() }
  if (v.mode === 'live') {
    spec.measure_key = v.measureKey
    spec.params = v.params
    if (v.metricName.trim()) spec.name = v.metricName.trim()
  }
  if (v.mode === 'logged') {
    spec.name = v.metricName.trim()
    if (v.unit) spec.unit = v.unit
    spec.direction = v.direction
    if (v.frequency) spec.frequency = v.frequency
    if (v.metricDescription.trim()) spec.description = v.metricDescription.trim()
  }
  if (v.mode === 'milestone' && v.metricName.trim()) spec.name = v.metricName.trim()
  if (v.mode === 'live' || v.mode === 'logged') {
    spec.target_value = numberOrNull(v.target)
    spec.warn_value = numberOrNull(v.warn)
  }
  return spec
}

function wordingRequest(v: FormValues, nameTouched: boolean): WordingRequest | null {
  if (v.mode === 'none' || (v.mode === 'live' && !v.measureKey)) return null
  const r: WordingRequest = { kind: v.mode }
  if (v.mode === 'live') {
    r.measure_key = v.measureKey
    r.params = v.params
  }
  if (nameTouched && v.metricName.trim()) r.name = v.metricName.trim()
  if (v.unit) r.unit = v.unit
  if (v.mode !== 'milestone') {
    r.direction = v.direction
    r.target_value = numberOrNull(v.target)
    r.warn_value = numberOrNull(v.warn)
  }
  if (v.mode === 'logged' && v.frequency) r.frequency = v.frequency
  return r
}

const dueFor = (v: FormValues) => (v.type === 'achieve' ? v.dueDate : '')
const reviewFor = (v: FormValues) => (v.type === 'maintain' ? v.reviewDate : '')
const UNTOUCHED = { evaluation: false, metricName: false, clauses: false, target: false }

/** The objective's own fields that changed, blanks as the API wants them. */
function changedFields(initial: FormValues, v: FormValues, withEvaluation: boolean): ObjectiveUpdate {
  const out: ObjectiveUpdate = {}
  if (v.name.trim() !== initial.name.trim()) out.name = v.name.trim()
  if (v.plannedActions !== initial.plannedActions) out.planned_actions = v.plannedActions.trim()
  if (v.resources !== initial.resources) out.resources = v.resources.trim()
  if (withEvaluation && v.evaluationMethod !== initial.evaluationMethod) out.evaluation_method = v.evaluationMethod.trim()
  if (v.type !== initial.type) out.objective_type = v.type
  if (v.teamId !== initial.teamId) out.owning_team_id = v.teamId || null
  if (v.ownerUserId !== initial.ownerUserId) out.owner_user_id = v.ownerUserId || null
  // Only the date the chosen type shows counts; a date hidden by switching type is not sent.
  if (dueFor(v) !== dueFor(initial)) out.due_date = dueFor(v) || null
  if (reviewFor(v) !== reviewFor(initial)) out.review_date = reviewFor(v) || null
  if (!same(v.clauses, initial.clauses)) out.iso_clauses = v.clauses
  if (v.lifecycle !== initial.lifecycle && initial.lifecycle !== 'proposed' && v.lifecycle !== 'proposed') {
    out.lifecycle_status = v.lifecycle
  }
  return out
}

export default function ObjectiveFormModal({ organizationId, objective, onClose }: Props): JSX.Element {
  const queryClient = useQueryClient()
  const id = useId()
  const isAdmin = useHasOrgRole(organizationId, 'admin')
  const editing = objective !== undefined
  const initial = useMemo(() => fromRecord(objective), [objective])

  const [values, setValues] = useState<FormValues>(initial)
  const [touched, setTouched] = useState({
    evaluation: editing && initial.evaluationMethod !== '',
    metricName: editing && initial.metricName !== '',
    clauses: editing && initial.clauses.length > 0,
    target: editing && initial.target !== '',
  })
  const [confirmNow, setConfirmNow] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [clauseDraft, setClauseDraft] = useState('')

  const set = <K extends keyof FormValues>(key: K, value: FormValues[K]) =>
    setValues((v) => ({ ...v, [key]: value }))

  const teams = useTeamsQuery(organizationId)
  const members = useOrgMembersQuery(organizationId)
  const searchText = useDebounce(values.name.trim(), 300)
  const measures = useIsmsMeasures(organizationId, searchText)

  // Debounce the request by its text form so a new object each render does not restart the timer.
  const requestText = JSON.stringify(wordingRequest(values, touched.metricName))
  const debouncedRequest = useDebounce(requestText, 250)
  const request = useMemo(() => JSON.parse(debouncedRequest) as WordingRequest | null, [debouncedRequest])
  const wording = useMetricWording(organizationId, request)
  const initialRequestText = useMemo(
    () => JSON.stringify(wordingRequest(initial, editing && initial.metricName !== '')),
    [initial, editing],
  )

  useEffect(() => {
    const data = wording.data
    if (!data || wording.isPlaceholderData) return
    // Edit: stored text that is exactly the generated sentence for the initial metric was
    // never written by a person, so it follows later replies (e.g. a new target).
    const generated =
      editing &&
      touched.evaluation &&
      debouncedRequest === initialRequestText &&
      values.evaluationMethod === initial.evaluationMethod &&
      data.evaluation_method === initial.evaluationMethod
    if (generated) setTouched((t) => ({ ...t, evaluation: false }))
    const followEvaluation = !touched.evaluation || generated
    setValues((v) => ({
      ...v,
      evaluationMethod: followEvaluation ? data.evaluation_method : v.evaluationMethod,
      metricName: touched.metricName ? v.metricName : data.metric_name,
    }))
    // Only a new reply refills; typing sets the flags and stops it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wording.data])

  const metricLocked = editing && objective.lifecycle_status !== 'proposed' && !isAdmin
  // Same rule on the server: type and due date of a confirmed objective are admin-only (403 otherwise).
  const scheduleLocked = metricLocked
  // And closing or reopening it (the Status select): archive and restore sit in the register.
  const lifecycleLocked = metricLocked
  const metricChanged = METRIC_FIELDS.some((k) => !same(values[k], initial[k]))
  const sendsMetric = metricChanged && !metricLocked && values.mode !== 'none'

  // A different measure or mode starts the metric afresh: what was typed or filled for the
  // old one (name, evaluation text, target, clauses) no longer describes it.
  const pickMeasure = (m: MeasureOut) => {
    // The first measure chosen keeps what was typed beforehand; switching away from one does not.
    const fresh = values.mode !== 'none' && (values.mode !== 'live' || values.measureKey !== m.key)
    const keep = fresh ? UNTOUCHED : touched
    if (fresh) setTouched(UNTOUCHED)
    setValues((v) => ({
      ...v,
      mode: 'live',
      measureKey: m.key,
      params: v.mode === 'live' && v.measureKey === m.key ? v.params : Object.fromEntries(m.params.map((p) => [p.key, p.default])),
      unit: m.unit,
      direction: m.direction,
      metricName: keep.metricName ? v.metricName : '',
      metricDescription: '',
      frequency: '',
      target: keep.target ? v.target : asText(m.suggested_target),
      warn: keep.target ? v.warn : asText(m.suggested_warn),
      clauses: keep.clauses ? v.clauses : m.clauses.slice(0, MAX_CLAUSES),
    }))
  }

  const pickMode = (mode: 'logged' | 'milestone') => {
    if (values.mode === mode) return
    if (values.mode !== 'none') setTouched(UNTOUCHED)
    setValues((v) => ({
      ...v,
      mode,
      measureKey: '',
      params: {},
      metricName: values.mode === 'none' ? v.metricName : '',
      metricDescription: '',
      unit: '',
      direction: 'at_least',
      frequency: '',
      type: mode === 'milestone' ? 'achieve' : v.type,
      target: '',
      warn: '',
    }))
  }

  const addClause = () => {
    const c = clauseDraft.trim()
    if (!c || c.length > MAX_CLAUSE_LENGTH || values.clauses.length >= MAX_CLAUSES || values.clauses.includes(c)) return
    set('clauses', [...values.clauses, c])
    setTouched((t) => ({ ...t, clauses: true }))
    setClauseDraft('')
  }

  const measured = values.mode === 'live' || values.mode === 'logged'
  const targetProblem = measured ? validateTarget(values.unit, values.direction, values.target, values.warn) : null
  const willConfirm = !editing && isAdmin && confirmNow
  let problem: string | null = null
  if (!values.name.trim()) problem = 'Name the security objective.'
  else if (values.mode === 'live' && !values.measureKey) problem = 'Choose a measure.'
  else if (values.mode === 'logged' && !values.metricName.trim()) problem = 'Name the metric.'
  else if (values.mode === 'logged' && !values.unit) problem = 'Choose a unit for the metric.'
  else if (values.mode === 'logged' && !values.frequency) problem = 'Choose how often the value is entered.'
  else if (targetProblem) problem = targetProblem
  else if (willConfirm && !values.teamId) problem = 'Choose a responsible team before confirming'
  else if (willConfirm && values.mode === 'none') problem = 'Choose a metric before confirming'
  else if (willConfirm && measured && numberOrNull(values.target) === null) problem = 'Set a target before confirming'
  else if (willConfirm && values.mode === 'milestone' && !values.dueDate) problem = 'Set a completion date before confirming'

  const dirty = !same(values, initial) || confirmNow

  const submitCreate = async () => {
    const spec = metricSpec(values)
    const body: ObjectiveCreate = {
      name: values.name.trim(),
      planned_actions: values.plannedActions.trim(),
      resources: values.resources.trim(),
      objective_type: values.type,
      owning_team_id: values.teamId || null,
      owner_user_id: values.ownerUserId || null,
      due_date: dueFor(values) || null,
      review_date: reviewFor(values) || null,
      iso_clauses: values.clauses,
    }
    // The evaluation text travels once: in the metric when there is one.
    if (spec) body.metric = spec
    else body.evaluation_method = values.evaluationMethod.trim()
    const saved = await createSecurityObjective(organizationId, body)
    if (willConfirm) {
      try {
        await confirmSecurityObjective(organizationId, saved.id, {})
        toast.success('Objective added and confirmed')
      } catch (err) {
        // The objective exists; a retry here would only resend the same refused confirm.
        toast.error(
          `The objective was saved as proposed but could not be confirmed: ${errorText(err)}. Confirm it from the register.`,
        )
      }
    } else {
      toast.success('Objective added')
    }
    await invalidateSecurityObjectives(queryClient, organizationId)
    onClose()
  }

  const submitEdit = async () => {
    const o = objective!
    const patch = changedFields(initial, values, !sendsMetric)
    if (Object.keys(patch).length > 0) await updateSecurityObjective(organizationId, o.id, patch)
    if (sendsMetric) await setObjectiveMetric(organizationId, o.id, metricSpec(values)!)
    toast.success('Objective updated')
    await invalidateSecurityObjectives(queryClient, organizationId)
    onClose()
  }

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      if (editing) await submitEdit()
      else await submitCreate()
    } catch (err) {
      setError(errorText(err))
      // An edit is PATCH then PUT: the first may have landed, so show the stored state.
      await invalidateSecurityObjectives(queryClient, organizationId)
    } finally {
      setBusy(false)
    }
  }

  const m = objective?.metric
  const footerNote = !editing
    ? 'Saved as proposed until an admin confirms the team and target.'
    : m?.target_set_by_name
      ? `Target set by ${m.target_set_by_name} · ${formatDateTime(m.target_set_at)}${
          objective?.reading.denominator !== null && objective?.reading.denominator !== undefined && m.min_population !== null
            ? ` · based on ${objective.reading.denominator} records (minimum ${m.min_population})`
            : ''
        }`
      : undefined

  const teamListed = (teams.data ?? []).some((t) => t.id === values.teamId)
  const unitMark = values.unit === 'percent' ? '%' : ''

  return (
    <ObjModal
      title={editing ? 'Edit security objective' : 'New security objective'}
      reference={editing ? objective.ref : undefined}
      aside="ISO/IEC 27001 clause 6.2"
      busy={busy}
      dirty={dirty}
      canSubmit={problem === null}
      submitLabel="Save objective"
      onSubmit={() => void submit()}
      onClose={onClose}
      bodyClassName="obj-form"
      footerNote={footerNote}
      footerExtra={
        !editing && isAdmin ? (
          <label className="obj-confirm-now">
            <input type="checkbox" checked={confirmNow} onChange={(e) => setConfirmNow(e.target.checked)} />
            Confirm now
          </label>
        ) : undefined
      }
    >
      <div className="obj-form-col">
        <h3 className="obj-form-heading">The objective</h3>
        <div className="form-group">
          <label htmlFor={`${id}-name`}>Security objective</label>
          <input
            id={`${id}-name`}
            className="form-control"
            maxLength={300}
            value={values.name}
            onChange={(e) => set('name', e.target.value)}
            data-autofocus
          />
        </div>
        <div className="form-group">
          <label htmlFor={`${id}-actions`}>What will be done</label>
          <textarea id={`${id}-actions`} className="form-control" rows={3} maxLength={4000}
            value={values.plannedActions} onChange={(e) => set('plannedActions', e.target.value)} />
        </div>
        <div className="form-group">
          <label htmlFor={`${id}-resources`}>Resources required</label>
          <textarea id={`${id}-resources`} className="form-control" rows={2} maxLength={4000}
            value={values.resources} onChange={(e) => set('resources', e.target.value)} />
        </div>
        <div className="deficiency-form-row">
          <div className="form-group">
            <label htmlFor={`${id}-team`}>Responsible team</label>
            <select id={`${id}-team`} className="form-control" value={values.teamId}
              onChange={(e) => set('teamId', e.target.value)}>
              <option value="">Choose a team</option>
              {values.teamId && !teamListed && (
                <option value={values.teamId}>{objective?.owning_team_name || 'A team no longer listed'}</option>
              )}
              {(teams.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            {objective?.owner_text && !values.teamId && (
              <p className="deficiency-field-hint">Previously “{objective.owner_text}”</p>
            )}
          </div>
          <div className="form-group">
            <label htmlFor={`${id}-person`}>Named person <small className="obj-label-note obj-label-note--plain">optional</small></label>
            <select id={`${id}-person`} className="form-control" value={values.ownerUserId}
              onChange={(e) => set('ownerUserId', e.target.value)}>
              <option value="">None</option>
              {values.ownerUserId && !(members.data ?? []).some((u) => u.id === values.ownerUserId) && (
                <option value={values.ownerUserId}>{objective?.owner_name || 'A person no longer listed'}</option>
              )}
              {(members.data ?? []).map((u) => <option key={u.id} value={u.id}>{u.display_name || u.email}</option>)}
            </select>
          </div>
        </div>
        <div className="deficiency-form-row">
          <div className="obj-field">
            <span className="deficiency-form-label obj-form-label">Type</span>
            <div className="view-toggle" role="group" aria-label="Type">
              <button type="button" className={values.type === 'maintain' ? 'active' : ''} disabled={scheduleLocked}
                aria-pressed={values.type === 'maintain'} onClick={() => set('type', 'maintain')}>Maintain</button>
              <button type="button" className={values.type === 'achieve' ? 'active' : ''} disabled={scheduleLocked}
                aria-pressed={values.type === 'achieve'} onClick={() => set('type', 'achieve')}>Achieve by a date</button>
            </div>
          </div>
          {values.type === 'achieve' ? (
            <div className="form-group">
              <label htmlFor={`${id}-due`}>Complete by</label>
              <input id={`${id}-due`} type="date" className="form-control" value={values.dueDate}
                disabled={scheduleLocked} onChange={(e) => set('dueDate', e.target.value)} />
            </div>
          ) : (
            <div className="form-group">
              <label htmlFor={`${id}-review`}>Review by</label>
              <input id={`${id}-review`} type="date" className="form-control" value={values.reviewDate}
                onChange={(e) => set('reviewDate', e.target.value)} />
            </div>
          )}
        </div>
        {scheduleLocked && (
          <p className="deficiency-field-hint">Only an admin can change the type or due date of a confirmed objective.</p>
        )}
        {editing && initial.lifecycle !== 'proposed' && (
          <div className="form-group">
            <label htmlFor={`${id}-status`}>Status</label>
            <select id={`${id}-status`} className="form-control" value={values.lifecycle}
              disabled={lifecycleLocked} onChange={(e) => set('lifecycle', e.target.value as Lifecycle)}>
              <option value="active">Active</option>
              <option value="achieved">Achieved</option>
              <option value="missed">Missed</option>
            </select>
            {lifecycleLocked && (
              <p className="deficiency-field-hint">Only an admin can archive, restore or close a confirmed objective.</p>
            )}
          </div>
        )}
        <div className="obj-field">
          <span className="deficiency-form-label obj-form-label">
            ISO 27001 clauses <small className="obj-label-note obj-label-note--plain">suggested from the measure</small>
          </span>
          <ClauseChips
            clauses={values.clauses}
            onRemove={(c) => {
              set('clauses', values.clauses.filter((x) => x !== c))
              setTouched((t) => ({ ...t, clauses: true }))
            }}
          />
          {values.clauses.length < MAX_CLAUSES && (
            <div className="obj-clause-add">
              <input
                className="form-control obj-clause-input"
                aria-label="Clause to add"
                maxLength={MAX_CLAUSE_LENGTH}
                value={clauseDraft}
                onChange={(e) => setClauseDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    addClause()
                  }
                }}
              />
              <button type="button" className="btn-link" onClick={addClause}>Add clause</button>
            </div>
          )}
        </div>
      </div>

      <div className="obj-form-col">
        <h3 className="obj-form-heading">How it will be measured</h3>
        <p className="gov-muted">Matched to your wording. Each value is worked out from this organisation’s records right now.</p>
        {editing && m?.kind === 'unset' && (m.description || m.target_text) && (
          <p className="deficiency-field-hint">
            Carried over from the old list: measure “{m.description}”, target “{m.target_text}”.
          </p>
        )}
        {metricLocked && (
          <p className="doc-notice doc-notice-warning">Only an admin can change the metric of a confirmed objective.</p>
        )}
        <fieldset className="obj-measure-fieldset" disabled={metricLocked} data-testid="metric-pane">
          <MeasurePicker
            measures={measures.data ?? []}
            loading={measures.isLoading}
            error={measures.isError ? errorText(measures.error) : null}
            mode={values.mode}
            measureKey={values.measureKey}
            params={values.params}
            onPickMeasure={pickMeasure}
            onParam={(key, value) => set('params', { ...values.params, [key]: value })}
            onMode={pickMode}
            disabled={metricLocked}
          />
          {values.mode === 'logged' && (
            <>
              <div className="deficiency-form-row">
                <div className="form-group">
                  <label htmlFor={`${id}-mname`}>Metric name</label>
                  <input id={`${id}-mname`} className="form-control" maxLength={300} value={values.metricName}
                    onChange={(e) => {
                      set('metricName', e.target.value)
                      setTouched((t) => ({ ...t, metricName: true }))
                    }} />
                </div>
                <div className="form-group">
                  <label htmlFor={`${id}-unit`}>Unit</label>
                  <select id={`${id}-unit`} className="form-control" value={values.unit}
                    onChange={(e) => set('unit', e.target.value as FormValues['unit'])}>
                    <option value="">Choose</option>
                    <option value="percent">Percent</option>
                    <option value="count">Count</option>
                  </select>
                </div>
              </div>
              <div className="form-group">
                <label htmlFor={`${id}-freq`}>How often it is entered</label>
                <select id={`${id}-freq`} className="form-control" value={values.frequency}
                  onChange={(e) => set('frequency', e.target.value as FormValues['frequency'])}>
                  <option value="">Choose</option>
                  <option value="monthly">Monthly</option>
                  <option value="quarterly">Quarterly</option>
                  <option value="annually">Annually</option>
                </select>
              </div>
              <div className="form-group">
                <label htmlFor={`${id}-mdesc`}>What is counted <small className="obj-label-note">optional</small></label>
                <textarea id={`${id}-mdesc`} className="form-control" rows={2} maxLength={4000}
                  value={values.metricDescription} onChange={(e) => set('metricDescription', e.target.value)} />
              </div>
            </>
          )}
          {measured && (
            <div className="obj-target-row">
              <span className="obj-target-label">Target</span>
              <select
                className="form-control obj-target-input"
                aria-label="Direction"
                value={values.direction}
                disabled={values.mode === 'live' || metricLocked}
                onChange={(e) => set('direction', e.target.value as MetricDirection)}
              >
                <option value="at_least">at least</option>
                <option value="at_most">at most</option>
              </select>
              <input
                type="number"
                className="form-control obj-target-input"
                aria-label="Target value"
                step="any"
                value={values.target}
                onChange={(e) => {
                  set('target', e.target.value)
                  setTouched((t) => ({ ...t, target: true }))
                }}
              />
              <span>{unitMark}</span>
              <span>{values.direction === 'at_most' ? 'warn above' : 'warn below'}</span>
              <input
                type="number"
                className="form-control obj-target-input"
                aria-label="Warning level"
                step="any"
                value={values.warn}
                onChange={(e) => {
                  set('warn', e.target.value)
                  setTouched((t) => ({ ...t, target: true }))
                }}
              />
              <span>{unitMark}</span>
              <span>{values.mode === 'live' ? '· measured nightly' : values.frequency ? `· logged ${values.frequency}` : ''}</span>
            </div>
          )}
          {targetProblem && <p className="deficiency-field-hint deficiency-field-hint--error">{targetProblem}</p>}
          {values.mode === 'milestone' && <p className="gov-muted">Done or not done by the due date.</p>}
        </fieldset>
        <div className="form-group">
          <label htmlFor={`${id}-eval`}>
            How results are evaluated <small className="obj-label-note">written from the measure · editable</small>
          </label>
          <textarea
            id={`${id}-eval`}
            className="form-control"
            rows={3}
            maxLength={4000}
            value={values.evaluationMethod}
            onChange={(e) => {
              set('evaluationMethod', e.target.value)
              setTouched((t) => ({ ...t, evaluation: true }))
            }}
          />
        </div>
        {values.mode !== 'logged' && values.mode !== 'none' && (
          <div className="form-group">
            <label htmlFor={`${id}-metric`}>
              Supporting ISMS metric <small className="obj-label-note">shown on the dashboard</small>
            </label>
            <input
              id={`${id}-metric`}
              className="form-control"
              maxLength={300}
              value={values.metricName}
              disabled={metricLocked}
              onChange={(e) => {
                set('metricName', e.target.value)
                setTouched((t) => ({ ...t, metricName: true }))
              }}
            />
            {wording.data?.metric_description && <p className="deficiency-field-hint">{wording.data.metric_description}</p>}
          </div>
        )}
        {problem && problem !== targetProblem && (values.name.trim() !== '' || confirmNow) && (
          <p className="deficiency-field-hint deficiency-field-hint--error">{problem}</p>
        )}
        {error && <div role="alert" className="gov-error">{error}</div>}
      </div>
    </ObjModal>
  )
}
