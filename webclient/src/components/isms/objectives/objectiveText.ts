// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Words and small sums for the objectives register, its forms and the ISMS
 * metrics dashboard. Pure: no React, no API.
 *
 * The server owns every value and its rounding (`value_text`); nothing here
 * rebuilds a number. Timestamps arrive as naive UTC strings and date-only
 * fields as `YYYY-MM-DD`, so neither is handed to `new Date()` as it stands.
 */
import type {
  MetricDirection,
  MetricOut,
  MetricUnit,
  ObjectiveOut,
  ObjectiveSummary,
  PerformanceStatus,
} from '../../../data/securityObjectivesApi'

export type StatusIconName =
  | 'check' | 'triangle' | 'cross' | 'arrow' | 'diamond' | 'circle' | 'clock' | 'check-circle' | 'cross-circle'

export const STATUS_META: Record<PerformanceStatus, { icon: StatusIconName; label: string }> = {
  on_track: { icon: 'check', label: 'On track' },
  at_risk: { icon: 'triangle', label: 'At risk' },
  off_track: { icon: 'cross', label: 'Off track' },
  in_progress: { icon: 'arrow', label: 'In progress' },
  proposed: { icon: 'diamond', label: 'Proposed · confirm target' },
  insufficient_data: { icon: 'circle', label: 'Insufficient data' },
  first_measurement_due: { icon: 'clock', label: 'First measurement due' },
  measurement_overdue: { icon: 'clock', label: 'Measurement overdue' },
  not_measured: { icon: 'circle', label: 'Not measured' },
  achieved: { icon: 'check-circle', label: 'Achieved' },
  missed: { icon: 'cross-circle', label: 'Missed' },
}

/** Achieved or missed: the objective is over, so nothing more is logged and its value is not judged. */
export const isClosed = (o: Pick<ObjectiveOut, 'lifecycle_status'>): boolean =>
  o.lifecycle_status === 'achieved' || o.lifecycle_status === 'missed'

/** The chip's word; "In progress" carries the days left when known. */
export function statusText(status: PerformanceStatus, daysLeft?: number | null): string {
  const label = STATUS_META[status].label
  if (status === 'in_progress' && typeof daysLeft === 'number' && daysLeft >= 0) {
    return `${label} · ${daysLeft} ${daysLeft === 1 ? 'day' : 'days'} left`
  }
  return label
}

/** The tile's kind tag. */
export function kindLabel(metric: Pick<MetricOut, 'kind'> | null | undefined): string {
  switch (metric?.kind) {
    case 'live':
      return 'Live'
    case 'logged':
      return 'Logged'
    case 'milestone':
      return 'Milestone'
    default:
      return 'No metric'
  }
}

export const errorText = (err: unknown): string => (err instanceof Error ? err.message : String(err))

/** `''` (or anything that is not a number) is "none", never 0: 0 is a real target. */
export function numberOrNull(text: string): number | null {
  const t = text.trim()
  if (t === '') return null
  const n = Number(t)
  return Number.isFinite(n) ? n : null
}

/** `YYYY-MM-DD` as "31 Mar 2027", built from its parts so no time zone can move the day. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return ''
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  if (!y || !m || !d) return iso
  return new Date(y, m - 1, d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

/** A naive UTC timestamp from the API as "18 Nov 2026, 02:10" in the reader's time. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const zoned = /(Z|[+-]\d\d:?\d\d)$/.test(iso) ? iso : `${iso}Z`
  const when = new Date(zoned)
  if (Number.isNaN(when.getTime())) return iso
  return when.toLocaleString('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

/** The register's "Complete by" cell. */
export function completeBy(o: Pick<ObjectiveOut, 'objective_type' | 'due_date' | 'review_date'>):
  { main: string; sub: string | null; gap: boolean } {
  if (o.objective_type === 'maintain') {
    return { main: 'Ongoing', sub: o.review_date ? `Review ${formatDate(o.review_date)}` : null, gap: false }
  }
  return o.due_date ? { main: formatDate(o.due_date), sub: null, gap: false } : { main: 'No date', sub: null, gap: true }
}

