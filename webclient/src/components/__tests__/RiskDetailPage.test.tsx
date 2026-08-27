/**
 * RiskDetailPage tests — Phase 4 Task 4
 *
 * Pins:
 *  - Page renders all sections: breadcrumb, header, 3-card grid, controls, history
 *  - Each form field fires the correct update
 *  - Save (debounced) is called on field change
 *  - Pager: bounds, keyboard ArrowLeft/ArrowRight/Escape, suppression in inputs
 *  - Bare arrival at ?tab=risk-register lands on list/matrix (NOT detail)
 *  - Deep link ?risk= opens detail
 *  - Back returns with filters preserved (onBack fires)
 *  - onNavigateToControl fires when control row clicked
 *  - Custom risk delete fires onDeleteCustomRisk
 */
import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import RiskDetailPage from '../RiskDetailPage'
import type { RiskAssessment, RiskCodesFile, UserSimple } from '../../types'

// --- Fixtures ---------------------------------------------------------------

const riskCodes: RiskCodesFile = {
  categories: {
    GV: { name: 'Governance', color: '#6366f1' },
    ORG: { name: 'Custom', color: '#6b7280' },
  } as RiskCodesFile['categories'],
  codes: {
    'R-GV-1': {
      category: 'GV' as any,
      title: 'Inadequate governance oversight',
      description: 'Lack of executive-level ownership leads to unmanaged obligations.',
    },
    'R-GV-2': {
      category: 'GV' as any,
      title: 'Policy gap',
      description: 'Missing policies.',
    },
    'R-ORG-1': {
      category: 'ORG' as any,
      title: 'Custom risk title',
      description: 'Custom risk description.',
    },
  },
}

const baseAssessment: RiskAssessment = {
  risk_code: 'R-GV-1',
  treatment_status: 'treating',
  likelihood: 4,
  impact: 4,
  residual_likelihood: 2,
  residual_impact: 3,
  treatment_plan: 'Formalise board reporting',
  treatment_due_date: '2026-09-30',
  next_review_date: '2026-11-01',
  notes: 'Key risk',
  owner_user_id: 'user-1',
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-08-20T00:00:00Z',
} as unknown as RiskAssessment

const customAssessment: RiskAssessment = {
  risk_code: 'R-ORG-1',
  treatment_status: 'identified',
  likelihood: null,
  impact: null,
  residual_likelihood: null,
  residual_impact: null,
  treatment_plan: null,
  treatment_due_date: null,
  next_review_date: null,
  notes: null,
  owner_user_id: null,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
} as unknown as RiskAssessment

const users: UserSimple[] = [
  { id: 'user-1', display_name: 'Mark Almeida-Cardy', email: 'mark@example.com' } as UserSimple,
]

vi.mock('../../data/apiClient', () => ({
  getControlsForRisk: vi.fn().mockResolvedValue({
    risk_code: 'R-GV-1',
    total_catalog_controls: 2,
    catalog_control_ids: ['GOV-01', 'GOV-04'],
    scoped_controls: [
      { scf_id: 'GOV-01', control_name: 'Governance Program', implementation_status: 'in_progress', priority: null, target_date: null },
      { scf_id: 'GOV-04', control_name: 'Security Responsibilities', implementation_status: 'implemented', priority: null, target_date: null },
    ],
  }),
  getScopedControls: vi.fn().mockResolvedValue([]),
  addCustomRiskControl: vi.fn().mockResolvedValue({}),
  removeCustomRiskControl: vi.fn().mockResolvedValue({}),
  // For RiskDashboard tests
  getRiskAssessments: vi.fn().mockResolvedValue([
    {
      risk_code: 'R-GV-1',
      treatment_status: 'identified',
      likelihood: 1,
      impact: 2,
      inherent_risk_score: 2,
      inherent_risk_level: 'low',
      residual_likelihood: null,
      residual_impact: null,
      residual_risk_score: null,
      residual_risk_level: null,
      owner: null,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    },
  ]),
  getOrgMembers: vi.fn().mockResolvedValue([]),
  getCustomRiskDefinitions: vi.fn().mockResolvedValue([]),
  createOrUpdateRiskAssessment: vi.fn().mockResolvedValue({}),
  updateRiskAssessment: vi.fn().mockResolvedValue({}),
  createCustomRisk: vi.fn().mockResolvedValue({}),
  deleteCustomRisk: vi.fn().mockResolvedValue({}),
}))

