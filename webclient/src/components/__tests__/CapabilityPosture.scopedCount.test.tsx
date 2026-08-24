/**
 * Capability Posture — the SCOPED CONTROLS headline is a DISTINCT count (#808).
 *
 * A control legitimately belongs to more than one capability theme, so the
 * per-theme `X of Y scoped` figures overlap by design. Summing them produces a
 * number that reads as a distinct total but is not one: in production it put
 * `SCOPED CONTROLS 200` on the Capability Posture page beside the Dashboard's
 * `CONTROLS IN SCOPE 127`, on the same organisation, in the same session.
 *
 * The property held here is that the headline comes from
 * `scoped-controls/stats.in_scope` — the same authoritative figure the
 * Dashboard reports — and specifically NOT from `sum(theme.scoped_controls)`.
 * The fixture makes the two disagree so the assertion cannot pass by accident.
 *
 * The per-theme cards are asserted to still show their own overlapping counts:
 * fixing the headline must not "fix" figures that were never wrong.
 *
 * The second describe covers the issue's other question — whether OVERALL KPS
 * shares the defect. It does not, and the test pins why.
 *
 * Placeholder theme codes here are deliberately not real KSI codes.
 */
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CapabilityPosture from '../CapabilityPosture'
import {
  getCapabilityThemes,
  getCapabilityThemeEvidencePosture,
  fetchScopedControlStats,
} from '../../data/apiClient'
import type { ScopedControlStatsResponse } from '../../data/apiClient'
import type { CapabilityThemeResponse } from '../../types'

vi.mock('../../data/apiClient', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../data/apiClient')>()
  return {
    ...actual,
    getCapabilityThemes: vi.fn(),
    getCapabilityThemeEvidencePosture: vi.fn(),
    fetchScopedControlStats: vi.fn(),
  }
})

const ORG_ID = 'org-1'

/** Distinct in-scope controls, as the authoritative stats endpoint counts them. */
const DISTINCT_IN_SCOPE = 40

/** Per-theme scoped counts that overlap: they sum to 65, not 40. */
const THEME_SCOPED = [28, 15, 22]
const SUM_OF_THEME_SCOPED = THEME_SCOPED.reduce((a, b) => a + b, 0)

function theme(index: number, scoped: number, composite = 0.29): CapabilityThemeResponse {
  return {
    theme_code: `THEME-${index}`,
    name: `Theme ${index}`,
    description: 'A capability theme.',
    ksi_reference: null,
    icon: null,
    display_order: index,
    total_controls: 100,
    scoped_controls: scoped,
    posture: {
      monitored: 0,
      implemented: scoped,
      ready_for_review: 0,
      in_progress: 0,
      not_started: 0,
      at_risk: 0,
      not_applicable: 0,
      deferred: 0,
    },
    maturity_score: 3,
    implementation_coverage: 0.5,
    implementation_band: 'Moderate',
    maturity_band: 'Moderate',
    evidence_coverage: 0.5,
    evidence_coverage_band: 'Moderate',
    evidence_quality: 0.5,
    evidence_quality_band: 'Moderate',
    evidence_quality_warning: null,
    composite_score: composite,
    composite_band: 'Developing',
  }
}

function stats(): ScopedControlStatsResponse {
  return {
    total_controls: 1534,
    in_scope: DISTINCT_IN_SCOPE,
    implemented: 19,
    not_started: 44,
    in_progress: 62,
    not_applicable: 2,
    at_risk: 0,
    deferred: 0,
    ready_for_review: 0,
    monitored: 0,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getCapabilityThemes).mockResolvedValue({
    themes: THEME_SCOPED.map((scoped, i) => theme(i + 1, scoped)),
  })
  vi.mocked(getCapabilityThemeEvidencePosture).mockResolvedValue({ themes: [] })
  vi.mocked(fetchScopedControlStats).mockResolvedValue(stats())
})

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>
      <CapabilityPosture organizationId={ORG_ID} />
    </QueryClientProvider>
  )
}

describe('Capability Posture scoped-controls headline', () => {
  it('reports the distinct in-scope count from scoped-controls/stats', async () => {
    renderPage()

    const tile = await screen.findByTestId('cp-scoped-controls')
    expect(tile.textContent?.trim()).toBe(String(DISTINCT_IN_SCOPE))
    expect(fetchScopedControlStats).toHaveBeenCalledWith(ORG_ID)
  })

  it('does not report the sum of the per-theme scoped counts', async () => {
    renderPage()

    const tile = await screen.findByTestId('cp-scoped-controls')
    // 65 here stands in for production's 200: the overlapping sum presented as
    // a distinct total. Guarding the inequality directly means a regression to
    // sum(theme.scoped) fails here even if the fixture numbers change.
    expect(SUM_OF_THEME_SCOPED).not.toBe(DISTINCT_IN_SCOPE)
    expect(tile.textContent?.trim()).not.toBe(String(SUM_OF_THEME_SCOPED))
  })

  it('leaves the per-theme scoped figures overlapping, as they should be', async () => {
    renderPage()

    await screen.findByTestId('cp-scoped-controls')
    for (const scoped of THEME_SCOPED) {
      expect(screen.getByText(`${scoped} of 100 scoped`)).toBeInTheDocument()
    }
  })
})

/** Read the `.kpi-value` belonging to the tile whose `.kpi-label` is `label`. */
function kpiValue(container: HTMLElement, label: string): string {
  const labels = Array.from(container.querySelectorAll('.kpi-label'))
  const match = labels.find((el) => el.textContent?.trim() === label)
  if (!match) throw new Error(`no KPI tile labelled ${label}`)
  return match.closest('.kpi-card')?.querySelector('.kpi-value')?.textContent?.trim() ?? ''
}

describe('Capability Posture OVERALL KPS', () => {
  // Issue #808 asked whether OVERALL KPS shares the scoped tile's defect. It
  // does not: each theme's composite_score is computed server-side from that
  // theme's OWN scoped denominator (backend/api/ksi_scoring.py — IC and EC both
  // divide by `scoped − not_applicable` for the single theme), and the page
  // then takes a plain mean across themes. No sum of per-theme counts appears
  // anywhere in that arithmetic. This test pins the property so a later change
  // to a size-weighted mean — which WOULD reintroduce the double-counted
  // denominator — fails here rather than shipping.
  it('is the unweighted mean of per-theme composites, not weighted by theme size', async () => {
    vi.mocked(getCapabilityThemes).mockResolvedValue({
      themes: [theme(1, 28, 0.1), theme(2, 15, 0.4), theme(3, 22, 0.7)],
    })

    const { container } = renderPage()
    await screen.findByTestId('cp-scoped-controls')

    // Unweighted mean: (0.1 + 0.4 + 0.7) / 3 = 0.40 → 40%.
    expect(kpiValue(container, 'OVERALL KPS')).toBe('40%')
    // Weighted by the per-theme scoped counts it would be
    // (28·0.1 + 15·0.4 + 22·0.7) / 65 = 0.372 → 37%.
    expect(kpiValue(container, 'OVERALL KPS')).not.toBe('37%')
  })
})
