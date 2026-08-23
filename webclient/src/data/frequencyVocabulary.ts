/**
 * Collection-frequency vocabulary — frontend half of the single source of
 * truth (#783).
 *
 * The authoritative declaration is `backend/services/frequency_vocabulary.py`.
 * This file mirrors its `UI_OPTIONS` and `CANONICAL_FREQUENCIES` exactly, and
 * `backend/tests/test_frequency_vocabulary.py::test_typescript_module_matches_python`
 * parses THIS FILE and fails CI if the two drift.
 *
 * Before #783 the wizard declared its own option list, which offered `annually`
 * — a value the freshness map had no key for, so every annual control was
 * judged against 30 days instead of 370. Two declarations of one concept is the
 * defect; do not add a third. If you need a different option set for a new
 * surface, derive it from FREQUENCY_OPTIONS here.
 */

export interface FrequencyOption {
  value: string
  label: string
}

/**
 * Every legal value of `evidence_tracking.frequency`, in cadence order.
 * Mirrors `CANONICAL_FREQUENCIES` in the Python module.
 */
export const CANONICAL_FREQUENCIES = [
  'real_time',
  'daily',
  'weekly',
  'biweekly',
  'monthly',
  'quarterly',
  'semi_annual',
  'annual',
  'on_demand',
] as const

export type CanonicalFrequency = (typeof CANONICAL_FREQUENCIES)[number]

/**
 * Values offered in dropdowns, in display order. A deliberate SUBSET of
 * CANONICAL_FREQUENCIES — `biweekly` is still accepted on the write path for
 * legacy and bulk-imported rows, but is not promoted to new users.
 * Mirrors `UI_OPTIONS` in the Python module.
 */
export const FREQUENCY_OPTIONS: FrequencyOption[] = [
  { value: 'real_time', label: 'Real-time' },
  { value: 'daily', label: 'Daily' },
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'quarterly', label: 'Quarterly' },
  { value: 'semi_annual', label: 'Semi-annually' },
  { value: 'annual', label: 'Annually' },
  { value: 'on_demand', label: 'On demand' },
]

/** Human label for a stored frequency value, falling back to the raw string. */
export function frequencyLabel(value: string | null | undefined): string {
  if (!value) return ''
  const match = FREQUENCY_OPTIONS.find(o => o.value === value)
  return match ? match.label : value
}

/**
 * Options for a controlled <select> bound to `current`.
 *
 * A plain FREQUENCY_OPTIONS list silently renders as "Not set" whenever the
 * stored value isn't in it — which happens for `biweekly` (canonical but not
 * offered) and for the free-text values the column held before #783. The user
 * would then see no value, be unable to tell what is stored, and overwrite it
 * by touching any other field. This appends the current value as its own option
 * so it is always visible and always selectable.
 */
export function frequencyOptionsFor(current: string | null | undefined): FrequencyOption[] {
  if (!current) return FREQUENCY_OPTIONS
  if (FREQUENCY_OPTIONS.some(o => o.value === current)) return FREQUENCY_OPTIONS
  return [...FREQUENCY_OPTIONS, { value: current, label: `${current} (unrecognised)` }]
}