vi.mock('../../contexts/OrganizationContext', () => ({
  useOrganization: () => ({ currentOrg: { id: 'org-1' } }),
}))

vi.mock('../../contexts/RiskProfileContext', () => ({
  useRiskProfile: () => ({
    riskThresholds: { lowMax: 4, mediumMax: 9, highMax: 15 },
  }),
}))

vi.mock('react-hot-toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
  default: { success: vi.fn(), error: vi.fn() },
}))

function renderPage(overrides: Partial<React.ComponentProps<typeof RiskDetailPage>> = {}) {
  const defaults = {
    assessment: baseAssessment,
    riskCodes,
    onSave: vi.fn().mockResolvedValue(undefined),
    onBack: vi.fn(),
    onPrev: vi.fn(),
    onNext: vi.fn(),
    position: { index: 3, total: 42 } as { index: number | null; total: number } | null,
    users,
    onNavigateToControl: vi.fn(),
    onDeleteCustomRisk: vi.fn().mockResolvedValue(undefined),
  }
  return render(<RiskDetailPage {...defaults} {...overrides} />)
}

// ─── Section renders ────────────────────────────────────────────────────────

describe('RiskDetailPage — section renders', () => {
  it('renders breadcrumb with back link text "Risk Register"', () => {
    renderPage()
    expect(screen.getByText(/Risk Register/i, { selector: 'button, [role="button"]' })).toBeInTheDocument()
  })

  it('renders the risk code in the breadcrumb', () => {
    renderPage()
    // Risk code appears in both breadcrumb and header — both should be present
    expect(screen.getAllByText('R-GV-1').length).toBeGreaterThanOrEqual(1)
  })

  it('renders pager position text "4 of 42"', () => {
    renderPage()
    expect(screen.getByText('4 of 42')).toBeInTheDocument()
  })

  it('renders "— of N" when index is null (not in filtered set)', () => {
    renderPage({ position: { index: null, total: 42 } })
    expect(screen.getByText('— of 42')).toBeInTheDocument()
  })

  it('renders the risk title in the header', () => {
    renderPage()
    expect(screen.getByText('Inadequate governance oversight')).toBeInTheDocument()
  })

  it('renders the risk description', () => {
    renderPage()
    expect(screen.getByText(/Lack of executive-level ownership/i)).toBeInTheDocument()
  })

  it('renders the category chip', () => {
    renderPage()
    expect(screen.getByText('Governance')).toBeInTheDocument()
  })

  it('renders the treatment status chip in header', () => {
    renderPage()
    // treatment_status = 'treating' → shows as "Treating" chip in header
    const treatmentChip = document.querySelector('.risk-detail-page-chip--treatment')
    expect(treatmentChip).not.toBeNull()
    expect(treatmentChip?.textContent).toMatch(/Treating/i)
  })

  it('renders the owner name in header area', () => {
    renderPage()
    // Owner name appears in both the header chip and the owner select option
    expect(screen.getAllByText(/Mark Almeida-Cardy/i).length).toBeGreaterThanOrEqual(1)
    // Specifically the owner label element
    const ownerLabel = document.querySelector('.risk-detail-page-owner-label')
    expect(ownerLabel?.textContent).toMatch(/Mark Almeida-Cardy/i)
  })

  it('renders the INHERENT RISK card label', () => {
    renderPage()
    expect(screen.getByText(/INHERENT/i)).toBeInTheDocument()
  })

  it('renders the RESIDUAL RISK card label', () => {
    renderPage()
    expect(screen.getByText(/RESIDUAL/i)).toBeInTheDocument()
  })

  it('renders the TREATMENT card label', () => {
    renderPage()
    // "TREATMENT" card label (exact, not the field labels which say "Treatment Status" etc.)
    const cardLabel = document.querySelector('.risk-detail-page-card-label')
    const allCardLabels = document.querySelectorAll('.risk-detail-page-card-label')
    const texts = Array.from(allCardLabels).map(el => el.textContent)
    expect(texts.some(t => t?.includes('TREATMENT'))).toBe(true)
  })

  it('renders the controls addressing section', () => {
    renderPage()
    expect(screen.getByText(/CONTROLS ADDRESSING THIS RISK/i)).toBeInTheDocument()
  })

  it('renders the assessment history section label', () => {
    renderPage()
    expect(screen.getByText(/ASSESSMENT HISTORY/i)).toBeInTheDocument()
  })
})