/** Objectives with a metric that is judged: not proposed and not unmeasured. */
/**
 * Objectives with a reading against a target: on track, at risk, off track,
 * or in progress towards a date. Never-measured states (first measurement
 * due, overdue, thin data, no metric) and proposed ones are not counted.
 */
export const measuredCount = (s: ObjectiveSummary): number => s.on_track + s.at_risk + s.off_track + s.in_progress

/**
 * "14 objectives · 9 measured · 2 proposed". `proposed` is passed by the
 * register as a count by lifecycle so it agrees with the proposed band;
 * the summary's own figure counts by reading status.
 */
export function summaryLine(s: ObjectiveSummary, proposed: number = s.proposed): string {
  const parts = [`${s.objectives} ${s.objectives === 1 ? 'objective' : 'objectives'}`, `${measuredCount(s)} measured`]
  if (proposed > 0) parts.push(`${proposed} proposed`)
  return parts.join(' · ')
}

/** What sits beside the chip: who logged it, or how many records the value rests on. */
export function readingAside(o: ObjectiveOut): string {
  const r = o.reading
  const kind = o.metric?.kind
  if (r.status === 'measurement_overdue') return r.measured_on ? `Last logged ${formatDate(r.measured_on)}` : ''
  if ((kind === 'logged' || kind === 'milestone') && r.measured_on) {
    return `Logged ${formatDate(r.measured_on)}${r.logged_by_name ? ` by ${r.logged_by_name}` : ''}`
  }
  if (kind === 'live') {
    const segment = r.segments.find((s) => s.count > 0)
    return [r.population, segment ? `${segment.count} ${segment.label}` : ''].filter(Boolean).join(' · ')
  }
  return ''
}

/** The tile's target line. */
export function targetLine(o: ObjectiveOut): string {
  const m = o.metric
  if (!m || m.kind === 'unset') {
    return o.source === 'migrated' ? 'Brought over from the old Objectives 6.2 list' : ''
  }
  const by = o.objective_type === 'achieve' && o.due_date ? ` by ${formatDate(o.due_date)}` : ''
  const label = o.reading.target_label || m.target_text
  // Closed: say what the target was, never how the value compares with it (the chip says how it ended).
  if (isClosed(o)) {
    if (m.kind === 'milestone') return o.due_date ? `Was due ${formatDate(o.due_date)}` : ''
    return label ? `Target was ${label}` : ''
  }
  if (m.kind === 'milestone') return o.due_date ? `Done or not done by ${formatDate(o.due_date)}` : 'Done or not done'
  if (o.lifecycle_status === 'proposed') return label ? `Suggested target: ${label}` : 'No target suggested'
  return label ? `Target ${label}${by}` : 'No target set'
}

/** The first rule a target and warning level break, or null. Blank values are allowed. */
export function validateTarget(
  unit: MetricUnit | '' | null,
  direction: MetricDirection,
  target: string,
  warn: string,
): string | null {
  const t = numberOrNull(target)
  const w = numberOrNull(warn)
  if (target.trim() !== '' && t === null) return 'The target must be a number.'
  if (warn.trim() !== '' && w === null) return 'The warning level must be a number.'
  if (w !== null && t === null) return 'Set a target before a warning level.'
  for (const [value, what] of [[t, 'target'], [w, 'warning level']] as const) {
    if (value === null) continue
    if (unit === 'percent' && (value < 0 || value > 100)) return `A percentage ${what} must be between 0 and 100.`
    if (unit === 'count' && value < 0) return `A count ${what} cannot be below 0.`
  }
  if (t !== null && w !== null) {
    if (direction === 'at_least' && !(w < t)) return 'The warning level must sit below the target.'
    if (direction === 'at_most' && !(w > t)) return 'The warning level must sit above the target.'
  }
  return null
}
