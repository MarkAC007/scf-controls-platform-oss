/**
 * The health card reads coverage, not arrival (#789, ISC-66).
 *
 * The traffic light on these cards is now computed from how old the evidence's
 * *coverage period* is. The card's caption was still the upload date, which
 * produced the worst possible artefact: a red card that says "Last upload:
 * Today". A user looking at that has no way to tell whether the system is
 * broken or they are.
 *
 * Both facts are still shown — they are both true and both useful — but they
 * are labelled as the different things they are, and the card discloses whether
 * the coverage date is something a preparer asserted or the upload date
 * standing in for one.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, within } from '@testing-library/react'
import EvidenceDashboardTab from '../EvidenceDashboardTab'

vi.mock('../../../data/apiClient', () => ({
  getEvidenceHealth: vi.fn(),
  getUpcomingEvidence: vi.fn(),
  getWindowAssessmentSummary: vi.fn(),
  refreshStaleWindowAssessments: vi.fn(),
}))

vi.mock('../../../data/scopingService', () => ({
  getScopedControl: vi.fn(() => undefined),
  getEvidenceTracking: vi.fn(() => undefined),
}))

import {
  getEvidenceHealth,
  getUpcomingEvidence,
  getWindowAssessmentSummary,
} from '../../../data/apiClient'

function healthItem(overrides: Record<string, unknown> = {}) {
  return {
    evidence_id: 'E-AST-01',
    evidence_name: 'Asset inventory',
    collecting_system: 'Manual',
    frequency: 'quarterly',
    last_file_uploaded_at: '2026-08-23T09:00:00Z',
    days_since_upload: 0,
    coverage_through: '2026-06-30',
    days_since_coverage: 54,
    staleness_basis: 'asserted_period',
    staleness_threshold_days: 90,
    status: 'amber',
    file_count: 2,
    latest_validation_status: null,
    latest_assessment_status: null,
    latest_assessment_score: null,
    control_mappings: [],
    ...overrides,
  }
}

function health(items: Record<string, unknown>[]) {
  const count = (s: string) => items.filter((i) => i.status === s).length
  return {
    organization_id: 'org-1',
    summary: {
      total_tracked: items.length,
      green_count: count('green'),
      green_pct: 0,
      amber_count: count('amber'),
      amber_pct: 0,
      red_count: count('red'),
      red_pct: 0,
      unknown_count: count('unknown'),
    },
    items,
  }
}

function renderTab() {
  return render(
    <EvidenceDashboardTab
      organizationId="org-1"
      controls={[]}
      scopingData={{ controls: {} } as any}
    />
  )
}

beforeEach(() => {
  vi.mocked(getUpcomingEvidence).mockResolvedValue({ items: [], total: 0 } as any)
  vi.mocked(getWindowAssessmentSummary).mockResolvedValue({
    organization_id: 'org-1',
    total_windows_assessed: 0,
    sufficient_count: 0,
    insufficient_count: 0,
    partial_count: 0,
    average_relevance_score: null,
  } as any)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('health card: coverage is the headline', () => {
  it('leads with coverage age, not the upload date', async () => {
    vi.mocked(getEvidenceHealth).mockResolvedValue(health([healthItem()]) as any)
    renderTab()

    const coverage = await screen.findByTestId('ehd-coverage')
    expect(coverage.textContent).toContain('Covers to:')
    expect(coverage.textContent).toContain('54d ago')
  })

  it('keeps the upload date, demoted rather than deleted', async () => {
    vi.mocked(getEvidenceHealth).mockResolvedValue(health([healthItem()]) as any)
    renderTab()

    const upload = await screen.findByTestId('ehd-upload')
    expect(upload.textContent).toBe('Last upload: Today')
  })

  it('no longer captions a stale card with a fresh number', async () => {
    // The regression this whole change exists for: uploaded today, covering a
    // period that ended nearly two months ago, and the card is amber.
    vi.mocked(getEvidenceHealth).mockResolvedValue(health([healthItem()]) as any)
    renderTab()

    const coverage = await screen.findByTestId('ehd-coverage')
    expect(coverage.textContent).not.toContain('Today')
  })
})

describe('health card: basis disclosure', () => {
  it('says when the preparer asserted the period', async () => {
    vi.mocked(getEvidenceHealth).mockResolvedValue(health([healthItem()]) as any)
    renderTab()

    const basis = await screen.findByTestId('ehd-basis')
    expect(basis.textContent).toBe('asserted')
    expect(basis.getAttribute('title')).toContain('asserted the period')
  })

  it('says when the upload date is standing in for one', async () => {
    vi.mocked(getEvidenceHealth).mockResolvedValue(
      health([healthItem({ staleness_basis: 'upload_date', days_since_coverage: 0 })]) as any
    )
    renderTab()

    const basis = await screen.findByTestId('ehd-basis')
    expect(basis.textContent).toBe('from upload date')
    expect(basis.getAttribute('title')).toContain('stands in')
  })

  it('discloses nothing on evidence with no files at all', async () => {
    vi.mocked(getEvidenceHealth).mockResolvedValue(
      health([
        healthItem({
          file_count: 0,
          days_since_upload: null,
          days_since_coverage: null,
          coverage_through: null,
          last_file_uploaded_at: null,
          status: 'red',
        }),
      ]) as any
    )
    renderTab()

    const coverage = await screen.findByTestId('ehd-coverage')
    expect(coverage.textContent).toContain('Never')
    expect(screen.queryByTestId('ehd-basis')).toBeNull()
  })
})

describe('stale alerts list', () => {
  it('orders by coverage age, so the worst-covered is first', async () => {
    vi.mocked(getEvidenceHealth).mockResolvedValue(
      health([
        healthItem({ evidence_id: 'E-FRESH', days_since_coverage: 10, days_since_upload: 200 }),
        healthItem({ evidence_id: 'E-STALE', days_since_coverage: 200, days_since_upload: 0, status: 'red' }),
      ]) as any
    )
    renderTab()

    // ``findAllByText`` because each id appears twice on the page: once in the
    // alerts list and once on its own card in the grid below it.
    await screen.findAllByText('E-STALE')
    const list = document.querySelector('.edt-stale-list') as HTMLElement
    const ids = within(list)
      .getAllByText(/^E-/)
      .map((n) => n.textContent)
    expect(ids).toEqual(['E-STALE', 'E-FRESH'])
  })

  it('stops calling days-since-upload "overdue"', async () => {
    vi.mocked(getEvidenceHealth).mockResolvedValue(health([healthItem()]) as any)
    renderTab()

    const age = await screen.findByTestId('edt-stale-age')
    expect(age.textContent).toBe('Covers to 54d ago')
    expect(age.textContent).not.toContain('overdue')
  })

  it('says so plainly when nothing has ever been covered', async () => {
    vi.mocked(getEvidenceHealth).mockResolvedValue(
      health([healthItem({ days_since_coverage: null, coverage_through: null, status: 'red' })]) as any
    )
    renderTab()

    const age = await screen.findByTestId('edt-stale-age')
    expect(age.textContent).toBe('No coverage')
  })
})
