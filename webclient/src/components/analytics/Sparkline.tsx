// SPDX-License-Identifier: AGPL-3.0-only
/**
 * The trend line on a dashboard tile, as in the approved mock-up: a small
 * hand-built SVG (no chart library), the line in the primary colour with an
 * end dot, and a dashed line at the target when there is one.
 *
 * The picture is aria-hidden; its meaning is a visually hidden sentence built
 * only from numbers the server sent. Nothing here judges or re-rounds a value.
 */
import type { JSX } from 'react'
import type { MetricUnit, ObjectiveOut, TrendPoint } from '../../data/securityObjectivesApi'
import { formatDate } from '../isms/objectives/objectiveText'

export const SPARK_W = 112
export const SPARK_H = 36
const PAD = 4

export interface SparkPoint extends TrendPoint {
  /** True for today's live value, appended after the stored trend. */
  today?: boolean
}

/**
 * The stored trend, plus today's live value as the last point when it is a number and the server's
 * date for it (`appendOn`, a live reading's `measured_on`) is later than the last stored point, so the
 * line ends where the big number is. `appendOn` is null for a logged metric, whose value is already the
 * trend's last point, and when the server sent no date: then nothing is appended. No browser clock.
 */
export function sparkPoints(trend: TrendPoint[], value: number | null, appendOn: string | null): SparkPoint[] {
  const points: SparkPoint[] = [...trend]
  const last = points[points.length - 1]
  if (appendOn && typeof value === 'number' && Number.isFinite(value) && (!last || appendOn > last.measured_on)) {
    points.push({ measured_on: appendOn, value, today: true })
  }
  return points
}

export interface SparkGeometry {
  xs: number[]
  ys: number[]
  /** The target line's height, or null when there is no numeric target. */
  targetY: number | null
}

/** Coordinates in a 112 × 36 box. The range covers values and target; a flat range sits at mid-height. */
export function sparkGeometry(points: { value: number }[], target: number | null): SparkGeometry {
  const values = points.map((p) => p.value)
  const all = target === null ? values : [...values, target]
  if (all.length === 0) return { xs: [], ys: [], targetY: null }
  const lo = Math.min(...all)
  const hi = Math.max(...all)
  const span = hi - lo
  const y = (v: number) => (span > 0 ? SPARK_H - PAD - ((v - lo) * (SPARK_H - 2 * PAD)) / span : SPARK_H / 2)
  const n = points.length
  // One point sits at the right, where the line would end.
  const xs = points.map((_, i) => (n > 1 ? PAD + (i * (SPARK_W - 2 * PAD)) / (n - 1) : SPARK_W - PAD))
  return { xs, ys: values.map(y), targetY: target === null ? null : y(target) }
}

/** For an older server that sends no `value_text` on a trend point: the number as sent (81.0 prints as 81), with % for a percentage. */
const asSent = (v: number, unit: MetricUnit | null) => `${Number(v)}${unit === 'percent' ? '%' : ''}`

/** "Trend: 38% on 5 Nov 2026 to 43% today, 9 measurements." Built from the server's numbers and texts only. */
export function sparkSentence(points: SparkPoint[], unit: MetricUnit | null, todayText: string): string {
  const at = (p: SparkPoint) => (p.today ? `${todayText || asSent(p.value, unit)} today` : `${p.value_text ?? asSent(p.value, unit)} on ${formatDate(p.measured_on)}`)
  const count = `${points.length} measurement${points.length === 1 ? '' : 's'}`
  if (points.length === 1) return `Trend: ${at(points[0])}, ${count}.`
  return `Trend: ${at(points[0])} to ${at(points[points.length - 1])}, ${count}.`
}

export default function Sparkline({ objective }: { objective: ObjectiveOut }): JSX.Element | null {
  const { metric, reading } = objective
  if (!metric || metric.kind === 'unset' || metric.kind === 'milestone') return null
  // An older server sends no trend: read it as none.
  // Only a live reading is measured today, on the server's date.
  const points = sparkPoints(reading.trend ?? [], reading.value, metric.kind === 'live' ? reading.measured_on : null)
  if (points.length === 0) return null
  const target = typeof metric.target_value === 'number' ? metric.target_value : null
  const { xs, ys, targetY } = sparkGeometry(points, target)
  const last = points.length - 1
  const sentence = sparkSentence(points, metric.unit, reading.value_text)

  return (
    <div className="ismsm-spark-wrap">
      <svg
        className="ismsm-spark"
        viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
        width={SPARK_W}
        height={SPARK_H}
        aria-hidden="true"
        focusable="false"
        data-testid="tile-spark"
      >
        {targetY !== null && (
          <line className="ismsm-spark-target" x1={0} x2={SPARK_W} y1={targetY} y2={targetY} data-testid="spark-target" />
        )}
        {points.length > 1 && (
          <polyline
            className="ismsm-spark-line"
            points={xs.map((x, i) => `${x.toFixed(1)},${ys[i].toFixed(1)}`).join(' ')}
            data-testid="spark-line"
          />
        )}
        <circle className="ismsm-spark-dot" cx={xs[last]} cy={ys[last]} r={3.5} data-testid="spark-dot" />
      </svg>
      <span className="ismsm-sr-only">{sentence}</span>
    </div>
  )
}
