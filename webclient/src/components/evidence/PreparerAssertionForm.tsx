import { useMemo } from 'react'
import type { PreparerAssertionsInput } from '../../data/apiClient'

/**
 * Capture of what the preparer asserts about an artefact (#786, #802).
 *
 * Three things an auditor tests that the platform could not previously express:
 *
 * - **Period coverage.** Freshness is anchored to the upload date, so a 2023
 *   access review dragged in this morning paints green. What is actually being
 *   tested is whether the uploaded set covers the observation window, and that
 *   needs the window the artefact itself covers.
 * - **Population and sampling.** "This evidence item has 2 files" is not a
 *   conclusion. Two of two joiners and two of four hundred are different
 *   findings; without a declared population, neither is expressible.
 * - **IPE.** Completeness and accuracy of Information Produced by the Entity is
 *   the most-failed area in Big 4 testing.
 *
 * Every field is optional and the panel starts collapsed, because most uploads
 * will assert nothing and a wall of empty inputs between a user and a drop zone
 * is how a drop zone stops being used. A blank field means **not asserted**,
 * and that is a legitimate, honest state — it is never defaulted, and the
 * display side renders it as "Not asserted" rather than as an empty value that
 * reads like an oversight.
 *
 * The form is deliberately *ambient* rather than part of the upload state
 * machine. Uploads start the instant a file is dropped, with no interaction in
 * between, so a form that gated the drop would have to interrupt it. Instead
 * these values sit beside the drop zone and are applied to whatever is dropped
 * next — which also matches how batches actually work: a set of files dropped
 * together is normally one population, one period and one extract.
 */

export interface AssertionFormValues {
  effectivePeriodStart: string
  effectivePeriodEnd: string
  populationSize: string
  populationSource: string
  sampleSize: string
  sampleMethod: string
  sampleBasis: string
  ipeSourceSystem: string
  ipeQueryOrFilter: string
  ipeExtractedAt: string
  ipeCompletenessCheck: string
}

export const EMPTY_ASSERTIONS: AssertionFormValues = {
  effectivePeriodStart: '',
  effectivePeriodEnd: '',
  populationSize: '',
  populationSource: '',
  sampleSize: '',
  sampleMethod: '',
  sampleBasis: '',
  ipeSourceSystem: '',
  ipeQueryOrFilter: '',
  ipeExtractedAt: '',
  ipeCompletenessCheck: '',
}

/**
 * Selection methods an auditor recognises. Free text would produce a column of
 * synonyms nobody can group, and a required vocabulary would refuse a method
 * this list has not thought of — hence a datalist: suggested, not enforced.
 */
export const SAMPLE_METHODS = [
  'full population',
  'random',
  'systematic',
  'haphazard',
  'judgemental',
  'risk-based',
]

const MAX_PROSE = 2000
const MAX_QUERY = 4000

/** Fields the preparer filled in, as the confirm endpoint wants them. */
export function toAssertionPayload(v: AssertionFormValues): PreparerAssertionsInput {
  const text = (s: string) => {
    const trimmed = s.trim()
    return trimmed === '' ? undefined : trimmed
  }
  const count = (s: string) => {
    const trimmed = s.trim()
    if (trimmed === '') return undefined
    const n = Number(trimmed)
    return Number.isFinite(n) ? n : undefined
  }

  const all: PreparerAssertionsInput = {
    effective_period_start: text(v.effectivePeriodStart),
    effective_period_end: text(v.effectivePeriodEnd),
    population_size: count(v.populationSize),
    population_source: text(v.populationSource),
    sample_size: count(v.sampleSize),
    sample_method: text(v.sampleMethod),
    sample_basis: text(v.sampleBasis),
    ipe_source_system: text(v.ipeSourceSystem),
    ipe_query_or_filter: text(v.ipeQueryOrFilter),
    ipe_extracted_at: text(v.ipeExtractedAt),
    ipe_completeness_check: text(v.ipeCompletenessCheck),
  }

  // Drop the keys nothing was typed into. `JSON.stringify` would omit an
  // `undefined` value anyway, so this changes no bytes on the wire — but the
  // object is spread into the confirm body, and a body carrying eleven
  // `undefined` keys reads, to anyone debugging it, as eleven assertions the
  // preparer made and then blanked. It is cheaper to not say them.
  return Object.fromEntries(
    Object.entries(all).filter(([, value]) => value !== undefined),
  ) as PreparerAssertionsInput
}

