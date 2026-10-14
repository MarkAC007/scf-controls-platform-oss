/**
 * CapabilityTestFormModal — record a capability test with its findings, or
 * correct one (PIG R11).
 *
 * The server's two rules are checked here as it checks them, in its words:
 * the day it was run is not after the server's day, and the result agrees
 * with the findings (passed with none; passed with findings or failed with at
 * least one). A correction counts the findings already recorded, cancelled
 * ones excluded; findings are added to a test with its own action, which also
 * corrects a test recorded as passed. Anything else the server refuses (a
 * control not in scope, a risk not assessed) shows in the alert.
 *
 * Recording cannot be undone: a test is never deleted, and each finding
 * becomes a deficiency.
 *
 * Mounted only while open. It cannot be dismissed while a save is in flight,
 * and Escape or a backdrop click is ignored once anything is typed; Cancel or
 * ✕ still close it. Everything is read-only while it saves.
 */
import { useId, useMemo, useRef, useState, type JSX } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import { createCapabilityTest, updateCapabilityTest } from '../../data/apiClient'
import { latestTestDay } from '../../hooks/useCapabilityTests'
import { invalidateDeficiencies } from '../../hooks/useDeficiencies'
import { useConfirm } from '../../contexts/ConfirmContext'
import { useDialogFocus } from '../../hooks/useDialogFocus'
import { useModalDismiss } from '../../hooks/useModalDismiss'
import { formatAuditDate } from '../../utils/auditValue'
import type {
  CapabilityTest,
  CapabilityTestCreate,
  CapabilityTestResult,
  CapabilityTestType,
  CapabilityTestUpdate,
} from '../../types'
import {
  RECORD_WARNING,
  RESULTS,
  RESULT_LABELS,
  TESTED_ON_FUTURE,
  TEST_TYPES,
  TEST_TYPE_LABELS,
  resultProblem,
} from './capabilityTestText'
import FindingFields, { emptyFinding, findingBody, findingTouched, type FindingDraft } from './FindingFields'

/** The server takes at most this many findings in one request. */
const MAX_FINDINGS = 100

interface Props {
  organizationId: string
  /** Correct this test; record a new one when absent. */
  test?: CapabilityTest
  onClose: () => void
}

interface FormValues {
  testType: CapabilityTestType
  title: string
  testedOn: string
  scenario: string
  result: CapabilityTestResult
  outcome: string
}

const fromRecord = (t?: CapabilityTest): FormValues => ({
  testType: t?.test_type ?? 'incident_response',
  title: t?.title ?? '',
  testedOn: t?.tested_on ?? '',
  scenario: t?.scenario ?? '',
  result: t?.result ?? 'passed',
  outcome: t?.outcome ?? '',
})

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err))

/** The correction's PATCH body: the fields that changed. None can be cleared. */
function changedFields(initial: FormValues, values: FormValues): CapabilityTestUpdate {
  const body: CapabilityTestUpdate = {}
  const text = (v: string) => v.trim()
  if (values.testType !== initial.testType) body.test_type = values.testType
  if (text(values.title) !== text(initial.title)) body.title = text(values.title)
  if (values.testedOn !== initial.testedOn) body.tested_on = values.testedOn
  if (text(values.scenario) !== text(initial.scenario)) body.scenario = text(values.scenario)
  if (values.result !== initial.result) body.result = values.result
  if (text(values.outcome) !== text(initial.outcome)) body.outcome = text(values.outcome)
  return body
}

