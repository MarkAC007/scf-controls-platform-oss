/**
 * The dashboard is one page answering three questions — what needs doing,
 * where we are, who owns it — rather than tabs split by data source.
 */
import { act, render, screen, within } from '@testing-library/react'
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

function makeStats(overrides: Partial<DashboardStats> = {}): DashboardStats {
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
    ...overrides,
  }
}

const SCOPING: ScopedControlsFile = {
  organizationId: 'org-1',
  controls: { 'CTL-001': { status: 'implemented' } },
} as unknown as ScopedControlsFile

async function renderDashboard(overrides: Partial<DashboardStats> = {}) {
  useDashboardStats.mockReturnValue(makeStats(overrides))
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

describe('Dashboard single page', () => {
  beforeEach(() => {
    useDashboardStats.mockReset()
  })

  it('has no tabs', async () => {
    await renderDashboard()
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
    expect(screen.queryByRole('tab')).not.toBeInTheDocument()
  })

  it('renders the three sections in order', async () => {
    await renderDashboard()
    const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    expect(headings).toEqual(['Needs attention', 'Where we are', 'Who owns it'])
  })

  it('puts the work queue under Needs attention', async () => {
    await renderDashboard()
    const attention = screen.getByRole('region', { name: 'Needs attention' })
    expect(within(attention).getByTestId('work-queue-panel')).toBeInTheDocument()
    expect(within(attention).getByText('Evidence not yet tracked')).toBeInTheDocument()
  })

  it('splits the implementation bar by status', async () => {
    await renderDashboard()
    const bar = screen.getByRole('img', { name: /2 implemented, 3 in progress, 0 at risk, 5 not started/ })
    const widths = Array.from(bar.children).map((el) => (el as HTMLElement).style.width)
    expect(widths).toEqual(['20%', '30%', '50%'])
  })

  it('shows an empty state when no control has a maturity level', async () => {
    await renderDashboard()
    expect(screen.getByText(/No control has a maturity level yet/)).toBeInTheDocument()
  })

  it('lists owners with Unassigned first and flagged', async () => {
    await renderDashboard({
      controlsByTeam: { Platform: 6, Unassigned: 3, Security: 1 },
      evidenceByOwnerCounts: { 'a@example.com': { total: 4, tracked: 1 } } as DashboardStats['evidenceByOwnerCounts'],
    })
    const owners = screen.getByRole('region', { name: 'Who owns it' })
    const rows = within(owners).getAllByRole('listitem')
    expect(rows.map((r) => r.querySelector('.dashboard-owner-name')?.textContent)).toEqual([
      'Unassigned',
      'Platform',
      'Security',
      'a@example.com',
    ])
    expect(rows[0]).toHaveClass('is-unassigned')
    expect(within(owners).getByText('1/4 tracked')).toBeInTheDocument()
  })
})
