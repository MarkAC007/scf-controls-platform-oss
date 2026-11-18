// SPDX-License-Identifier: AGPL-3.0-only
/**
 * One objective's metric in depth, on the Analytics tab at
 * `?tab=capability-posture&view=isms-metrics&metric=<objective id>`.
 * AnalyticsPage owns the URL; this screen only names destinations.
 *
 * Header and actions; the value with its segment bar and trend; how it is
 * measured; what is holding it back (each row links into its register); and
 * the measurement log, which is the chart's table view.
 */
import { useState, type CSSProperties, type JSX } from 'react'
import type {
  Breakdown,
  MetricDetail as MetricDetailData,
  ObjectiveOut,
  RecordLink,
  ResultOut,
} from '../../data/securityObjectivesApi'
import { useIsOrgEditor } from '../../hooks/useHasOrgRole'
import { useMetricDetail } from '../../hooks/useSecurityObjectives'
import AppLink, { type AppDestination } from '../AppLink'
import DeficiencyFormModal, { type DeficiencyPrefill } from '../deficiencies/DeficiencyFormModal'
import ClauseChips from '../isms/objectives/ClauseChips'
import EvaluationModal from '../isms/objectives/EvaluationModal'
import StatusChip from '../isms/objectives/StatusChip'
import { completeBy, errorText, formatDate, formatDateTime, kindLabel, targetLine } from '../isms/objectives/objectiveText'
import TrendChart, { countText, targetThen } from './TrendChart'

const LOG_FIRST = 10
const LOG_STEP = 20

/** Where a breakdown row (or the register link) opens. A record with no key opens its register. */
export function recordDestination(link: RecordLink): AppDestination {
  const key = link.key
  switch (link.kind) {
    case 'risk':
      return key ? { kind: 'risk', id: key } : { kind: 'register', of: 'risk' }
    case 'vendor':
      return key ? { kind: 'vendor', id: key } : { kind: 'register', of: 'vendor' }
    case 'evidence':
      return key ? { kind: 'evidence', id: key } : { kind: 'register', of: 'evidence' }
    case 'control':
      return key ? { kind: 'control', id: key } : { kind: 'register', of: 'library' }
    case 'capability_test':
      return key ? { kind: 'capability-test', id: key } : { kind: 'register', of: 'capability-test' }
    // The deficiencies page and the audit programme open no single record by URL.
    case 'deficiency':
      return { kind: 'register', of: 'deficiencies' }
    case 'audit':
      return { kind: 'register', of: 'audit-programme' }
  }
}

/** What the deficiency form starts with when raised from an off-track objective. */
export function deficiencyPrefill(o: ObjectiveOut): DeficiencyPrefill {
  const r = o.reading
  const target = r.target_label || o.metric?.target_text || 'its target'
  const population = r.population ? ` (${r.population})` : ''
  const measured = r.measured_on ? `, measured ${formatDate(r.measured_on)}` : ''
  return {
    title: `Objective ${o.ref} off track: ${o.name}`.slice(0, 255),
    description:
      `${o.metric?.name ?? 'The metric'} reads ${r.value_text || 'no value'}${population}${measured}, ` +
      `against a target of ${target}. Raised from the ISMS metrics detail screen.`,
    priority: 'high',
    source: 'security_objective',
    source_ref: o.id,
  }
}

// A value a collector sent names the collector, and the person whose key it used when the row
// carries one (B4). The server's `origin` is never shown: it reads "system" for people signed in
// through the identity provider.
const sourceText = (r: ResultOut) =>
  r.collector
    ? `Collector ${r.collector}${r.entered_by_name ? ` (key of ${r.entered_by_name})` : ''}`
    : r.source === 'logged' ? `Logged${r.entered_by_name ? ` by ${r.entered_by_name}` : ''}` : 'Computed'