export default function CapabilityTestFormModal({ organizationId, test, onClose }: Props): JSX.Element {
  const queryClient = useQueryClient()
  const confirm = useConfirm()
  const dialogRef = useRef<HTMLDivElement>(null)
  useDialogFocus(dialogRef)
  const id = useId()

  const editing = test !== undefined
  const initial = useMemo(() => fromRecord(test), [test])
  const [values, setValues] = useState<FormValues>(initial)
  // Each finding keeps its key when one before it is removed.
  const [findings, setFindings] = useState<{ key: number; draft: FindingDraft }[]>([])
  const nextKey = useRef(0)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const set = <K extends keyof FormValues>(key: K, value: FormValues[K]) =>
    setValues((v) => ({ ...v, [key]: value }))

  const latest = latestTestDay()
  const patch = editing ? changedFields(initial, values) : {}
  const findingCount = editing ? test.finding_count : findings.length
  // A correction is checked, as on the server, only for what it changes.
  const dateProblem =
    (!editing || patch.tested_on !== undefined) && values.testedOn > latest ? TESTED_ON_FUTURE : null
  const ruleProblem = editing && patch.result === undefined ? null : resultProblem(values.result, findingCount)
  const untitled = findings.findIndex((f) => f.draft.title.trim() === '')
  const findingProblem = untitled >= 0 ? `Finding ${untitled + 1}: title cannot be blank` : null

  const missing = [
    values.title.trim() === '' && 'Title',
    values.testedOn === '' && 'Tested on',
    values.scenario.trim() === '' && 'Scenario',
    values.outcome.trim() === '' && 'Outcome',
  ].filter((f): f is string => !!f)
  const complete = missing.length === 0
  const canSubmit =
    complete &&
    !dateProblem &&
    !ruleProblem &&
    !findingProblem &&
    (!editing || Object.keys(patch).length > 0) &&
    !busy
  const dirty = editing
    ? Object.keys(patch).length > 0
    : (Object.keys(initial) as (keyof FormValues)[]).some((k) => values[k] !== initial[k]) || findings.length > 0

  const dismiss = () => {
    if (!busy) onClose()
  }
  const dismissIfClean = () => {
    if (!dirty) dismiss()
  }
  // Stays registered while busy or dirty, so Escape cannot reach the page behind.
  useModalDismiss(true, dismissIfClean)

  const addFinding = () => setFindings((all) => [...all, { key: nextKey.current++, draft: emptyFinding() }])
  const updateFinding = (key: number, draft: FindingDraft) =>
    setFindings((all) => all.map((f) => (f.key === key ? { key, draft } : f)))
  const removeFinding = async (key: number, index: number) => {
    const found = findings.find((f) => f.key === key)
    if (
      found &&
      findingTouched(found.draft) &&
      !(await confirm({
        title: 'Remove finding',
        message: `Remove finding ${index + 1}? What was typed in it is lost.`,
        confirmLabel: 'Remove finding',
        destructive: true,
      }))
    )
      return
    setFindings((all) => all.filter((f) => f.key !== key))
  }

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      if (editing) {
        await updateCapabilityTest(organizationId, test.id, patch)
        toast.success('Capability test corrected')
      } else {
        const body: CapabilityTestCreate = {
          test_type: values.testType,
          title: values.title.trim(),
          tested_on: values.testedOn,
          scenario: values.scenario.trim(),
          result: values.result,
          outcome: values.outcome.trim(),
          findings: findings.map((f) => findingBody(f.draft)),
        }
        await createCapabilityTest(organizationId, body)
        toast.success(
          findings.length
            ? `Capability test recorded, with ${findings.length} finding${findings.length === 1 ? '' : 's'} raised as deficiencies`
            : 'Capability test recorded',
        )
      }
      await invalidateDeficiencies(queryClient, organizationId)
      onClose()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  const heading = editing ? 'Correct capability test' : 'Record capability test'
  // A fresh form is not told off for being empty; once anything is typed, or a
  // correction clears a field, each empty one is named.
  const requiredProblems = dirty ? missing.map((f) => `${f} is required`) : []
  const problems = [dateProblem, ...requiredProblems, ruleProblem, findingProblem].filter((p): p is string => !!p)

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
                readOnly={busy}
                aria-required="true"
                onChange={(e) => set('title', e.target.value)}
                data-autofocus
              />
            </div>

            <div className="deficiency-form-row">
              <div className="form-group">
                <label htmlFor={`${id}-type`}>Type</label>
                <select
                  id={`${id}-type`}
                  className="form-control"
                  value={values.testType}
                  disabled={busy}
                  onChange={(e) => set('testType', e.target.value as CapabilityTestType)}
                >
                  {TEST_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {TEST_TYPE_LABELS[t]}
                    </option>
                  ))}
                </select>
              </div>
              <div className="form-group">
                <label htmlFor={`${id}-tested-on`}>Tested on</label>
                <input
                  id={`${id}-tested-on`}
                  type="date"
                  className="form-control"
                  value={values.testedOn}
                  // A day already recorded stands, or the browser would refuse to submit an unchanged one.
                  max={editing && initial.testedOn > latest ? initial.testedOn : latest}
                  readOnly={busy}
                  aria-required="true"
                  aria-invalid={dateProblem ? true : undefined}
                  aria-describedby={dateProblem ? `${id}-tested-on-hint ${id}-tested-on-problem` : `${id}-tested-on-hint`}
                  onChange={(e) => set('testedOn', e.target.value)}
                />
                <p id={`${id}-tested-on-hint`} className="deficiency-field-hint">
                  The day it was run; {formatAuditDate(latest)} at the latest.
                </p>
              </div>
            </div>

            <div className="form-group">
              <label htmlFor={`${id}-scenario`}>Scenario</label>
              <textarea
                id={`${id}-scenario`}
                className="form-control"
                rows={3}
                value={values.scenario}
                placeholder="What was tested, and how"
                readOnly={busy}
                aria-required="true"
                onChange={(e) => set('scenario', e.target.value)}
              />
            </div>

            <div className="form-group">
              <label htmlFor={`${id}-result`}>Result</label>
              <select
                id={`${id}-result`}
                className="form-control"
                value={values.result}
                disabled={busy}
                aria-describedby={`${id}-result-hint`}
                onChange={(e) => set('result', e.target.value as CapabilityTestResult)}
              >
                {RESULTS.map((r) => (
                  <option key={r} value={r}>
                    {RESULT_LABELS[r]}
                  </option>
                ))}
              </select>
              <p id={`${id}-result-hint`} className="deficiency-field-hint">
                Passed means nothing was found. Passed with findings and failed each need at least one finding
                {editing
                  ? `; this test has ${test.finding_count} (cancelled ones are not counted).`
                  : '.'}
              </p>
            </div>

            <div className="form-group">
              <label htmlFor={`${id}-outcome`}>Outcome</label>
              <textarea
                id={`${id}-outcome`}
                className="form-control"
                rows={3}
                value={values.outcome}
                placeholder="What happened, and what was learned"
                readOnly={busy}
                aria-required="true"
                onChange={(e) => set('outcome', e.target.value)}
              />
            </div>

            {!editing && (
              <section className="capability-findings-section" aria-labelledby={`${id}-findings-heading`}>
                <h3 id={`${id}-findings-heading`} className="deficiency-form-label">
                  Findings ({findings.length})
                </h3>
                <p className="deficiency-field-hint">{RECORD_WARNING}</p>
                {findings.map((f, i) => (
                  <FindingFields
                    key={f.key}
                    organizationId={organizationId}
                    value={f.draft}
                    onChange={(next) => updateFinding(f.key, next)}
                    busy={busy}
                    legend={`Finding ${i + 1}`}
                    onRemove={() => void removeFinding(f.key, i)}
                    autoFocus
                  />
                ))}
                <button
                  type="button"
                  className="btn-secondary btn-small"
                  onClick={addFinding}
                  disabled={busy || findings.length >= MAX_FINDINGS}
                >
                  Add a finding
                </button>
              </section>
            )}

            {/* Mounted throughout, so a screen reader hears the first problem as well as the rest. */}
            <div aria-live="polite">
              {problems.length > 0 && (
                <ul className="capability-form-problems">
                  {problems.map((p) => (
                    <li key={p} id={p === dateProblem ? `${id}-tested-on-problem` : undefined}>
                      {p}
                    </li>
                  ))}
                </ul>
              )}
              {editing && ruleProblem && test.finding_count === 0 && (
                <p className="deficiency-field-hint">
                  {test.result === 'passed'
                    ? 'A test recorded as passed is corrected with its first finding: use Add finding on the test.'
                    : 'This test has no findings that count: add one with Add finding first.'}
                </p>
              )}
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
              {busy ? 'Saving…' : editing ? 'Save' : 'Record test'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
