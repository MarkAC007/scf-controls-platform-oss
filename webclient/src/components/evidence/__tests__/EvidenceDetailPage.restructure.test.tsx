/**
 * EvidenceDetailPage.restructure.test.tsx — item C3.
 *
 * Three structural changes to the evidence detail page, and the one regression
 * each of them can cause:
 *
 *   1. Collaborators (the polymorphic `assignments` picker) is removed.
 *   2. Owning teams is promoted out of the collaboration block at the foot of
 *      the page and into "Your Collection Record". The promoted section MUST
 *      carry its `evidenceDbId && organizationId` gate with it — OwningTeams
 *      requires a saved tracking row's database id, so rendering it unguarded
 *      on an unsaved item is the failure this file exists to catch.
 *   3. The assignee PICKER is gone, but `evidence_tracking.assigned_user_id`
 *      is tier 1 of the live notification chain, so an existing assignee is
 *      still shown read-only with a Clear action. A null assignee renders
 *      nothing at all.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

// ─── Stubs ────────────────────────────────────────────────────────────────────

// Mutable so a single mock factory can serve both the "saved item" and the
// "never saved" case — the gate under test reads its id from here.
const trackingRow = vi.hoisted(() => ({ current: null as { id?: string } | null }))

vi.mock('../../../hooks/useHasOrgRole', () => ({
  useHasOrgRole: () => false,
  useIsOrgEditor: () => false,
}))
vi.mock('../../maturity', () => ({
  MaturityBadge: () => <span data-testid="maturity-badge" />,
  MaturityStepper: () => <div data-testid="maturity-stepper" />,
  MaturityAdvisoryCard: () => <div data-testid="maturity-advisory-card" />,
}))
vi.mock('../WindowReviewPanel', () => ({
  WindowReviewPanel: () => <div data-testid="window-review-panel" />,
}))
// The per-user picker this page once mounted no longer exists to mock: it was
// withdrawn with the rest of individual assignment. The assertions below still
// hold, and now hold more strongly -- the section cannot be reinstated here
// under any testid, because there is no component left to reinstate.
vi.mock('../../OwningTeams', () => ({
  default: () => <div data-testid="owning-teams">Owning teams</div>,
}))
vi.mock('../../ModernCommentThread', () => ({
  ModernCommentThread: () => <div data-testid="modern-comment-thread" />,
}))
vi.mock('../../EvidenceTaskList', () => ({
  EvidenceTaskList: () => <div data-testid="evidence-task-list" />,
}))
vi.mock('../../provenance/ScfReference', () => ({
  ScfReference: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))
vi.mock('../../../data/featureFlags', () => ({
  PER_WINDOW_REVIEW_ENABLED: false,
}))
vi.mock('../../../data/scopingService', () => ({
  getScopedControl: () => null,
  getEvidenceTracking: () => trackingRow.current,
}))
vi.mock('../../../data/frequencyVocabulary', () => ({
  frequencyOptionsFor: () => [{ value: 'annual', label: 'Annual' }],
  FREQUENCY_OPTIONS: [{ value: 'annual', label: 'Annual' }],
}))
// The evidence barrel. EvidenceAssigneeSelect is stubbed with a recognisable
// node so "the picker is gone" is a positive assertion, not an assumption.
vi.mock('../index', () => ({
  RecipeCard: () => <div data-testid="recipe-card" />,
  RecipeConfidenceBadge: () => <span data-testid="recipe-confidence-badge" />,
  EvidenceTemplateGuidance: () => <div data-testid="evidence-template-guidance" />,
  EvidenceFileUpload: () => <div data-testid="evidence-file-upload" />,
  EvidenceFileList: () => <div data-testid="evidence-file-list" />,
  EvidenceAssigneeSelect: () => <div data-testid="evidence-assignee-select" />,
  UntrackedUploadNotice: () => <div data-testid="untracked-upload-notice" />,
}))

// ─── Import after mocks ───────────────────────────────────────────────────────

import EvidenceDetailPage, { type EvidenceDetailPageProps } from '../EvidenceDetailPage'
import type { ScopedControlsFile } from '../../../types'

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const SCOPING_DATA = {
  organizationId: 'org-1',
  controls: {},
  evidence_tracking: {},
  scoped_controls: [],
} as unknown as ScopedControlsFile

const EVIDENCE_ITEM = {
  id: 'EHRS01',
  title: 'Background Check Records',
  domain: 'Human Resources',
  controlCount: 3,
}

const JANE = { id: 'user-jane', email: 'jane@example.invalid', display_name: 'Jane Doe' }

function makeProps(overrides: Partial<EvidenceDetailPageProps> = {}): EvidenceDetailPageProps {
  return {
    evidenceItem: EVIDENCE_ITEM,
    tracking: {},
    requiringControls: [],
    position: { index: 2, total: 50 },
    onPrev: vi.fn(),
    onNext: vi.fn(),
    onBack: vi.fn(),
    scopingData: SCOPING_DATA,
    systems: [],
    orgMembers: [],
    suggestions: null,
    loadingSuggestions: false,
    collectionGuidance: null,
    loadingGuidance: false,
    feedbackSubmitted: null,
    fileListRefreshTrigger: 0,
    saving: false,
    canManageTeams: true,
    erlData: {},
    evidenceTemplates: {},
    onUpdateTracking: vi.fn(),
    onRecipeFeedback: vi.fn(),
    onFileUploaded: vi.fn(),
    onReloadTeamAssignments: vi.fn(),
    onNavigateToControl: vi.fn(),
    ...overrides,
  }
}

/** A saved evidence item: tracking row exists, so it has a database id. */
function saved(): void {
  trackingRow.current = { id: 'ev-db-1' }
}

