/**
 * RiskRegister explorer chrome tests (Phase 3 Task 5)
 *
 * Pins:
 *  - FilterSidebar aside element present (status + category filters)
 *  - Search input (toolbar)
 *  - "+ Add Custom Risk" button present
 *  - Risk level summary strip (low/medium/high/critical counts)
 *  - ExplorerListRow rendered (explorer-row-id class) with risk code
 *  - Inline selects preserved (likelihood, impact, status)
 *  - Category badge rendered
 *  - Cell-filter chip rendered when filterByCell is set
 *  - Sortable header row rendered
 *  - Score badge rendered for assessed risk
 *
 * RiskDashboard is NOT tested here — matrix/slide-over are untouched.
 * We test RiskAssessmentList directly (the list panel).
 */
import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import RiskAssessmentList from '../RiskAssessmentList'
import type { RiskAssessment, RiskCodesFile } from '../../types'

// Minimal risk codes fixture
const riskCodes: RiskCodesFile = {
  categories: {
    GV: { name: 'Governance', color: '#6366f1' },
    AC: { name: 'Access Control', color: '#3b82f6' },
    ORG: { name: 'Custom', color: '#6b7280' },
  } as RiskCodesFile['categories'],
  codes: {
    'R-GV-1': { category: 'GV' as any, title: 'Security Governance', description: 'Inadequate governance' },
    'R-AC-1': { category: 'AC' as any, title: 'Access Control Risk', description: 'Access control failure' },
  },
}

const assessments: RiskAssessment[] = [
  {
    risk_code: 'R-GV-1',
    treatment_status: 'identified',
    likelihood: 4,
    impact: 5,
    inherent_risk_score: 20,
    inherent_risk_level: 'critical',
    residual_likelihood: 2,
    residual_impact: 3,
    residual_risk_score: 6,
    residual_risk_level: 'medium',
    owner: null,
  } as unknown as RiskAssessment,
  {
    risk_code: 'R-AC-1',
    treatment_status: 'treating',
    likelihood: 2,
    impact: 2,
    inherent_risk_score: 4,
    inherent_risk_level: 'low',
    residual_likelihood: null,
    residual_impact: null,
    residual_risk_score: null,
    residual_risk_level: null,
    owner: { display_name: 'Alice', email: 'alice@example.com' },
  } as unknown as RiskAssessment,
]

function renderList(overrides?: Partial<React.ComponentProps<typeof RiskAssessmentList>>) {
  const props = {
    assessments,
    riskCodes,
    onSelectRisk: vi.fn(),
    onUpdateRisk: vi.fn(),
    selectedRiskCode: null,
    filterByCell: null,
    matrixType: 'inherent' as const,
    ...overrides,
  }
  return render(<RiskAssessmentList {...props} />)
}