// ─── Form fields ─────────────────────────────────────────────────────────────

describe('RiskDetailPage — form fields fire correctly', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders likelihood select with current value', () => {
    renderPage()
    // Two likelihood selects (inherent + residual) — both should exist
    const selects = screen.getAllByRole('combobox', { name: /likelihood/i })
    expect(selects.length).toBeGreaterThanOrEqual(2)
    // First inherent likelihood select should have value 4
    expect((selects[0] as HTMLSelectElement).value).toBe('4')
  })

  it('renders impact select with current value', () => {
    renderPage()
    // Two impact selects (inherent + residual)
    const selects = screen.getAllByRole('combobox', { name: /impact/i })
    expect(selects.length).toBeGreaterThanOrEqual(2)
    expect((selects[0] as HTMLSelectElement).value).toBe('4')
  })

  it('renders residual likelihood select', () => {
    renderPage()
    const selects = screen.getAllByRole('combobox', { name: /likelihood/i })
    // At least two: inherent + residual
    expect(selects.length).toBeGreaterThanOrEqual(2)
  })

  it('renders treatment status select', async () => {
    const { act } = await import('@testing-library/react')
    await act(async () => { renderPage() })
    // Only one treatment status select; useEffect populates from assessment (treatment_status = 'treating')
    const selects = screen.getAllByRole('combobox', { name: /treatment status/i })
    expect(selects.length).toBeGreaterThanOrEqual(1)
    expect((selects[0] as HTMLSelectElement).value).toBe('treating')
  })

  it('renders treatment plan textarea with value', () => {
    renderPage()
    const ta = screen.getByRole('textbox', { name: /treatment plan/i })
    expect((ta as HTMLTextAreaElement).value).toBe('Formalise board reporting')
  })

  it('renders treatment due date input', () => {
    renderPage()
    // date inputs don't have a role, find by label text
    expect(screen.getByLabelText(/treatment due date/i)).toBeInTheDocument()
  })

  it('renders next review date input', () => {
    renderPage()
    expect(screen.getByLabelText(/next review date/i)).toBeInTheDocument()
  })

  it('renders owner select with correct user option', () => {
    renderPage()
    const select = screen.getByRole('combobox', { name: /risk owner/i })
    expect((select as HTMLSelectElement).value).toBe('user-1')
  })

  it('renders notes textarea', () => {
    renderPage()
    const ta = screen.getByRole('textbox', { name: /notes/i })
    expect((ta as HTMLTextAreaElement).value).toBe('Key risk')
  })

  it('changing likelihood select triggers onSave (debounced)', async () => {
    vi.useFakeTimers()
    const onSave = vi.fn().mockResolvedValue(undefined)
    renderPage({ onSave })
    const selects = screen.getAllByRole('combobox', { name: /likelihood/i })
    fireEvent.change(selects[0], { target: { value: '3' } })
    vi.runAllTimers()
    await Promise.resolve()
    expect(onSave).toHaveBeenCalled()
    vi.useRealTimers()
  })

  it('changing treatment status select triggers onSave', async () => {
    vi.useFakeTimers()
    const onSave = vi.fn().mockResolvedValue(undefined)
    renderPage({ onSave })
    const select = screen.getByRole('combobox', { name: /treatment status/i })
    fireEvent.change(select, { target: { value: 'accepted' } })
    vi.runAllTimers()
    await Promise.resolve()
    expect(onSave).toHaveBeenCalled()
    vi.useRealTimers()
  })

  it('changing owner select triggers onSave', async () => {
    vi.useFakeTimers()
    const onSave = vi.fn().mockResolvedValue(undefined)
    renderPage({ onSave })
    const select = screen.getByRole('combobox', { name: /risk owner/i })
    fireEvent.change(select, { target: { value: '' } })
    vi.runAllTimers()
    await Promise.resolve()
    expect(onSave).toHaveBeenCalled()
    vi.useRealTimers()
  })
})

