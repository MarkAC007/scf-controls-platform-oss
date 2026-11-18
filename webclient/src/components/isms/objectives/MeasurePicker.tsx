// SPDX-License-Identifier: AGPL-3.0-only
/**
 * The measure list in the objective form: the whole library, best match
 * first, each with today's value worked out from this organisation's
 * records. Controlled: the form owns every value.
 *
 * Outcome measures come first, best match first as the server sends them;
 * supporting measures (use of the platform) follow under their own heading.
 */
import { Fragment, useId, type JSX } from 'react'
import type { MeasureOut } from '../../../data/securityObjectivesApi'

export type MeasureMode = 'none' | 'live' | 'logged' | 'milestone'

interface Props {
  measures: MeasureOut[]
  loading: boolean
  error: string | null
  mode: MeasureMode
  measureKey: string
  params: Record<string, unknown>
  onPickMeasure: (m: MeasureOut) => void
  onParam: (key: string, value: unknown) => void
  onMode: (mode: 'logged' | 'milestone') => void
  disabled: boolean
}

export default function MeasurePicker({
  measures, loading, error, mode, measureKey, params, onPickMeasure, onParam, onMode, disabled,
}: Props): JSX.Element {
  const name = useId()
  // A measure without a group (an older server) counts as an outcome measure.
  const outcome = measures.filter((m) => m.group !== 'supporting')
  const supporting = measures.filter((m) => m.group === 'supporting')
  const ordered = [...outcome, ...supporting]
  return (
    <>
      {loading && measures.length === 0 && <p className="gov-muted">Loading the measures…</p>}
      {error && <p className="deficiency-field-hint deficiency-field-hint--error">The measures could not be loaded: {error}</p>}
      {measures.length > 0 && (
        <div className="obj-measure-list" role="radiogroup" aria-label="Measures">
          {ordered.map((m, index) => {
            const selected = mode === 'live' && m.key === measureKey
            const heading = supporting.length > 0 && index === outcome.length && (
              <h4 className="obj-measure-group" data-testid="supporting-heading">
                Supporting measures (use of the platform)
              </h4>
            )
            return (
              <Fragment key={m.key}>
              {heading}
              <label className={`obj-measure${selected ? ' is-selected' : ''}`}>
                <input
                  type="radio"
                  name={name}
                  className="obj-measure-radio"
                  checked={selected}
                  disabled={disabled}
                  onChange={() => onPickMeasure(m)}
                  aria-label={m.name}
                />
                <div className="obj-measure-main">
                  <h4>
                    {m.name}
                    {index === 0 && m.match > 0 && <span className="obj-measure-best">best match</span>}
                  </h4>
                  <p>{m.description}</p>
                </div>
                <div className="obj-measure-today">
                  Today
                  <b>{m.today.value_text || '—'}</b>
                  {m.today.population}
                </div>
                {selected && m.params.length > 0 && (
                  <div className="obj-measure-params">
                    {m.params.map((p) => (
                      // A span, not a label: the card is already the radio's label, and labels must not nest.
                      <span key={p.key} className="obj-param">
                        <span aria-hidden="true">{p.label}</span>
                        {p.kind === 'number' ? (
                          <input
                            type="number"
                            aria-label={p.label}
                            value={String(params[p.key] ?? p.default)}
                            disabled={disabled}
                            onChange={(e) => onParam(p.key, e.target.value === '' ? '' : Number(e.target.value))}
                          />
                        ) : (
                          <select
                            aria-label={p.label}
                            value={String(params[p.key] ?? p.default)}
                            disabled={disabled}
                            onChange={(e) => onParam(p.key, e.target.value)}
                          >
                            {(p.choices ?? []).map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                          </select>
                        )}
                      </span>
                    ))}
                  </div>
                )}
              </label>
              </Fragment>
            )
          })}
        </div>
      )}
      <p className="obj-measure-alt">
        None of these fit?
        <button type="button" className="btn-link" disabled={disabled} aria-pressed={mode === 'logged'} onClick={() => onMode('logged')}>
          Log it ourselves
        </button>
        ·
        <button type="button" className="btn-link" disabled={disabled} aria-pressed={mode === 'milestone'} onClick={() => onMode('milestone')}>
          Milestone
        </button>
      </p>
    </>
  )
}
