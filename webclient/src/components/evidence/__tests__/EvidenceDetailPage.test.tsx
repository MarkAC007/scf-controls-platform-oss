/**
 * EvidenceDetailPage.test.tsx
 *
 * Test coverage per task-3 brief:
 *   - Breadcrumb: back button, breadcrumb label, id chip
 *   - Pager: position text, bounds (first/last), disabled states, "— of N"
 *   - Keyboard: ArrowLeft→onPrev, ArrowRight→onNext, Esc→onBack
 *   - Keyboard suppression in <input>, <textarea>, <select>
 *   - Dropdown guard: Esc suppressed when .theme-menu-panel present
 *   - All sections present (smoke): tracking toggle, collecting system,
 *     maturity stepper, method input, frequency select, comments textarea,
 *     files section, required-by-controls section
 *   - Listener removed on unmount
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi, afterEach } from 'vitest'

// ─── Stubs ────────────────────────────────────────────────────────────────────

// #881: the page resolves the viewer's org role to decide whether the evidence
// file list may offer review and AI-assess controls. These tests are about
// layout and keyboard behaviour, so the role is stubbed rather than provided
// through a real AuthProvider — the gate itself is covered in
// hooks/__tests__/useHasOrgRole.test.ts.
vi.mock('../../../hooks/useHasOrgRole', () => ({
  useHasOrgRole: () => false,
  useIsOrgEditor: () => false,
}))
vi.mock('../MaturityStepper', () => ({
  MaturityStepper: () => <div data-testid="maturity-stepper" />,
}))
vi.mock('../MaturityBadge', () => ({
  MaturityBadge: () => <span data-testid="maturity-badge" />,
}))
vi.mock('../MaturityAdvisoryCard', () => ({
  MaturityAdvisoryCard: () => <div data-testid="maturity-advisory-card" />,
}))
vi.mock('../../maturity', () => ({
  MaturityBadge: () => <span data-testid="maturity-badge" />,
  MaturityStepper: () => <div data-testid="maturity-stepper" />,
  MaturityAdvisoryCard: () => <div data-testid="maturity-advisory-card" />,
}))
vi.mock('../../EvidenceFileUpload', () => ({
  EvidenceFileUpload: () => <div data-testid="evidence-file-upload" />,
}))
vi.mock('../../EvidenceFileList', () => ({
  EvidenceFileList: () => <div data-testid="evidence-file-list" />,
}))
vi.mock('../../EvidenceAssigneeSelect', () => ({
  EvidenceAssigneeSelect: () => <div data-testid="evidence-assignee-select" />,
}))
vi.mock('../../UntrackedUploadNotice', () => ({
  UntrackedUploadNotice: ({ onStartTracking }: { onStartTracking: () => void }) => (
    <button data-testid="untracked-upload-notice" onClick={onStartTracking}>
      Start tracking
    </button>
  ),
}))
vi.mock('../../RecipeCard', () => ({
  RecipeCard: () => <div data-testid="recipe-card" />,
}))
vi.mock('../../RecipeConfidenceBadge', () => ({
  RecipeConfidenceBadge: () => <span data-testid="recipe-confidence-badge" />,
}))
vi.mock('../../EvidenceTemplateGuidance', () => ({
  EvidenceTemplateGuidance: () => <div data-testid="evidence-template-guidance" />,
}))
vi.mock('../WindowReviewPanel', () => ({
  WindowReviewPanel: () => <div data-testid="window-review-panel" />,
}))
vi.mock('../../AssignmentPicker', () => ({
  AssignmentPicker: () => <div data-testid="assignment-picker" />,
}))
vi.mock('../../OwningTeams', () => ({
  default: () => <div data-testid="owning-teams" />,
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
  getEvidenceTracking: () => null,
}))
vi.mock('../../../data/frequencyVocabulary', () => ({
  frequencyOptionsFor: (_current: string | null | undefined) => [
    { value: 'annual', label: 'Annual' },
    { value: 'quarterly', label: 'Quarterly' },
  ],
  FREQUENCY_OPTIONS: [
    { value: 'annual', label: 'Annual' },
    { value: 'quarterly', label: 'Quarterly' },
  ],
}))

// Mock the evidence barrel imports used inside EvidenceDetailPage
vi.mock('../index', () => ({
  RecipeCard: () => <div data-testid="recipe-card" />,
  RecipeConfidenceBadge: () => <span data-testid="recipe-confidence-badge" />,
  EvidenceTemplateGuidance: () => <div data-testid="evidence-template-guidance" />,
  EvidenceFileUpload: () => <div data-testid="evidence-file-upload" />,
  EvidenceFileList: () => <div data-testid="evidence-file-list" />,
  EvidenceAssigneeSelect: () => <div data-testid="evidence-assignee-select" />,
  UntrackedUploadNotice: ({ onStartTracking }: { onStartTracking: () => void }) => (
    <button data-testid="untracked-upload-notice" onClick={onStartTracking}>
      Start tracking
    </button>
  ),
}))

// ─── Import after mocks ───────────────────────────────────────────────────────

import EvidenceDetailPage, { type EvidenceDetailPageProps } from '../EvidenceDetailPage'
import type { ScopedControlsFile } from '../../../types'

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const SCOPING_DATA: ScopedControlsFile = {
  organizationId: 'org-1',
  controls: {},
  evidence_tracking: {},
  scoped_controls: [],
} as unknown as ScopedControlsFile

const EVIDENCE_ITEM = {
  id: 'E-HRS-01',
  title: 'Background Check Records',
  domain: 'Human Resources',
  controlCount: 3,
}

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
    memberTypeOf: () => undefined,
    suggestions: null,
    loadingSuggestions: false,
    collectionGuidance: null,
    loadingGuidance: false,
    feedbackSubmitted: null,
    fileListRefreshTrigger: 0,
    saving: false,
    canManageTeams: false,
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

// ─── Tests ────────────────────────────────────────────────────────────────────

afterEach(() => {
  vi.restoreAllMocks()
})

describe('EvidenceDetailPage', () => {
  // ── Breadcrumb ─────────────────────────────────────────────────────────────

  describe('breadcrumb', () => {
    it('renders "Evidence" as the back button text', () => {
      render(<EvidenceDetailPage {...makeProps()} />)
      expect(screen.getByRole('button', { name: /back to evidence/i })).toBeInTheDocument()
    })

    it('renders the evidence id in the breadcrumb id chip', () => {
      render(<EvidenceDetailPage {...makeProps()} />)
      const idChip = screen.getByTestId('evidence-detail-id')
      expect(idChip.textContent).toBe('E-HRS-01')
    })

    it('fires onBack when the back button is clicked', () => {
      const onBack = vi.fn()
      render(<EvidenceDetailPage {...makeProps({ onBack })} />)
      fireEvent.click(screen.getByRole('button', { name: /back to evidence/i }))
      expect(onBack).toHaveBeenCalledTimes(1)
    })
  })

  // ── Pager ──────────────────────────────────────────────────────────────────

  describe('pager', () => {
    it('renders "3 of 50" position text (1-based)', () => {
      render(<EvidenceDetailPage {...makeProps({ position: { index: 2, total: 50 } })} />)
      expect(screen.getByText('3 of 50')).toBeInTheDocument()
    })

    it('renders "— of N" when index is null (item not in filtered set)', () => {
      render(<EvidenceDetailPage {...makeProps({ position: { index: null, total: 50 } })} />)
      expect(screen.getByText('— of 50')).toBeInTheDocument()
    })

    it('shows no position text when position is null (total unknown)', () => {
      render(<EvidenceDetailPage {...makeProps({ position: null })} />)
      expect(screen.queryByTestId('evidence-detail-position')).not.toBeInTheDocument()
    })

    it('prev button is disabled at index 0', () => {
      render(<EvidenceDetailPage {...makeProps({ position: { index: 0, total: 10 } })} />)
      expect(screen.getByRole('button', { name: 'Previous evidence item' })).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Next evidence item' })).not.toBeDisabled()
    })

    it('next button is disabled at last index', () => {
      render(<EvidenceDetailPage {...makeProps({ position: { index: 9, total: 10 } })} />)
      expect(screen.getByRole('button', { name: 'Next evidence item' })).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Previous evidence item' })).not.toBeDisabled()
    })

    it('both pager buttons are disabled when position is null', () => {
      render(<EvidenceDetailPage {...makeProps({ position: null })} />)
      expect(screen.getByRole('button', { name: 'Previous evidence item' })).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Next evidence item' })).toBeDisabled()
    })

    it('both pager buttons are disabled when index is null', () => {
      render(<EvidenceDetailPage {...makeProps({ position: { index: null, total: 5 } })} />)
      expect(screen.getByRole('button', { name: 'Previous evidence item' })).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Next evidence item' })).toBeDisabled()
    })

    it('prev button fires onPrev', () => {
      const onPrev = vi.fn()
      render(<EvidenceDetailPage {...makeProps({ onPrev, position: { index: 3, total: 10 } })} />)
      fireEvent.click(screen.getByRole('button', { name: 'Previous evidence item' }))
      expect(onPrev).toHaveBeenCalledTimes(1)
    })

    it('next button fires onNext', () => {
      const onNext = vi.fn()
      render(<EvidenceDetailPage {...makeProps({ onNext, position: { index: 3, total: 10 } })} />)
      fireEvent.click(screen.getByRole('button', { name: 'Next evidence item' }))
      expect(onNext).toHaveBeenCalledTimes(1)
    })
  })

  // ── Keyboard ───────────────────────────────────────────────────────────────

  describe('keyboard shortcuts', () => {
    it('ArrowLeft fires onPrev', () => {
      const onPrev = vi.fn()
      render(<EvidenceDetailPage {...makeProps({ onPrev, position: { index: 3, total: 10 } })} />)
      fireEvent.keyDown(window, { key: 'ArrowLeft' })
      expect(onPrev).toHaveBeenCalledTimes(1)
    })

    it('ArrowRight fires onNext', () => {
      const onNext = vi.fn()
      render(<EvidenceDetailPage {...makeProps({ onNext, position: { index: 3, total: 10 } })} />)
      fireEvent.keyDown(window, { key: 'ArrowRight' })
      expect(onNext).toHaveBeenCalledTimes(1)
    })

    it('Escape fires onBack', () => {
      const onBack = vi.fn()
      render(<EvidenceDetailPage {...makeProps({ onBack })} />)
      fireEvent.keyDown(window, { key: 'Escape' })
      expect(onBack).toHaveBeenCalledTimes(1)
    })

    it('ArrowRight inside an <input> does NOT fire onNext', () => {
      const onNext = vi.fn()
      render(
        <div>
          <EvidenceDetailPage {...makeProps({ onNext, position: { index: 3, total: 10 } })} />
          <input data-testid="outer-input" type="text" />
        </div>,
      )
      const input = screen.getByTestId('outer-input')
      fireEvent.keyDown(input, { key: 'ArrowRight', target: input })
      expect(onNext).not.toHaveBeenCalled()
    })

    it('ArrowRight inside a <textarea> does NOT fire onNext', () => {
      const onNext = vi.fn()
      render(
        <div>
          <EvidenceDetailPage {...makeProps({ onNext, position: { index: 3, total: 10 } })} />
          <textarea data-testid="outer-textarea" />
        </div>,
      )
      const ta = screen.getByTestId('outer-textarea')
      fireEvent.keyDown(ta, { key: 'ArrowRight', target: ta })
      expect(onNext).not.toHaveBeenCalled()
    })

    it('Esc is suppressed when .theme-menu-panel is in DOM', () => {
      const onBack = vi.fn()
      const panel = document.createElement('div')
      panel.className = 'theme-menu-panel'
      document.body.appendChild(panel)
      try {
        render(<EvidenceDetailPage {...makeProps({ onBack })} />)
        fireEvent.keyDown(window, { key: 'Escape' })
        expect(onBack).not.toHaveBeenCalled()
      } finally {
        document.body.removeChild(panel)
      }
    })

    it('keyboard listener is removed on unmount', () => {
      const onNext = vi.fn()
      const { unmount } = render(
        <EvidenceDetailPage {...makeProps({ onNext, position: { index: 3, total: 10 } })} />,
      )
      unmount()
      fireEvent.keyDown(window, { key: 'ArrowRight' })
      expect(onNext).not.toHaveBeenCalled()
    })
  })

  // ── Sections present (smoke) ───────────────────────────────────────────────

  describe('sections present (smoke)', () => {
    it('renders the evidence title', () => {
      render(<EvidenceDetailPage {...makeProps()} />)
      expect(screen.getByText('Background Check Records')).toBeInTheDocument()
    })

    it('renders the evidence id in the header', () => {
      render(<EvidenceDetailPage {...makeProps()} />)
      // The id appears in breadcrumb AND header
      expect(screen.getAllByText('E-HRS-01').length).toBeGreaterThanOrEqual(1)
    })

    it('renders domain in the header', () => {
      render(<EvidenceDetailPage {...makeProps()} />)
      expect(screen.getByText('Human Resources')).toBeInTheDocument()
    })

    it('renders the tracking toggle checkbox', () => {
      render(<EvidenceDetailPage {...makeProps()} />)
      expect(screen.getByText(/Evidence Collection Active/i)).toBeInTheDocument()
    })

    it('renders the Collecting System select', () => {
      render(<EvidenceDetailPage {...makeProps()} />)
      expect(screen.getByText(/Collecting System/i)).toBeInTheDocument()
    })

    it('renders the Method of Collection input', () => {
      render(<EvidenceDetailPage {...makeProps()} />)
      expect(screen.getByText(/Method of Collection/i)).toBeInTheDocument()
      expect(
        screen.getByPlaceholderText(/Automated export/i),
      ).toBeInTheDocument()
    })

    it('renders the Frequency select', () => {
      render(<EvidenceDetailPage {...makeProps()} />)
      expect(screen.getByText(/^Frequency$/)).toBeInTheDocument()
    })

    it('renders the Comments textarea', () => {
      render(<EvidenceDetailPage {...makeProps()} />)
      expect(
        screen.getByPlaceholderText(/Additional notes about evidence collection/i),
      ).toBeInTheDocument()
    })

    it('renders the evidence files section header', () => {
      render(<EvidenceDetailPage {...makeProps()} />)
      expect(screen.getByText('Your Evidence Files')).toBeInTheDocument()
    })

    it('renders the Required by Controls section', () => {
      render(<EvidenceDetailPage {...makeProps()} />)
      expect(screen.getByText(/Required by Controls/i)).toBeInTheDocument()
    })

    it('renders the Collection Record section', () => {
      render(<EvidenceDetailPage {...makeProps()} />)
      expect(screen.getByText(/Your Collection Record/i)).toBeInTheDocument()
    })

    it('shows "Not Tracked" badge when is_tracked is false', () => {
      render(<EvidenceDetailPage {...makeProps({ tracking: { is_tracked: false } })} />)
      expect(screen.getByText('Not Tracked')).toBeInTheDocument()
    })

    it('shows "Tracked" badge when is_tracked is true', () => {
      render(<EvidenceDetailPage {...makeProps({ tracking: { is_tracked: true } })} />)
      expect(screen.getByText('Tracked')).toBeInTheDocument()
    })

    it('shows "✓ Active" badge on collection record when tracked', () => {
      render(<EvidenceDetailPage {...makeProps({ tracking: { is_tracked: true } })} />)
      expect(screen.getByText('✓ Active')).toBeInTheDocument()
    })

    it('shows "Saving…" chip when saving is true', () => {
      render(<EvidenceDetailPage {...makeProps({ saving: true })} />)
      expect(screen.getByText('Saving…')).toBeInTheDocument()
    })

    it('renders requiring controls pills when controls are provided', () => {
      const ctrl = {
        scf_id: 'HR-01',
        control_name: 'Personnel Security',
        scf_domain: 'HR',
        control_description: 'Ensure personnel security',
        artifactsResolved: [],
        frameworksResolved: {},
      } as any
      render(<EvidenceDetailPage {...makeProps({ requiringControls: [ctrl] })} />)
      expect(screen.getAllByText(/HR-01/).length).toBeGreaterThanOrEqual(1)
    })

    it('shows "No controls require this evidence" when requiringControls is empty', () => {
      render(<EvidenceDetailPage {...makeProps({ requiringControls: [] })} />)
      expect(screen.getByText(/No controls require this evidence/i)).toBeInTheDocument()
    })

    it('shows evidence-save-hint when no evidenceDbId (tracking has no id)', () => {
      // getEvidenceTracking returns null → no id → shows save hint
      render(<EvidenceDetailPage {...makeProps()} />)
      expect(
        screen.getByText(/Save this evidence tracking to enable tasks, assignments and comments/i),
      ).toBeInTheDocument()
    })
  })

  // ── Esc-to-list state preservation: filter/scroll state lives in parent ─────
  // EvidenceDetailPage is a pure presentation component: it calls onBack and the
  // PARENT (EvidenceReview) clears the selection. This test confirms onBack is
  // the only exit path — no internal state causes a spurious back navigation.

  describe('Esc-to-list (state preservation contract)', () => {
    it('onBack is the sole exit mechanism — Esc calls it exactly once', () => {
      const onBack = vi.fn()
      render(<EvidenceDetailPage {...makeProps({ onBack })} />)
      fireEvent.keyDown(window, { key: 'Escape' })
      expect(onBack).toHaveBeenCalledTimes(1)
    })

    it('multiple Esc presses each call onBack once (no deduplication)', () => {
      const onBack = vi.fn()
      render(<EvidenceDetailPage {...makeProps({ onBack })} />)
      fireEvent.keyDown(window, { key: 'Escape' })
      fireEvent.keyDown(window, { key: 'Escape' })
      expect(onBack).toHaveBeenCalledTimes(2)
    })
  })
})
