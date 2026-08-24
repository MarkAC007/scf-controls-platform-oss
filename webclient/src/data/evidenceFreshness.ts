/**
 * Cadence and coverage, kept separately legible (#789 audit lane).
 *
 * The evidence health dashboard used to show one number — days since the newest
 * file was uploaded — and colour the card by it. That conflated two different
 * questions:
 *
 *   **Coverage** — how old is the period this evidence describes? This is what
 *   staleness means, and what the traffic light is now computed from.
 *   **Arrival** — when did the file turn up? Useful, but not the same thing, and
 *   a red card reading "Last upload: Today" is a card nobody can act on.
 *
 * There is a third thing a reader needs, which the old UI could not have shown
 * because the data did not exist: whether the coverage date is something a
 * preparer *asserted*, or the upload date standing in for one. Those carry very
 * different weight, and the card says which.
 *
 * These helpers live here rather than in a component because two components
 * render the same card. Consolidating those is worth doing and is not this
 * change; agreeing on the words is.
 */

export interface FreshnessFields {
  days_since_upload: number | null
  coverage_through: string | null
  days_since_coverage: number | null
  staleness_basis: 'asserted_period' | 'upload_date'
}

/** "Today" / "12d ago" / "Never" — for a day count that may not exist. */
export function ageLabel(days: number | null): string {
  if (days === null) return 'Never'
  if (days === 0) return 'Today'
  if (days < 0) return 'Future'
  return `${days}d ago`
}

/** How old the *coverage* is. This is the number the status colour follows. */
export function coverageLabel(item: FreshnessFields): string {
  return ageLabel(item.days_since_coverage)
}

/** How old the newest *file* is. Reported alongside, never instead. */
export function uploadLabel(item: FreshnessFields): string {
  return ageLabel(item.days_since_upload)
}

/**
 * Whether the coverage date is a claim or a proxy, in words a reader can act on.
 * Returns null when there is nothing to disclose (no evidence at all).
 */
export function basisLabel(item: FreshnessFields): string | null {
  if (item.days_since_coverage === null) return null
  return item.staleness_basis === 'asserted_period' ? 'asserted' : 'from upload date'
}

/** Long-form for a title attribute — the same distinction, spelled out. */
export function basisTitle(item: FreshnessFields): string | null {
  if (item.days_since_coverage === null) return null
  return item.staleness_basis === 'asserted_period'
    ? 'The preparer asserted the period this evidence covers'
    : 'Nothing was asserted, so the upload date stands in for the coverage period'
}

/**
 * Sort key for "most stale first" lists.
 *
 * Never-collected sorts worst. It used to fall back to 999, which is a number
 * and therefore ranked *below* anything genuinely older than that — a case that
 * cannot arise today but is a trap sitting in wait.
 */
export function stalenessSortKey(item: FreshnessFields): number {
  return item.days_since_coverage ?? Number.POSITIVE_INFINITY
}