/** True when the preparer has asserted at least one thing. */
export function hasAnyAssertion(v: AssertionFormValues): boolean {
  return Object.values(v).some(value => value.trim() !== '')
}

/**
 * The same two rules the backend enforces, checked here so a preparer is told
 * before the bytes move rather than after. The server remains the authority —
 * this is a courtesy, not a gate that can be trusted.
 */
export function assertionErrors(v: AssertionFormValues): string[] {
  const errors: string[] = []
  const start = v.effectivePeriodStart.trim()
  const end = v.effectivePeriodEnd.trim()

  if (start !== '' && end === '') {
    errors.push('Effective period needs an end date as well as a start date.')
  }
  if (end !== '' && start === '') {
    errors.push('Effective period needs a start date as well as an end date.')
  }
  if (start !== '' && end !== '' && end < start) {
    errors.push('Effective period end cannot be before its start.')
  }

  const population = v.populationSize.trim()
  const sample = v.sampleSize.trim()
  if (population !== '' && sample !== '') {
    const p = Number(population)
    const s = Number(sample)
    if (Number.isFinite(p) && Number.isFinite(s) && s > p) {
      errors.push(`Sample size (${s}) cannot exceed the population (${p}).`)
    }
  }
  return errors
}

export interface PreparerAssertionFormProps {
  values: AssertionFormValues
  onChange: (values: AssertionFormValues) => void
  expanded: boolean
  onToggle: () => void
  /** Inputs are disabled while an upload is in flight. */
  disabled?: boolean
}