beforeEach(() => {
  trackingRow.current = null
  vi.clearAllMocks()
})

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('EvidenceDetailPage — Collaborators removed', () => {
  it('renders no Collaborators section on a SAVED item, where it used to appear', () => {
    saved()
    render(<EvidenceDetailPage {...makeProps()} />)
    expect(screen.queryByTestId('assignment-picker')).not.toBeInTheDocument()
    expect(screen.queryByText(/collaborators/i)).not.toBeInTheDocument()
  })

  it('still renders the comment thread, which shares the block Collaborators left', () => {
    saved()
    render(<EvidenceDetailPage {...makeProps()} />)
    expect(screen.getByTestId('modern-comment-thread')).toBeInTheDocument()
  })
})

describe('EvidenceDetailPage — Owning teams promoted', () => {
  it('renders Owning teams INSIDE "Your Collection Record"', () => {
    saved()
    render(<EvidenceDetailPage {...makeProps()} />)
    const record = screen.getByTestId('evidence-collection-record')
    const teams = screen.getByTestId('owning-teams')
    expect(record).toContainElement(teams)
  })

  it('no longer renders Owning teams inside the collaboration block', () => {
    saved()
    const { container } = render(<EvidenceDetailPage {...makeProps()} />)
    const collab = container.querySelector('.evidence-collaboration-container')
    expect(collab).not.toBeNull()
    expect(collab?.querySelector('[data-testid="owning-teams"]')).toBeNull()
  })

  // ── The gate-move regression guard ──────────────────────────────────────────
  // OwningTeams needs the tracking row's database id. If the promoted section
  // lost the `evidenceDbId && organizationId` condition on the way up the page,
  // these two are what catch it.

  it('does NOT render Owning teams when the item has no database id (never saved)', () => {
    trackingRow.current = null
    expect(() => render(<EvidenceDetailPage {...makeProps()} />)).not.toThrow()
    expect(screen.getByTestId('evidence-collection-record')).toBeInTheDocument()
    expect(screen.queryByTestId('owning-teams')).not.toBeInTheDocument()
  })

  it('does NOT render Owning teams when organizationId is missing', () => {
    saved()
    const noOrg = { ...SCOPING_DATA, organizationId: undefined } as unknown as ScopedControlsFile
    expect(() =>
      render(<EvidenceDetailPage {...makeProps({ scopingData: noOrg })} />),
    ).not.toThrow()
    expect(screen.getByTestId('evidence-collection-record')).toBeInTheDocument()
    expect(screen.queryByTestId('owning-teams')).not.toBeInTheDocument()
  })
})

