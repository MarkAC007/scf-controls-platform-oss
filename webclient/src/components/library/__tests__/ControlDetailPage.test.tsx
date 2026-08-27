/**
 * ControlDetailPage.test.tsx — TDD tests for the full-width control detail page.
 *
 * Step 1 (RED): all tests fail because ControlDetailPage does not yet exist.
 *
 * Test coverage:
 *   - Renders id, title, assessment question
 *   - Parity spot-checks: artifacts section, mappings tab switch, assessment tab mocked
 *   - Pager: buttons fire onPrev/onNext, disabled at position null and bounds
 *   - Keyboard: ArrowRight fires onNext, Esc fires onBack, ArrowRight in <input> does NOT fire
 *   - Back button fires onBack
 *   - Evidence card action fires onNavigateToEvidence
 */
import { fireEvent, render, screen, act } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'

// ─── Stubs ────────────────────────────────────────────────────────────────────

// Mock subcomponents that make external calls
vi.mock('../../AssessmentObjectivesList', () => ({
  default: ({ scfId }: { scfId: string }) => (
    <div data-testid="assessment-objectives-list" data-scf-id={scfId}>
      AssessmentObjectivesList mock
    </div>
  ),
}))

vi.mock('../../CDMControlPanel', () => ({
  default: ({ organizationId }: { organizationId: string }) => (
    <div data-testid="cdm-control-panel" data-org-id={organizationId}>
      CDMControlPanel mock
    </div>
  ),
}))

vi.mock('../../GraphView', () => ({
  default: () => <div data-testid="graph-view">GraphView mock</div>,
}))

vi.mock('../../MaturityRoadmap', () => ({
  default: () => <div data-testid="maturity-roadmap">MaturityRoadmap mock</div>,
}))

vi.mock('../../BusinessSizeGuidance', () => ({
  default: () => <div data-testid="business-size-guidance">BusinessSizeGuidance mock</div>,
}))

vi.mock('../../SCRMFocusBadges', () => ({
  default: () => <div data-testid="scrm-focus-badges">SCRMFocusBadges mock</div>,
}))

vi.mock('../../RiskThreatContext', () => ({
  default: () => <div data-testid="risk-threat-context">RiskThreatContext mock</div>,
}))

vi.mock('../../provenance/WorkspaceRecord', () => ({
  WorkspaceRecord: ({ children, title }: { children: React.ReactNode; title: string }) => (
    <div data-testid="workspace-record" data-title={title}>
      {children}
    </div>
  ),
}))

vi.mock('../../DeprecatedBadge', async () => {
  const actual = await vi.importActual<typeof import('../../DeprecatedBadge')>('../../DeprecatedBadge')
  return {
    default: () => <span data-testid="deprecated-badge" />,
    getCatalogLifecycle: actual.getCatalogLifecycle,
    isDeprecated: actual.isDeprecated,
  }
})

// Mock health data fetch — resolve synchronously to avoid act() warnings
vi.mock('../../../data/apiClient', () => ({
  getEvidenceHealth: vi.fn().mockResolvedValue({ items: [] }),
}))

vi.mock('../../../data/scopingService', () => ({
  getEvidenceTracking: vi.fn().mockReturnValue(null),
}))

// ─── Import after mocks ───────────────────────────────────────────────────────

import ControlDetailPage, { type ControlDetailPageProps } from '../ControlDetailPage'
import type { EnrichedControl } from '../../../types'

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeControl(overrides: Partial<EnrichedControl> = {}): EnrichedControl {
  return {
    scf_id: 'GOV-04',
    scf_domain: 'Governance',
    control_name: 'Assigned Security Responsibilities',
    control_description: 'Mechanisms exist to assign security responsibilities.',
    control_question: 'Does the organization assign security responsibilities?',
    nist_csf_function: 'Govern',
    control_weighting: 8,
    pptdf_applicability: { people: true, process: false, technology: false, data: false, facility: false },
    artifactsResolved: [
      { id: 'EVI-001', title: 'Security Policy', domain: 'Governance' },
      { id: 'EVI-002', title: 'Role Definitions', domain: 'Governance' },
    ],
    frameworksResolved: {
      'ISO 27001': ['A.5.2'],
      'SOC 2': ['CC1.2'],
      'Cyber Essentials': ['CE-1'],
      'NIST 800-53': ['PM-2'],
    },
    frameworksCount: 4,
    ...overrides,
  }
}

