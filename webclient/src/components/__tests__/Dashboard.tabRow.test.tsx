/**
 * Confirms the dashboard tab strip is rendered via the explorer TabRow
 * component (Phase-4 Task 2). Content rendering is covered by other suites;
 * this pins the visual chrome change only.
 */
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { DashboardStats } from '../../hooks/useDashboardStats'
import type { EnrichedControl, ScopedControlsFile } from '../../types'

vi.mock('../../data/apiClient', () => ({
  getEvidenceGaps: vi.fn().mockResolvedValue({ gaps: [] }),
  getFrameworkReadiness: vi.fn().mockResolvedValue({ frameworks: [] }),
}))

vi.mock('../dashboard/WorkQueuePanel', () => ({
  default: () => <div data-testid="work-queue-panel" />,
}))

const useDashboardStats = vi.fn()
vi.mock('../../hooks/useDashboardStats', () => ({
  useDashboardStats: (...args: unknown[]) => useDashboardStats(...args),
}))

import Dashboard from '../Dashboard'

function makeStats(): DashboardStats {
  return {
    selectedCount: 10,
    topDomains: [],
    statusCounts: {
      not_started: 5,
      in_progress: 3,
      implemented: 2,
      at_risk: 0,
      ready_for_review: 0,
      monitored: 0,
      not_applicable: 0,
      deferred: 0,
    },
    implementedPercentage: 20,
    controlsByTeam: {},
    maturityCounts: {},
    averageMaturity: 0,
    totalEvidence: 10,
    trackedEvidence: 2,
    evidencePercentage: 20,
    evidenceByOwnerCounts: {},
    frameworkStats: [],
    evidenceMaturityDistribution: {} as DashboardStats['evidenceMaturityDistribution'],
  }
}

const SCOPING: ScopedControlsFile = {
  organizationId: 'org-1',
  controls: { 'CTL-001': { status: 'implemented' } },
} as unknown as ScopedControlsFile

async function renderDashboard() {
  useDashboardStats.mockReturnValue(makeStats())
  await act(async () => {
    render(
      <Dashboard
        controls={[] as EnrichedControl[]}
        scopingData={SCOPING}
        onScopingDataChange={() => {}}
      />,
    )
  })
}

describe('Dashboard uses explorer TabRow chrome (Phase-4 Task 2)', () => {
  beforeEach(() => {
    useDashboardStats.mockReset()
  })

  it('renders a tablist role for the tab strip', async () => {
    await renderDashboard()
    expect(screen.getByRole('tablist')).toBeInTheDocument()
  })

  it('renders all four tabs with role=tab', async () => {
    await renderDashboard()
    expect(screen.getByRole('tab', { name: 'Implementation' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Maturity' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Evidence' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Frameworks' })).toBeInTheDocument()
  })

  it('marks Implementation as active by default (aria-selected=true)', async () => {
    await renderDashboard()
    const impl = screen.getByRole('tab', { name: 'Implementation' })
    expect(impl).toHaveAttribute('aria-selected', 'true')
  })

  it('switches active tab on click', async () => {
    const user = userEvent.setup()
    await renderDashboard()
    const maturity = screen.getByRole('tab', { name: 'Maturity' })
    await user.click(maturity)
    expect(maturity).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Implementation' })).toHaveAttribute('aria-selected', 'false')
  })
})
