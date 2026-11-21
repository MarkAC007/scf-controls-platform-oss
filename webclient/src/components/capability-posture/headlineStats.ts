// SPDX-License-Identifier: AGPL-3.0-only
import type { ScopedControlStatsResponse } from '../../data/apiClient'
import type { CapabilityThemeResponse } from '../../types'

export interface HeadlineStats {
  overallKps: number | null
  /** Distinct at-risk controls in scope; null until the stats have loaded. */
  atRisk: number | null
}

/**
 * Figures for the KPI row above the theme cards.
 *
 * #1279: the at-risk headline must count each control once. A control maps to
 * several capability themes or to none, so summing the per-theme
 * `posture.at_risk` counts both double-counts and misses controls.
 * `scoped-controls/stats.at_risk` is the distinct figure the Dashboard reports.
 */
export function headlineStats(
  themes: CapabilityThemeResponse[],
  scopedStats: ScopedControlStatsResponse | undefined
): HeadlineStats | null {
  if (themes.length === 0) return null
  let compositeSum = 0
  let compositeCount = 0

  for (const t of themes) {
    if (t.composite_score !== null) {
      compositeSum += t.composite_score
      compositeCount += 1
    }
  }

  return {
    overallKps: compositeCount > 0 ? compositeSum / compositeCount : null,
    atRisk: scopedStats ? scopedStats.at_risk : null,
  }
}