/** The value part's width in percent, to one decimal: 2 of 4 is 50, 50 of 66 is 75.8. */
export const segWidth = (numerator: number, denominator: number): number =>
  Number(Math.max(0, Math.min(100, (numerator / denominator) * 100)).toFixed(1))

/**
 * How a server segment sits against the population, by its label. The server sends no kind yet, so
 * these are the measure library's own labels (backend `services/isms_metrics/library.py`); an
 * unknown label is never drawn as a bar part. A `kind` per segment from the server would be better.
 */
const BAD_SEGMENTS = ['above appetite', 'overdue', 'no due date', 'failed']
const UNKNOWN_SEGMENTS = ['not assessed']
/** Counted inside the good number (accepted risks), so it overlaps the value part: legend only. */
const OVERLAP_SEGMENTS = ['accepted']

export type PartKind = 'value' | 'count' | 'bad' | 'unknown' | 'overlap' | 'other'
export interface BarItem { kind: PartKind; label: string; count: number; part: boolean }

export function segmentKind(label: string): Exclude<PartKind, 'value' | 'count'> {
  if (BAD_SEGMENTS.includes(label)) return 'bad'
  if (UNKNOWN_SEGMENTS.includes(label)) return 'unknown'
  if (OVERLAP_SEGMENTS.includes(label)) return 'overlap'
  return 'other'
}

/**
 * The bar's parts and legend, as the mock-up's composition bar: the value share, then the known-bad and
 * unknown shares as their own parts. Parts are drawn only when the server's counts say they are disjoint:
 * every segment's label is known, and the good count plus the bad and unknown counts is exactly the
 * population. Otherwise the bar is the value share alone and the segments are legend text. Widths come
 * from server counts only; no share is worked out that the server did not send.
 *
 * The numerator is the good count only when more is better (`at_least`). For `at_most` it is the count
 * to keep down (open material deficiencies, deficiencies past the limit, AI verdicts waiting): the part is
 * a plain `count` in the data ink, never green and never the status colour, and nothing else is drawn.
 */
export function barItems(
  numerator: number,
  denominator: number,
  segments: { label: string; count: number }[],
  direction: string | null = null,
): BarItem[] {
  const good = direction === 'at_least'
  const kinds = segments.map((sg) => ({ ...sg, kind: segmentKind(sg.label) }))
  const inside = kinds.filter((k) => k.kind === 'bad' || k.kind === 'unknown')
  const disjoint = good && kinds.every((k) => k.kind !== 'other')
    && numerator + inside.reduce((sum, k) => sum + k.count, 0) === denominator
  return [
    { kind: good ? 'value' : 'count', label: '', count: numerator, part: true },
    ...kinds.map((k) => ({
      kind: disjoint || (good && k.kind === 'overlap') ? k.kind : 'other' as PartKind,
      label: k.label,
      count: k.count,
      part: disjoint && (k.kind === 'bad' || k.kind === 'unknown') && k.count > 0,
    })),
  ]
}

function SegmentBar({ objective }: { objective: ObjectiveOut }): JSX.Element | null {
  const r = objective.reading
  if (r.numerator === null || r.denominator === null || r.denominator <= 0) return null
  const denominator = r.denominator
  const items = barItems(r.numerator, denominator, r.segments, objective.metric?.direction ?? null)
  const parts = items.filter((it) => it.part && it.count > 0)
  // The status colour is the chip's alone; the bar is good, bad and unknown, as the mock-up (or a plain count for at_most).
  return (
    <>
      <div className={`ismsm-segbar${parts.length > 1 ? ' ismsm-segbar--parts' : ''}`} aria-hidden="true">
        {parts.map((it) => (
          <i
            key={it.kind + it.label}
            className={`ismsm-seg ismsm-seg--${it.kind}`}
            style={{ '--seg': `${segWidth(it.count, denominator)}%` } as CSSProperties}
            data-testid={`segbar-${it.label === '' ? 'value' : `part-${it.label}`}`}
          />
        ))}
      </div>
      <ul className="ismsm-seglegend" data-testid="segbar-legend">
        {items.map((it) => (
          <li key={it.kind + it.label} data-kind={it.kind}>
            <span className={`ismsm-swatch ismsm-swatch--${it.kind}`} aria-hidden="true" />
            {it.label === '' ? r.population : `${it.count} ${it.label}`}
          </li>
        ))}
      </ul>
    </>
  )
}