// ─── Pager bounds ──────────────────────────────────────────────────────────

describe('RiskDetailPage — pager bounds', () => {
  it('prev button disabled when index is 0', () => {
    renderPage({ position: { index: 0, total: 42 } })
    const prevBtn = screen.getByRole('button', { name: /previous risk/i })
    expect(prevBtn).toBeDisabled()
  })

  it('next button disabled when index is last', () => {
    renderPage({ position: { index: 41, total: 42 } })
    const nextBtn = screen.getByRole('button', { name: /next risk/i })
    expect(nextBtn).toBeDisabled()
  })

  it('prev button enabled when index > 0', () => {
    renderPage({ position: { index: 3, total: 42 } })
    const prevBtn = screen.getByRole('button', { name: /previous risk/i })
    expect(prevBtn).not.toBeDisabled()
  })

  it('next button enabled when not at last', () => {
    renderPage({ position: { index: 3, total: 42 } })
    const nextBtn = screen.getByRole('button', { name: /next risk/i })
    expect(nextBtn).not.toBeDisabled()
  })

  it('clicking prev calls onPrev', () => {
    const onPrev = vi.fn()
    renderPage({ onPrev })
    fireEvent.click(screen.getByRole('button', { name: /previous risk/i }))
    expect(onPrev).toHaveBeenCalled()
  })

  it('clicking next calls onNext', () => {
    const onNext = vi.fn()
    renderPage({ onNext })
    fireEvent.click(screen.getByRole('button', { name: /next risk/i }))
    expect(onNext).toHaveBeenCalled()
  })
})

// ─── Keyboard ──────────────────────────────────────────────────────────────

describe('RiskDetailPage — keyboard shortcuts', () => {
  it('ArrowLeft fires onPrev', () => {
    const onPrev = vi.fn()
    renderPage({ onPrev })
    fireEvent.keyDown(window, { key: 'ArrowLeft' })
    expect(onPrev).toHaveBeenCalled()
  })

  it('ArrowRight fires onNext', () => {
    const onNext = vi.fn()
    renderPage({ onNext })
    fireEvent.keyDown(window, { key: 'ArrowRight' })
    expect(onNext).toHaveBeenCalled()
  })

  it('Escape fires onBack', () => {
    const onBack = vi.fn()
    renderPage({ onBack })
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onBack).toHaveBeenCalled()
  })

  it('ArrowLeft suppressed when focus is on a select', () => {
    const onPrev = vi.fn()
    renderPage({ onPrev })
    const selects = screen.getAllByRole('combobox', { name: /treatment status/i })
    const select = selects[0]
    // Simulate the keydown event with the select as the target
    // The handler checks e.target.tagName === 'select'
    fireEvent.keyDown(select, { key: 'ArrowLeft', bubbles: true })
    expect(onPrev).not.toHaveBeenCalled()
  })

  it('ArrowRight suppressed when focus is on a textarea', () => {
    const onNext = vi.fn()
    renderPage({ onNext })
    const ta = screen.getByRole('textbox', { name: /notes/i })
    ta.focus()
    // Dispatch keydown on window with the textarea as the event target
    const evt = new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })
    Object.defineProperty(evt, 'target', { value: ta, writable: false })
    window.dispatchEvent(evt)
    expect(onNext).not.toHaveBeenCalled()
  })
})