describe('RiskAssessmentList — Explorer chrome (Phase 3 Task 5)', () => {
  it('renders a FilterSidebar aside element', () => {
    renderList()
    expect(screen.getByRole('complementary')).toBeInTheDocument()
  })

  it('renders a search input in the toolbar', () => {
    renderList()
    expect(screen.getByRole('searchbox')).toBeInTheDocument()
  })

  it('changing an inline select does NOT fire onSelectRisk (stopPropagation isolates select from row click)', () => {
    const onSelectRisk = vi.fn()
    const onUpdateRisk = vi.fn()
    renderList({ onSelectRisk, onUpdateRisk })
    // Find a likelihood select and change it
    const likelihoodSelects = screen.getAllByRole('combobox', { name: /likelihood/i })
    fireEvent.change(likelihoodSelects[0], { target: { value: '3' } })
    // onUpdateRisk should fire (select handler fires), but NOT onSelectRisk (row click blocked)
    expect(onUpdateRisk).toHaveBeenCalled()
    expect(onSelectRisk).not.toHaveBeenCalled()
  })

  it('renders risk code in explorer-row-id slot', () => {
    renderList()
    const idEls = document.querySelectorAll('.explorer-row-id')
    const codes = Array.from(idEls).map(el => el.textContent)
    expect(codes).toContain('R-GV-1')
    expect(codes).toContain('R-AC-1')
  })

  it('renders category badge for each row', () => {
    renderList()
    // There may be multiple elements (row badge + filter option); at least one each
    expect(screen.getAllByText('Governance').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Access Control').length).toBeGreaterThan(0)
    // Verify the badge elements are in the DOM
    const badges = document.querySelectorAll('.risk-category-badge')
    expect(badges.length).toBe(2)
  })

  it('renders inline likelihood select preserving existing values', () => {
    renderList()
    // Two likelihood selects — values 4 and 2
    const selects = screen.getAllByRole('combobox')
    const selectValues = selects.map(s => (s as HTMLSelectElement).value)
    expect(selectValues).toContain('4')
    expect(selectValues).toContain('2')
  })

  it('fires onUpdateRisk when inline likelihood select changes', () => {
    const onUpdateRisk = vi.fn()
    renderList({ onUpdateRisk })
    // Find likelihood select by aria-label
    const likelihoodSelects = screen.getAllByRole('combobox', { name: /likelihood/i })
    // sorted default is risk_code asc: R-AC-1 first, R-GV-1 second
    // Change R-GV-1's likelihood (second row = index 1)
    fireEvent.change(likelihoodSelects[1], { target: { value: '3' } })
    expect(onUpdateRisk).toHaveBeenCalledWith('R-GV-1', { likelihood: 3 })
  })

  it('fires onUpdateRisk when inline impact select changes', () => {
    const onUpdateRisk = vi.fn()
    renderList({ onUpdateRisk })
    const impactSelects = screen.getAllByRole('combobox', { name: /impact/i })
    fireEvent.change(impactSelects[1], { target: { value: '2' } })
    expect(onUpdateRisk).toHaveBeenCalledWith('R-GV-1', { impact: 2 })
  })

  it('fires onUpdateRisk when inline status select changes', () => {
    const onUpdateRisk = vi.fn()
    renderList({ onUpdateRisk })
    const statusSelects = screen.getAllByRole('combobox', { name: /status/i })
    // R-GV-1 is second row (index 1), change status to 'treating'
    fireEvent.change(statusSelects[1], { target: { value: 'treating' } })
    expect(onUpdateRisk).toHaveBeenCalledWith('R-GV-1', { treatment_status: 'treating' })
  })

  it('renders score badge for a risk with an inherent score', () => {
    renderList()
    // R-GV-1 has inherent score 20
    const scoreBadges = document.querySelectorAll('.risk-score-badge')
    expect(scoreBadges.length).toBeGreaterThan(0)
    const texts = Array.from(scoreBadges).map(b => b.textContent)
    expect(texts).toContain('20')
  })

  it('renders sortable header row', () => {
    renderList()
    // Sortable headers are rendered as buttons (or clickable divs with role button)
    const codeHeader = screen.getByRole('button', { name: /code/i })
    expect(codeHeader).toBeInTheDocument()
  })

  it('renders cell-filter chip when filterByCell is set', () => {
    renderList({ filterByCell: { likelihood: 4, impact: 5 } })
    expect(screen.getByText(/L4.*I5|L4 × I5/i)).toBeInTheDocument()
  })

  it('clears cell filter when × button clicked', () => {
    const onSelectRisk = vi.fn()
    renderList({ filterByCell: { likelihood: 4, impact: 5 }, onSelectRisk })
    const clearBtn = screen.getByRole('button', { name: /clear.*filter|×/i })
    fireEvent.click(clearBtn)
    expect(onSelectRisk).toHaveBeenCalledWith('')
  })

  it('filters by search term — only matching rows shown', () => {
    renderList()
    const search = screen.getByRole('searchbox')
    fireEvent.change(search, { target: { value: 'governance' } })
    const idEls = document.querySelectorAll('.explorer-row-id')
    expect(idEls.length).toBe(1)
    expect(idEls[0].textContent).toBe('R-GV-1')
  })

  it('shows count of filtered risks in toolbar', () => {
    renderList()
    // Should show "2 risks" or "2 assessments" in the count area
    expect(screen.getByText(/2 risk/i)).toBeInTheDocument()
  })

  it('fires onSelectRisk when a row is clicked', () => {
    const onSelectRisk = vi.fn()
    renderList({ onSelectRisk })
    const rows = document.querySelectorAll('.explorer-row[role="button"]')
    expect(rows.length).toBeGreaterThan(0)
    // Default sort is risk_code asc: first row is R-AC-1
    fireEvent.click(rows[0])
    expect(onSelectRisk).toHaveBeenCalledWith('R-AC-1')
  })

  it('highlights selected row', () => {
    renderList({ selectedRiskCode: 'R-GV-1' })
    const highlighted = document.querySelector('.explorer-row--highlighted')
    expect(highlighted).not.toBeNull()
    expect(highlighted?.querySelector('.explorer-row-id')?.textContent).toBe('R-GV-1')
  })
})

// ── RiskDashboard level-strip tests ──────────────────────────────────────────

import { act } from '@testing-library/react'
import RiskDashboard from '../RiskDashboard'

// Stub all heavy API calls
vi.mock('../../data/apiClient', () => ({
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
    },
    {
      risk_code: 'R-AC-1',
      treatment_status: 'treating',
      likelihood: 3,
      impact: 3,
      inherent_risk_score: 9,
      inherent_risk_level: 'medium',
      residual_likelihood: null,
      residual_impact: null,
      residual_risk_score: null,
      residual_risk_level: null,
      owner: null,
    },
  ]),
  getOrgMembers: vi.fn().mockResolvedValue([]),
  getCustomRiskDefinitions: vi.fn().mockResolvedValue([]),
  createOrUpdateRiskAssessment: vi.fn().mockResolvedValue({}),
  updateRiskAssessment: vi.fn().mockResolvedValue({}),
  createCustomRisk: vi.fn().mockResolvedValue({}),
  deleteCustomRisk: vi.fn().mockResolvedValue({}),
  // RiskAssessmentDetail also calls getScopedControls
  getScopedControls: vi.fn().mockResolvedValue([]),
}))