export function PreparerAssertionForm({
  values,
  onChange,
  expanded,
  onToggle,
  disabled = false,
}: PreparerAssertionFormProps) {
  const errors = useMemo(() => assertionErrors(values), [values])
  const asserted = hasAnyAssertion(values)

  const set = (key: keyof AssertionFormValues) => (value: string) =>
    onChange({ ...values, [key]: value })

  return (
    <div className="preparer-assertions" data-testid="preparer-assertions">
      <button
        type="button"
        className="preparer-assertions-toggle"
        onClick={onToggle}
        aria-expanded={expanded}
        aria-controls="preparer-assertions-body"
        data-testid="preparer-assertions-toggle"
      >
        <span className="preparer-assertions-caret" aria-hidden="true">
          {expanded ? '▾' : '▸'}
        </span>
        <span className="preparer-assertions-title">Audit assertions</span>
        <span className="preparer-assertions-hint">
          {asserted
            ? 'Applied to the next file you upload'
            : 'Optional · period, population, sampling, IPE'}
        </span>
      </button>

      {expanded && (
        <div className="preparer-assertions-body" id="preparer-assertions-body">
          <p className="preparer-assertions-intro">
            What this evidence is evidence <em>of</em>. Anything you leave blank is
            recorded as <strong>not asserted</strong> — the platform will not guess it
            from the upload date.
          </p>

          <fieldset className="preparer-assertions-group" disabled={disabled}>
            <legend>Effective period</legend>
            <p className="preparer-assertions-note">
              The window the artefact covers, which is not the day you uploaded it.
            </p>
            <div className="form-row">
              <div className="form-group">
                <label htmlFor="assertion-period-start">Period start</label>
                <input
                  id="assertion-period-start"
                  data-testid="assertion-period-start"
                  type="date"
                  className="form-control"
                  value={values.effectivePeriodStart}
                  max={values.effectivePeriodEnd || undefined}
                  onChange={e => set('effectivePeriodStart')(e.target.value)}
                />
              </div>
              <div className="form-group">
                <label htmlFor="assertion-period-end">Period end</label>
                <input
                  id="assertion-period-end"
                  data-testid="assertion-period-end"
                  type="date"
                  className="form-control"
                  value={values.effectivePeriodEnd}
                  min={values.effectivePeriodStart || undefined}
                  onChange={e => set('effectivePeriodEnd')(e.target.value)}
                />
              </div>
            </div>
          </fieldset>

          <fieldset className="preparer-assertions-group" disabled={disabled}>
            <legend>Population and sample</legend>
            <p className="preparer-assertions-note">
              A file count is not a coverage statement. Two of two and two of four
              hundred are different findings.
            </p>
            <div className="form-row">
              <div className="form-group">
                <label htmlFor="assertion-population-size">Population size</label>
                <input
                  id="assertion-population-size"
                  data-testid="assertion-population-size"
                  type="number"
                  min={0}
                  className="form-control"
                  value={values.populationSize}
                  onChange={e => set('populationSize')(e.target.value)}
                />
              </div>
              <div className="form-group">
                <label htmlFor="assertion-sample-size">Sample size</label>
                <input
                  id="assertion-sample-size"
                  data-testid="assertion-sample-size"
                  type="number"
                  min={0}
                  className="form-control"
                  value={values.sampleSize}
                  onChange={e => set('sampleSize')(e.target.value)}
                />
              </div>
            </div>
            <div className="form-group">
              <label htmlFor="assertion-population-source">Population source</label>
              <input
                id="assertion-population-source"
                data-testid="assertion-population-source"
                type="text"
                className="form-control"
                maxLength={MAX_PROSE}
                placeholder="All joiners in Workday, 1 Jan – 31 Mar 2026"
                value={values.populationSource}
                onChange={e => set('populationSource')(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label htmlFor="assertion-sample-method">Selection method</label>
              <input
                id="assertion-sample-method"
                data-testid="assertion-sample-method"
                type="text"
                className="form-control"
                list="assertion-sample-methods"
                maxLength={100}
                value={values.sampleMethod}
                onChange={e => set('sampleMethod')(e.target.value)}
              />
              <datalist id="assertion-sample-methods">
                {SAMPLE_METHODS.map(method => (
                  <option key={method} value={method} />
                ))}
              </datalist>
            </div>
            <div className="form-group">
              <label htmlFor="assertion-sample-basis">Why this sample is adequate</label>
              <textarea
                id="assertion-sample-basis"
                data-testid="assertion-sample-basis"
                className="form-control"
                rows={2}
                maxLength={MAX_PROSE}
                value={values.sampleBasis}
                onChange={e => set('sampleBasis')(e.target.value)}
              />
            </div>
          </fieldset>

          <fieldset className="preparer-assertions-group" disabled={disabled}>
            <legend>Information produced by the entity</legend>
            <p className="preparer-assertions-note">
              For a system-generated export: what produced it, how, when, and how you
              know the extract is complete.
            </p>
            <div className="form-row">
              <div className="form-group">
                <label htmlFor="assertion-ipe-system">Source system</label>
                <input
                  id="assertion-ipe-system"
                  data-testid="assertion-ipe-system"
                  type="text"
                  className="form-control"
                  maxLength={255}
                  placeholder="Workday"
                  value={values.ipeSourceSystem}
                  onChange={e => set('ipeSourceSystem')(e.target.value)}
                />
              </div>
              <div className="form-group">
                <label htmlFor="assertion-ipe-extracted-at">Extract run on</label>
                <input
                  id="assertion-ipe-extracted-at"
                  data-testid="assertion-ipe-extracted-at"
                  type="date"
                  className="form-control"
                  value={values.ipeExtractedAt}
                  onChange={e => set('ipeExtractedAt')(e.target.value)}
                />
              </div>
            </div>
            <div className="form-group">
              <label htmlFor="assertion-ipe-query">Report, query or filter</label>
              <textarea
                id="assertion-ipe-query"
                data-testid="assertion-ipe-query"
                className="form-control"
                rows={2}
                maxLength={MAX_QUERY}
                placeholder="Report: New Hires, hire_date between 2026-01-01 and 2026-03-31"
                value={values.ipeQueryOrFilter}
                onChange={e => set('ipeQueryOrFilter')(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label htmlFor="assertion-ipe-completeness">Completeness and accuracy check</label>
              <textarea
                id="assertion-ipe-completeness"
                data-testid="assertion-ipe-completeness"
                className="form-control"
                rows={2}
                maxLength={MAX_PROSE}
                placeholder="Row count reconciled to the Workday headcount report"
                value={values.ipeCompletenessCheck}
                onChange={e => set('ipeCompletenessCheck')(e.target.value)}
              />
            </div>
          </fieldset>

          {errors.length > 0 && (
            <ul
              className="preparer-assertions-errors"
              role="alert"
              data-testid="preparer-assertions-errors"
            >
              {errors.map(error => (
                <li key={error}>{error}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
