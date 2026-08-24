import { act, render } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { DashboardStats } from '../../hooks/useDashboardStats'
import type { EnrichedControl, ScopedControlsFile } from '../../types'

// The dashboard fires network calls on mount; stub them out so the render is pure.
vi.mock('../../data/apiClient', () => ({
  getEvidenceGaps: vi.fn().mockResolvedValue({ gaps: [] }),
  getFrameworkReadiness: vi.fn().mockResolvedValue({ frameworks: [] }),
}))

const useDashboardStats = vi.fn()
vi.mock('../../hooks/useDashboardStats', () => ({
  useDashboardStats: (...args: unknown[]) => useDashboardStats(...args),
}))

import Dashboard from '../Dashboard'

function makeStats(atRisk: number): DashboardStats {
  return {
    selectedCount: 127,
    topDomains: [],
    statusCounts: {
      not_started: 0,
      in_progress: 62,
      implemented: 19,
      at_risk: atRisk,
      ready_for_review: 0,
      monitored: 0,
      not_applicable: 0,
      deferred: 0,
    },
    implementedPercentage: 15,
    controlsByTeam: {},
    maturityCounts: {},
    averageMaturity: 0,
    totalEvidence: 115,
    trackedEvidence: 13,
    evidencePercentage: 11,
    evidenceByOwnerCounts: {},
    frameworkStats: [],
    evidenceMaturityDistribution: {} as DashboardStats['evidenceMaturityDistribution'],
  }
}

async function renderWithAtRisk(atRisk: number) {
  useDashboardStats.mockReturnValue(makeStats(atRisk))
  let container!: HTMLElement
  // Let the mount-time gap/readiness fetches settle so React does not warn about act().
  await act(async () => {
    container = render(
      <Dashboard
        controls={[] as EnrichedControl[]}
        scopingData={{ controls: {} } as unknown as ScopedControlsFile}
        onScopingDataChange={() => {}}
      />
    ).container
  })
  // "At Risk" also appears in the implementation breakdown, so anchor on the KPI label.
  const label = [...container.querySelectorAll('.kpi-label')].find(el => el.textContent === 'At Risk')
  return label!.closest('.kpi-card') as HTMLElement
}

describe('Dashboard AT RISK tile subtitle (#812)', () => {
  beforeEach(() => {
    useDashboardStats.mockReset()
  })

  it('does not tell the user action is required when nothing is at risk', async () => {
    const card = await renderWithAtRisk(0)
    const subtitle = card.querySelector('.kpi-secondary')

    expect(subtitle).toHaveTextContent('No controls at risk')
    expect(subtitle).not.toHaveTextContent('Immediate action required')
  })

  it('tells the user action is required when controls are at risk', async () => {
    const card = await renderWithAtRisk(4)
    const subtitle = card.querySelector('.kpi-secondary')

    expect(subtitle).toHaveTextContent('Immediate action required')
  })
})