vi.mock('../../contexts/RiskProfileContext', () => ({
  useRiskProfile: () => ({
    riskThresholds: { lowMax: 4, mediumMax: 9, highMax: 15 },
  }),
}))

vi.mock('../../contexts/OrganizationContext', () => ({
  useOrganization: () => ({ currentOrg: 'org-1' }),
}))

describe('RiskDashboard — level summary strip', () => {
  it('renders the level summary strip with level-label mono elements', async () => {
    await act(async () => {
      render(<RiskDashboard organizationId="org-1" />)
    })
    // The strip uses .risk-level-label elements (uppercase mono font)
    const labels = document.querySelectorAll('.risk-level-label')
    const labelTexts = Array.from(labels).map(l => l.textContent)
    expect(labelTexts).toContain('LOW')
    expect(labelTexts).toContain('MEDIUM')
    expect(labelTexts).toContain('HIGH')
    expect(labelTexts).toContain('CRITICAL')
  })

  it('renders "+ Add Custom Risk" button in the dashboard strip (always visible in default matrix view)', async () => {
    await act(async () => {
      render(<RiskDashboard organizationId="org-1" />)
    })
    // Default is matrix view with no cell selected; button should be visible in the strip
    const btn = screen.getByRole('button', { name: /add custom risk/i })
    expect(btn).toBeInTheDocument()
    // Verify it opens the modal when clicked
    fireEvent.click(btn)
    // Modal should render
    expect(screen.getByText('Add Custom Risk')).toBeInTheDocument()
  })
})
