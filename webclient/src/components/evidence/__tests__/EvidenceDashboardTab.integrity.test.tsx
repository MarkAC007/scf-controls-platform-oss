/**
 * EvidenceDashboardTab — numeric and display integrity (#788).
 *
 * Two defects, one shape: the backend computed something honest and the UI
 * either invented a number for it or threw it away.
 *
 *   - `days_until_due: null` ("never collected") used to arrive as -999 and
 *     render as "Overdue (999d)" — a fact the user had no way to know was made
 *     up.
 *   - `skipped_detail` explained why a refresh queued nothing; the component
 *     read only the counts, so "Queued 0 of 1" was the entire explanation.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import EvidenceDashboardTab, {
  groupSkipReasons,
  formatSkipIds,
} from '../EvidenceDashboardTab'
import type { UpcomingEvidenceItem } from '../../../data/apiClient'

vi.mock('../../../data/apiClient', () => ({
  getEvidenceHealth: vi.fn(),
  getUpcomingEvidence: vi.fn(),
  getWindowAssessmentSummary: vi.fn(),
  // #881: the per-file assessment card sits alongside the windowed one.
  // Rejecting is the honest default here — these tests are about freshness and
  // skip reasons, and the card renders nothing when the summary won't load.
  getAssessmentSummary: vi.fn(() => Promise.reject(new Error('not under test'))),
  // #881 WS3: the confirmation queue card is part of this tab now. Rejecting
  // keeps it out of the way of tests about the health cards, and the card
  // renders its own error state rather than affecting anything asserted here.
  getAssessmentReviewQueue: vi.fn(() => Promise.reject(new Error('not under test'))),
  refreshStaleWindowAssessments: vi.fn(),
  // #822 phase 2: the Owner Workload cards resolve member_type through this to
  // badge a contractor owner. Empty means no badge, leaving these tests to
  // assert the freshness and coverage behaviour they were written for.
  getOrgMemberSummaries: vi.fn(() => Promise.resolve([])),
}))

// #881 WS3: the queue card gates its call-to-action on org role, and the role
// hook reads AuthContext. Without this the tab cannot render outside a
// provider at all — see hooks/useHasOrgRole.
vi.mock('../../../hooks/useHasOrgRole', () => ({
  useHasOrgRole: () => false,
  useIsOrgEditor: () => false,
}))

vi.mock('../../../data/scopingService', () => ({
  getScopedControl: vi.fn(() => undefined),
  getEvidenceTracking: vi.fn(() => undefined),
}))

import {
  getEvidenceHealth,
  getUpcomingEvidence,
  getWindowAssessmentSummary,
  refreshStaleWindowAssessments,
} from '../../../data/apiClient'

const emptyHealth = {
  organization_id: 'org-1',
  summary: {
    total_tracked: 0,
    green_count: 0,
    green_pct: 0,
    amber_count: 0,
    amber_pct: 0,
    red_count: 0,
    red_pct: 0,
    unknown_count: 0,
  },
  items: [],
}

function upcoming(overrides: Partial<UpcomingEvidenceItem>): UpcomingEvidenceItem {
  return {
    evidence_id: 'E-AST-01',
    evidence_name: 'Asset inventory',
    frequency: 'monthly',
    collecting_system: 'Manual',
    last_uploaded_at: null,
    next_due: null,
    days_until_due: null,
    is_overdue: true,
    file_count: 0,
    ...overrides,
  } as UpcomingEvidenceItem
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
  vi.mocked(getEvidenceHealth).mockResolvedValue(emptyHealth as any)
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

// ---------------------------------------------------------------------------
// Never collected
// ---------------------------------------------------------------------------

describe('never-collected evidence', () => {
  it('renders "Never collected" instead of a day count', async () => {
    vi.mocked(getUpcomingEvidence).mockResolvedValue({
      items: [upcoming({})],
      total: 1,
    } as any)

    renderTab()

    expect(await screen.findByText('Never collected')).toBeInTheDocument()
  })

  it('never renders the old 999 sentinel', async () => {
    vi.mocked(getUpcomingEvidence).mockResolvedValue({
      items: [upcoming({})],
      total: 1,
    } as any)

    const { container } = renderTab()
    await screen.findByText('Never collected')

    expect(container.textContent).not.toContain('999')
  })

  it('still shows a real overdue count when there is one', async () => {
    vi.mocked(getUpcomingEvidence).mockResolvedValue({
      items: [
        upcoming({
          evidence_id: 'E-AST-02',
          last_uploaded_at: '2026-01-01T00:00:00Z',
          next_due: '2026-02-01T00:00:00Z',
          days_until_due: -12,
          is_overdue: true,
        }),
      ],
      total: 1,
    } as any)

    renderTab()

    expect(await screen.findByText('Overdue (12d)')).toBeInTheDocument()
  })

  it('shows the countdown for an item that is merely due soon', async () => {
    vi.mocked(getUpcomingEvidence).mockResolvedValue({
      items: [
        upcoming({
          evidence_id: 'E-AST-03',
          last_uploaded_at: '2026-08-01T00:00:00Z',
          next_due: '2026-08-31T00:00:00Z',
          days_until_due: 8,
          is_overdue: false,
        }),
      ],
      total: 1,
    } as any)

    renderTab()

    expect(await screen.findByText('In 8d')).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Skip reasons
// ---------------------------------------------------------------------------

describe('refresh skip reasons', () => {
  it('shows why nothing was queued', async () => {
    vi.mocked(refreshStaleWindowAssessments).mockResolvedValue({
      queued: 0,
      candidates: 1,
      skipped: 1,
      skipped_detail: [
        { evidence_id: 'E-AST-01', reason: 'no tracking row or frequency' },
      ],
    } as any)

    renderTab()
    fireEvent.click(await screen.findByText('Reassess Stale Windows'))

    expect(
      await screen.findByText('no tracking row or frequency')
    ).toBeInTheDocument()
    expect(screen.getByText('Queued 0 of 1')).toBeInTheDocument()
  })

  it('names the evidence the reason applies to', async () => {
    vi.mocked(refreshStaleWindowAssessments).mockResolvedValue({
      queued: 0,
      candidates: 1,
      skipped: 1,
      skipped_detail: [
        { evidence_id: 'E-AST-01', reason: 'no frequency set' },
      ],
    } as any)

    renderTab()
    fireEvent.click(await screen.findByText('Reassess Stale Windows'))

    expect(await screen.findByText('E-AST-01')).toBeInTheDocument()
  })

  it('renders nothing when nothing was skipped', async () => {
    vi.mocked(refreshStaleWindowAssessments).mockResolvedValue({
      queued: 3,
      candidates: 3,
      skipped: 0,
      skipped_detail: [],
    } as any)

    const { container } = renderTab()
    fireEvent.click(await screen.findByText('Reassess Stale Windows'))

    await screen.findByText('Queued 3 of 3')
    expect(container.querySelector('.edt-skip-reasons')).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Grouping helpers
// ---------------------------------------------------------------------------

describe('groupSkipReasons', () => {
  it('groups repeats rather than listing one row per evidence id', () => {
    const groups = groupSkipReasons([
      { evidence_id: 'E-1', reason: 'no frequency set' },
      { evidence_id: 'E-2', reason: 'no frequency set' },
      { evidence_id: 'E-3', reason: 'no tracking row' },
    ])

    expect(groups).toHaveLength(2)
    expect(groups[0]).toEqual({
      reason: 'no frequency set',
      ids: ['E-1', 'E-2'],
    })
  })

  it('puts the most common reason first', () => {
    const groups = groupSkipReasons([
      { evidence_id: 'E-1', reason: 'rare' },
      { evidence_id: 'E-2', reason: 'common' },
      { evidence_id: 'E-3', reason: 'common' },
    ])

    expect(groups.map(g => g.reason)).toEqual(['common', 'rare'])
  })

  it('handles an absent or empty list', () => {
    expect(groupSkipReasons([])).toEqual([])
    expect(groupSkipReasons(undefined)).toEqual([])
    expect(groupSkipReasons(null)).toEqual([])
  })

  it('covers the bulk endpoint reason strings too', () => {
    // `assess-windows-bulk` emits a different vocabulary from refresh-stale;
    // both are plain strings, so one grouping path serves both.
    const groups = groupSkipReasons([
      { evidence_id: 'E-1', reason: 'no tracking row' },
      { evidence_id: 'E-2', reason: 'no frequency set' },
    ])

    expect(groups.map(g => g.reason).sort()).toEqual([
      'no frequency set',
      'no tracking row',
    ])
  })

  it('falls back to a label when the reason is blank', () => {
    const groups = groupSkipReasons([{ evidence_id: 'E-1', reason: '' }])

    expect(groups[0].reason).toBe('skipped')
  })
})

describe('formatSkipIds', () => {
  it('lists a short set in full', () => {
    expect(formatSkipIds(['E-1', 'E-2'])).toBe('E-1, E-2')
  })

  it('caps a long set rather than printing a wall of ids', () => {
    const ids = ['E-1', 'E-2', 'E-3', 'E-4', 'E-5', 'E-6', 'E-7']

    expect(formatSkipIds(ids)).toBe('E-1, E-2, E-3, E-4, E-5 and 2 more')
  })
})
