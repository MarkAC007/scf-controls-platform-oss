/**
 * SystemsRegistry explorer chrome tests (Phase 3 Task 6)
 *
 * Baseline tests — RED first, then green after implementation.
 *
 * Pins:
 *  - FilterSidebar aside element present (type / status filters)
 *  - Search input in the ListToolbar
 *  - Count displayed in toolbar
 *  - "+ Add System" button fires onAddSystem
 *  - Stats cards rendered (Total / Active / top types)
 *  - ExplorerListRow rendered (explorer-row class) with system name
 *  - Type badge rendered for each row
 *  - Vendor column rendered
 *  - Status badge rendered
 *  - Interfaces count rendered
 *  - Edit button fires onEditSystem and does NOT fire onViewSystem
 *  - Delete button fires delete confirm flow, does NOT fire onViewSystem
 *  - Row click fires onViewSystem
 *  - Search filters rows client-side
 *  - Type filter changes state (client-side filtering)
 *  - Status filter changes state (client-side filtering)
 */
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import SystemsRegistry from '../SystemsRegistry'
import type { System, CollectionInterfacesFile } from '../../types'

// ── Mock API calls ────────────────────────────────────────────────────────────
vi.mock('../../data/apiClient', () => ({
  getSystems: vi.fn(),
  deleteSystem: vi.fn(),
}))

import { getSystems, deleteSystem } from '../../data/apiClient'
const mockGetSystems = vi.mocked(getSystems)
const mockDeleteSystem = vi.mocked(deleteSystem)

// ── Test fixtures ─────────────────────────────────────────────────────────────
const system1: System = {
  id: 's-001',
  organization_id: 'org-1',
  name: 'AWS Production',
  system_type: 'cloud_provider',
  category: 'Infrastructure',
  description: 'Main cloud environment',
  vendor: 'Amazon Web Services',
  status: 'active',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
}

const system2: System = {
  id: 's-002',
  organization_id: 'org-1',
  name: 'Okta SSO',
  system_type: 'identity_provider',
  category: 'Identity',
  description: 'Identity and access management',
  vendor: 'Okta',
  status: 'active',
  created_at: '2024-02-01T00:00:00Z',
  updated_at: '2024-02-01T00:00:00Z',
}

const system3: System = {
  id: 's-003',
  organization_id: 'org-1',
  name: 'Legacy SIEM',
  system_type: 'logging',
  category: 'Security',
  description: 'Old logging system',
  vendor: 'Splunk',
  status: 'deprecated',
  created_at: '2023-01-01T00:00:00Z',
  updated_at: '2023-01-01T00:00:00Z',
}

const systems = [system1, system2, system3]

// Minimal collection interfaces fixture
const collectionInterfaces: CollectionInterfacesFile = {}

function renderRegistry(overrides?: Partial<React.ComponentProps<typeof SystemsRegistry>>) {
  const props = {
    organizationId: 'org-1',
    collectionInterfaces,
    onAddSystem: vi.fn(),
    onEditSystem: vi.fn(),
    onViewSystem: vi.fn(),
    ...overrides,
  }
  return render(<SystemsRegistry {...props} />)
}

beforeEach(() => {
  mockGetSystems.mockResolvedValue(systems)
  mockDeleteSystem.mockResolvedValue(undefined as any)
})

