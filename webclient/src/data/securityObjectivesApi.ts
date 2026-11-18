// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Security objectives (ISO/IEC 27001 clause 6.2) and the ISMS metrics that
 * measure them. Shapes are the build contract's, section 5. Reading needs
 * viewer; writing needs editor; confirming, and changing the metric of a
 * confirmed objective, need admin. Objectives are archived, never deleted.
 *
 * Every timestamp the API sends is a naive UTC ISO string with no zone;
 * date-only fields are `YYYY-MM-DD`.
 */
import { apiFetch } from './apiClient'

export type Lifecycle = 'proposed' | 'active' | 'achieved' | 'missed'
export type MetricKind = 'live' | 'logged' | 'milestone' | 'unset'
export type PerformanceStatus =
  | 'on_track' | 'at_risk' | 'off_track' | 'in_progress' | 'proposed'
  | 'insufficient_data' | 'first_measurement_due' | 'measurement_overdue' | 'not_measured'
  // A closed objective (lifecycle achieved or missed): its value is shown, not judged.
  | 'achieved' | 'missed'
export type MetricUnit = 'percent' | 'count'
export type MetricDirection = 'at_least' | 'at_most'
export type LoggedFrequency = 'monthly' | 'quarterly' | 'annually'
export type ObjectiveType = 'maintain' | 'achieve'

export interface Segment { label: string; count: number }

export interface MetricOut {
  id: string
  kind: MetricKind
  measure_key: string | null
  params: Record<string, unknown>
  name: string
  description: string
  unit: MetricUnit | null
  direction: MetricDirection | null
  target_value: number | null
  warn_value: number | null
  target_text: string
  min_population: number | null
  frequency: 'nightly' | LoggedFrequency | null
  target_set_by_user_id: string | null
  target_set_by_name: string | null
  target_set_at: string | null
  active_from: string
}

export interface Reading {
  status: PerformanceStatus
  value: number | null
  value_text: string
  numerator: number | null
  denominator: number | null
  population: string
  segments: Segment[]
  target_label: string
  measured_on: string | null
  logged_by_name: string | null
  current_period_start: string | null
  days_left: number | null
  /** Up to 12 stored results of the active metric, oldest first, same measure only. Missing from an older server: read as []. */
  trend?: TrendPoint[]
  /** Pace towards an "achieve by a date" target, or null. Missing from an older server: read as null. */
  pace?: Pace | null
}

export interface TrendPoint {
  measured_on: string
  value: number
  /** The server's wording of the value ("15%"). Missing from an older server. */
  value_text?: string
}

export interface Pace {
  state: 'on_pace' | 'behind'
  label: string
  expected_value: number
  expected_text: string
  /** Today's value worded with the expected value, for the pace line ("50.6%"). Missing from an older server. */
  value_text?: string
  baseline_on: string
  baseline_value: number
  due_date: string
  target_value: number
}

export interface ObjectiveOut {
  id: string
  ref: string
  name: string
  planned_actions: string
  resources: string
  evaluation_method: string
  objective_type: ObjectiveType
  owning_team_id: string | null
  owning_team_name: string | null
  owner_user_id: string | null
  owner_name: string | null
  owner_text: string | null
  due_date: string | null
  review_date: string | null
  lifecycle_status: Lifecycle
  iso_clauses: string[]
  source: string
  last_evaluated_at: string | null
  last_evaluated_by_name: string | null
  last_evaluation_note: string | null
  created_at: string
  updated_at: string
  archived_at: string | null
  metric: MetricOut | null
  reading: Reading
}

export interface ObjectiveSummary {
  objectives: number
  on_track: number
  at_risk: number
  off_track: number
  needs_attention: number
  in_progress: number
  proposed: number
  not_measured: number
  overdue: number
  insufficient_data: number
  /** Closed objectives; not counted in on_track … needs_attention. */
  achieved: number
  missed: number
}

export interface ObjectiveList {
  items: ObjectiveOut[]
  summary: ObjectiveSummary
  measured_at: string
}