function makeProps(overrides: Partial<ControlDetailPageProps> = {}): ControlDetailPageProps {
  return {
    control: makeControl(),
    position: { index: 8, total: 346 },
    onPrev: vi.fn(),
    onNext: vi.fn(),
    onBack: vi.fn(),
    onNavigateToEvidence: vi.fn(),
    ...overrides,
  }
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('ControlDetailPage', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  // ── Rendering: id, title, assessment question ─────────────────────────────

  it('renders the control scf_id in the breadcrumb and header', () => {
    render(<ControlDetailPage {...makeProps()} />)
    // The scf_id appears in multiple places — breadcrumb and header
    const ids = screen.getAllByText('GOV-04')
    expect(ids.length).toBeGreaterThanOrEqual(1)
  })

  it('renders the control title', () => {
    render(<ControlDetailPage {...makeProps()} />)
    expect(screen.getByText('Assigned Security Responsibilities')).toBeInTheDocument()
  })

  it('renders the assessment question in a quote block', () => {
    render(<ControlDetailPage {...makeProps()} />)
    expect(
      screen.getByText(/Does the organization assign security responsibilities/),
    ).toBeInTheDocument()
  })

  it('renders the control description', () => {
    render(<ControlDetailPage {...makeProps()} />)
    expect(
      screen.getByText(/Mechanisms exist to assign security responsibilities/),
    ).toBeInTheDocument()
  })

  // ── Breadcrumb bar ────────────────────────────────────────────────────────

  it('renders breadcrumb back button with correct aria-label', () => {
    render(<ControlDetailPage {...makeProps()} />)
    const backBtn = screen.getByRole('button', { name: /controls/i })
    expect(backBtn).toBeInTheDocument()
  })

  it('renders "9 of 346" position text (1-based display)', () => {
    render(<ControlDetailPage {...makeProps({ position: { index: 8, total: 346 } })} />)
    expect(screen.getByText(/9 of 346/)).toBeInTheDocument()
  })

  it('renders "— of N" when position has index null (item not in filtered set)', () => {
    // { index: null, total } → item resolved via deep-link but not in filtered set
    render(<ControlDetailPage {...makeProps({ position: { index: null, total: 100 } })} />)
    // Should show em-dash position text
    expect(screen.getByText(/— of 100/)).toBeInTheDocument()
    // Both pager buttons are disabled
    const prevBtn = screen.getByRole('button', { name: 'Previous control' })
    const nextBtn = screen.getByRole('button', { name: 'Next control' })
    expect(prevBtn).toBeDisabled()
    expect(nextBtn).toBeDisabled()
  })

  it('renders no position text when position is null (total unknown)', () => {
    // position null → total unknown (still resolving), no position text shown
    render(<ControlDetailPage {...makeProps({ position: null })} />)
    expect(screen.queryByText(/of \d+/)).not.toBeInTheDocument()
    // Pager buttons still present but disabled
    expect(screen.getByRole('button', { name: 'Previous control' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next control' })).toBeDisabled()
  })

  // ── Pager buttons ─────────────────────────────────────────────────────────

  it('prev/next buttons fire onPrev and onNext', () => {
    const onPrev = vi.fn()
    const onNext = vi.fn()
    render(<ControlDetailPage {...makeProps({ onPrev, onNext, position: { index: 5, total: 10 } })} />)
    fireEvent.click(screen.getByRole('button', { name: 'Previous control' }))
    expect(onPrev).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: 'Next control' }))
    expect(onNext).toHaveBeenCalledTimes(1)
  })

  it('prev button is disabled at first index (index 0)', () => {
    render(<ControlDetailPage {...makeProps({ position: { index: 0, total: 10 } })} />)
    expect(screen.getByRole('button', { name: 'Previous control' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next control' })).not.toBeDisabled()
  })

  it('next button is disabled at last index (index === total - 1)', () => {
    render(<ControlDetailPage {...makeProps({ position: { index: 9, total: 10 } })} />)
    expect(screen.getByRole('button', { name: 'Next control' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Previous control' })).not.toBeDisabled()
  })

  it('both pager buttons are disabled when position is null', () => {
    render(<ControlDetailPage {...makeProps({ position: null })} />)
    expect(screen.getByRole('button', { name: 'Previous control' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next control' })).toBeDisabled()
  })

  // ── Back button ───────────────────────────────────────────────────────────

  it('back button fires onBack', () => {
    const onBack = vi.fn()
    render(<ControlDetailPage {...makeProps({ onBack })} />)
    const backBtn = screen.getByRole('button', { name: /controls/i })
    fireEvent.click(backBtn)
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  // ── Keyboard shortcuts ────────────────────────────────────────────────────

  it('ArrowRight key fires onNext', () => {
    const onNext = vi.fn()
    render(<ControlDetailPage {...makeProps({ onNext, position: { index: 5, total: 10 } })} />)
    fireEvent.keyDown(window, { key: 'ArrowRight' })
    expect(onNext).toHaveBeenCalledTimes(1)
  })

  it('ArrowLeft key fires onPrev', () => {
    const onPrev = vi.fn()
    render(<ControlDetailPage {...makeProps({ onPrev, position: { index: 5, total: 10 } })} />)
    fireEvent.keyDown(window, { key: 'ArrowLeft' })
    expect(onPrev).toHaveBeenCalledTimes(1)
  })

  it('Escape key fires onBack', () => {
    const onBack = vi.fn()
    render(<ControlDetailPage {...makeProps({ onBack })} />)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('ArrowRight typed inside an <input> does NOT fire onNext', () => {
    const onNext = vi.fn()
    const { container } = render(
      <div>
        <ControlDetailPage {...makeProps({ onNext, position: { index: 5, total: 10 } })} />
        <input data-testid="text-input" type="text" />
      </div>,
    )
    const input = container.querySelector('input[data-testid="text-input"]') as HTMLInputElement
    fireEvent.keyDown(input, { key: 'ArrowRight', target: input })
    expect(onNext).not.toHaveBeenCalled()
  })

  it('keyboard listener is removed on unmount', () => {
    const onNext = vi.fn()
    const { unmount } = render(
      <ControlDetailPage {...makeProps({ onNext, position: { index: 5, total: 10 } })} />,
    )
    unmount()
    fireEvent.keyDown(window, { key: 'ArrowRight' })
    expect(onNext).not.toHaveBeenCalled()
  })

  it('Esc does NOT fire onBack when theme-menu-panel is present in DOM', () => {
    const onBack = vi.fn()
    // Insert a .theme-menu-panel element to simulate the theme menu being open
    const panel = document.createElement('div')
    panel.className = 'theme-menu-panel'
    document.body.appendChild(panel)
    try {
      render(<ControlDetailPage {...makeProps({ onBack })} />)
      fireEvent.keyDown(window, { key: 'Escape' })
      expect(onBack).not.toHaveBeenCalled()
    } finally {
      document.body.removeChild(panel)
    }
  })

  it('Esc fires onBack when no dropdown panel is present in DOM', () => {
    const onBack = vi.fn()
    render(<ControlDetailPage {...makeProps({ onBack })} />)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  // ── Evidence card ─────────────────────────────────────────────────────────

  it('evidence card "Open in Evidence workspace" fires onNavigateToEvidence with first artifact id', () => {
    const onNavigateToEvidence = vi.fn()
    render(<ControlDetailPage {...makeProps({ onNavigateToEvidence })} />)
    const evidenceLink = screen.getByText(/Open in Evidence workspace/i)
    fireEvent.click(evidenceLink)
    expect(onNavigateToEvidence).toHaveBeenCalledTimes(1)
  })

  // ── Implementation card ───────────────────────────────────────────────────

  it('shows implementation status chip when scopingEntry is provided', () => {
    const scopingEntry = {
      selected: true,
      implementation_status: 'in_progress',
      maturity: 'L1',
      owner: 'SecOps',
    }
    render(<ControlDetailPage {...makeProps({ scopingEntry })} />)
    expect(screen.getByText(/in.?progress/i)).toBeInTheDocument()
  })

  it('shows "Not in scope" quiet state when scopingEntry is null', () => {
    render(<ControlDetailPage {...makeProps({ scopingEntry: null })} />)
    expect(screen.getByText(/not in scope/i)).toBeInTheDocument()
  })

  it('shows "Not in scope" quiet state when scopingEntry has selected=false', () => {
    render(
      <ControlDetailPage {...makeProps({ scopingEntry: { selected: false } })} />,
    )
    expect(screen.getByText(/not in scope/i)).toBeInTheDocument()
  })

  it('shows "In scope" chip in header when selected=true', () => {
    const scopingEntry = { selected: true }
    render(<ControlDetailPage {...makeProps({ scopingEntry })} />)
    expect(screen.getByText(/in scope/i)).toBeInTheDocument()
  })

  // ── Framework mappings card ───────────────────────────────────────────────

  it('shows first 3 framework chips + "+N more" in the summary card', () => {
    const control = makeControl({
      frameworksResolved: {
        'ISO 27001': ['A.5.2'],
        'SOC 2': ['CC1.2'],
        'Cyber Essentials': ['CE-1'],
        'NIST 800-53': ['PM-2'],
      },
      frameworksCount: 4,
    })
    render(<ControlDetailPage {...makeProps({ control })} />)
    // First 3 chips visible
    expect(screen.getByText('ISO 27001')).toBeInTheDocument()
    expect(screen.getByText('SOC 2')).toBeInTheDocument()
    expect(screen.getByText('Cyber Essentials')).toBeInTheDocument()
    // "+1 more" for the 4th
    expect(screen.getByText(/\+1 more/)).toBeInTheDocument()
  })

  // ── Parity: artifacts section ─────────────────────────────────────────────

  it('renders audit artifacts section in the Details tab', () => {
    render(<ControlDetailPage {...makeProps()} />)
    // Details tab is active by default
    expect(screen.getByText(/Audit Artifacts/i)).toBeInTheDocument()
    // EVI-001 appears in artifact-id-badge (and possibly evidence-status-id)
    expect(screen.getAllByText('EVI-001').length).toBeGreaterThanOrEqual(1)
    // Security Policy appears in both artifact list and evidence status
    expect(screen.getAllByText('Security Policy').length).toBeGreaterThanOrEqual(1)
  })

  // ── Parity: tab switching ─────────────────────────────────────────────────

  it('switches to Mappings tab and renders framework groups', () => {
    render(<ControlDetailPage {...makeProps()} />)
    const mappingsTab = screen.getByRole('tab', { name: /Mappings/i })
    fireEvent.click(mappingsTab)
    // ISO 27001 may appear in both summary card chip and mappings tab
    expect(screen.getAllByText('ISO 27001').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('A.5.2')).toBeInTheDocument()
  })

  it('switches to Assessment tab and renders AssessmentObjectivesList', () => {
    render(<ControlDetailPage {...makeProps()} />)
    const assessmentTab = screen.getByRole('tab', { name: /Assessment/i })
    fireEvent.click(assessmentTab)
    expect(screen.getByTestId('assessment-objectives-list')).toBeInTheDocument()
  })

  it('Mappings tab button shows framework count', () => {
    render(<ControlDetailPage {...makeProps()} />)
    const mappingsTab = screen.getByRole('tab', { name: /Mappings/i })
    expect(mappingsTab).toHaveTextContent('4')
  })

  // ── Parity: header area items ─────────────────────────────────────────────

  it('renders SCF source tag', () => {
    render(<ControlDetailPage {...makeProps()} />)
    expect(screen.getByText(/SCF Catalog/i)).toBeInTheDocument()
  })

  it('renders domain and validation cadence when present', () => {
    const control = makeControl({ validation_cadence: 'Annual' })
    render(<ControlDetailPage {...makeProps({ control })} />)
    // Governance appears in both domain badge and artifacts section
    expect(screen.getAllByText('Governance').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('Annual')).toBeInTheDocument()
  })

  it('renders CSF function chip in header', () => {
    render(<ControlDetailPage {...makeProps()} />)
    // "Govern" from nist_csf_function
    expect(screen.getAllByText('Govern').length).toBeGreaterThanOrEqual(1)
  })

  it('renders PPTDF chip when pptdf_applicability is present', () => {
    const control = makeControl({
      pptdf_applicability: { people: true, process: false, technology: false, data: false, facility: false },
    })
    render(<ControlDetailPage {...makeProps({ control })} />)
    expect(screen.getByText(/People/i)).toBeInTheDocument()
  })

  it('renders risk & threat context component', () => {
    render(<ControlDetailPage {...makeProps()} />)
    expect(screen.getByTestId('risk-threat-context')).toBeInTheDocument()
  })

  it('renders SCRM focus badges component', () => {
    render(<ControlDetailPage {...makeProps()} />)
    expect(screen.getByTestId('scrm-focus-badges')).toBeInTheDocument()
  })

  it('renders maturity roadmap component', () => {
    render(<ControlDetailPage {...makeProps()} />)
    expect(screen.getByTestId('maturity-roadmap')).toBeInTheDocument()
  })

  it('renders business size guidance component', () => {
    render(<ControlDetailPage {...makeProps()} />)
    expect(screen.getByTestId('business-size-guidance')).toBeInTheDocument()
  })

  // ── Evidence count in evidence card ───────────────────────────────────────

  it('evidence card shows artifact and tracked counts', () => {
    render(<ControlDetailPage {...makeProps()} />)
    // Should show "2 items linked" for the 2 artifacts
    expect(screen.getByText(/2 items linked/i)).toBeInTheDocument()
  })

  // ── Graph toggle ──────────────────────────────────────────────────────────

  it('graph toggle button is rendered in header area', () => {
    render(<ControlDetailPage {...makeProps()} />)
    // Looking for a graph toggle button
    const graphBtn = screen.getByRole('button', { name: /graph/i })
    expect(graphBtn).toBeInTheDocument()
  })

  it('clicking graph toggle shows GraphView', () => {
    render(<ControlDetailPage {...makeProps()} />)
    const graphBtn = screen.getByRole('button', { name: /graph/i })
    fireEvent.click(graphBtn)
    expect(screen.getByTestId('graph-view')).toBeInTheDocument()
  })

  // ── Knowledge Base tab ────────────────────────────────────────────────────

  it('Knowledge Base tab renders CDMControlPanel when organizationId is provided', async () => {
    await act(async () => {
      render(
        <ControlDetailPage
          {...makeProps({ organizationId: 'org-123' })}
        />,
      )
    })
    const kbTab = screen.getByRole('tab', { name: /Knowledge Base/i })
    await act(async () => {
      fireEvent.click(kbTab)
    })
    expect(screen.getByTestId('cdm-control-panel')).toBeInTheDocument()
  })

  it('Knowledge Base tab does not render CDMControlPanel when organizationId is absent', async () => {
    await act(async () => {
      render(<ControlDetailPage {...makeProps({ organizationId: undefined })} />)
    })
    const kbTab = screen.getByRole('tab', { name: /Knowledge Base/i })
    await act(async () => {
      fireEvent.click(kbTab)
    })
    expect(screen.queryByTestId('cdm-control-panel')).not.toBeInTheDocument()
  })
})
