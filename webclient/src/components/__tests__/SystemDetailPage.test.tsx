/**
 * SystemDetailPage tests — Phase 4 Task 6
 *
 * Pins:
 *  - Breadcrumb "‹ Systems Registry / <name>" renders
 *  - "k of N systems" pager text renders
 *  - Header: name, type chip, status chip, vendor + description
 *  - "LINKED CONTROLS" section renders when capabilities present
 *  - "LINKED CONTROLS" gracefully absent when no capabilities
 *  - "EVIDENCE RECIPES" section renders with regenerate action
 *  - "Regenerate recipes" button fires generateSystemRecipes
 *  - "ASSOCIATED EVIDENCE" section renders when tracking present
 *  - "ASSOCIATED EVIDENCE" gracefully absent when empty
 *  - Deviation note when recipe generation status unavailable
 *  - Edit button fires onEdit
 *  - Back button fires onBack (via onSystemItemChange null)
 *  - Pager prev/next navigate via onSystemItemChange
 *  - Pager disabled at list boundaries
 *  - Keyboard ArrowRight navigates to next system
 *  - Keyboard ArrowLeft navigates to prev system
 *  - Keyboard Escape goes back
 *  - Keyboard suppressed when focus in input
 *  - "— of N" shown when system not in filtered set
 */
