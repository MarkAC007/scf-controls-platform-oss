/**
 * Confirms the EvidenceReview evidence-list surface.
 *
 * Explorer principle: bare arrival at ?tab=evidence&view=workspace (no ?item=)
 * lands on the LIST, not the detail page. Auto-select fires (guard-pinned) and
 * picks the first item, but evidenceDetailOpen is false, so the list renders.
 *
 * Detail is shown only when:
 *   a) the URL already has ?item= (deep-link) — seeded true in useState
 *   b) the user clicks a row — selectEvidence sets it true
 *   c) Back / Esc — handleEvidenceBack sets it false → list again
 */
import { render, screen, fireEvent, act } from '@testing-library/react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import EvidenceReview from '../EvidenceReview'
import type { EnrichedControl, ScopedControlsFile } from '../../types'

// Heavy dependencies that don't affect list panel structure
vi.mock('../../data/apiClient', () => ({
  getSystems: vi.fn().mockResolvedValue([]),
  getOrgMembers: vi.fn().mockResolvedValue([]),
  listTeams: vi.fn().mockResolvedValue([]),
  batchAssignTeamToItems: vi.fn().mockResolvedValue({
    type: 'evidence',
    team_id: 'team-1',
    created: 0,
    updated: 0,
    demoted: 0,
    notified: 0,
  }),
  getEvidenceSuggestions: vi.fn().mockResolvedValue(null),
  submitRecipeFeedback: vi.fn().mockResolvedValue(undefined),
  batchUpdateEvidenceTracking: vi.fn().mockResolvedValue({
    evidence: [],
    updated: 0,
    created: 0,
    failed: 0,
    errors: [],
  }),
}))
vi.mock('../../data/scopingService', () => ({
  saveScopedControls: vi.fn().mockResolvedValue(undefined),
  getScopedControl: vi.fn().mockReturnValue({ selected: true }),
  getEvidenceTracking: vi.fn().mockReturnValue(null),
  updateEvidenceTracking: vi.fn().mockResolvedValue({}),
}))
vi.mock('../../hooks/useOrgMemberTypes', () => ({
  useOrgMemberTypes: () => ({ memberTypeOf: () => undefined }),
}))
vi.mock('../../hooks/useTeamAssignments', () => ({
  useTeamAssignments: () => ({
    accountableFor: () => null,
    teamsFor: () => [],
    reload: vi.fn(),
  }),
  matchesTeamFilters: () => true,
  accountableTeamLabel: () => null,
}))
vi.mock('../../hooks/useIsOrgAdmin', () => ({
  useIsOrgAdmin: () => false,
}))
vi.mock('../../hooks/useTeamFilteredEvidence', () => ({
  useTeamFilteredEvidence: () => ({ trackingIds: null, loading: false, error: null }),
}))
vi.mock('../TeamListFilters', () => ({
  default: () => <div data-testid="team-filters" />,
  ALL: 'all',
}))
vi.mock('../AccountableOwnerTypeFilter', () => ({
  default: () => <div data-testid="owner-type-filter" />,
  ALL_OWNER_TYPES: 'all',
}))
// Stub EvidenceDetailPage so we can inspect props without rendering its full tree.
vi.mock('../evidence/EvidenceDetailPage', () => ({
  default: ({
    evidenceItem,
    tracking,
    onBack,
  }: {
    evidenceItem: { id: string }
    tracking: Record<string, unknown>
    onBack: () => void
  }) => (
    <div
      data-testid="evidence-detail-page"
      data-evidence-id={evidenceItem.id}
      data-has-tracking={JSON.stringify(tracking)}
    >
      <button data-testid="back-button" onClick={onBack}>Back</button>
    </div>
  ),
}))

const artifact = { id: 'E-TST-01', title: 'Test Evidence', domain: 'Testing' }
const control: EnrichedControl = {
  scf_id: 'TST-01',
  control_name: 'Test Control',
  scf_domain: 'Testing',
  artifactsResolved: [artifact],
} as unknown as EnrichedControl

const scopingData: ScopedControlsFile = {
  organizationId: 'org-1',
  controls: { 'TST-01': { selected: true } },
  evidence_tracking: {},
} as unknown as ScopedControlsFile

