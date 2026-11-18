// SPDX-License-Identifier: AGPL-3.0-only
/**
 * The metric detail screen's trend: a hand-built SVG, no chart library.
 *
 * - The value line, one point per result in the metric's current unit
 *   (a result recorded in another unit is left out: it cannot share an axis).
 * - Target and warning as stepped lines, each step drawn from the result's
 *   own `target_value` / `warn_value`, so a changed target shows where it
 *   changed instead of being redrawn back over the past.
 * - Fewer than two plotted results: an empty state, never a one-point line.
 * - With a pace (an "achieve by a date" objective), a dashed straight line
 *   from the baseline to the target on the due date, and the x range runs
 *   to the due date. The line uses the server's numbers only.
 *
 * Text is drawn in text tokens (CSS classes), never in a series colour. The
 * measurement log beside the chart is its table view.
 */
import { useId, useRef, useState, type CSSProperties, type JSX, type KeyboardEvent } from 'react'
import type { MetricUnit, Pace, ResultOut } from '../../data/securityObjectivesApi'
import { formatDate } from '../isms/objectives/objectiveText'

const W = 640
const H = 240
const PAD = { left: 40, right: 84, top: 14, bottom: 28 }

/** Days since the epoch for a `YYYY-MM-DD` date, built from its parts (no time zone). */
function dayNumber(iso: string): number {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return Date.UTC(y, (m || 1) - 1, d || 1) / 86_400_000
}

/** "≥ 100%", "≤ 5", or "none": the target in force when the result was taken. */
export function targetThen(r: Pick<ResultOut, 'target_value' | 'direction' | 'unit'>): string {
  if (r.target_value === null || r.target_value === undefined) return 'none'
  const sign = r.direction === 'at_most' ? '≤' : '≥'
  return `${sign} ${r.target_value}${r.unit === 'percent' ? '%' : ''}`
}

/** "9 of 66", or '' when the result has no count. */
export const countText = (r: Pick<ResultOut, 'numerator' | 'denominator'>): string =>
  r.numerator !== null && r.denominator !== null ? `${r.numerator} of ${r.denominator}` : ''

/** False only when the server says the result measured something else; a missing flag is true. */
export const sameMeasure = (r: Pick<ResultOut, 'same_measure'>): boolean => r.same_measure !== false

/**
 * The results the chart plots, oldest first: the same measure, the current unit, with a value.
 * A logged result (one with `period_start`) is one point per period, placed on the period's first day:
 * the newest entry of the period counts (`created_at`, then `id`), as a correction replaces the entry it
 * corrects. This is the server's trend rule, so the pace line starts on the first dot. The returned
 * result's `measured_on` is that plotted date; the measurement log still lists every entry as written.
 */
export function plottable(results: ResultOut[], unit: MetricUnit | null): ResultOut[] {
  const kept = results.filter((r) => r.value !== null && unit !== null && r.unit === unit && sameMeasure(r))
  const computed = kept.filter((r) => !r.period_start)
  const newest = new Map<string, ResultOut>()
  for (const r of kept) {
    if (!r.period_start) continue
    const have = newest.get(r.period_start)
    if (!have || r.created_at > have.created_at || (r.created_at === have.created_at && r.id > have.id)) newest.set(r.period_start, r)
  }
  const periods = [...newest.values()].map((r) => ({ ...r, measured_on: r.period_start as string }))
  return [...computed, ...periods]
    .sort((a, b) => a.measured_on.localeCompare(b.measured_on) || a.created_at.localeCompare(b.created_at))
}

/** A round axis top for counts: 1, 2 or 5 times a power of ten, at least the largest value. */
export function niceMax(n: number): number {
  if (!(n > 0)) return 1
  const power = 10 ** Math.floor(Math.log10(n))
  for (const step of [1, 2, 5, 10]) if (step * power >= n) return step * power
  return 10 * power
}

/**
 * A stepped path through each point's level: flat to the next point, then a
 * vertical step to its level. Breaks where a point has no level.
 */
export function stepPath(xs: number[], levels: (number | null)[], y: (v: number) => number): string {
  let d = ''
  let open = false
  levels.forEach((level, i) => {
    if (level === null) {
      open = false
      return
    }
    d += open ? `V${y(level).toFixed(1)}` : `M${xs[i].toFixed(1)},${y(level).toFixed(1)}`
    d += `H${(xs[i + 1] ?? xs[i]).toFixed(1)}`
    open = true
  })
  return d
}