/** The level word a residual-level cell starts with ("Critical · 20"), lower case, or '' when it is not one. */
export function levelWord(text: string): string {
  const word = text.trim().split(/[\s·]/)[0].toLowerCase()
  return ['low', 'medium', 'high', 'critical'].includes(word) ? word : ''
}

/**
 * A residual level as a coloured pill, as the mock-up's `.lv`. The server sends the cell as words, so the
 * colour is read from the first word; a level key from the server would be better.
 */
function LevelPill({ text }: { text: string }): JSX.Element {
  const word = levelWord(text)
  return word ? <span className={`ismsm-lv ismsm-lv--${word}`}>{text}</span> : <>{text}</>
}

function BreakdownTable({ breakdown }: { breakdown: Breakdown }): JSX.Element {
  const groups = [...new Set(breakdown.rows.map((r) => r.group).filter(Boolean))]
  return (
    <section className="ismsm-card ismsm-breakdown" aria-labelledby="ismsm-breakdown-title">
      <h3 id="ismsm-breakdown-title" className="ismsm-card-title">
        What is holding this back <span className="ismsm-card-sub">· {breakdown.title}</span>
      </h3>
      <div className="ismsm-table-wrap">
        <table className="ismsm-table">
          <thead>
            <tr>
              {groups.length > 0 && <th scope="col">Why</th>}
              {breakdown.columns.map((c) => <th key={c.key} scope="col">{c.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {breakdown.rows.map((row) => (
              <tr key={row.key} data-testid={`breakdown-row-${row.key}`}>
                {groups.length > 0 && <td className="ismsm-table-group">{row.group}</td>}
                {breakdown.columns.map((c, i) => (
                  <td key={c.key}>
                    {i === 0 && row.link ? (
                      <AppLink to={recordDestination(row.link)}>{row.cells[c.key] ?? row.key}</AppLink>
                    ) : c.key === 'level' ? (
                      <LevelPill text={row.cells[c.key] ?? ''} />
                    ) : (
                      row.cells[c.key] ?? ''
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="ismsm-card-foot">
        {breakdown.total > breakdown.rows.length && (
          <span>Showing {breakdown.rows.length} of {breakdown.total} · </span>
        )}
        <AppLink to={recordDestination(breakdown.register)} data-testid="breakdown-register">
          {breakdown.register_label}
        </AppLink>
      </p>
    </section>
  )
}

function MeasurementLog({ results }: { results: ResultOut[] }): JSX.Element {
  const [shown, setShown] = useState(LOG_FIRST)
  return (
    <section className="ismsm-card ismsm-log" aria-labelledby="ismsm-log-title">
      <h3 id="ismsm-log-title" className="ismsm-card-title">Measurement log</h3>
      {results.length === 0 ? (
        <p className="gov-muted">Nothing measured yet.</p>
      ) : (
        <div className="ismsm-table-wrap">
          <table className="ismsm-table">
            <thead>
              <tr>
                <th scope="col">Date</th>
                <th scope="col">Value</th>
                <th scope="col">Count</th>
                <th scope="col">Target then</th>
                <th scope="col">Source</th>
              </tr>
            </thead>
            <tbody>
              {results.slice(0, shown).map((r) => (
                <tr key={r.id} data-testid="log-row">
                  <td>{formatDate(r.measured_on)}</td>
                  <td>{r.value_text || '—'}</td>
                  <td>{countText(r) || '—'}</td>
                  <td>{targetThen(r)}</td>
                  <td>{sourceText(r)}{r.note ? <span className="ismsm-log-note">{r.note}</span> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {results.length > shown && (
        <p className="ismsm-card-foot">
          <button type="button" className="btn-link" onClick={() => setShown((n) => n + LOG_STEP)}>
            Show more ({results.length - shown} more)
          </button>
        </p>
      )}
    </section>
  )
}

function HowMeasured({ objective }: { objective: ObjectiveOut }): JSX.Element {
  const { metric, reading } = objective
  const review = completeBy(objective)
  const evaluated = objective.last_evaluated_at
    ? `${formatDateTime(objective.last_evaluated_at)}${objective.last_evaluated_by_name ? `, ${objective.last_evaluated_by_name}` : ''}`
    : 'Not yet'
  return (
    <section className="ismsm-card ismsm-how" aria-labelledby="ismsm-how-title">
      <h3 id="ismsm-how-title" className="ismsm-card-title">How this is measured</h3>
      {(objective.evaluation_method || metric?.description) && (
        <div className="ismsm-formula">
          {objective.evaluation_method && <p>{objective.evaluation_method}</p>}
          {metric?.description && <p className="ismsm-formula-sub">{metric.description}</p>}
        </div>
      )}
      <dl className="ismsm-facts">
        <dt>Today</dt>
        <dd>{reading.population || reading.value_text || '—'}</dd>
        <dt>Target set by</dt>
        <dd>
          {metric?.target_set_by_name
            ? `${metric.target_set_by_name}${metric.target_set_at ? `, ${formatDate(metric.target_set_at)}` : ''}`
            : 'Not confirmed yet'}
        </dd>
        <dt>Last evaluated</dt>
        <dd>
          {evaluated}
          {objective.last_evaluation_note && <q className="ismsm-quote">{objective.last_evaluation_note}</q>}
        </dd>
        <dt>{objective.objective_type === 'achieve' ? 'Due' : 'Review by'}</dt>
        <dd>{review.sub ? review.sub.replace(/^Review /, '') : review.main}</dd>
      </dl>
      {(objective.planned_actions || objective.resources) && (
        <>
          <h4 className="ismsm-plan-title">The plan</h4>
          {objective.planned_actions && <p className="ismsm-plan">{objective.planned_actions}</p>}
          {objective.resources && <p className="ismsm-plan ismsm-plan--muted">Resources: {objective.resources}</p>}
        </>
      )}
    </section>
  )
}

function DetailBody({ organizationId, data, onRaised }: { organizationId: string; data: MetricDetailData; onRaised: () => void }): JSX.Element {
  const canEdit = useIsOrgEditor(organizationId)
  const [evaluating, setEvaluating] = useState(false)
  const [raising, setRaising] = useState(false)
  const o = data.objective
  const { metric, reading } = o
  const offTrack = reading.status === 'off_track'
  const line = targetLine(o)
  const raised = data.raised_deficiencies
  // An older server sends no pace: read it as none.
  const pace = reading.pace ?? null

  return (
    <>
      <div className="ismsm-detail-head">
        <div className="ismsm-detail-title">
          <h2>
            <span className="obj-ref">{o.ref}</span> {o.name}
          </h2>
          <div className="ismsm-detail-meta">
            <StatusChip status={reading.status} daysLeft={reading.days_left} />
            <span>{kindLabel(metric)} metric</span>
            <span>·</span>
            <span>{o.owning_team_name ?? 'No team yet'}</span>
            <ClauseChips clauses={o.iso_clauses} />
          </div>
        </div>
        <div className="ismsm-detail-actions">
          <AppLink to={{ kind: 'objective', id: o.id }} className="btn-secondary">Open objective</AppLink>
          {canEdit && (
            <button type="button" className="btn-secondary" onClick={() => setEvaluating(true)}>
              Record evaluation
            </button>
          )}
          {canEdit && offTrack && (
            <button type="button" className="btn-primary" onClick={() => setRaising(true)}>
              Raise deficiency
            </button>
          )}
          {raised > 0 && (
            <p className="ismsm-raised" data-testid="already-raised">
              {raised === 1 ? '1 open deficiency' : `${raised} open deficiencies`} already raised ·{' '}
              <AppLink to={{ kind: 'register', of: 'deficiencies' }}>Open Deficiencies</AppLink>
            </p>
          )}
        </div>
      </div>

      <div className="ismsm-detail-grid">
        <section className="ismsm-card ismsm-value" aria-labelledby="ismsm-value-title">
          <h3 id="ismsm-value-title" className="ismsm-card-title">{metric?.name ?? 'No metric chosen'}</h3>
          <div className="ismsm-value-row">
            <span className="ismsm-value-big">{reading.value_text || '—'}</span>
            {reading.population && <span className="ismsm-value-pop">{reading.population}</span>}
            {data.change && <span className="ismsm-value-change">{data.change.text}</span>}
          </div>
          <SegmentBar objective={o} />
          {line && <p className="ismsm-value-target">{line}{metric?.frequency ? ` · measured ${metric.frequency}` : ''}</p>}
          {/* A milestone or unset metric has no unit and never a trend: no chart, not a "two measurements" promise. */}
          {metric?.unit && <TrendChart results={data.results} unit={metric.unit} name={metric.name} pace={pace} />}
          {pace && (
            <p className="ismsm-pace-line" data-testid="detail-pace">
              {pace.value_text
                ? `Today ${pace.value_text}, expected ${pace.expected_text}. `
                : `Expected by today: ${pace.expected_text}. `}
              <span className={`ismsm-pace ismsm-pace--${pace.state}`}>{pace.label}</span>.
            </p>
          )}
        </section>
        <HowMeasured objective={o} />
        {data.breakdown ? (
          <BreakdownTable breakdown={data.breakdown} />
        ) : (
          <section className="ismsm-card ismsm-breakdown" aria-labelledby="ismsm-breakdown-title">
            <h3 id="ismsm-breakdown-title" className="ismsm-card-title">What is holding this back</h3>
            <p className="gov-muted">
              {metric?.kind === 'live' ? 'Nothing listed: no record is holding this back.' : 'Only live measures list the records behind the value.'}
            </p>
          </section>
        )}
        <MeasurementLog results={data.results} />
      </div>

      {evaluating && <EvaluationModal organizationId={organizationId} objective={o} onClose={() => setEvaluating(false)} />}
      {raising && (
        <DeficiencyFormModal organizationId={organizationId} prefill={deficiencyPrefill(o)} onClose={() => {
          setRaising(false)
          // The already-raised count is the detail call's; read it again so a second click sees the first.
          onRaised()
        }} />
      )}
    </>
  )
}

interface Props {
  organizationId: string
  objectiveId: string
}

export default function MetricDetail({ organizationId, objectiveId }: Props): JSX.Element {
  const detail = useMetricDetail(organizationId, objectiveId)
  return (
    <div className="cp-container ismsm ismsm-detail" data-testid="metric-detail">
      <AppLink to={{ kind: 'isms-metrics' }} className="ismsm-back">← Analytics · ISMS metrics</AppLink>
      {detail.isLoading ? (
        <div className="ismsm-card cp-skeleton" aria-busy="true">
          <div className="cp-skeleton-line cp-skeleton-title" />
          <div className="cp-skeleton-line cp-skeleton-bar" />
        </div>
      ) : detail.isError ? (
        <div className="cp-error" role="alert">This metric could not be loaded: {errorText(detail.error)}</div>
      ) : detail.data ? (
        <DetailBody organizationId={organizationId} data={detail.data} onRaised={() => void detail.refetch()} />
      ) : null}
    </div>
  )
}