describe('EvidenceDetailPage — legacy assignee', () => {
  it('renders no assignee UI at all when assigned_user_id is null', () => {
    saved()
    render(<EvidenceDetailPage {...makeProps({ tracking: { assigned_user_id: null } })} />)
    expect(screen.queryByTestId('evidence-legacy-assignee')).not.toBeInTheDocument()
    expect(screen.queryByTestId('evidence-assignee-select')).not.toBeInTheDocument()
    expect(screen.queryByText(/assignee/i)).not.toBeInTheDocument()
  })

  it('renders no assignee UI at all when assigned_user_id is absent', () => {
    saved()
    render(<EvidenceDetailPage {...makeProps({ tracking: {} })} />)
    expect(screen.queryByTestId('evidence-legacy-assignee')).not.toBeInTheDocument()
    expect(screen.queryByText(/assignee/i)).not.toBeInTheDocument()
  })

  it('renders a read-only legacy row naming the assignee when one is set', () => {
    saved()
    render(
      <EvidenceDetailPage
        {...makeProps({
          tracking: { assigned_user_id: JANE.id, assigned_user: JANE },
        })}
      />,
    )
    const row = screen.getByTestId('evidence-legacy-assignee')
    expect(row).toBeInTheDocument()
    expect(row).toHaveTextContent('Jane Doe')
    expect(row.textContent).toMatch(/legacy/i)
  })

  it('offers no way to SET an assignee — the picker is gone', () => {
    saved()
    render(
      <EvidenceDetailPage
        {...makeProps({
          tracking: { assigned_user_id: JANE.id, assigned_user: JANE },
          orgMembers: [JANE],
        })}
      />,
    )
    expect(screen.queryByTestId('evidence-assignee-select')).not.toBeInTheDocument()
    expect(
      screen.getByTestId('evidence-legacy-assignee').querySelector('select'),
    ).toBeNull()
  })

  it('explains that tier 1 routing continues until the assignee is cleared', () => {
    saved()
    render(
      <EvidenceDetailPage
        {...makeProps({ tracking: { assigned_user_id: JANE.id, assigned_user: JANE } })}
      />,
    )
    const row = screen.getByTestId('evidence-legacy-assignee')
    expect(row.textContent).toMatch(/owning teams/i)
    expect(row.textContent).toMatch(/until/i)
  })

  it('falls back to the raw user id when the assignee no longer resolves', () => {
    saved()
    render(
      <EvidenceDetailPage
        {...makeProps({ tracking: { assigned_user_id: 'user-gone' }, orgMembers: [] })}
      />,
    )
    expect(screen.getByTestId('evidence-legacy-assignee')).toHaveTextContent('user-gone')
  })

  it('resolves the name from orgMembers when the server did not embed the user', () => {
    saved()
    render(
      <EvidenceDetailPage
        {...makeProps({ tracking: { assigned_user_id: JANE.id }, orgMembers: [JANE] })}
      />,
    )
    expect(screen.getByTestId('evidence-legacy-assignee')).toHaveTextContent('Jane Doe')
  })

  // The existing PATCH path is `onUpdateTracking(id, field, value)`. For
  // `assigned_user_id`, '' is the established "unassign" value — scopingService
  // turns it into an explicit null on the wire (see the companion chain test).
  it('Clear sends the unassign value through the existing onUpdateTracking path', () => {
    saved()
    const onUpdateTracking = vi.fn()
    render(
      <EvidenceDetailPage
        {...makeProps({
          tracking: { assigned_user_id: JANE.id, assigned_user: JANE },
          onUpdateTracking,
        })}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /clear/i }))
    expect(onUpdateTracking).toHaveBeenCalledTimes(1)
    expect(onUpdateTracking).toHaveBeenCalledWith('EHRS01', 'assigned_user_id', '')
  })

  it('shows the legacy row on an unsaved item too — it is not behind the team gate', () => {
    trackingRow.current = null
    render(
      <EvidenceDetailPage
        {...makeProps({ tracking: { assigned_user_id: JANE.id, assigned_user: JANE } })}
      />,
    )
    expect(screen.getByTestId('evidence-legacy-assignee')).toBeInTheDocument()
  })
})