export interface MetricSpec {
  kind: 'live' | 'logged' | 'milestone'
  measure_key?: string
  params?: Record<string, unknown>
  name?: string
  description?: string
  unit?: MetricUnit
  direction?: MetricDirection
  target_value?: number | null
  warn_value?: number | null
  frequency?: LoggedFrequency
  evaluation_method?: string
}

export interface ObjectiveCreate {
  name: string
  planned_actions?: string
  resources?: string
  evaluation_method?: string
  objective_type?: ObjectiveType
  owning_team_id?: string | null
  owner_user_id?: string | null
  due_date?: string | null
  review_date?: string | null
  iso_clauses?: string[]
  metric?: MetricSpec
}

export interface ObjectiveUpdate {
  name?: string
  planned_actions?: string
  resources?: string
  evaluation_method?: string
  objective_type?: ObjectiveType
  owning_team_id?: string | null
  owner_user_id?: string | null
  owner_text?: string | null
  due_date?: string | null
  review_date?: string | null
  iso_clauses?: string[]
  lifecycle_status?: 'active' | 'achieved' | 'missed'
  archived?: boolean
}

export interface ConfirmBody {
  owning_team_id?: string
  target_value?: number
  warn_value?: number | null
  due_date?: string
  review_date?: string
}

export interface ResultCreate { value: number; period_start?: string; note?: string }

export interface ResultOut {
  id: string
  measured_on: string
  period_start: string | null
  value: number | null
  value_text: string
  numerator: number | null
  denominator: number | null
  status: PerformanceStatus
  source: 'computed' | 'logged'
  entered_by_name: string | null
  note: string | null
  target_label: string
  created_at: string
  /** The target in force on this result's metric version (phase 2). */
  target_value: number | null
  warn_value: number | null
  direction: MetricDirection | null
  unit: MetricUnit | null
  /** False when the result measured something other than the metric in force (A1); missing means true. */
  same_measure?: boolean
  /** The collector name a logged value came with (phase 3 B4); missing on an older server.
   * The server's `origin` is deliberately not typed here: it says "system" for people signed in
   * through the identity provider, so it is never shown. */
  collector?: string | null
}

/** A derived to-do: log the current period's value of a logged metric (phase 3, B2). */
export interface MeasurementTask {
  id: string
  objective_id: string
  ref: string
  title: string
  metric_name: string
  unit: MetricUnit | null
  frequency: LoggedFrequency
  period_start: string
  due_date: string
  status: 'not_started' | 'completed'
  first: boolean
  mine: boolean
  owning_team_id: string | null
  owning_team_name: string | null
  owner_name: string | null
  completed_date: string | null
  value_text: string | null
}

/** One organisation's summary counts from stored rows (phase 3, B3). */
export interface PortfolioItem extends ObjectiveSummary {
  organization_id: string
  organization_name: string
  last_measured_on: string | null
  stale: boolean
}

export interface Portfolio { items: PortfolioItem[]; generated_at: string }

export type RecordKind = 'risk' | 'deficiency' | 'evidence' | 'vendor' | 'control' | 'capability_test' | 'audit'
/** What that screen opens a record by; `key: null` is the register itself. */
export interface RecordLink { kind: RecordKind; key: string | null }
export interface BreakdownRow { key: string; group: string; cells: Record<string, string>; link: RecordLink | null }
export interface Breakdown {
  title: string
  columns: { key: string; label: string }[]
  /** Worst first, at most `breakdown_limit`. */
  rows: BreakdownRow[]
  /** Rows before the limit. */
  total: number
  register: RecordLink
  register_label: string
}
/** "up 2 points in the last 60 days, to the last stored measurement". */
export interface Change { delta: number; days: number; text: string }

/** The metric detail screen: one objective, its results (newest first, up to 100) and what holds it back. */
export interface MetricDetail {
  objective: ObjectiveOut
  results: ResultOut[]
  metric_versions: MetricOut[]
  /** Null for logged, milestone, unset, closed or archived objectives, and when nothing holds it back. */
  breakdown: Breakdown | null
  /** Null with fewer than two results in the current unit. */
  change: Change | null
  /** Open deficiencies raised from this objective. */
  raised_deficiencies: number
}

export interface ParamSpec {
  key: string
  label: string
  kind: 'number' | 'choice'
  default: number | string
  choices?: { value: string; label: string }[]
}

