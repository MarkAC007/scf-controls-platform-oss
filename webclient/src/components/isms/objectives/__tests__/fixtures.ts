// SPDX-License-Identifier: AGPL-3.0-only
/** Neutral fixtures for the objectives and metrics tests. */
import type {
  MeasureOut,
  MetricOut,
  ObjectiveList,
  ObjectiveOut,
  ObjectiveSummary,
  Reading,
  ResultOut,
} from '../../../../data/securityObjectivesApi'

export const ORG = 'org-one'

export function metric(over: Partial<MetricOut> = {}): MetricOut {
  return {
    id: 'm-1', kind: 'live', measure_key: 'risk_within_appetite', params: {},
    name: 'Risks within appetite (%)', description: 'Live from the Risk Register, measured nightly.',
    unit: 'percent', direction: 'at_least', target_value: 100, warn_value: 90,
    target_text: 'at least 100%', min_population: 5, frequency: 'nightly',
    target_set_by_user_id: 'u-1', target_set_by_name: 'Pat Example', target_set_at: '2026-11-18T02:10:00',
    active_from: '2026-11-18T02:10:00',
    ...over,
  }
}

export function reading(over: Partial<Reading> = {}): Reading {
  return {
    status: 'off_track', value: 14, value_text: '14%', numerator: 9, denominator: 66,
    population: '9 of 66 risks', segments: [{ label: 'not assessed', count: 48 }],
    target_label: 'at least 100% · warning below 90%', measured_on: '2026-11-18',
    logged_by_name: null, current_period_start: null, days_left: null,
    ...over,
  }
}

export function objective(over: Partial<ObjectiveOut> = {}): ObjectiveOut {
  return {
    id: 'obj-1', ref: 'OBJ-01', name: 'Keep risk within appetite',
    planned_actions: 'Assess every risk and treat those above the acceptable level.',
    resources: 'Risk owners’ time', evaluation_method: 'Risks whose residual level is acceptable.',
    objective_type: 'maintain', owning_team_id: 'team-ops', owning_team_name: 'Risk and Compliance',
    owner_user_id: null, owner_name: null, owner_text: null,
    due_date: null, review_date: '2027-03-31', lifecycle_status: 'active', iso_clauses: ['6.1.3', '8.3'],
    source: 'starter:risk', last_evaluated_at: null, last_evaluated_by_name: null, last_evaluation_note: null,
    created_at: '2026-11-18T02:10:00', updated_at: '2026-11-18T02:10:00', archived_at: null,
    metric: metric(), reading: reading(),
    ...over,
  }
}

export function summary(over: Partial<ObjectiveSummary> = {}): ObjectiveSummary {
  return {
    objectives: 14, on_track: 2, at_risk: 2, off_track: 3, needs_attention: 5,
    in_progress: 2, proposed: 2, not_measured: 1, overdue: 1, insufficient_data: 1, achieved: 0, missed: 0,
    ...over,
  }
}

export function list(items: ObjectiveOut[], over: Partial<ObjectiveSummary> = {}): ObjectiveList {
  return { items, summary: summary(over), measured_at: '2026-11-18T02:10:00' }
}

export function measure(over: Partial<MeasureOut> = {}): MeasureOut {
  return {
    key: 'risk_within_appetite', name: 'Risks within appetite',
    description: 'Share of risks at or below the acceptable level.', formula: 'within / all',
    unit: 'percent', direction: 'at_least', suggested_target: 100, suggested_warn: 90,
    clauses: ['6.1.3', '8.3'], min_population: 5, source_label: 'Risk Register', params: [],
    today: { value: 14, value_text: '14%', population: '9 of 66 risks', segments: [] },
    match: 2, group: 'outcome',
    ...over,
  }
}

export function result(over: Partial<ResultOut> = {}): ResultOut {
  return {
    id: 'r-1', measured_on: '2026-09-30', period_start: null, value: 12, value_text: '12%',
    numerator: 8, denominator: 66, status: 'off_track', source: 'computed', entered_by_name: null,
    note: null, target_label: 'at least 100%', created_at: '2026-09-30T02:00:00',
    target_value: 100, warn_value: 90, direction: 'at_least', unit: 'percent',
    ...over,
  }
}
