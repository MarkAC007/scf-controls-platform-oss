/**
 * Capability Posture — restyle-pinning tests (Phase 5, Task 1).
 *
 * Pins the structural properties that the Explorer restyle introduces:
 *  1. KPI row renders all three values (KPS, scoped-count, at-risk)
 *  2. ThemeCard click navigates to detail view
 *  3. Back button in detail returns to grid
 *
 * These do NOT test visual appearance (CSS classes, colours) — only that
 * the interactive and data-rendering contract is preserved after the restyle.
 */
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CapabilityPosture from '../CapabilityPosture'
import {
  getCapabilityThemes,
  getCapabilityThemeEvidencePosture,
  fetchScopedControlStats,
  getCapabilityThemeControls,
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
    getCapabilityThemeControls: vi.fn(),
    getEvidenceHealth: vi.fn().mockResolvedValue({ items: [] }),
  }
})

const ORG_ID = 'org-restyle'

function makeTheme(index: number, atRisk = 0, composite = 0.55): CapabilityThemeResponse {
  return {
    theme_code: `RESTYLE-${index}`,
    name: `Restyle Theme ${index}`,
    description: 'Pinning test theme.',
    ksi_reference: `KSI-R${index}`,
    icon: null,
    display_order: index,
    total_controls: 50,
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
    maturity_score: 2,
    implementation_coverage: 0.5,
    implementation_band: 'Moderate',
    maturity_band: 'Moderate',
    evidence_coverage: 0.4,
    evidence_coverage_band: 'Developing',
    evidence_quality: 0.3,
    evidence_quality_band: 'Developing',
    evidence_quality_warning: null,
    composite_score: composite,
    composite_band: 'Developing',
  }
}

function makeStats(): ScopedControlStatsResponse {
  return {
    total_controls: 1000,
    in_scope: 87,
    implemented: 40,
    not_started: 20,
    in_progress: 15,
    not_applicable: 2,
    at_risk: 5,
    deferred: 0,
    ready_for_review: 5,
    monitored: 5,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getCapabilityThemes).mockResolvedValue({
    themes: [makeTheme(1, 3), makeTheme(2, 0)],
  })
  vi.mocked(getCapabilityThemeEvidencePosture).mockResolvedValue({ themes: [] })
  vi.mocked(fetchScopedControlStats).mockResolvedValue(makeStats())
  vi.mocked(getCapabilityThemeControls).mockResolvedValue({ controls: [], total: 0 } as unknown as Awaited<ReturnType<typeof getCapabilityThemeControls>>)
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

describe('CapabilityPosture restyle — KPI row', () => {
  it('renders the OVERALL KPS kpi-label', async () => {
    renderPage()
    const label = await screen.findByText('OVERALL KPS')
    expect(label).toBeInTheDocument()
  })

  it('renders the SCOPED CONTROLS kpi-label', async () => {
    renderPage()
    const label = await screen.findByText('SCOPED CONTROLS')
    expect(label).toBeInTheDocument()
  })

  it('renders the AT RISK kpi-label when any theme has at-risk controls', async () => {
    renderPage()
    const label = await screen.findByText('AT RISK')
    expect(label).toBeInTheDocument()
  })

  it('shows the scoped-controls count from the stats endpoint', async () => {
    renderPage()
    const tile = await screen.findByTestId('cp-scoped-controls')
    expect(tile.textContent?.trim()).toBe('87')
  })
})

describe('CapabilityPosture restyle — grid⇄detail navigation', () => {
  it('renders theme cards in the grid view', async () => {
    renderPage()
    await screen.findByText('Restyle Theme 1')
    expect(screen.getByText('Restyle Theme 2')).toBeInTheDocument()
  })

  it('clicking a theme card enters the detail view', async () => {
    renderPage()
    const card = await screen.findByText('Restyle Theme 1')
    fireEvent.click(card.closest('button')!)
    expect(await screen.findByText('Back to Themes')).toBeInTheDocument()
  })

  it('back button returns to the grid view', async () => {
    renderPage()
    const card = await screen.findByText('Restyle Theme 1')
    fireEvent.click(card.closest('button')!)
    const backBtn = await screen.findByText('Back to Themes')
    fireEvent.click(backBtn)
    // Grid should be visible again
    expect(await screen.findByText('Restyle Theme 1')).toBeInTheDocument()
    expect(screen.getByText('Restyle Theme 2')).toBeInTheDocument()
  })
})