beforeEach(() => {
  // Bare arrival: no ?item=
  window.history.replaceState({}, '', '/?tab=evidence&view=workspace')
})
afterEach(() => {
  window.history.replaceState({}, '', '/')
})

describe('bare workspace arrival lands on the list', () => {
  it('shows the FilterSidebar when no ?item= in URL', () => {
    render(
      <EvidenceReview
        controls={[control]}
        scopingData={scopingData}
        onScopingDataChange={() => {}}
      />,
    )
    // The list renders — FilterSidebar aside must be present
    expect(document.querySelector('aside')).not.toBeNull()
    // Detail page must NOT be visible
    expect(screen.queryByTestId('evidence-detail-page')).toBeNull()
  })

  it('shows the search input in the toolbar', () => {
    render(
      <EvidenceReview
        controls={[control]}
        scopingData={scopingData}
        onScopingDataChange={() => {}}
      />,
    )
    expect(screen.queryByPlaceholderText(/search/i)).not.toBeNull()
    expect(screen.queryByTestId('evidence-detail-page')).toBeNull()
  })

  it('renders evidence rows in the list', () => {
    render(
      <EvidenceReview
        controls={[control]}
        scopingData={scopingData}
        onScopingDataChange={() => {}}
      />,
    )
    // The evidence row has data-evidence-id set
    expect(document.querySelector('[data-evidence-id="E-TST-01"]')).not.toBeNull()
    expect(screen.queryByTestId('evidence-detail-page')).toBeNull()
  })
})

describe('deep-link arrival with ?item= shows detail page', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/?tab=evidence&view=workspace&item=E-TST-01')
  })

  it('renders EvidenceDetailPage immediately', () => {
    render(
      <EvidenceReview
        controls={[control]}
        scopingData={scopingData}
        onScopingDataChange={() => {}}
      />,
    )
    const stub = screen.getByTestId('evidence-detail-page')
    expect(stub).toBeInTheDocument()
    expect(stub.getAttribute('data-evidence-id')).toBe('E-TST-01')
  })
})

describe('row click → detail; Back → list', () => {
  it('opens detail when a row is clicked', () => {
    render(
      <EvidenceReview
        controls={[control]}
        scopingData={scopingData}
        onScopingDataChange={() => {}}
      />,
    )
    // Start: list is visible, no detail
    expect(screen.queryByTestId('evidence-detail-page')).toBeNull()

    // The ExplorerListRow renders a role=button inside the evidence-card-modern
    // wrapper. The onClick handler lives on that inner element.
    const card = document.querySelector('[data-evidence-id="E-TST-01"]') as HTMLElement
    expect(card).not.toBeNull()
    const rowButton = card.querySelector('[role="button"]') as HTMLElement
    expect(rowButton).not.toBeNull()
    act(() => {
      fireEvent.click(rowButton)
    })

    // Detail page should now be visible
    expect(screen.getByTestId('evidence-detail-page')).toBeInTheDocument()
  })

  it('returns to list when Back is pressed from detail', () => {
    window.history.replaceState({}, '', '/?tab=evidence&view=workspace&item=E-TST-01')
    render(
      <EvidenceReview
        controls={[control]}
        scopingData={scopingData}
        onScopingDataChange={() => {}}
      />,
    )
    // Detail is open (deep-link)
    expect(screen.getByTestId('evidence-detail-page')).toBeInTheDocument()

    // Press Back
    act(() => {
      fireEvent.click(screen.getByTestId('back-button'))
    })

    // List is restored
    expect(screen.queryByTestId('evidence-detail-page')).toBeNull()
    expect(document.querySelector('aside')).not.toBeNull()
  })
})

describe('bare arrival selects nothing', () => {
  it('paints no active row and leaves the URL without ?item=', () => {
    render(
      <EvidenceReview
        controls={[control]}
        scopingData={scopingData}
        onScopingDataChange={() => {}}
      />,
    )
    // No phantom selection: the old master-detail auto-select claimed the
    // first item and rewrote the URL; the full-page detail world must not.
    expect(document.querySelector('.evidence-card-modern.active')).toBeNull()
    expect(window.location.search).not.toContain('item=')
  })
})
