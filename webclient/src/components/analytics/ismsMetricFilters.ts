// SPDX-License-Identifier: AGPL-3.0-only
/**
 * The ISMS metrics dashboard filters: team, clause group and status.
 *
 * Team and clause narrow both the tiles and the summary strip (the strip then
 * counts what is shown, worked out here the same way the server does).
 * Status narrows the tiles only: the strip is what the status filter picks
 * from, so it must not shrink to the one status picked.
 */
import type { ObjectiveOut, ObjectiveSummary, PerformanceStatus } from '../../data/securityObjectivesApi'

/** Same shape as the URL module's IsmsMetricFilters; '' is "any". */
export interface MetricFilters { team: string; clause: string; status: string }

export const NO_FILTERS: MetricFilters = { team: '', clause: '', status: '' }
/** The team filter value for objectives with no owning team. */
export const NO_TEAM = 'none'

/** ISO 27001 clause groups. An objective matches when any of its clauses starts with the prefix. */
export const CLAUSE_GROUPS: { value: string; label: string; pill: string; prefix: string }[] = [
  { value: '6', label: 'Planning (6)', pill: 'Planning 6', prefix: '6' },
  { value: '7', label: 'Support (7)', pill: 'Support 7', prefix: '7' },
  { value: '8', label: 'Operation (8)', pill: 'Operation 8', prefix: '8' },
  { value: '9', label: 'Performance (9)', pill: 'Performance 9', prefix: '9' },
  { value: '10', label: 'Improvement (10)', pill: 'Improvement 10', prefix: '10' },
  { value: 'annex-a', label: 'Annex A', pill: 'Annex A', prefix: 'A.' },
]

const NEEDS_ATTENTION: PerformanceStatus[] = [
  'proposed', 'insufficient_data', 'first_measurement_due', 'measurement_overdue', 'not_measured',
]
const OVERDUE: PerformanceStatus[] = ['first_measurement_due', 'measurement_overdue']

export const STATUS_FILTERS: { value: string; label: string; matches: (s: PerformanceStatus) => boolean }[] = [
  { value: 'on_track', label: 'On track', matches: (s) => s === 'on_track' },
  { value: 'at_risk', label: 'At risk', matches: (s) => s === 'at_risk' },
  { value: 'off_track', label: 'Off track', matches: (s) => s === 'off_track' },
  { value: 'in_progress', label: 'In progress', matches: (s) => s === 'in_progress' },
  { value: 'needs_attention', label: 'Needs attention', matches: (s) => NEEDS_ATTENTION.includes(s) },
  { value: 'closed', label: 'Achieved or missed', matches: (s) => s === 'achieved' || s === 'missed' },
]

/** The register's status pills, as the mock-up's register toolbar: three judgements and "Not measured". */
export const REGISTER_STATUS_FILTERS: { value: string; label: string; matches: (s: PerformanceStatus) => boolean }[] = [
  { value: 'off_track', label: 'Off track', matches: (s) => s === 'off_track' },
  { value: 'at_risk', label: 'At risk', matches: (s) => s === 'at_risk' },
  { value: 'on_track', label: 'On track', matches: (s) => s === 'on_track' },
  { value: 'not_measured', label: 'Not measured', matches: (s) => s === 'not_measured' },
]

/** The register's rows under its own team and status pills (view state only, nothing in the URL). */
export function registerFilter(items: ObjectiveOut[], team: string, status: string): ObjectiveOut[] {
  const rule = REGISTER_STATUS_FILTERS.find((f) => f.value === status)
  return applyFilters(items, { team, clause: '', status: '' }).tiles.filter((o) => !rule || rule.matches(o.reading.status))
}

/** True when the clause (6.1.3, A.5.1) falls in the group. "10" never matches "1.0", and "6" never "60". */
export function clauseInGroup(clause: string, prefix: string): boolean {
  const c = clause.trim()
  if (prefix === 'A.') return c.toUpperCase().startsWith('A.')
  return c === prefix || c.startsWith(`${prefix}.`)
}

const matchesTeam = (o: ObjectiveOut, team: string) =>
  !team || (team === NO_TEAM ? !o.owning_team_id : o.owning_team_id === team)

function matchesClause(o: ObjectiveOut, clause: string): boolean {
  if (!clause) return true
  const group = CLAUSE_GROUPS.find((g) => g.value === clause)
  // A value the dashboard does not know filters nothing, rather than everything.
  if (!group) return true
  return o.iso_clauses.some((c) => clauseInGroup(c, group.prefix))
}

function matchesStatus(o: ObjectiveOut, status: string): boolean {
  if (!status) return true
  const rule = STATUS_FILTERS.find((f) => f.value === status)
  return rule ? rule.matches(o.reading.status) : true
}

export const isFiltered = (f: MetricFilters): boolean => Boolean(f.team || f.clause || f.status)

/** The items the strip counts (team and clause) and the tiles shown (all three). */
export function applyFilters(items: ObjectiveOut[], f: MetricFilters): { strip: ObjectiveOut[]; tiles: ObjectiveOut[] } {
  const strip = items.filter((o) => matchesTeam(o, f.team) && matchesClause(o, f.clause))
  return { strip, tiles: strip.filter((o) => matchesStatus(o, f.status)) }
}

/** The summary over unarchived items, as the server's `summarise` builds it. */
export function summarise(items: ObjectiveOut[]): ObjectiveSummary {
  const live = items.filter((o) => o.archived_at === null)
  const count = (statuses: PerformanceStatus[]) => live.filter((o) => statuses.includes(o.reading.status)).length
  return {
    objectives: live.length,
    on_track: count(['on_track']),
    at_risk: count(['at_risk']),
    off_track: count(['off_track']),
    in_progress: count(['in_progress']),
    proposed: count(['proposed']),
    not_measured: count(['not_measured']),
    insufficient_data: count(['insufficient_data']),
    needs_attention: count(NEEDS_ATTENTION),
    overdue: count(OVERDUE),
    achieved: count(['achieved']),
    missed: count(['missed']),
  }
}

/**
 * The teams named on the dashboard's objectives, by name, for the team filter. A team in the URL that no
 * objective carries (a bookmark from before a move) is kept as "Unknown team", so the select shows what is filtering.
 */
export function teamOptions(items: ObjectiveOut[], selected = ''): { value: string; label: string }[] {
  const options = listedTeams(items)
  if (!selected || options.some((o) => o.value === selected)) return options
  return [...options, { value: selected, label: selected === NO_TEAM ? 'No team' : 'Unknown team' }]
}

function listedTeams(items: ObjectiveOut[]): { value: string; label: string }[] {
  const named = new Map<string, string>()
  let unowned = false
  for (const o of items) {
    if (o.owning_team_id) named.set(o.owning_team_id, o.owning_team_name ?? 'Unnamed team')
    else unowned = true
  }
  const options = [...named].map(([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label))
  return unowned ? [...options, { value: NO_TEAM, label: 'No team' }] : options
}