/** The tooltip's lines, also each point's accessible name (joined). */
export function tipParts(r: ResultOut): string[] {
  const count = countText(r)
  return [formatDate(r.measured_on), `${r.value_text}${count ? ` · ${count}` : ''}`, `Target then ${targetThen(r)}`]
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

interface Tick { day: number; label: string; anchor?: 'start' | 'middle' | 'end' }

/** "18 Nov": a short date for an axis inside one month. */
function shortDate(iso: string): string {
  const [, m, d] = iso.slice(0, 10).split('-').map(Number)
  return `${d} ${MONTHS[m - 1]}`
}

/**
 * Month labels at the first of each month inside the range, at most seven.
 * A range inside one calendar month has no first-of-month: label its first and last dates instead.
 */
export function axisTicks(first: string, last: string): Tick[] {
  const months = monthTicks(first, last)
  if (months.length > 0) return months
  return [
    { day: dayNumber(first), label: shortDate(first), anchor: 'start' },
    { day: dayNumber(last), label: shortDate(last), anchor: 'end' },
  ]
}

function monthTicks(first: string, last: string): Tick[] {
  let [y, m] = first.slice(0, 7).split('-').map(Number)
  const end = dayNumber(last)
  const ticks: Tick[] = []
  // The first month counts only if it starts on the range's first day.
  if (dayNumber(first) !== Date.UTC(y, m - 1, 1) / 86_400_000) {
    m += 1
    if (m > 12) { m = 1; y += 1 }
  }
  for (;;) {
    const day = Date.UTC(y, m - 1, 1) / 86_400_000
    if (day > end) break
    ticks.push({ day, label: m === 1 ? `Jan ${String(y).slice(2)}` : MONTHS[m - 1] })
    m += 1
    if (m > 12) { m = 1; y += 1 }
  }
  const every = Math.ceil(ticks.length / 7)
  return ticks.filter((_, i) => i % every === 0)
}

interface Props {
  results: ResultOut[]
  unit: MetricUnit | null
  /** The metric's name, for the summary read out in place of the picture. */
  name: string
  /** The server's pace, or null: draws the pace line when set. */
  pace?: Pace | null
}

export default function TrendChart({ results, unit, name, pace = null }: Props): JSX.Element {
  const [active, setActive] = useState<number | null>(null)
  const points = plottable(results, unit)
  // The one point in the tab order (roving tabindex); the newest until the arrows move it.
  const [cursor, setCursor] = useState<number | null>(null)
  const pointRefs = useRef<(SVGGElement | null)[]>([])
  const summaryId = useId()
  const otherMeasure = results.some((r) => !sameMeasure(r))
  const note = otherMeasure && (
    <p className="ismsm-trend-note" data-testid="trend-other-measure">Earlier results under a different measure are in the log.</p>
  )

  if (points.length < 2) {
    return (
      <>
        <div className="ismsm-trend-empty" data-testid="trend-empty">
          <p>The trend starts once there are two measurements.</p>
          <p className="ismsm-trend-empty-sub">
            {points.length === 1
              ? `One so far: ${points[0].value_text} on ${formatDate(points[0].measured_on)}.`
              : 'None yet.'}
          </p>
        </div>
        {note}
      </>
    )
  }
  const stop = cursor !== null && cursor < points.length ? cursor : points.length - 1
  const moveTo = (i: number) => {
    setCursor(i)
    setActive(i)
    pointRefs.current[i]?.focus()
  }
  const onKey = (e: KeyboardEvent<SVGGElement>, i: number) => {
    const next = { ArrowLeft: i - 1, ArrowRight: i + 1, Home: 0, End: points.length - 1 }[e.key]
    if (next === undefined) return
    e.preventDefault()
    moveTo(Math.max(0, Math.min(points.length - 1, next)))
  }

  const values = points.map((p) => p.value as number)
  const targets = points.map((p) => p.target_value ?? null)
  const warns = points.map((p) => p.warn_value ?? null)
  const top = unit === 'percent'
    ? 100
    : niceMax(Math.max(
      ...values,
      ...targets.filter((v): v is number => v !== null),
      ...warns.filter((v): v is number => v !== null),
      ...(pace ? [pace.baseline_value, pace.target_value] : []),
    ))
  const plotW = W - PAD.left - PAD.right
  const plotH = H - PAD.top - PAD.bottom
  const y = (v: number) => PAD.top + plotH * (1 - Math.max(0, Math.min(top, v)) / top)

  const days = points.map((p) => dayNumber(p.measured_on))
  // With a pace line the range runs from the earlier of baseline and first result to the due date.
  const firstIso = pace && pace.baseline_on < points[0].measured_on ? pace.baseline_on : points[0].measured_on
  const lastIso = pace && pace.due_date > points[points.length - 1].measured_on ? pace.due_date : points[points.length - 1].measured_on
  const startDay = dayNumber(firstIso)
  const span = dayNumber(lastIso) - startDay
  // All on one day: spread evenly by order instead of piling up.
  const xs = days.map((d, i) => PAD.left + plotW * (span > 0 ? (d - startDay) / span : i / (points.length - 1)))
  const xOfDay = (d: number) => PAD.left + plotW * ((d - startDay) / (span || 1))

  const gridValues = unit === 'percent' ? [0, 25, 50, 75, 100] : [0, top / 2, top]
  const axisLabel = (v: number) => (unit === 'percent' ? `${v}%` : String(Math.round(v * 100) / 100))
  const ticks = span > 0 ? axisTicks(firstIso, lastIso) : []
  const last = points[points.length - 1]
  const lastX = xs[xs.length - 1]
  const lastTarget = targets[targets.length - 1]
  const lastWarn = warns[warns.length - 1]

  const summary =
    `Trend of ${name}: ${points.length} measurements from ${formatDate(points[0].measured_on)} ` +
    `to ${formatDate(last.measured_on)}, from ${points[0].value_text} to ${last.value_text}. ` +
    `Target ${targetThen(last)}.`

  const tip = active !== null ? points[active] : null
  const tipStyle = active !== null
    ? ({ '--tip-x': `${(xs[active] / W) * 100}%`, '--tip-y': `${(y(values[active]) / H) * 100}%` } as CSSProperties)
    : undefined

  return (
    <div className="ismsm-trend">
      {/* The summary is a sibling, not an image role over the points, so each point can be reached and named. */}
      <p id={summaryId} className="ismsm-sr-only" data-testid="trend-summary">{summary}</p>
      <svg
        className="ismsm-trend-svg"
        viewBox={`0 0 ${W} ${H}`}
        role="group"
        aria-labelledby={summaryId}
        data-testid="trend-chart"
      >
        {gridValues.map((v) => (
          <g key={v}>
            <line className="ismsm-trend-grid" x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} />
            <text className="ismsm-trend-axis" x={PAD.left - 6} y={y(v) + 3} textAnchor="end">{axisLabel(v)}</text>
          </g>
        ))}
        {ticks.map((t) => (
          <text key={t.day} className="ismsm-trend-axis" x={xOfDay(t.day)} y={H - 8} textAnchor={t.anchor ?? 'middle'}>{t.label}</text>
        ))}

        {warns.some((v) => v !== null) && (
          <path className="ismsm-trend-warn" d={stepPath(xs, warns, y)} data-testid="trend-warn" />
        )}
        {targets.some((v) => v !== null) && (
          <path className="ismsm-trend-target" d={stepPath(xs, targets, y)} data-testid="trend-target" />
        )}
        {lastTarget !== null && (
          <text className="ismsm-trend-label" x={lastX + 8} y={y(lastTarget) + 3}>Target {targetThen(last).slice(2)}</text>
        )}
        {lastWarn !== null && (
          <text className="ismsm-trend-label" x={lastX + 8} y={y(lastWarn) + (lastTarget !== null && Math.abs(y(lastWarn) - y(lastTarget)) < 12 ? 14 : 3)}>
            Warning {lastWarn}{unit === 'percent' ? '%' : ''}
          </text>
        )}

        {pace && (
          <line
            className="ismsm-trend-pace"
            x1={xOfDay(dayNumber(pace.baseline_on))}
            y1={y(pace.baseline_value)}
            x2={xOfDay(dayNumber(pace.due_date))}
            y2={y(pace.target_value)}
            data-testid="trend-pace"
          />
        )}
        <polyline
          className="ismsm-trend-line"
          points={xs.map((x, i) => `${x.toFixed(1)},${y(values[i]).toFixed(1)}`).join(' ')}
          data-testid="trend-line"
        />
        <text className="ismsm-trend-value" x={lastX + 8} y={y(values[values.length - 1]) - 8}>{last.value_text}</text>

        {points.map((p, i) => (
          <g
            key={p.id}
            ref={(el) => { pointRefs.current[i] = el }}
            className={`ismsm-trend-point${active === i ? ' is-active' : ''}`}
            role="img"
            aria-label={tipParts(p).join(', ')}
            tabIndex={i === stop ? 0 : -1}
            data-testid="trend-point"
            onMouseEnter={() => setActive(i)}
            onMouseLeave={() => setActive(null)}
            onFocus={() => { setActive(i); setCursor(i) }}
            onBlur={() => setActive(null)}
            onKeyDown={(e) => onKey(e, i)}
          >
            <circle className="ismsm-trend-hit" cx={xs[i]} cy={y(values[i])} r={9} />
            <circle className="ismsm-trend-dot" cx={xs[i]} cy={y(values[i])} r={i === points.length - 1 ? 5 : 4} />
          </g>
        ))}
      </svg>
      {tip && (
        // aria-hidden: the focused point already carries this text as its name.
        <div className="ismsm-trend-tip" aria-hidden="true" style={tipStyle} data-testid="trend-tip">
          {tipParts(tip).map((line, i) => (i === 0 ? <b key={i}>{line}</b> : <span key={i}>{line}</span>))}
        </div>
      )}
      {pace && (
        <ul className="ismsm-trend-legend" data-testid="trend-legend">
          <li><i className="ismsm-trend-key ismsm-trend-key--value" aria-hidden="true" />Value</li>
          {targets.some((v) => v !== null) && <li><i className="ismsm-trend-key ismsm-trend-key--target" aria-hidden="true" />Target</li>}
          <li><i className="ismsm-trend-key ismsm-trend-key--pace" aria-hidden="true" />Pace to target</li>
        </ul>
      )}
      {note}
    </div>
  )
}
