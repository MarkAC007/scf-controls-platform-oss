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
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { toast } from 'react-hot-toast'
import { answerConfirm, confirmClosed, confirmWrapper, findConfirm } from '../../__tests__/confirmDialog'
import RiskDetailPage from '../RiskDetailPage'
import {
  addCustomRiskControl,
  fetchScopedControlsPage,
  getControlsForRisk,
  getCustomRiskControlLinks,
  removeCustomRiskControl,
  setCustomRiskControlDocument,
} from '../../data/apiClient'
import type { RiskAssessment, RiskCodesFile, UserSimple } from '../../types'

// RiskDetailPage and the panels it hosts read server state through react-query.
function queryWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
}

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
  fetchScopedControlsPage: vi.fn().mockResolvedValue({ total: 0, limit: 30, offset: 0, controls: [] }),
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
  getRiskAssessment: vi.fn().mockRejectedValue(new Error('not stubbed')),
  // PIG R7
  getCustomRiskControlLinks: vi.fn().mockResolvedValue([]),
  setCustomRiskControlDocument: vi.fn().mockResolvedValue(undefined),
  acceptRisk: vi.fn(),
  withdrawRiskAcceptance: vi.fn(),
}))

vi.mock('../../data/documentsApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../data/documentsApi')>()),
  listDocuments: vi.fn().mockResolvedValue([
    { id: 'doc-1', title: 'Access Control Standard', document_type: 'standard', lifecycle_status: 'approved' },
  ]),
}))

const roleState = vi.hoisted(() => ({ isAdmin: true }))
vi.mock('../../hooks/useIsOrgAdmin', () => ({
  useIsOrgAdmin: () => roleState.isAdmin,
  default: () => roleState.isAdmin,
}))
vi.mock('../../hooks/useHasOrgRole', () => ({ useIsOrgEditor: () => true }))
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: { db_id: 'user-1' } }) }))

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
  return render(<RiskDetailPage {...defaults} {...overrides} />, { wrapper: confirmWrapper(queryWrapper()) })
}

type CatalogRow = { scf_id: string; control_name: string; control_description?: string }

/**
 * The scoped-controls page endpoint as the server behaves: in-scope rows in
 * scf_id order, matched on id, title or description, paged by offset and a
 * limit the server caps at 200.
 */
function serveCatalog(rows: CatalogRow[]) {
  vi.mocked(fetchScopedControlsPage).mockImplementation(async (params = {}) => {
    const term = (params.search ?? '').toLowerCase()
    const matches = rows
      .filter((r) => [r.scf_id, r.control_name, r.control_description ?? ''].some((v) => v.toLowerCase().includes(term)))
      .sort((a, b) => a.scf_id.localeCompare(b.scf_id))
    const offset = params.offset ?? 0
    const limit = Math.min(params.limit ?? 50, 200)
    return searchPage(matches.slice(offset, offset + limit), { total: matches.length, offset, limit })
  })
}

