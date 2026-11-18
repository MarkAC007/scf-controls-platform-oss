// SPDX-License-Identifier: AGPL-3.0-only
/**
 * The dashboard is one page answering four questions — what needs doing,
 * where we are, who owns it, how it has changed — rather than tabs split by data source.
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

vi.mock('../dashboard/IsmsSummaryPanel', () => ({
  default: ({ organizationId }: { organizationId: string }) => (
    <section aria-labelledby="isms-stub" data-testid="isms-summary-panel" data-org={organizationId}>
      <h2 id="isms-stub">ISMS metrics</h2>
    </section>
  ),
}))

vi.mock('../dashboard/PostureHistoryPanel', () => ({
  default: ({ organizationId, onOpenDocuments }: { organizationId: string; onOpenDocuments?: () => void }) => (
    <button type="button" data-testid="posture-history-panel" data-org={organizationId} onClick={onOpenDocuments} />
  ),
}))

const useDashboardStats = vi.fn()
// The control maturity card reads its targets through its own component; here
// it is a marker, and `ControlTargetStanding.test.tsx` covers what it draws.
vi.mock('../dashboard/ControlTargetStanding', () => ({
  default: () => <div data-testid="control-target-standing" />,
}))

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

async function renderDashboard(overrides: Partial<DashboardStats> = {}, onNavigateToDocuments?: () => void) {
  useDashboardStats.mockReturnValue(makeStats(overrides))
  await act(async () => {
    render(
      <Dashboard
        controls={[] as EnrichedControl[]}
        scopingData={SCOPING}
        onScopingDataChange={() => {}}
        onNavigateToDocuments={onNavigateToDocuments}
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

  it('renders the sections in order', async () => {
    await renderDashboard()
    const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    expect(headings).toEqual(['ISMS metrics', 'Needs attention', 'Where we are', 'Who owns it', 'How it has changed'])
  })

  it('puts the ISMS metrics summary for this organisation between the posture figures and Needs attention', async () => {
    await renderDashboard()
    const panel = screen.getByTestId('isms-summary-panel')
    expect(panel).toHaveAttribute('data-org', 'org-1')
    const figures = document.querySelector('.dashboard-kpis')!
    const attention = screen.getByRole('region', { name: 'Needs attention' })
    expect(figures.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(panel.compareDocumentPosition(attention) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('has no ISMS metrics summary before an organisation is known', async () => {
    useDashboardStats.mockReturnValue(makeStats())
    await act(async () => {
      render(
        <Dashboard
          controls={[] as EnrichedControl[]}
          scopingData={{ ...SCOPING, organizationId: undefined } as unknown as ScopedControlsFile}
          onScopingDataChange={() => {}}
        />,
      )
    })
    expect(screen.queryByTestId('isms-summary-panel')).not.toBeInTheDocument()
  })

  it('leaves the At risk figure plain when no control is at risk', async () => {
    await renderDashboard()
    expect(document.querySelector('.dashboard-kpis .kpi-risk')).toBeNull()
  })

  it('marks the At risk figure when a control is at risk', async () => {
    await renderDashboard({ statusCounts: { ...makeStats().statusCounts, at_risk: 4 } })
    const marked = document.querySelector('.dashboard-kpis .kpi-risk')!
    expect(within(marked as HTMLElement).getByText('At Risk')).toBeInTheDocument()
    expect(within(marked as HTMLElement).getByText('4')).toBeInTheDocument()
  })

  it('puts the posture snapshots under How it has changed, linked to Generated Documents', async () => {
    const toDocuments = vi.fn()
    await renderDashboard({}, toDocuments)
    const change = screen.getByRole('region', { name: 'How it has changed' })
    const panel = within(change).getByTestId('posture-history-panel')
    expect(panel).toHaveAttribute('data-org', 'org-1')
    panel.click()
    expect(toDocuments).toHaveBeenCalledTimes(1)
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
    expect(screen.queryByTestId('control-target-standing')).not.toBeInTheDocument()
  })

  it('presents control maturity and evidence collection maturity the same way', async () => {
    await renderDashboard({
      maturityCounts: { L0: 1, L1: 2, L2: 3, L3: 4, L4: 0, L5: 0, unset: 7 },
      evidenceMaturityDistribution: { L0: 0, L1: 0, L2: 6, L3: 2, L4: 0, L5: 0 },
    })
    const status = screen.getByRole('region', { name: 'Where we are' })
    const cards = Array.from(status.querySelectorAll<HTMLElement>('.dashboard-maturity-grid > .maturity-distribution-widget'))
    expect(cards.map((c) => within(c).getByRole('heading', { level: 3 }).textContent)).toEqual([
      'Control maturity', 'Evidence collection maturity',
    ])
    // The same parts in the same order in both: score, count, six level rows.
    const shape = (c: HTMLElement) => Array.from(c.querySelectorAll('.maturity-distribution-score, .maturity-distribution-summary, .maturity-distribution-row'))
      .map((el) => el.className.split(' ')[0])
    expect(shape(cards[0])).toEqual(shape(cards[1]))
    expect(shape(cards[0])).toHaveLength(8)
    // Controls alone carry a target: it has its own card under the pair, and
    // the control score is left ungraded for the target to judge.
    expect(status.querySelector('.dashboard-maturity-grid')!.nextElementSibling).toBe(
      within(status).getByTestId('control-target-standing'),
    )
    expect(cards[0].querySelector('.maturity-grade')).toBeNull()
    expect(cards[0].querySelector('.maturity-score-label')).toHaveTextContent('average level')
    expect(cards[1].querySelector('.maturity-grade')).not.toBeNull()
    expect(cards[0].querySelector('.maturity-distribution-summary')).toHaveTextContent(/^10\s*controls assessed$/)
    expect(cards[1].querySelector('.maturity-distribution-summary')).toHaveTextContent(/^8\s*evidence items assessed$/)
    // Controls without a level are not counted as assessed.
    expect(cards[0].querySelector('.maturity-score-value')).toHaveTextContent('2.0')
    expect(cards[1].querySelector('.maturity-score-value')).toHaveTextContent('2.3')
  })

  it('names the control levels as the control screens do, not with the evidence scale', async () => {
    await renderDashboard({ maturityCounts: { L0: 0, L1: 0, L2: 3, L3: 0, L4: 0, L5: 0 } })
    const card = document.querySelector<HTMLElement>('.dashboard-maturity-grid > .maturity-distribution-widget')!
    expect(within(card).getByText('Defined', { selector: '.maturity-distribution-name' })).toBeInTheDocument()
    expect(within(card).queryByText('Developing', { selector: '.maturity-distribution-name' })).not.toBeInTheDocument()
  })

  it('shows control maturity when every rated control is at L0', async () => {
    await renderDashboard({ maturityCounts: { L0: 4, L1: 0, L2: 0, L3: 0, L4: 0, L5: 0 } })
    expect(screen.queryByText(/No control has a maturity level yet/)).not.toBeInTheDocument()
    expect(document.querySelector('.dashboard-maturity-grid .maturity-distribution-summary')).toHaveTextContent(/^4\s*controls assessed$/)
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
