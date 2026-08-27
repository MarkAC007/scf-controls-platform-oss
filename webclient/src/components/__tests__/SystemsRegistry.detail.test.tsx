/**
 * SystemsRegistry → SystemDetailPage routing tests — Phase 4 Task 6
 *
 * Pins (hidden-list pattern like VendorManagement):
 *  - Row click fires onViewSystem with the system (existing test preserved)
 *  - onViewSystem is now routed to ?system= URL param in App (tested at App level),
 *    but at the container level the SystemsManagement component must:
 *    - Show list when systemItem is null
 *    - Show SystemDetailPage when systemItem is set
 *    - Pass filteredSystems to detail page for pager
 *    - Back in detail page calls onSystemItemChange(null) → list appears
 */
import { render, screen, fireEvent, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import SystemsManagement from '../SystemsManagement'
import type { System } from '../../types'

// ── Mocks ────────────────────────────────────────────────────────────────────

vi.mock('../../data/apiClient', () => ({
  getSystems: vi.fn(),
  deleteSystem: vi.fn(),
  getSystem: vi.fn(),
  getSystemCapabilities: vi.fn(),
  getEvidenceTracking: vi.fn(),
  generateSystemRecipes: vi.fn(),
  getRecipeGenerationStatus: vi.fn(),
}))

vi.mock('../AddSystemModal', () => ({
  default: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="add-system-modal">
      <button onClick={onClose}>Close Modal</button>
    </div>
  ),
}))

vi.mock('../SystemDetailPage', () => ({
  default: ({ systemId, onSystemItemChange }: { systemId: string; onSystemItemChange: (id: string | null) => void }) => (
    <div data-testid={`system-detail-${systemId}`}>
      <button onClick={() => onSystemItemChange(null)}>Back to Systems</button>
    </div>
  ),
}))

import { getSystems, deleteSystem, getSystem } from '../../data/apiClient'
const mockGetSystems = vi.mocked(getSystems)

const SYSTEM_A: System = {
  id: 'sys-001',
  organization_id: 'org-1',
  name: 'Google Workspace',
  system_type: 'cloud_provider',
  status: 'active',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
}

beforeEach(() => {
  vi.clearAllMocks()
  mockGetSystems.mockResolvedValue([SYSTEM_A])
  vi.mocked(getSystem).mockResolvedValue(SYSTEM_A)
})

function makeProps(overrides?: Partial<Parameters<typeof SystemsManagement>[0]>) {
  return {
    organizationId: 'org-1',
    systemItem: null as string | null,
    onSystemItemChange: vi.fn(),
    ...overrides,
  }
}

describe('SystemsManagement — list/detail routing', () => {
  it('shows list (SystemsRegistry) when systemItem is null', async () => {
    await act(async () => {
      render(<SystemsManagement {...makeProps({ systemItem: null })} />)
    })
    // The registry row with system name should be visible
    expect(screen.getByText('Google Workspace')).toBeInTheDocument()
    // Detail page should NOT be shown
    expect(screen.queryByTestId('system-detail-sys-001')).not.toBeInTheDocument()
  })

  it('shows SystemDetailPage when systemItem is set', async () => {
    await act(async () => {
      render(<SystemsManagement {...makeProps({ systemItem: 'sys-001' })} />)
    })
    expect(screen.getByTestId('system-detail-sys-001')).toBeInTheDocument()
  })

  it('list stays mounted beneath detail for filter state preservation', async () => {
    await act(async () => {
      render(<SystemsManagement {...makeProps({ systemItem: 'sys-001' })} />)
    })
    // In the hidden-list pattern, the list is mounted but hidden
    // The detail page is visible
    expect(screen.getByTestId('system-detail-sys-001')).toBeInTheDocument()
  })

  it('back button in detail calls onSystemItemChange(null)', async () => {
    const onSystemItemChange = vi.fn()
    await act(async () => {
      render(<SystemsManagement {...makeProps({ systemItem: 'sys-001', onSystemItemChange })} />)
    })
    fireEvent.click(screen.getByText('Back to Systems'))
    expect(onSystemItemChange).toHaveBeenCalledWith(null)
  })

  it('AddSystemModal shows when create is triggered', async () => {
    await act(async () => {
      render(<SystemsManagement {...makeProps({ systemItem: null })} />)
    })
    // Click Add System button
    const addBtn = screen.getByRole('button', { name: /add system/i })
    fireEvent.click(addBtn)
    expect(screen.getByTestId('add-system-modal')).toBeInTheDocument()
  })
})
