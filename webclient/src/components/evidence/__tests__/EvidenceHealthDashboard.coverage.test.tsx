/**
 * The *other* health card reads coverage too (#789, ISC-66).
 *
 * There are two copies of this card — one here, one in EvidenceDashboardTab —
 * and they are rendered by different screens. Consolidating them is a separate
 * change; what this file does is make the duplication expensive to break. A
 * mutation sweep found that a fix applied to one copy and not the other went
 * entirely unnoticed, which is precisely the failure mode duplication produces.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import EvidenceHealthDashboard from '../EvidenceHealthDashboard'

vi.mock('../../../data/apiClient', () => ({
  getEvidenceHealth: vi.fn(),
}))

import { getEvidenceHealth } from '../../../data/apiClient'

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

function respond(items: Record<string, unknown>[]) {
  vi.mocked(getEvidenceHealth).mockResolvedValue({
    organization_id: 'org-1',
    summary: {
      total_tracked: items.length,
      green_count: 0,
      green_pct: 0,
      amber_count: items.filter((i) => i.status === 'amber').length,
      amber_pct: 0,
      red_count: items.filter((i) => i.status === 'red').length,
      red_pct: 0,
      unknown_count: 0,
    },
    items,
  } as any)
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

beforeEach(() => {
  respond([healthItem()])
})

describe('EvidenceHealthDashboard health card', () => {
  it('leads with coverage age, not the upload date', async () => {
    render(<EvidenceHealthDashboard organizationId="org-1" />)

    const coverage = await screen.findByTestId('ehd-coverage')
    expect(coverage.textContent).toContain('Covers to:')
    expect(coverage.textContent).toContain('54d ago')
    expect(coverage.textContent).not.toContain('Today')
  })

  it('keeps the upload date as a secondary line', async () => {
    render(<EvidenceHealthDashboard organizationId="org-1" />)

    const upload = await screen.findByTestId('ehd-upload')
    expect(upload.textContent).toBe('Last upload: Today')
  })

  it('discloses whether the coverage date was asserted or inferred', async () => {
    render(<EvidenceHealthDashboard organizationId="org-1" />)
    expect((await screen.findByTestId('ehd-basis')).textContent).toBe('asserted')

    cleanup()
    respond([healthItem({ staleness_basis: 'upload_date' })])
    render(<EvidenceHealthDashboard organizationId="org-1" />)
    expect((await screen.findByTestId('ehd-basis')).textContent).toBe('from upload date')
  })
})