export interface MeasureOut {
  key: string
  name: string
  description: string
  formula: string
  unit: MetricUnit
  direction: MetricDirection
  suggested_target: number | null
  suggested_warn: number | null
  clauses: string[]
  min_population: number
  source_label: string
  params: ParamSpec[]
  today: { value: number | null; value_text: string; population: string; segments: Segment[] }
  match: number
  /** Outcome measures first; supporting ones measure use of the platform. */
  group: 'outcome' | 'supporting'
}

export interface WordingRequest {
  kind: 'live' | 'logged' | 'milestone'
  measure_key?: string
  params?: Record<string, unknown>
  name?: string
  unit?: MetricUnit
  direction?: MetricDirection
  target_value?: number | null
  warn_value?: number | null
  frequency?: LoggedFrequency
}

export interface Wording {
  evaluation_method: string
  metric_name: string
  metric_description: string
  target_text: string
  target_label: string
}

const base = (orgId: string) => `/organizations/${encodeURIComponent(orgId)}`
const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) })

export async function listSecurityObjectives(orgId: string, includeArchived = false): Promise<ObjectiveList> {
  return apiFetch(`${base(orgId)}/security-objectives${includeArchived ? '?include_archived=true' : ''}`)
}

export async function createSecurityObjective(orgId: string, body: ObjectiveCreate): Promise<ObjectiveOut> {
  return apiFetch(`${base(orgId)}/security-objectives`, json('POST', body))
}

export async function updateSecurityObjective(orgId: string, id: string, body: ObjectiveUpdate): Promise<ObjectiveOut> {
  return apiFetch(`${base(orgId)}/security-objectives/${encodeURIComponent(id)}`, json('PATCH', body))
}

export async function setObjectiveMetric(orgId: string, id: string, body: MetricSpec): Promise<ObjectiveOut> {
  return apiFetch(`${base(orgId)}/security-objectives/${encodeURIComponent(id)}/metric`, json('PUT', body))
}

export async function confirmSecurityObjective(orgId: string, id: string, body: ConfirmBody): Promise<ObjectiveOut> {
  return apiFetch(`${base(orgId)}/security-objectives/${encodeURIComponent(id)}/confirm`, json('POST', body))
}

export async function recordObjectiveEvaluation(orgId: string, id: string, note: string): Promise<ObjectiveOut> {
  return apiFetch(`${base(orgId)}/security-objectives/${encodeURIComponent(id)}/evaluation`, json('POST', { note }))
}

export async function listIsmsMeasures(orgId: string, text: string): Promise<MeasureOut[]> {
  return apiFetch(`${base(orgId)}/isms-metrics/measures${text ? `?text=${encodeURIComponent(text)}` : ''}`)
}

export async function getMetricWording(orgId: string, body: WordingRequest): Promise<Wording> {
  return apiFetch(`${base(orgId)}/isms-metrics/wording`, json('POST', body))
}

export async function getIsmsMetrics(orgId: string): Promise<ObjectiveList> {
  return apiFetch(`${base(orgId)}/isms-metrics`)
}

export async function getMetricDetail(orgId: string, id: string, breakdownLimit = 10): Promise<MetricDetail> {
  return apiFetch(`${base(orgId)}/isms-metrics/${encodeURIComponent(id)}?breakdown_limit=${breakdownLimit}`)
}

export async function logMetricResult(orgId: string, id: string, body: ResultCreate): Promise<ObjectiveOut> {
  return apiFetch(`${base(orgId)}/isms-metrics/${encodeURIComponent(id)}/results`, json('POST', body))
}

export async function listMeasurementTasks(
  orgId: string,
  flags: { assignedToMe: boolean; includeDone: boolean },
): Promise<MeasurementTask[]> {
  const q = new URLSearchParams({ assigned_to_me: String(flags.assignedToMe), include_done: String(flags.includeDone) })
  return apiFetch(`${base(orgId)}/isms-metrics/measurement-tasks?${q.toString()}`)
}

/** Every organisation the caller can open; not under an organisation, and apiFetch adds `/api`. */
export async function getIsmsPortfolio(): Promise<Portfolio> {
  return apiFetch('/isms-metrics/portfolio')
}