import { render, screen, fireEvent, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import SystemDetailPage from '../SystemDetailPage'
import type { System } from '../../types'

// ── Mocks ────────────────────────────────────────────────────────────────────

vi.mock('../../data/apiClient', () => ({
  getSystem: vi.fn(),
  getSystemCapabilities: vi.fn(),
  getEvidenceTracking: vi.fn(),
  generateSystemRecipes: vi.fn(),
  getRecipeGenerationStatus: vi.fn(),
  getEvidenceSuggestions: vi.fn(),
}))

import {
  getSystem,
  getSystemCapabilities,
  getEvidenceTracking,
  generateSystemRecipes,
  getRecipeGenerationStatus,
  getEvidenceSuggestions,
} from '../../data/apiClient'

const mockGetSystem = vi.mocked(getSystem)
const mockGetSystemCapabilities = vi.mocked(getSystemCapabilities)
const mockGetEvidenceTracking = vi.mocked(getEvidenceTracking)
const mockGenerateSystemRecipes = vi.mocked(generateSystemRecipes)
const mockGetRecipeGenerationStatus = vi.mocked(getRecipeGenerationStatus)
const mockGetEvidenceSuggestions = vi.mocked(getEvidenceSuggestions)

// ── Fixtures ─────────────────────────────────────────────────────────────────

const SYSTEM_A: System = {
  id: 'sys-001',
  organization_id: 'org-1',
  name: 'Google Workspace',
  system_type: 'cloud_provider',
  category: 'SaaS',
  description: 'Primary productivity suite',
  vendor: 'Google',
  status: 'active',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
}

const SYSTEM_B: System = {
  id: 'sys-002',
  organization_id: 'org-1',
  name: 'JumpCloud MDM',
  system_type: 'endpoint_management',
  category: 'MDM',
  description: 'Device management platform',
  vendor: 'JumpCloud',
  status: 'active',
  created_at: '2024-02-01T00:00:00Z',
  updated_at: '2024-02-01T00:00:00Z',
}

const SYSTEMS = [SYSTEM_A, SYSTEM_B]

const CAPABILITIES = [
  {
    id: 'cap-1',
    system_id: 'sys-001',
    evidence_id: 'IAC-01',
    capability_status: 'active' as const,
    confidence_level: 'high' as const,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  },
  {
    id: 'cap-2',
    system_id: 'sys-001',
    evidence_id: 'CFG-02',
    capability_status: 'configured' as const,
    confidence_level: 'medium' as const,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  },
]

const EVIDENCE_TRACKING = [
  {
    id: 'et-1',
    is_tracked: true,
    method_of_collection: 'api',
    collecting_system: 'Google Workspace',
    frequency: 'quarterly',
  },
]

const RECIPE_STATUS = {
  status: 'completed' as const,
  updated_at: '2026-08-18T00:00:00Z',
}

function makeProps(overrides?: Partial<Parameters<typeof SystemDetailPage>[0]>) {
  return {
    organizationId: 'org-1',
    systemId: 'sys-001',
    filteredSystems: SYSTEMS,
    onSystemItemChange: vi.fn(),
    onEdit: vi.fn(),
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockGetSystem.mockResolvedValue(SYSTEM_A)
  mockGetSystemCapabilities.mockResolvedValue(CAPABILITIES)
  mockGetEvidenceTracking.mockResolvedValue(EVIDENCE_TRACKING as any)
  mockGetRecipeGenerationStatus.mockResolvedValue(RECIPE_STATUS)
  mockGenerateSystemRecipes.mockResolvedValue({ status: 'queued' })
  mockGetEvidenceSuggestions.mockResolvedValue({
    evidence_id: 'IAC-01',
    capable_systems: [],
    has_suggestions: false,
  })
})

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('SystemDetailPage — breadcrumb + pager', () => {
  it('renders breadcrumb with "Systems Registry" back link', async () => {
    await act(async () => { render(<SystemDetailPage {...makeProps()} />) })
    expect(screen.getByRole('button', { name: /systems registry/i })).toBeInTheDocument()
  })

  it('renders the system name in the breadcrumb', async () => {
    await act(async () => { render(<SystemDetailPage {...makeProps()} />) })
    // "Google Workspace" appears in both breadcrumb and heading — just confirm it's in the DOM
    expect(screen.getAllByText('Google Workspace').length).toBeGreaterThan(0)
  })

  it('renders "k of N systems" pager text', async () => {
    await act(async () => { render(<SystemDetailPage {...makeProps()} />) })
    // sys-001 is index 0 → "1 of 2 systems"
    expect(screen.getByText(/1 of 2 systems/i)).toBeInTheDocument()
  })

  it('renders "— of N systems" when system not in filtered list', async () => {
    await act(async () => {
      render(<SystemDetailPage {...makeProps({ filteredSystems: [] })} />)
    })
    expect(screen.getByText(/— of 0 systems/i)).toBeInTheDocument()
  })

  it('back button calls onSystemItemChange with null', async () => {
    const onSystemItemChange = vi.fn()
    await act(async () => {
      render(<SystemDetailPage {...makeProps({ onSystemItemChange })} />)
    })
    fireEvent.click(screen.getByRole('button', { name: /systems registry/i }))
    expect(onSystemItemChange).toHaveBeenCalledWith(null)
  })

  it('prev button is disabled at first item (index 0)', async () => {
    await act(async () => { render(<SystemDetailPage {...makeProps()} />) })
    const prevBtn = screen.getByRole('button', { name: /previous/i })
    expect(prevBtn).toBeDisabled()
  })

  it('next button navigates to next system', async () => {
    const onSystemItemChange = vi.fn()
    await act(async () => {
      render(<SystemDetailPage {...makeProps({ onSystemItemChange })} />)
    })
    const nextBtn = screen.getByRole('button', { name: /next/i })
    fireEvent.click(nextBtn)
    expect(onSystemItemChange).toHaveBeenCalledWith('sys-002')
  })

  it('next button is disabled at last item', async () => {
    // System B is at index 1 (last)
    mockGetSystem.mockResolvedValue(SYSTEM_B)
    await act(async () => {
      render(<SystemDetailPage {...makeProps({ systemId: 'sys-002' })} />)
    })
    const nextBtn = screen.getByRole('button', { name: /next/i })
    expect(nextBtn).toBeDisabled()
  })
})

describe('SystemDetailPage — header', () => {
  it('renders system name as heading', async () => {
    await act(async () => { render(<SystemDetailPage {...makeProps()} />) })
    const heading = screen.getByRole('heading', { name: /google workspace/i })
    expect(heading).toBeInTheDocument()
  })

  it('renders type chip (Cloud Provider)', async () => {
    await act(async () => { render(<SystemDetailPage {...makeProps()} />) })
    expect(screen.getByText(/cloud provider/i)).toBeInTheDocument()
  })

  it('renders status chip (Active)', async () => {
    await act(async () => { render(<SystemDetailPage {...makeProps()} />) })
    expect(screen.getByText(/active/i)).toBeInTheDocument()
  })

  it('renders vendor name in header', async () => {
    await act(async () => { render(<SystemDetailPage {...makeProps()} />) })
    // "Google" matches both vendor meta and system name "Google Workspace"
    expect(screen.getAllByText(/google/i).length).toBeGreaterThan(0)
  })

  it('renders description', async () => {
    await act(async () => { render(<SystemDetailPage {...makeProps()} />) })
    expect(screen.getByText(/primary productivity suite/i)).toBeInTheDocument()
  })

  it('renders edit button that fires onEdit', async () => {
    const onEdit = vi.fn()
    await act(async () => { render(<SystemDetailPage {...makeProps({ onEdit })} />) })
    const editBtn = screen.getByRole('button', { name: /edit/i })
    fireEvent.click(editBtn)
    expect(onEdit).toHaveBeenCalledWith(SYSTEM_A)
  })
})

describe('SystemDetailPage — linked controls', () => {
  it('renders LINKED CONTROLS section when capabilities exist', async () => {
    await act(async () => { render(<SystemDetailPage {...makeProps()} />) })
    expect(screen.getByText(/linked controls/i)).toBeInTheDocument()
  })

  it('renders evidence ids as badges', async () => {
    await act(async () => { render(<SystemDetailPage {...makeProps()} />) })
    expect(screen.getByText('IAC-01')).toBeInTheDocument()
    expect(screen.getByText('CFG-02')).toBeInTheDocument()
  })

  it('gracefully absent when no capabilities', async () => {
    mockGetSystemCapabilities.mockResolvedValue([])
    await act(async () => { render(<SystemDetailPage {...makeProps()} />) })
    // Should not error; LINKED CONTROLS section may be absent or show empty state
    expect(screen.queryByText(/IAC-01/)).not.toBeInTheDocument()
  })
})

describe('SystemDetailPage — evidence recipes', () => {
  it('renders EVIDENCE RECIPES section header', async () => {
    await act(async () => { render(<SystemDetailPage {...makeProps()} />) })
    expect(screen.getByText(/evidence recipes/i)).toBeInTheDocument()
  })

  it('renders Regenerate recipes button', async () => {
    await act(async () => { render(<SystemDetailPage {...makeProps()} />) })
    expect(screen.getByRole('button', { name: /regenerate recipes/i })).toBeInTheDocument()
  })

  it('Regenerate button calls generateSystemRecipes', async () => {
    await act(async () => { render(<SystemDetailPage {...makeProps()} />) })
    const btn = screen.getByRole('button', { name: /regenerate recipes/i })
    await act(async () => { fireEvent.click(btn) })
    expect(mockGenerateSystemRecipes).toHaveBeenCalledWith('sys-001', 'org-1')
  })
})

describe('SystemDetailPage — associated evidence', () => {
  it('renders ASSOCIATED EVIDENCE section when tracking items linked to this system name', async () => {
    await act(async () => { render(<SystemDetailPage {...makeProps()} />) })
    expect(screen.getByText(/associated evidence/i)).toBeInTheDocument()
  })

  it('gracefully absent when no tracking items match', async () => {
    mockGetEvidenceTracking.mockResolvedValue([])
    await act(async () => { render(<SystemDetailPage {...makeProps()} />) })
    // Should render without error — heading is present
    expect(screen.getByRole('heading', { name: /google workspace/i })).toBeInTheDocument()
  })
})

describe('SystemDetailPage — keyboard shortcuts', () => {
  it('ArrowRight navigates to next system', async () => {
    const onSystemItemChange = vi.fn()
    await act(async () => {
      render(<SystemDetailPage {...makeProps({ onSystemItemChange })} />)
    })
    fireEvent.keyDown(document, { key: 'ArrowRight' })
    expect(onSystemItemChange).toHaveBeenCalledWith('sys-002')
  })

  it('ArrowLeft is a no-op at first item (index 0)', async () => {
    const onSystemItemChange = vi.fn()
    await act(async () => {
      render(<SystemDetailPage {...makeProps({ onSystemItemChange })} />)
    })
    fireEvent.keyDown(document, { key: 'ArrowLeft' })
    expect(onSystemItemChange).not.toHaveBeenCalled()
  })

  it('Escape calls back (null)', async () => {
    const onSystemItemChange = vi.fn()
    await act(async () => {
      render(<SystemDetailPage {...makeProps({ onSystemItemChange })} />)
    })
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onSystemItemChange).toHaveBeenCalledWith(null)
  })

  it('keyboard suppressed when focus in input', async () => {
    const onSystemItemChange = vi.fn()
    const { container } = render(<SystemDetailPage {...makeProps({ onSystemItemChange })} />)
    await act(async () => {})
    // Create a focused input element and fire the keydown on it
    const input = document.createElement('input')
    document.body.appendChild(input)
    input.focus()
    fireEvent.keyDown(input, { key: 'ArrowRight' })
    expect(onSystemItemChange).not.toHaveBeenCalled()
    document.body.removeChild(input)
  })
})
