// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Capability Posture — the AT RISK headline counts each control once (#1279).
 *
 * Same shape of defect as #808 on the SCOPED CONTROLS tile: a control may
 * belong to several capability themes, or to none, so summing the per-theme
 * `posture.at_risk` counts is wrong in both directions. The fixture mirrors
 * the QA finding: three controls at risk, one of them in three themes and one
 * in no theme, which the per-theme sum reports as 4.
 *
 * The headline must come from `scoped-controls/stats.at_risk`, the distinct
 * count the Dashboard reports. The per-theme badges keep their own counts.
 *
 * Placeholder theme codes here are deliberately not real KSI codes.
 */
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CapabilityPosture from '../CapabilityPosture'
import { headlineStats } from '../capability-posture/headlineStats'
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

/** Distinct at-risk controls: one in three themes, one in one, one in none. */
const DISTINCT_AT_RISK = 3

/** Per-theme at-risk counts for that data: they sum to 4, not 3. */
const THEME_AT_RISK = [1, 2, 1, 0]
const SUM_OF_THEME_AT_RISK = THEME_AT_RISK.reduce((a, b) => a + b, 0)

function theme(index: number, atRisk: number, composite: number | null = 0.29): CapabilityThemeResponse {
  return {
    theme_code: `THEME-${index}`,
    name: `Theme ${index}`,
    description: 'A capability theme.',
    ksi_reference: null,
    icon: null,
    display_order: index,
    total_controls: 100,
    scoped_controls: 20,
    posture: {
      monitored: 0,
      implemented: 20 - atRisk,
      ready_for_review: 0,
      in_progress: 0,
      not_started: 0,
      at_risk: atRisk,
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

function stats(atRisk = DISTINCT_AT_RISK): ScopedControlStatsResponse {
  return {
    total_controls: 1534,
    in_scope: 40,
    implemented: 37,
    not_started: 0,
    in_progress: 0,
    not_applicable: 0,
    at_risk: atRisk,
    deferred: 0,
    ready_for_review: 0,
    monitored: 0,
  }
}

const THEMES = THEME_AT_RISK.map((atRisk, i) => theme(i + 1, atRisk))

describe('headlineStats', () => {
  it('takes the at-risk count from the distinct stats, not the per-theme sum', () => {
    expect(SUM_OF_THEME_AT_RISK).not.toBe(DISTINCT_AT_RISK)
    expect(headlineStats(THEMES, stats())?.atRisk).toBe(DISTINCT_AT_RISK)
  })

  it('counts an at-risk control that belongs to no theme', () => {
    const noneAtRiskInThemes = [theme(1, 0), theme(2, 0)]
    expect(headlineStats(noneAtRiskInThemes, stats(1))?.atRisk).toBe(1)
  })

  it('reports no at-risk figure until the stats have loaded', () => {
    expect(headlineStats(THEMES, undefined)?.atRisk).toBeNull()
  })

  it('keeps OVERALL KPS as the unweighted mean of the scored themes', () => {
    const themes = [theme(1, 0, 0.1), theme(2, 0, 0.4), theme(3, 0, 0.7), theme(4, 0, null)]
    expect(headlineStats(themes, stats())?.overallKps).toBeCloseTo(0.4)
    expect(headlineStats([theme(1, 0, null)], stats())?.overallKps).toBeNull()
  })

  it('is null when there are no themes', () => {
    expect(headlineStats([], stats())).toBeNull()
  })
})

/** Read the `.kpi-value` belonging to the tile whose `.kpi-label` is `label`. */
function kpiValue(container: HTMLElement, label: string): string | null {
  const labels = Array.from(container.querySelectorAll('.kpi-label'))
  const match = labels.find((el) => el.textContent?.trim() === label)
  if (!match) return null
  return match.closest('.kpi-card')?.querySelector('.kpi-value')?.textContent?.trim() ?? ''
}

describe('Capability Posture at-risk headline', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getCapabilityThemes).mockResolvedValue({ themes: THEMES })
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

  it('shows the distinct at-risk count on the AT RISK tile', async () => {
    const { container } = renderPage()

    await screen.findByText(String(DISTINCT_AT_RISK), { selector: '.kpi-value' })
    expect(kpiValue(container, 'AT RISK')).toBe(String(DISTINCT_AT_RISK))
  })

  it('leaves the per-theme at-risk badges as they are', async () => {
    renderPage()

    await screen.findByText(String(DISTINCT_AT_RISK), { selector: '.kpi-value' })
    expect(screen.getByText('2 at risk')).toBeInTheDocument()
    expect(screen.getAllByText('1 at risk')).toHaveLength(2)
  })

  it('hides the tile when no control is at risk', async () => {
    vi.mocked(fetchScopedControlStats).mockResolvedValue(stats(0))
    const { container } = renderPage()

    await screen.findByTestId('cp-scoped-controls')
    await screen.findByText('40', { selector: '.kpi-value' })
    expect(kpiValue(container, 'AT RISK')).toBeNull()
  })
})