/** A page of the catalog-joined scoped-control search, as the server returns it. */
function searchPage(
  controls: CatalogRow[],
  page: { total: number; offset: number; limit: number } = { total: controls.length, offset: 0, limit: 200 },
) {
  return {
    ...page,
    controls: controls.map((c) => ({ ...c, scf_domain: c.scf_id.split('-')[0], selected: true })),
  } as never
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

// ─── Risk acceptance + standards exceptions (PIG R7) ─────────────────────

describe('RiskDetailPage — risk acceptance', () => {
  const accepted = {
    ...baseAssessment,
    treatment_status: 'accepted',
    accepted_by: 'user-1',
    accepted_at: '2026-10-01T09:30:00',
    acceptance_rationale: 'Inside appetite',
    acceptance_expires_on: '2099-03-31',
    acceptance_state: 'approved',
  } as RiskAssessment

  it('hosts the acceptance panel on the detail page', () => {
    renderPage()
    expect(screen.getByRole('region', { name: 'Risk acceptance' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Accept risk' })).toBeInTheDocument()
  })

  it('shows the approval badge in the header for an accepted risk', () => {
    renderPage({ assessment: accepted })
    const header = document.querySelector('.risk-detail-page-chips-row') as HTMLElement
    expect(header.querySelector('.risk-acceptance-badge--approved')?.textContent).toMatch(/^Approved · expires/)
  })
})

describe('RiskDetailPage — custom risk control link document', () => {
  // Opaque placeholder: the page only passes the control id through.
  const CONTROL = 'TESTCONTROL'

  beforeEach(() => {
    vi.mocked(addCustomRiskControl).mockClear()
    vi.mocked(fetchScopedControlsPage).mockResolvedValue(searchPage([{ scf_id: CONTROL, control_name: 'Test control' }]))
  })

  afterEach(() => {
    vi.mocked(fetchScopedControlsPage).mockResolvedValue(searchPage([]))
  })

  // #1156: the result listed only "AST-02" (the scoped-controls list carries
  // no title), and sat below the document select as if it belonged to it.
  it('lists a search result as its id and title, between the search input and the document select', async () => {
    serveCatalog([{ scf_id: 'AST-02', control_name: 'Asset Inventories' }])
    renderPage({ assessment: customAssessment })
    fireEvent.click(await screen.findByRole('button', { name: '+ Add Control' }))
    const input = screen.getByPlaceholderText('Search controls by ID or name...')
    fireEvent.change(input, { target: { value: 'AST' } })

    const result = await screen.findByRole('button', { name: 'AST-02 — Asset Inventories' })
    expect(fetchScopedControlsPage).toHaveBeenCalledWith(
      expect.objectContaining({ search: 'AST', scope_status: 'in_scope' }),
      'org-1',
    )
    // The separator belongs to the title, so the id column stays aligned:
    // the button holds the two spans and no bare text.
    expect(Array.from(result.childNodes).every((n) => n.nodeType === Node.ELEMENT_NODE)).toBe(true)
    expect(result.querySelector('.control-search-result-name')).toHaveTextContent('— Asset Inventories')

    const list = result.closest('.control-search-results') as HTMLElement
    expect(list.previousElementSibling).toHaveClass('control-search-input-row')
    expect(list.previousElementSibling).toContainElement(input)
    expect(list.nextElementSibling).toContainElement(screen.getByLabelText('Supporting document (optional)'))
  })

  it('leaves out a control the risk already links', async () => {
    const linkedId = (await getControlsForRisk('R-ORG-1', 'org-1')).catalog_control_ids[0]
    serveCatalog([
      { scf_id: linkedId, control_name: 'Already linked' },
      { scf_id: 'AST-02', control_name: 'Asset Inventories' },
    ])
    renderPage({ assessment: customAssessment })
    fireEvent.click(await screen.findByRole('button', { name: '+ Add Control' }))
    fireEvent.change(screen.getByPlaceholderText('Search controls by ID or name...'), { target: { value: 'a' } })
    await screen.findByRole('button', { name: 'AST-02 — Asset Inventories' })
    expect(screen.queryByRole('button', { name: new RegExp(`^${linkedId} — `) })).toBeNull()
  })

  // #1156 review: with 30+ controls already linked, a capped page of matches
  // could hold nothing but linked rows and description hits.
  it('finds the unlinked matches past 35 linked ones and 200 description hits, id matches first', async () => {
    const pad = (n: number) => String(n).padStart(2, '0')
    const linked = Array.from({ length: 35 }, (_, i) => `IAC-${pad(i + 1)}`)
    vi.mocked(getControlsForRisk).mockResolvedValueOnce({
      risk_code: 'R-ORG-1',
      total_catalog_controls: linked.length,
      catalog_control_ids: linked,
      scoped_controls: linked.map((scf_id) => ({
        scf_id, control_name: `Linked ${scf_id}`, implementation_status: 'implemented', priority: null, target_date: null,
      })),
    } as never)
    serveCatalog([
      ...Array.from({ length: 40 }, (_, i) => ({ scf_id: `IAC-${pad(i + 1)}`, control_name: `Identity control ${i + 1}` })),
      ...Array.from({ length: 200 }, (_, i) => ({
        scf_id: `GOV-${String(i + 1).padStart(3, '0')}`,
        control_name: `Governance ${i + 1}`,
        control_description: 'Mentions iac in passing',
      })),
    ])
    renderPage({ assessment: customAssessment })
    expect(await screen.findByRole('button', { name: 'IAC-35' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '+ Add Control' }))
    fireEvent.change(screen.getByPlaceholderText('Search controls by ID or name...'), { target: { value: 'IAC' } })

    await screen.findByRole('button', { name: 'IAC-36 — Identity control 36' })
    const names = Array.from(document.querySelectorAll('.control-search-result-btn')).map((b) => b.textContent ?? '')
    expect(names.slice(0, 5).map((n) => n.slice(0, 6))).toEqual(['IAC-36', 'IAC-37', 'IAC-38', 'IAC-39', 'IAC-40'])
    expect(names.slice(5).every((n) => n.startsWith('GOV-'))).toBe(true)
    expect(names.some((n) => /^IAC-(0\d|[12]\d|3[0-5]) /.test(n))).toBe(false)
  })

  // #1156 review: results for the previous term stayed clickable while the
  // next one was still being typed.
  it('withdraws the previous results as soon as the term changes', async () => {
    serveCatalog([
      { scf_id: 'AST-02', control_name: 'Asset Inventories' },
      { scf_id: 'GOV-01', control_name: 'Governance Program' },
    ])
    renderPage({ assessment: customAssessment })
    fireEvent.click(await screen.findByRole('button', { name: '+ Add Control' }))
    const input = screen.getByPlaceholderText('Search controls by ID or name...')
    fireEvent.change(input, { target: { value: 'AST' } })
    await screen.findByRole('button', { name: 'AST-02 — Asset Inventories' })

    fireEvent.change(input, { target: { value: 'ASTX' } })
    expect(screen.queryByRole('button', { name: 'AST-02 — Asset Inventories' })).toBeNull()
    expect(screen.getByText('Searching…')).toBeInTheDocument()
    expect(await screen.findByText('No matching controls found')).toBeInTheDocument()
  })

  it('links a control with the supporting document picked for it', async () => {
    renderPage({ assessment: customAssessment })
    fireEvent.click(await screen.findByRole('button', { name: '+ Add Control' }))
    await screen.findByRole('option', { name: /Access Control Standard/ })
    fireEvent.change(screen.getByLabelText('Supporting document (optional)'), { target: { value: 'doc-1' } })
    fireEvent.change(screen.getByPlaceholderText('Search controls by ID or name...'), { target: { value: 'test' } })
    fireEvent.click(await screen.findByRole('button', { name: new RegExp(CONTROL) }))
    await waitFor(() =>
      expect(addCustomRiskControl).toHaveBeenCalledWith('R-ORG-1', CONTROL, 'org-1', 'doc-1'),
    )
  })

  it('links a control with no document when none is picked', async () => {
    renderPage({ assessment: customAssessment })
    fireEvent.click(await screen.findByRole('button', { name: '+ Add Control' }))
    fireEvent.change(screen.getByPlaceholderText('Search controls by ID or name...'), { target: { value: 'test' } })
    fireEvent.click(await screen.findByRole('button', { name: new RegExp(CONTROL) }))
    await waitFor(() =>
      expect(addCustomRiskControl).toHaveBeenCalledWith('R-ORG-1', CONTROL, 'org-1', null),
    )
  })

  it('shows the API detail when the link is refused', async () => {
    vi.mocked(addCustomRiskControl).mockRejectedValueOnce(new Error('No such document in this organisation'))
    renderPage({ assessment: customAssessment })
    fireEvent.click(await screen.findByRole('button', { name: '+ Add Control' }))
    fireEvent.change(screen.getByPlaceholderText('Search controls by ID or name...'), { target: { value: 'test' } })
    fireEvent.click(await screen.findByRole('button', { name: new RegExp(CONTROL) }))
    expect(await screen.findByRole('alert')).toHaveTextContent('No such document in this organisation')
  })

  it("shows a linked control's document, read from the custom-risk links", async () => {
    // The first scoped control the controls mock returns, read at runtime.
    const linkedId = (await getControlsForRisk('R-ORG-1', 'org-1')).scoped_controls[0].scf_id
    vi.mocked(getCustomRiskControlLinks).mockResolvedValueOnce([{ scf_id: linkedId, document_id: 'doc-1' }])
    renderPage({ assessment: customAssessment })
    expect(await screen.findByText('Access Control Standard')).toBeInTheDocument()
    expect(getCustomRiskControlLinks).toHaveBeenCalledWith('R-ORG-1', 'org-1')
    expect(screen.getByRole('button', { name: `Change document for ${linkedId}` })).toBeInTheDocument()
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
      render(<RiskDashboard organizationId="org-1" riskItem={null} onRiskItemChange={vi.fn()} />, { wrapper: queryWrapper() })
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
        />,
        { wrapper: queryWrapper() },
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

// ─── PIG U5 review: approvals in force, link states, wiring ──────────────

const inForce = {
  accepted_by: 'user-1',
  accepted_at: '2026-10-01T09:30:00',
  acceptance_rationale: 'Inside appetite',
  acceptance_expires_on: '2099-03-31',
  acceptance_state: 'approved',
}
const approvedRisk = { ...baseAssessment, treatment_status: 'accepted', ...inForce } as RiskAssessment
const approvedCustom = { ...customAssessment, treatment_status: 'accepted', ...inForce } as RiskAssessment
const WITHDRAW = (code: string) => `This withdraws the admin approval of ${code}'s acceptance. Continue?`

describe('RiskDetailPage — ordinary saves against an approval in force', () => {
  beforeEach(() => {
    roleState.isAdmin = true
  })
  afterEach(() => {
    roleState.isAdmin = true
  })

  const residualImpact = () => document.getElementById('residual-impact-R-GV-1') as HTMLSelectElement

  it('asks before a residual re-score withdraws the approval, and saves nothing on cancel', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    renderPage({ assessment: approvedRisk, onSave })
    fireEvent.change(residualImpact(), { target: { value: '4' } })
    expect(await answerConfirm(false)).toBe(WITHDRAW('R-GV-1'))
    expect(residualImpact()).toHaveValue('3')
    await new Promise((resolve) => setTimeout(resolve, 600))
    expect(onSave).not.toHaveBeenCalled()
  })

  it('asks once per approval, then saves the re-score', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    renderPage({ assessment: approvedRisk, onSave })
    fireEvent.change(residualImpact(), { target: { value: '4' } })
    expect(await answerConfirm(true)).toBe(WITHDRAW('R-GV-1'))
    await waitFor(() => expect(residualImpact()).toHaveValue('4'))
    const inherentLikelihood = document.getElementById('inherent-likelihood-R-GV-1') as HTMLSelectElement
    fireEvent.change(inherentLikelihood, { target: { value: '5' } })
    // Asked once: the second re-score of the same approval opens nothing.
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(inherentLikelihood).toHaveValue('5')
    await waitFor(() => expect(onSave).toHaveBeenCalled())
    expect(onSave.mock.calls[0][1]).toMatchObject({ residual_impact: 4, likelihood: 5, treatment_status: 'accepted' })
  })

  it('asks an admin before a status change away from Accepted withdraws the approval', async () => {
    renderPage({ assessment: approvedRisk })
    fireEvent.change(screen.getByLabelText('Treatment Status'), { target: { value: 'treating' } })
    expect(await answerConfirm(false)).toBe(WITHDRAW('R-GV-1'))
    expect(screen.getByLabelText('Treatment Status')).toHaveValue('accepted')
  })

  it('tells an editor the status will stay Accepted', async () => {
    roleState.isAdmin = false
    renderPage({ assessment: approvedRisk })
    fireEvent.change(screen.getByLabelText('Treatment Status'), { target: { value: 'treating' } })
    expect(await answerConfirm(false)).toBe(
      "R-GV-1's acceptance is approved, and only an admin can withdraw it: the status will stay Accepted. Continue?",
    )
  })

  it('asks again after a cancel: a refusal does not count as the once-per-approval answer', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    renderPage({ assessment: approvedRisk, onSave })
    fireEvent.change(residualImpact(), { target: { value: '4' } })
    await answerConfirm(false)
    expect(residualImpact()).toHaveValue('3')
    fireEvent.change(residualImpact(), { target: { value: '4' } })
    expect(await answerConfirm(true)).toBe(WITHDRAW('R-GV-1'))
    await waitFor(() => expect(residualImpact()).toHaveValue('4'))
    await waitFor(() => expect(onSave).toHaveBeenCalled())
    expect(onSave.mock.calls[0][1]).toMatchObject({ residual_impact: 4 })
  })

  it('applies an editor\'s change once they continue past the stays-Accepted warning', async () => {
    roleState.isAdmin = false
    renderPage({ assessment: approvedRisk })
    fireEvent.change(screen.getByLabelText('Treatment Status'), { target: { value: 'treating' } })
    expect(screen.getByLabelText('Treatment Status')).toHaveValue('accepted')
    await answerConfirm(true)
    await waitFor(() => expect(screen.getByLabelText('Treatment Status')).toHaveValue('treating'))
  })

  it('asks once for two changes made while the question is open: both apply on continue', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    renderPage({ assessment: approvedRisk, onSave })
    const inherentLikelihood = document.getElementById('inherent-likelihood-R-GV-1') as HTMLSelectElement
    fireEvent.change(residualImpact(), { target: { value: '4' } })
    await findConfirm()
    fireEvent.change(inherentLikelihood, { target: { value: '5' } })
    expect(await answerConfirm(true)).toBe(WITHDRAW('R-GV-1'))
    // The second change waited on the same answer: no second question.
    await waitFor(() => expect(inherentLikelihood).toHaveValue('5'))
    expect(residualImpact()).toHaveValue('4')
    expect(screen.queryByRole('alertdialog')).toBeNull()
    await waitFor(() => expect(onSave).toHaveBeenCalled())
    expect(onSave.mock.calls[0][1]).toMatchObject({ residual_impact: 4, likelihood: 5 })
  })

  it('drops both changes made while the question is open when it is cancelled', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    renderPage({ assessment: approvedRisk, onSave })
    const inherentLikelihood = document.getElementById('inherent-likelihood-R-GV-1') as HTMLSelectElement
    const before = inherentLikelihood.value
    fireEvent.change(residualImpact(), { target: { value: '4' } })
    await findConfirm()
    fireEvent.change(inherentLikelihood, { target: { value: '5' } })
    await answerConfirm(false)
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(residualImpact()).toHaveValue('3')
    expect(inherentLikelihood).toHaveValue(before)
    await new Promise((resolve) => setTimeout(resolve, 600))
    expect(onSave).not.toHaveBeenCalled()
  })

  it('does not ask when no approval is in force', () => {
    renderPage({ assessment: { ...approvedRisk, acceptance_expires_on: '2020-01-01', acceptance_state: 'expired' } as RiskAssessment })
    fireEvent.change(residualImpact(), { target: { value: '4' } })
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(residualImpact()).toHaveValue('4')
  })
})

describe('RiskDetailPage — custom-risk link changes and the approval', () => {
  let linkedId: string

  beforeEach(async () => {
    vi.mocked(addCustomRiskControl).mockClear()
    vi.mocked(removeCustomRiskControl).mockClear()
    vi.mocked(setCustomRiskControlDocument).mockClear()
    vi.mocked(toast.success).mockClear()
    linkedId = (await getControlsForRisk('R-ORG-1', 'org-1')).scoped_controls[0].scf_id
  })

  it('confirms before removing a link while the approval is in force; cancel keeps it', async () => {
    renderPage({ assessment: approvedCustom })
    fireEvent.click(await screen.findByRole('button', { name: `Remove ${linkedId}` }))
    expect(await answerConfirm(false)).toBe(WITHDRAW('R-ORG-1'))
    expect(removeCustomRiskControl).not.toHaveBeenCalled()
  })

  it('re-reads the risk after a link change and says the approval was withdrawn', async () => {
    const onRefreshAssessment = vi.fn().mockResolvedValue({
      ...approvedCustom, accepted_at: null, accepted_by: null, acceptance_state: 'unapproved',
    })
    renderPage({ assessment: approvedCustom, onRefreshAssessment })
    fireEvent.click(await screen.findByRole('button', { name: `Remove ${linkedId}` }))
    await answerConfirm(true)
    await waitFor(() => expect(onRefreshAssessment).toHaveBeenCalledWith('R-ORG-1'))
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        `${linkedId} unlinked. Approval withdrawn: R-ORG-1's control links changed.`,
      ),
    )
  })

  it('re-reads the risk after a link is added, with no confirmation when nothing is approved', async () => {
    vi.mocked(fetchScopedControlsPage).mockResolvedValue(searchPage([{ scf_id: 'TESTCONTROL', control_name: 'Test control' }]))
    const onRefreshAssessment = vi.fn().mockResolvedValue(customAssessment)
    renderPage({ assessment: customAssessment, onRefreshAssessment })
    fireEvent.click(await screen.findByRole('button', { name: '+ Add Control' }))
    fireEvent.change(screen.getByPlaceholderText('Search controls by ID or name...'), { target: { value: 'test' } })
    fireEvent.click(await screen.findByRole('button', { name: /TESTCONTROL/ }))
    await waitFor(() => expect(onRefreshAssessment).toHaveBeenCalledWith('R-ORG-1'))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('TESTCONTROL linked'))
  })

  it("passes the confirmation to a link's document change", async () => {
    renderPage({ assessment: approvedCustom })
    fireEvent.click(await screen.findByRole('button', { name: `Add document for ${linkedId}` }))
    await screen.findByRole('option', { name: /Access Control Standard/ })
    fireEvent.change(screen.getByLabelText(`Supporting document for ${linkedId}`), { target: { value: 'doc-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await answerConfirm(false)).toBe(WITHDRAW('R-ORG-1'))
    expect(setCustomRiskControlDocument).not.toHaveBeenCalled()
  })

  it('deletes a custom risk only after confirmation, word for word', async () => {
    const onDeleteCustomRisk = vi.fn().mockResolvedValue(undefined)
    renderPage({ assessment: customAssessment, onDeleteCustomRisk })
    fireEvent.click(screen.getByRole('button', { name: 'Delete Risk' }))
    const open = await findConfirm()
    expect(open.title).toBe('Delete custom risk')
    expect(open.message).toBe('Delete custom risk R-ORG-1? This cannot be undone.')
    open.cancel()
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(onDeleteCustomRisk).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Delete Risk' }))
    expect(await answerConfirm(true)).toBe('Delete custom risk R-ORG-1? This cannot be undone.')
    await waitFor(() => expect(onDeleteCustomRisk).toHaveBeenCalledWith('R-ORG-1'))
  })
})

describe('RiskDetailPage — pager keys while a confirmation is open', () => {
  it('does not move to another risk on arrow keys or leave on Escape while the dialog is open', async () => {
    const onPrev = vi.fn()
    const onNext = vi.fn()
    const onBack = vi.fn()
    renderPage({ assessment: customAssessment, onPrev, onNext, onBack })
    fireEvent.click(screen.getByRole('button', { name: 'Delete Risk' }))
    const open = await findConfirm()
    const focused = document.activeElement as HTMLElement
    expect(open.dialog.contains(focused)).toBe(true)
    fireEvent.keyDown(focused, { key: 'ArrowRight' })
    fireEvent.keyDown(focused, { key: 'ArrowLeft' })
    expect(onNext).not.toHaveBeenCalled()
    expect(onPrev).not.toHaveBeenCalled()
    fireEvent.keyDown(focused, { key: 'Escape' })
    await confirmClosed()
    expect(onBack).not.toHaveBeenCalled()
    // Closed, the pager answers again.
    fireEvent.keyDown(document.body, { key: 'ArrowRight' })
    expect(onNext).toHaveBeenCalledTimes(1)
  })
})

describe('RiskDetailPage — custom-risk links loading and failure', () => {
  it('shows the links loading and offers no edit until they arrive', async () => {
    vi.mocked(getCustomRiskControlLinks).mockReturnValueOnce(new Promise(() => {}))
    renderPage({ assessment: customAssessment })
    expect((await screen.findAllByText('Loading supporting document…')).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: '+ Add Control' })).toBeNull()
    expect(screen.queryByRole('button', { name: /document for/ })).toBeNull()
    expect(screen.queryByText('No supporting document')).toBeNull()
  })

  it('shows a failed links read in an alert and offers no edit', async () => {
    vi.mocked(getCustomRiskControlLinks).mockRejectedValueOnce(new Error('Links unavailable'))
    renderPage({ assessment: customAssessment })
    expect(await screen.findByRole('alert')).toHaveTextContent('Links unavailable')
    expect(screen.queryByRole('button', { name: '+ Add Control' })).toBeNull()
    expect(screen.queryByRole('button', { name: /document for/ })).toBeNull()
    expect(screen.queryByText('No supporting document')).toBeNull()
  })
})

describe('RiskDetailPage — the pending autosave holds the acceptance decision', () => {
  afterEach(() => vi.useRealTimers())

  it('keeps Record disabled from an edit until its debounced save has landed', async () => {
    let finishSave: () => void = () => {}
    const onSave = vi.fn(() => new Promise<void>((resolve) => { finishSave = resolve }))
    renderPage({ onSave })
    fireEvent.click(screen.getByRole('button', { name: 'Accept risk' }))
    fireEvent.change(screen.getByLabelText('Rationale'), { target: { value: 'Inside appetite' } })
    fireEvent.change(screen.getByLabelText('Expires on'), { target: { value: '2099-03-31' } })
    const record = screen.getByRole('button', { name: 'Record acceptance' })
    expect(record).toBeEnabled()

    vi.useFakeTimers()
    fireEvent.change(screen.getByLabelText('Notes'), { target: { value: 'Edited' } })
    expect(record).toBeDisabled()
    await act(async () => { vi.advanceTimersByTime(500) })
    expect(onSave).toHaveBeenCalled()
    expect(record).toBeDisabled()
    await act(async () => { finishSave() })
    expect(record).toBeEnabled()
  })
})
