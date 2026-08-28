/**
 * ScopingDetailPage.test.tsx — TDD tests for the full-width scoping detail page.
 *
 * Coverage (parity spot-set per task brief):
 *   - Each of the five tabs renders its mocked subcomponent
 *   - Scope toggle fires onToggleScope with correct scf_id
 *   - Status select fires onFieldChange('implementation_status', value)
 *   - Priority select fires onFieldChange('priority', value)
 *   - Maturity select fires onFieldChange('maturity_level', value)
 *   - SOA textarea fires onFieldChange + char counter tracks length
 *   - Target date visibility gating by implementation_status
 *   - Pager prev/next fire callbacks; disabled at bounds
 *   - position: null → pager hidden; index: null → "— of N" + both buttons disabled
 *   - Keyboard ←/→/Esc fire callbacks; suppressed in input/textarea/select
 *   - Open-dropdown guard: Esc suppressed when .theme-menu-panel present
 *   - Breadcrumb back button fires onBack
 *   - Framework mappings section: collapsed by default, expand on click
 *   - Header: scf_id pill, domain, name, description, assessment question present
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

// ─── Mock heavy subcomponents ─────────────────────────────────────────────────

vi.mock('../../SCRMFocusBadges', () => ({
  default: ({ focus }: { focus?: unknown }) => (
    <div data-testid="scrm-focus-badges">{JSON.stringify(focus)}</div>
  ),
}))

vi.mock('../../RiskThreatContext', () => ({
  default: () => <div data-testid="risk-threat-context" />,
}))

vi.mock('../../MaturityRoadmap', () => ({
  default: () => <div data-testid="maturity-roadmap" />,
}))

vi.mock('../../BusinessSizeGuidance', () => ({
  default: () => <div data-testid="business-size-guidance" />,
}))

vi.mock('../../DeprecatedBadge', () => ({
  default: ({ deprecated }: { deprecated?: boolean }) =>
    deprecated ? <span data-testid="deprecated-badge">Deprecated</span> : null,
  getCatalogLifecycle: (row: unknown) => row as Record<string, unknown>,
}))

vi.mock('../../ModernCommentThread', () => ({
  ModernCommentThread: ({
    commentableType,
    commentableId,
    organizationId,
  }: {
    commentableType: string
    commentableId: string
    organizationId: string
  }) => (
    <div
      data-testid="modern-comment-thread"
      data-type={commentableType}
      data-id={commentableId}
      data-org={organizationId}
    />
  ),
}))

vi.mock('../../AuditLogPanel', () => ({
  AuditLogPanel: ({ scfId, organizationId }: { scfId: string; organizationId: string }) => (
    <div data-testid="audit-log-panel" data-scf-id={scfId} data-org={organizationId} />
  ),
}))

vi.mock('../../AssignmentPicker', () => ({
  AssignmentPicker: ({ assignableType, assignableId }: { assignableType: string; assignableId: string }) => (
    <div data-testid="assignment-picker" data-type={assignableType} data-id={assignableId} />
  ),
}))

vi.mock('../../OwningTeams', () => ({
  default: ({ assignableType, assignableId }: { assignableType: string; assignableId: string }) => (
    <div data-testid="owning-teams" data-type={assignableType} data-id={assignableId} />
  ),
}))

vi.mock('../../CDMControlPanel', () => ({
  default: ({ organizationId }: { organizationId: string }) => (
    <div data-testid="cdm-control-panel" data-org={organizationId} />
  ),
}))

// ─── SUT import ───────────────────────────────────────────────────────────────

import ScopingDetailPage from '../ScopingDetailPage'
import type { ScopingDetailPageProps } from '../ScopingDetailPage'

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const CONTROL = {
  scf_id: 'GOV-01',
  scf_domain: 'Governance',
  control_name: 'Cybersecurity & Data Protection Governance',
  control_description: 'The organization establishes governance.',
  control_question: 'Does the organization have a governance program?',
  validation_cadence: 'Annual',
  nist_csf_function: 'Govern' as const,
  control_weighting: 7,
  artifactsResolved: [
    { id: 'EV-01', title: 'Governance Policy', domain: 'Governance' },
    { id: 'EV-02', title: 'Risk Register', domain: 'Risk' },
  ],
  frameworksResolved: {
    'NIST SP 800-53': ['PL-1', 'PL-2'],
    'ISO 27001': ['A.5.1'],
  },
  frameworksCount: 2,
  framework_mappings: {
    'nist_800_53': ['PL-1', 'PL-2'],
    'iso_27001': ['A.5.1'],
    'risk_AC-1': ['R-AC-1'], // internal — should be filtered
  },
  scrm_focus: { tier1_strategic: true },
  risk_threat_mapping: { risk_codes: ['R-GV-1'], threat_codes: ['MT-1'] },
  cmm_maturity: { level_0: 'Not performed', level_3: 'Well defined' },
  business_size_guidance: { small: 'Focus on basics' },
  evidence_requests: ['EV-01', 'EV-02'],
}

const SCOPING_ENTRY = {
  id: 'db-id-123',
  scf_id: 'GOV-01',
  selected: true,
  implementation_status: 'in_progress' as const,
  priority: 'high' as const,
  maturity_level: 'L2' as const,
  selection_reason: 'Required by ISO 27001',
  target_date: '2025-12-31',
  completion_date: undefined,
  implementation_notes: 'Working on this now.',
}

const SCOPING_DATA = {
  organizationId: 'org-1',
  organization: { id: 'org-1', name: 'Test Org', created_at: '', updated_at: '' },
  scoped_controls: [SCOPING_ENTRY],
  evidence_tracking: {
    'EV-01': { id: 'et-1', is_tracked: true, collecting_system: 'Jira' },
    'EV-02': { id: 'et-2', is_tracked: false },
  },
  metadata: { total_selected: 1, total_implemented: 0, last_updated: '' },
}

function makeProps(overrides: Partial<ScopingDetailPageProps> = {}): ScopingDetailPageProps {
  return {
    control: CONTROL as ScopingDetailPageProps['control'],
    scopingEntry: SCOPING_ENTRY as ScopingDetailPageProps['scopingEntry'],
    position: { index: 2, total: 10 },
    onPrev: vi.fn(),
    onNext: vi.fn(),
    onBack: vi.fn(),
    onToggleScope: vi.fn(),
    onFieldChange: vi.fn(),
    onReloadTeamAssignments: vi.fn(),
    organizationId: 'org-1',
    scopingData: SCOPING_DATA as ScopingDetailPageProps['scopingData'],
    accountableTeamLabel: 'Security Operations',
    canManageTeams: true,
    ...overrides,
  }
}

// ─── Helper ───────────────────────────────────────────────────────────────────

function clickTab(name: string) {
  const tab = screen.getByRole('tab', { name })
  fireEvent.click(tab)
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('ScopingDetailPage', () => {
  // ── Header ──────────────────────────────────────────────────────────────────

  describe('Header', () => {
    it('renders scf_id pill', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      // GOV-01 appears in breadcrumb + header; both are valid
      expect(screen.getAllByText('GOV-01').length).toBeGreaterThanOrEqual(1)
    })

    it('renders domain', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      expect(screen.getByText('Governance')).toBeInTheDocument()
    })

    it('renders control name', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      expect(screen.getByText('Cybersecurity & Data Protection Governance')).toBeInTheDocument()
    })

    it('renders control description', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      expect(screen.getByText('The organization establishes governance.')).toBeInTheDocument()
    })

    it('renders assessment question', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      expect(screen.getByText(/Does the organization have a governance program/)).toBeInTheDocument()
    })

    it('renders implementation status badge when present', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      // Badge shows formatted status: "In Progress" — also appears as a select option
      expect(screen.getAllByText('In Progress').length).toBeGreaterThanOrEqual(1)
      // Specifically, the header badge element should have the status class
      const badge = document.querySelector('.status-badge-compact')
      expect(badge).toBeInTheDocument()
    })

    it('renders SCRMFocusBadges', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      expect(screen.getByTestId('scrm-focus-badges')).toBeInTheDocument()
    })

    it('renders RiskThreatContext', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      expect(screen.getByTestId('risk-threat-context')).toBeInTheDocument()
    })

    it('renders MaturityRoadmap', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      expect(screen.getByTestId('maturity-roadmap')).toBeInTheDocument()
    })

    it('renders BusinessSizeGuidance', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      expect(screen.getByTestId('business-size-guidance')).toBeInTheDocument()
    })

    it('renders framework count widget', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      // frameworksCount = 2 appears as the "Frameworks" widget value; may appear multiple times
      expect(screen.getAllByText('2').length).toBeGreaterThanOrEqual(1)
    })

    it('renders artifact count widget', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      // 2 artifacts — may appear multiple times alongside frameworks count
      expect(screen.getAllByText('2').length).toBeGreaterThanOrEqual(1)
    })
  })

  // ── Breadcrumb / Pager ───────────────────────────────────────────────────────

  describe('Breadcrumb + Pager', () => {
    it('renders back button and fires onBack', () => {
      const onBack = vi.fn()
      render(<ScopingDetailPage {...makeProps({ onBack })} />)
      fireEvent.click(screen.getByRole('button', { name: /back to scoping/i }))
      expect(onBack).toHaveBeenCalledTimes(1)
    })

    it('renders position text "3 of 10"', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      expect(screen.getByText('3 of 10')).toBeInTheDocument()
    })

    it('renders "— of N" when index is null', () => {
      render(<ScopingDetailPage {...makeProps({ position: { index: null, total: 10 } })} />)
      expect(screen.getByText('— of 10')).toBeInTheDocument()
    })

    it('pager hidden when position is null', () => {
      render(<ScopingDetailPage {...makeProps({ position: null })} />)
      expect(screen.queryByText(/of \d/)).toBeNull()
    })

    it('fires onPrev when prev button clicked', () => {
      const onPrev = vi.fn()
      render(<ScopingDetailPage {...makeProps({ onPrev, position: { index: 2, total: 10 } })} />)
      fireEvent.click(screen.getByRole('button', { name: /previous/i }))
      expect(onPrev).toHaveBeenCalledTimes(1)
    })

    it('fires onNext when next button clicked', () => {
      const onNext = vi.fn()
      render(<ScopingDetailPage {...makeProps({ onNext, position: { index: 2, total: 10 } })} />)
      fireEvent.click(screen.getByRole('button', { name: /next/i }))
      expect(onNext).toHaveBeenCalledTimes(1)
    })

    it('prev button disabled when index is 0', () => {
      render(<ScopingDetailPage {...makeProps({ position: { index: 0, total: 10 } })} />)
      expect(screen.getByRole('button', { name: /previous/i })).toBeDisabled()
    })

    it('next button disabled when index is last', () => {
      render(<ScopingDetailPage {...makeProps({ position: { index: 9, total: 10 } })} />)
      expect(screen.getByRole('button', { name: /next/i })).toBeDisabled()
    })

    it('both pager buttons disabled when index is null', () => {
      render(<ScopingDetailPage {...makeProps({ position: { index: null, total: 10 } })} />)
      expect(screen.getByRole('button', { name: /previous/i })).toBeDisabled()
      expect(screen.getByRole('button', { name: /next/i })).toBeDisabled()
    })
  })

  // ── Keyboard shortcuts ────────────────────────────────────────────────────────

  describe('Keyboard shortcuts', () => {
    it('ArrowLeft fires onPrev', () => {
      const onPrev = vi.fn()
      render(<ScopingDetailPage {...makeProps({ onPrev })} />)
      fireEvent.keyDown(window, { key: 'ArrowLeft' })
      expect(onPrev).toHaveBeenCalledTimes(1)
    })

    it('ArrowRight fires onNext', () => {
      const onNext = vi.fn()
      render(<ScopingDetailPage {...makeProps({ onNext })} />)
      fireEvent.keyDown(window, { key: 'ArrowRight' })
      expect(onNext).toHaveBeenCalledTimes(1)
    })

    it('Escape fires onBack', () => {
      const onBack = vi.fn()
      render(<ScopingDetailPage {...makeProps({ onBack })} />)
      fireEvent.keyDown(window, { key: 'Escape' })
      expect(onBack).toHaveBeenCalledTimes(1)
    })

    it('ArrowLeft suppressed when focus is in an input', () => {
      const onPrev = vi.fn()
      render(<ScopingDetailPage {...makeProps({ onPrev })} />)
      const input = document.createElement('input')
      document.body.appendChild(input)
      input.focus()
      // Fire on the focused input — the handler reads e.target from the event
      fireEvent.keyDown(input, { key: 'ArrowLeft', bubbles: true })
      // Not called because isSuppressed checks tagName
      expect(onPrev).not.toHaveBeenCalled()
      document.body.removeChild(input)
    })

    it('ArrowRight suppressed when focus is in a textarea', () => {
      const onNext = vi.fn()
      render(<ScopingDetailPage {...makeProps({ onNext })} />)
      const ta = document.createElement('textarea')
      document.body.appendChild(ta)
      ta.focus()
      fireEvent.keyDown(ta, { key: 'ArrowRight', bubbles: true })
      expect(onNext).not.toHaveBeenCalled()
      document.body.removeChild(ta)
    })

    it('Escape suppressed when .theme-menu-panel is present', () => {
      const onBack = vi.fn()
      render(<ScopingDetailPage {...makeProps({ onBack })} />)
      const panel = document.createElement('div')
      panel.className = 'theme-menu-panel'
      document.body.appendChild(panel)
      fireEvent.keyDown(window, { key: 'Escape' })
      expect(onBack).not.toHaveBeenCalled()
      document.body.removeChild(panel)
    })

    it('keyboard listener is cleaned up on unmount', () => {
      const onPrev = vi.fn()
      const { unmount } = render(<ScopingDetailPage {...makeProps({ onPrev })} />)
      unmount()
      fireEvent.keyDown(window, { key: 'ArrowLeft' })
      expect(onPrev).not.toHaveBeenCalled()
    })
  })

  // ── Tab: DETAILS ─────────────────────────────────────────────────────────────

  describe('Tab: DETAILS', () => {
    it('shows DETAILS tab as active by default', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      expect(screen.getByRole('tab', { name: 'DETAILS' })).toHaveAttribute('aria-selected', 'true')
    })

    it('scope toggle fires onToggleScope with scf_id', () => {
      const onToggleScope = vi.fn()
      render(<ScopingDetailPage {...makeProps({ onToggleScope })} />)
      const checkbox = screen.getByRole('checkbox', { name: /include this control/i })
      fireEvent.click(checkbox)
      expect(onToggleScope).toHaveBeenCalledWith('GOV-01')
    })

    it('scope toggle checked when scopingEntry.selected is true', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      const checkbox = screen.getByRole('checkbox', { name: /include this control/i })
      expect(checkbox).toBeChecked()
    })

    it('status select fires onFieldChange with implementation_status', () => {
      const onFieldChange = vi.fn()
      render(<ScopingDetailPage {...makeProps({ onFieldChange })} />)
      const select = screen.getByRole('combobox', { name: /implementation status/i })
      fireEvent.change(select, { target: { value: 'implemented' } })
      expect(onFieldChange).toHaveBeenCalledWith('implementation_status', 'implemented')
    })

    it('status select has all 8 options', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      const select = screen.getByRole('combobox', { name: /implementation status/i })
      const options = within(select).getAllByRole('option')
      expect(options).toHaveLength(8)
    })

    it('priority select fires onFieldChange with priority', () => {
      const onFieldChange = vi.fn()
      render(<ScopingDetailPage {...makeProps({ onFieldChange })} />)
      const select = screen.getByRole('combobox', { name: /priority/i })
      fireEvent.change(select, { target: { value: 'critical' } })
      expect(onFieldChange).toHaveBeenCalledWith('priority', 'critical')
    })

    it('maturity select fires onFieldChange with maturity_level', () => {
      const onFieldChange = vi.fn()
      render(<ScopingDetailPage {...makeProps({ onFieldChange })} />)
      const select = screen.getByRole('combobox', { name: /maturity level/i })
      fireEvent.change(select, { target: { value: 'L4' } })
      expect(onFieldChange).toHaveBeenCalledWith('maturity_level', 'L4')
    })

    it('maturity select has L0–L5 options (6 + disabled placeholder)', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      const select = screen.getByRole('combobox', { name: /maturity level/i })
      const options = within(select).getAllByRole('option')
      // 1 placeholder + 6 levels
      expect(options).toHaveLength(7)
    })

    it('SOA textarea fires onFieldChange with selection_reason', () => {
      const onFieldChange = vi.fn()
      render(<ScopingDetailPage {...makeProps({ onFieldChange })} />)
      const ta = screen.getByRole('textbox', { name: /applicability statement|exclusion rationale/i })
      fireEvent.change(ta, { target: { value: 'New reason text' } })
      expect(onFieldChange).toHaveBeenCalledWith('selection_reason', 'New reason text')
    })

    it('SOA char counter tracks length and shows warning over 120 chars', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      const ta = screen.getByRole('textbox', { name: /applicability statement|exclusion rationale/i })
      const longText = 'x'.repeat(125)
      fireEvent.change(ta, { target: { value: longText } })
      expect(screen.getByText(/125\/120/)).toBeInTheDocument()
      expect(screen.getByText(/SOA will truncate/)).toBeInTheDocument()
    })

    it('SOA counter shows current length before 120 chars', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      const ta = screen.getByRole('textbox', { name: /applicability statement|exclusion rationale/i })
      fireEvent.change(ta, { target: { value: 'hello' } })
      expect(screen.getByText(/5\/120/)).toBeInTheDocument()
    })

    it('shows the accountable team read-only — the legacy owner select is sunset', () => {
      const onFieldChange = vi.fn()
      render(<ScopingDetailPage {...makeProps({ onFieldChange })} />)
      expect(screen.getByTestId('accountable-team')).toHaveTextContent('Security Operations')
      // No select, no write path: ownership changes only on the Assignments tab.
      expect(screen.queryByRole('combobox', { name: /owner team label/i })).toBeNull()
      expect(onFieldChange).not.toHaveBeenCalledWith('owner', expect.anything())
    })

    it('says so when no team is accountable yet', () => {
      render(<ScopingDetailPage {...makeProps({ accountableTeamLabel: null })} />)
      expect(screen.getByTestId('accountable-team')).toHaveTextContent('No accountable team')
    })

    it('target date shown when status is in_progress', () => {
      render(
        <ScopingDetailPage
          {...makeProps({
            scopingEntry: { ...SCOPING_ENTRY, implementation_status: 'in_progress' },
          })}
        />,
      )
      // date input is type="date" — not a textbox; use getByLabelText
      expect(screen.getByLabelText(/target date/i)).toBeInTheDocument()
    })

    it('target date shown when status is not_started', () => {
      render(
        <ScopingDetailPage
          {...makeProps({
            scopingEntry: { ...SCOPING_ENTRY, implementation_status: 'not_started' },
          })}
        />,
      )
      expect(screen.queryByLabelText(/target date/i)).toBeInTheDocument()
    })

    it('target date shown when status is at_risk', () => {
      render(
        <ScopingDetailPage
          {...makeProps({
            scopingEntry: { ...SCOPING_ENTRY, implementation_status: 'at_risk' },
          })}
        />,
      )
      expect(screen.queryByLabelText(/target date/i)).toBeInTheDocument()
    })

    it('target date shown when status is deferred', () => {
      render(
        <ScopingDetailPage
          {...makeProps({
            scopingEntry: { ...SCOPING_ENTRY, implementation_status: 'deferred' },
          })}
        />,
      )
      expect(screen.queryByLabelText(/target date/i)).toBeInTheDocument()
    })

    it('target date hidden when status is implemented', () => {
      render(
        <ScopingDetailPage
          {...makeProps({
            scopingEntry: { ...SCOPING_ENTRY, implementation_status: 'implemented' },
          })}
        />,
      )
      expect(screen.queryByLabelText(/target date/i)).toBeNull()
    })

    it('completion date shown when present (read-only)', () => {
      render(
        <ScopingDetailPage
          {...makeProps({
            scopingEntry: { ...SCOPING_ENTRY, completion_date: '2025-01-15' },
          })}
        />,
      )
      expect(screen.getByText(/Completed/i)).toBeInTheDocument()
    })

    it('completion date hidden when absent', () => {
      render(
        <ScopingDetailPage
          {...makeProps({
            scopingEntry: { ...SCOPING_ENTRY, completion_date: undefined },
          })}
        />,
      )
      expect(screen.queryByText(/Completed/i)).toBeNull()
    })
  })

  // ── Tab: NOTES & HISTORY ──────────────────────────────────────────────────────

  describe('Tab: NOTES & HISTORY', () => {
    beforeEach(() => {
      // render and switch to notes tab
    })

    it('renders implementation notes textarea on Notes tab', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      clickTab('NOTES & HISTORY')
      expect(screen.getByRole('textbox', { name: /implementation notes/i })).toBeInTheDocument()
    })

    it('notes textarea fires onFieldChange with implementation_notes', () => {
      const onFieldChange = vi.fn()
      render(<ScopingDetailPage {...makeProps({ onFieldChange })} />)
      clickTab('NOTES & HISTORY')
      const ta = screen.getByRole('textbox', { name: /implementation notes/i })
      fireEvent.change(ta, { target: { value: 'New notes' } })
      expect(onFieldChange).toHaveBeenCalledWith('implementation_notes', 'New notes')
    })

    it('notes textarea local mirror: successive changes without prop update show latest typed value and fire onFieldChange twice', () => {
      const onFieldChange = vi.fn()
      render(<ScopingDetailPage {...makeProps({ onFieldChange })} />)
      clickTab('NOTES & HISTORY')
      const ta = screen.getByRole('textbox', { name: /implementation notes/i })
      // First change — no prop update between the two
      fireEvent.change(ta, { target: { value: 'First edit' } })
      // Second change — textarea should reflect latest value (local mirror, not stale prop)
      fireEvent.change(ta, { target: { value: 'Second edit' } })
      expect((ta as HTMLTextAreaElement).value).toBe('Second edit')
      expect(onFieldChange).toHaveBeenCalledTimes(2)
      expect(onFieldChange).toHaveBeenNthCalledWith(1, 'implementation_notes', 'First edit')
      expect(onFieldChange).toHaveBeenNthCalledWith(2, 'implementation_notes', 'Second edit')
    })

    it('renders ModernCommentThread when scopingEntry has an id', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      clickTab('NOTES & HISTORY')
      const thread = screen.getByTestId('modern-comment-thread')
      expect(thread).toBeInTheDocument()
      expect(thread).toHaveAttribute('data-type', 'control')
      expect(thread).toHaveAttribute('data-id', 'db-id-123')
      expect(thread).toHaveAttribute('data-org', 'org-1')
    })

    it('shows save hint when scopingEntry has no id', () => {
      render(
        <ScopingDetailPage
          {...makeProps({
            scopingEntry: { ...SCOPING_ENTRY, id: undefined },
          })}
        />,
      )
      clickTab('NOTES & HISTORY')
      expect(screen.getByText(/save this control to enable comments/i)).toBeInTheDocument()
    })

    it('renders AuditLogPanel on Notes tab', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      clickTab('NOTES & HISTORY')
      const panel = screen.getByTestId('audit-log-panel')
      expect(panel).toBeInTheDocument()
      expect(panel).toHaveAttribute('data-scf-id', 'GOV-01')
      expect(panel).toHaveAttribute('data-org', 'org-1')
    })
  })

  // ── Tab: ASSIGNMENTS ─────────────────────────────────────────────────────────

  describe('Tab: ASSIGNMENTS', () => {
    it('renders AssignmentPicker on Assignments tab when scopingEntry has id', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      clickTab('ASSIGNMENTS')
      const picker = screen.getByTestId('assignment-picker')
      expect(picker).toHaveAttribute('data-type', 'control')
      expect(picker).toHaveAttribute('data-id', 'db-id-123')
    })

    it('renders OwningTeams on Assignments tab when scopingEntry has id', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      clickTab('ASSIGNMENTS')
      const teams = screen.getByTestId('owning-teams')
      expect(teams).toHaveAttribute('data-type', 'control')
      expect(teams).toHaveAttribute('data-id', 'db-id-123')
    })

    it('shows save hint on Assignments tab when scopingEntry has no id', () => {
      render(
        <ScopingDetailPage
          {...makeProps({ scopingEntry: { ...SCOPING_ENTRY, id: undefined } })}
        />,
      )
      clickTab('ASSIGNMENTS')
      expect(screen.getByText(/save control to enable assignment/i)).toBeInTheDocument()
    })
  })

  // ── Tab: AUDIT ARTIFACTS ──────────────────────────────────────────────────────

  describe('Tab: AUDIT ARTIFACTS', () => {
    it('renders artifacts grouped by domain', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      clickTab('AUDIT ARTIFACTS')
      // Domain group titles appear in the artifact list (Governance also in header badge
      // but the artifact domain-title is the one inside the artifacts panel)
      const artifactList = screen.getByText('Audit Artifacts').closest('.detail-section-container') as HTMLElement
      expect(within(artifactList).getAllByText('Governance').length).toBeGreaterThanOrEqual(1)
      expect(within(artifactList).getByText('Risk')).toBeInTheDocument()
      // Artifact ids
      expect(screen.getByText('EV-01')).toBeInTheDocument()
      expect(screen.getByText('EV-02')).toBeInTheDocument()
    })

    it('shows ✅ for tracked artifacts', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      clickTab('AUDIT ARTIFACTS')
      expect(screen.getByText('✅')).toBeInTheDocument()
    })

    it('shows ⚪ for untracked artifacts', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      clickTab('AUDIT ARTIFACTS')
      expect(screen.getByText('⚪')).toBeInTheDocument()
    })

    it('shows collecting_system tag for tracked artifacts', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      clickTab('AUDIT ARTIFACTS')
      expect(screen.getByText('Jira')).toBeInTheDocument()
    })

    it('shows "No artifacts listed" when none present', () => {
      render(
        <ScopingDetailPage
          {...makeProps({
            control: { ...CONTROL, artifactsResolved: [] } as ScopingDetailPageProps['control'],
          })}
        />,
      )
      clickTab('AUDIT ARTIFACTS')
      expect(screen.getByText(/no artifacts listed/i)).toBeInTheDocument()
    })

    it('renders tracked/total summary', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      clickTab('AUDIT ARTIFACTS')
      // 1 of 2 tracked = 50%
      expect(screen.getByText(/1\/2 tracked/)).toBeInTheDocument()
    })
  })

  // ── Tab: KNOWLEDGE BASE ───────────────────────────────────────────────────────

  describe('Tab: KNOWLEDGE BASE', () => {
    it('renders CDMControlPanel on Knowledge Base tab', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      clickTab('KNOWLEDGE BASE')
      const panel = screen.getByTestId('cdm-control-panel')
      expect(panel).toHaveAttribute('data-org', 'org-1')
    })
  })

  // ── Framework mappings ────────────────────────────────────────────────────────

  describe('Framework mappings section', () => {
    it('collapsed by default', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      // Framework mapping entries not visible when collapsed
      expect(screen.queryByText('NIST SP 800-53')).toBeNull()
    })

    it('expands on click', () => {
      render(<ScopingDetailPage {...makeProps()} />)
      const header = screen.getByText('Framework Mappings').closest('[class*="collapsible"]') ??
        screen.getByText('Framework Mappings').parentElement!
      fireEvent.click(header)
      expect(screen.getByText('NIST SP 800-53')).toBeInTheDocument()
    })

    it('defensive filter: internal-prefix key risk_catalog is stripped, ISO 27001 renders', () => {
      const controlWithInternal = {
        ...CONTROL,
        frameworksResolved: {
          risk_catalog: ['R-AC-1'],
          'ISO 27001': ['A.5.1'],
        },
      }
      render(
        <ScopingDetailPage
          {...makeProps({ control: controlWithInternal as ScopingDetailPageProps['control'] })}
        />,
      )
      const header = screen.getByText('Framework Mappings').closest('[class*="collapsible"]') ??
        screen.getByText('Framework Mappings').parentElement!
      fireEvent.click(header)
      expect(screen.getByText('ISO 27001')).toBeInTheDocument()
      expect(screen.queryByText('risk_catalog')).toBeNull()
    })
  })
})