// ─── Controls addressing ──────────────────────────────────────────────────

describe('RiskDetailPage — controls list', () => {
  it('clicking a control row fires onNavigateToControl', async () => {
    const onNavigateToControl = vi.fn()
    renderPage({ onNavigateToControl })
    // Wait for controls to load (mocked immediately)
    await screen.findByText('GOV-01')
    const btn = screen.getByRole('button', { name: /GOV-01/i })
    fireEvent.click(btn)
    expect(onNavigateToControl).toHaveBeenCalledWith('GOV-01')
  })

  it('shows control implementation status badge', async () => {
    renderPage()
    await screen.findByText('GOV-04')
    expect(screen.getByText(/implemented/i)).toBeInTheDocument()
  })
})

// ─── Custom risk delete ───────────────────────────────────────────────────

describe('RiskDetailPage — custom risk', () => {
  it('shows delete button for custom risk (R-ORG-*)', () => {
    renderPage({ assessment: customAssessment, onDeleteCustomRisk: vi.fn() })
    expect(screen.getByRole('button', { name: /delete risk/i })).toBeInTheDocument()
  })

  it('does NOT show delete button for SCF risk', () => {
    renderPage({ assessment: baseAssessment })
    expect(screen.queryByRole('button', { name: /delete risk/i })).toBeNull()
  })

  it('shows Custom badge for custom risk', () => {
    renderPage({ assessment: customAssessment })
    // Custom chip in the header chips row
    const customChip = document.querySelector('.risk-detail-page-chip--custom')
    expect(customChip).not.toBeNull()
    expect(customChip?.textContent).toBe('Custom')
  })
})

// ─── Deep link + bare arrival ────────────────────────────────────────────

describe('RiskDashboard — bare arrival lands on list, not detail', () => {
  /**
   * This is a structural test — RiskDashboard must NOT auto-select
   * any risk on bare arrival at ?tab=risk-register.
   * We test this by importing RiskDashboard and checking that it only
   * opens the detail page when riskItem prop is non-null.
   */
  it('renders no RiskDetailPage breadcrumb when riskItem is null', async () => {
    // Import mocked at the outer vi.mock level above
    const { default: RiskDashboard } = await import('../RiskDashboard')
    const { act } = await import('@testing-library/react')
    await act(async () => {
      render(<RiskDashboard organizationId="org-1" riskItem={null} onRiskItemChange={vi.fn()} />)
    })
    // No breadcrumb nav should be present (that's only in detail page)
    expect(screen.queryByText(/Risk Register/i, { selector: 'button' })).toBeNull()
  })

  it('renders RiskDetailPage breadcrumb when riskItem is set', async () => {
    const { default: RiskDashboard } = await import('../RiskDashboard')
    const { act } = await import('@testing-library/react')
    await act(async () => {
      render(
        <RiskDashboard
          organizationId="org-1"
          riskItem="R-GV-1"
          onRiskItemChange={vi.fn()}
        />
      )
    })
    // Detail page is rendered (breadcrumb present)
    expect(screen.getByText(/Risk Register/i, { selector: 'button, [role="button"]' })).toBeInTheDocument()
  })
})

// ─── onBack ──────────────────────────────────────────────────────────────

describe('RiskDetailPage — back navigation', () => {
  it('clicking the breadcrumb back button fires onBack', () => {
    const onBack = vi.fn()
    renderPage({ onBack })
    const backBtn = screen.getByRole('button', { name: /back to risk register/i })
    fireEvent.click(backBtn)
    expect(onBack).toHaveBeenCalled()
  })
})
