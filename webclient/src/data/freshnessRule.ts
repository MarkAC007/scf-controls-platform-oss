/**
 * The wording of the evidence freshness rule, in one place.
 *
 * "Fresh / Stale / Critical" are computed by `_calculate_status` in
 * `backend/api/evidence_health.py`. The dashboard shows the verdict, the item's
 * threshold and a coloured dot, and never states the rule that connects them —
 * so an item at 42 days against a 35-day threshold is amber, and the user has
 * no way to know that 53 would make it red. The grace band is the part nobody
 * can see.
 *
 * The threshold itself is per item: the evidence row's own
 * `staleness_warning_days` override, else the number of days its collection
 * frequency implies. That is why every string below says "its threshold" rather
 * than naming a number — a single number would be right for some rows and
 * confidently wrong for others, which is the failure this text exists to end.
 *
 * What the days are counted FROM changed in #57: the *coverage* date, meaning
 * the end of the preparer-asserted effective period, with the upload date
 * standing in only where nothing was asserted. These strings used to describe
 * the arrival date instead — a legend that explains the colours by a rule the
 * backend has stopped applying is worse than no legend, because it is believed.
 */

/**
 * Amber runs from the threshold up to this multiple of it; red is beyond.
 * Mirrors `int(threshold_days * 1.5)` in `_calculate_status`. Exported so the
 * copy below and any future caller cannot drift from each other.
 */
export const AMBER_GRACE_MULTIPLIER = 1.5

export type FreshnessStatus = 'green' | 'amber' | 'red' | 'unknown'

/** One-line rule per status, for tooltips and the legend. */
export const FRESHNESS_RULE: Record<FreshnessStatus, string> = {
  green: 'Fresh — the period this evidence covers ended within the item’s collection threshold.',
  amber: `Stale — past the threshold, but within ${AMBER_GRACE_MULTIPLIER}× of it.`,
  red: `Critical — more than ${AMBER_GRACE_MULTIPLIER}× the threshold since the period it covers ended.`,
  unknown: 'No Data — nothing collected yet, or the item has no collection frequency set.',
}

/** Shown once above the list, so the colours are legible without hovering. */
export const FRESHNESS_LEGEND =
  `Freshness is measured against each item’s own threshold: Fresh within it, ` +
  `Stale up to ${AMBER_GRACE_MULTIPLIER}× it, Critical beyond that. ` +
  `Age is counted from the period each file says it covers, falling back to its ` +
  `upload date where no period was asserted; each card shows which was used and ` +
  `the threshold it was judged against.`