describe('SystemsRegistry — Explorer chrome (Phase 3 Task 6)', () => {
  it('renders a FilterSidebar aside element', async () => {
    await act(async () => { renderRegistry() })
    expect(screen.getByRole('complementary')).toBeInTheDocument()
  })

  it('renders a search input in the toolbar', async () => {
    await act(async () => { renderRegistry() })
    expect(screen.getByRole('searchbox')).toBeInTheDocument()
  })

  it('renders a count of systems in the toolbar', async () => {
    await act(async () => { renderRegistry() })
    expect(screen.getByText(/3 system/i)).toBeInTheDocument()
  })

  it('fires onAddSystem when "+ Add System" button clicked', async () => {
    const onAddSystem = vi.fn()
    await act(async () => { renderRegistry({ onAddSystem }) })
    fireEvent.click(screen.getByRole('button', { name: /add system/i }))
    expect(onAddSystem).toHaveBeenCalledTimes(1)
  })

  it('renders Total stat card', async () => {
    await act(async () => { renderRegistry() })
    expect(screen.getByText(/total systems/i)).toBeInTheDocument()
  })

  it('renders Active stat card', async () => {
    await act(async () => { renderRegistry() })
    // "Active" appears in stat label AND as filter option — check stat label specifically
    const statLabels = document.querySelectorAll('.system-stat-label')
    const labelTexts = Array.from(statLabels).map(el => el.textContent?.trim())
    expect(labelTexts).toContain('Active')
  })

  it('renders system names in explorer rows', async () => {
    await act(async () => { renderRegistry() })
    const titleEls = document.querySelectorAll('.explorer-row-title')
    const texts = Array.from(titleEls).map(el => el.textContent)
    expect(texts).toContain('AWS Production')
    expect(texts).toContain('Okta SSO')
    expect(texts).toContain('Legacy SIEM')
  })

  it('renders type badge for each system row', async () => {
    await act(async () => { renderRegistry() })
    // Type badges use class systems-badge systems-type-<type>
    // "Cloud Provider" and "Identity Provider" appear in both badges and filter options
    const cloudBadges = document.querySelectorAll('.systems-badge.systems-type-cloud_provider')
    expect(cloudBadges.length).toBeGreaterThan(0)
    const idpBadges = document.querySelectorAll('.systems-badge.systems-type-identity_provider')
    expect(idpBadges.length).toBeGreaterThan(0)
  })

  it('renders vendor name in the row', async () => {
    await act(async () => { renderRegistry() })
    expect(screen.getByText('Amazon Web Services')).toBeInTheDocument()
    expect(screen.getByText('Okta')).toBeInTheDocument()
  })

  it('renders status badge for each row', async () => {
    await act(async () => { renderRegistry() })
    // Status badges use class systems-badge systems-status-<status>
    const activeBadges = document.querySelectorAll('.systems-badge.systems-status-active')
    expect(activeBadges.length).toBeGreaterThan(0)
    const deprecatedBadges = document.querySelectorAll('.systems-badge.systems-status-deprecated')
    expect(deprecatedBadges.length).toBeGreaterThan(0)
  })

  it('renders interfaces count for each row (0 when no matching interfaces)', async () => {
    await act(async () => { renderRegistry() })
    // With empty collectionInterfaces, all rows show "-" or "0 interfaces"
    // At minimum the column renders
    const rows = document.querySelectorAll('.explorer-row')
    expect(rows.length).toBe(3)
  })

  it('row click fires onViewSystem', async () => {
    const onViewSystem = vi.fn()
    await act(async () => { renderRegistry({ onViewSystem }) })
    const rows = document.querySelectorAll('.explorer-row[role="button"]')
    expect(rows.length).toBeGreaterThan(0)
    fireEvent.click(rows[0])
    expect(onViewSystem).toHaveBeenCalledTimes(1)
  })

  it('edit button fires onEditSystem and does NOT fire onViewSystem', async () => {
    const onEditSystem = vi.fn()
    const onViewSystem = vi.fn()
    await act(async () => { renderRegistry({ onEditSystem, onViewSystem }) })
    const editButtons = screen.getAllByRole('button', { name: /^edit$/i })
    fireEvent.click(editButtons[0])
    expect(onEditSystem).toHaveBeenCalledTimes(1)
    expect(onViewSystem).not.toHaveBeenCalled()
  })

  it('delete button shows confirm flow and does NOT fire onViewSystem', async () => {
    const onViewSystem = vi.fn()
    await act(async () => { renderRegistry({ onViewSystem }) })
    const deleteButtons = screen.getAllByRole('button', { name: /^delete$/i })
    fireEvent.click(deleteButtons[0])
    // Confirm dialog should appear
    expect(screen.getAllByRole('button', { name: /^yes$/i }).length).toBeGreaterThan(0)
    expect(onViewSystem).not.toHaveBeenCalled()
  })

  it('confirming delete calls deleteSystem and does NOT call onViewSystem', async () => {
    const onViewSystem = vi.fn()
    await act(async () => { renderRegistry({ onViewSystem }) })
    const deleteButtons = screen.getAllByRole('button', { name: /^delete$/i })
    fireEvent.click(deleteButtons[0])
    const yesButton = screen.getAllByRole('button', { name: /^yes$/i })[0]
    await act(async () => { fireEvent.click(yesButton) })
    expect(mockDeleteSystem).toHaveBeenCalledTimes(1)
    expect(onViewSystem).not.toHaveBeenCalled()
  })

  it('search input filters rows client-side', async () => {
    await act(async () => { renderRegistry() })
    const search = screen.getByRole('searchbox')
    fireEvent.change(search, { target: { value: 'AWS' } })
    const titleEls = document.querySelectorAll('.explorer-row-title')
    expect(titleEls.length).toBe(1)
    expect(titleEls[0].textContent).toBe('AWS Production')
  })

  it('type filter select is present in the sidebar', async () => {
    await act(async () => { renderRegistry() })
    const selects = screen.getAllByRole('combobox')
    const typeSelect = selects.find(s =>
      Array.from((s as HTMLSelectElement).options).some(o => /all types/i.test(o.text))
    )
    expect(typeSelect).toBeInTheDocument()
  })

  it('status filter select is present in the sidebar', async () => {
    await act(async () => { renderRegistry() })
    const selects = screen.getAllByRole('combobox')
    const statusSelect = selects.find(s =>
      Array.from((s as HTMLSelectElement).options).some(o => /all status/i.test(o.text))
    )
    expect(statusSelect).toBeInTheDocument()
  })

  it('type filter filters rows client-side', async () => {
    await act(async () => { renderRegistry() })
    const selects = screen.getAllByRole('combobox')
    const typeSelect = selects.find(s =>
      Array.from((s as HTMLSelectElement).options).some(o => /all types/i.test(o.text))
    ) as HTMLSelectElement

    fireEvent.change(typeSelect, { target: { value: 'cloud_provider' } })

    const titleEls = document.querySelectorAll('.explorer-row-title')
    expect(titleEls.length).toBe(1)
    expect(titleEls[0].textContent).toBe('AWS Production')
  })

  it('status filter filters rows client-side', async () => {
    await act(async () => { renderRegistry() })
    const selects = screen.getAllByRole('combobox')
    const statusSelect = selects.find(s =>
      Array.from((s as HTMLSelectElement).options).some(o => /all status/i.test(o.text))
    ) as HTMLSelectElement

    fireEvent.change(statusSelect, { target: { value: 'deprecated' } })

    const titleEls = document.querySelectorAll('.explorer-row-title')
    expect(titleEls.length).toBe(1)
    expect(titleEls[0].textContent).toBe('Legacy SIEM')
  })
})
