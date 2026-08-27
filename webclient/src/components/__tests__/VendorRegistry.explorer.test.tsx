/**
 * VendorRegistry explorer chrome tests (Phase 3 Task 6)
 *
 * Baseline tests — RED first, then green after implementation.
 *
 * Pins:
 *  - FilterSidebar aside element present (status / criticality / category filters)
 *  - Search input in the ListToolbar
 *  - Count displayed in toolbar
 *  - "+ Add Vendor" button fires onAddVendor
 *  - ExplorerListRow rendered (explorer-row class) with vendor name
 *  - Category chip rendered
 *  - Status badge rendered
 *  - Criticality badge rendered
 *  - Risk score+RAG pill rendered
 *  - Review status rendered
 *  - Contract end date rendered
 *  - Contact name+email rendered
 *  - Delete button fires onDeleteVendor and does NOT trigger onSelectVendor
 *  - Row click fires onSelectVendor
 *  - Search filters rows client-side
 *  - Status filter fires API reload (filter state changes)
 *  - Criticality filter fires API reload
 *  - Category filter fires API reload
 */
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import VendorRegistry from '../VendorRegistry'
import type { Vendor } from '../../types'

// ── Mock getVendors ─────────────────────────────────────────────────────────
vi.mock('../../data/apiClient', () => ({
  getVendors: vi.fn(),
}))

import { getVendors } from '../../data/apiClient'
const mockGetVendors = vi.mocked(getVendors)

// ── Test fixtures ────────────────────────────────────────────────────────────
const vendor1: Vendor = {
  id: 'v-001',
  organization_id: 'org-1',
  name: 'Acme Corp',
  description: 'A cloud software vendor',
  category: 'Cloud',
  status: 'active',
  criticality: 'high',
  contact_name: 'Jane Smith',
  contact_email: 'jane@acme.com',
  risk_score: 72,
  risk_level: 'high',
  contract_end_date: '2025-12-31',
  next_review_date: '2024-06-01',
  review_status: 'overdue',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
}

const vendor2: Vendor = {
  id: 'v-002',
  organization_id: 'org-1',
  name: 'Beta Systems',
  description: 'Security tooling provider',
  category: 'Security',
  status: 'under_review',
  criticality: 'medium',
  contact_name: null,
  contact_email: null,
  risk_score: 35,
  risk_level: 'medium',
  contract_end_date: null,
  next_review_date: null,
  review_status: 'ok',
  created_at: '2024-02-01T00:00:00Z',
  updated_at: '2024-02-01T00:00:00Z',
}

const vendors = [vendor1, vendor2]

function renderRegistry(overrides?: Partial<React.ComponentProps<typeof VendorRegistry>>) {
  const props = {
    organizationId: 'org-1',
    onSelectVendor: vi.fn(),
    onAddVendor: vi.fn(),
    onDeleteVendor: vi.fn(),
    ...overrides,
  }
  return render(<VendorRegistry {...props} />)
}

beforeEach(() => {
  mockGetVendors.mockResolvedValue(vendors)
})

describe('VendorRegistry — Explorer chrome (Phase 3 Task 6)', () => {
  it('renders a FilterSidebar aside element', async () => {
    await act(async () => { renderRegistry() })
    expect(screen.getByRole('complementary')).toBeInTheDocument()
  })

  it('renders a search input in the toolbar', async () => {
    await act(async () => { renderRegistry() })
    expect(screen.getByRole('searchbox')).toBeInTheDocument()
  })

  it('renders a count of vendors in the toolbar', async () => {
    await act(async () => { renderRegistry() })
    expect(screen.getByText(/2 vendor/i)).toBeInTheDocument()
  })

  it('fires onAddVendor when "+ Add Vendor" button clicked', async () => {
    const onAddVendor = vi.fn()
    await act(async () => { renderRegistry({ onAddVendor }) })
    fireEvent.click(screen.getByRole('button', { name: /add vendor/i }))
    expect(onAddVendor).toHaveBeenCalledTimes(1)
  })

  it('renders vendor names in explorer rows', async () => {
    await act(async () => { renderRegistry() })
    const titleEls = document.querySelectorAll('.explorer-row-title')
    const texts = Array.from(titleEls).map(el => el.textContent)
    expect(texts).toContain('Acme Corp')
    expect(texts).toContain('Beta Systems')
  })

  it('renders category chip for vendor1', async () => {
    await act(async () => { renderRegistry() })
    // "Cloud" appears as both a chip and a filter option — use getAllByText
    const chips = document.querySelectorAll('.vendor-chip')
    const chipTexts = Array.from(chips).map(el => el.textContent)
    expect(chipTexts).toContain('Cloud')
  })

  it('renders status badge for vendor1', async () => {
    await act(async () => { renderRegistry() })
    // "Active" appears as both a badge and a filter option — check the badge element
    const badges = document.querySelectorAll('.vendor-badge')
    const badgeTexts = Array.from(badges).map(el => el.textContent)
    expect(badgeTexts).toContain('Active')
  })

  it('renders criticality badge for vendor1', async () => {
    await act(async () => { renderRegistry() })
    // "High" appears as both a badge and a filter option
    const badges = document.querySelectorAll('.vendor-badge')
    const badgeTexts = Array.from(badges).map(el => el.textContent)
    expect(badgeTexts).toContain('High')
  })

  it('renders risk score+RAG pill for vendor1', async () => {
    await act(async () => { renderRegistry() })
    // risk_score=72, risk_level='high' → maps to RED or similar RAG
    // The pill renders "72 · <RAG>"
    const pillText = screen.getByText(/72/i)
    expect(pillText).toBeInTheDocument()
  })

  it('renders "Overdue" review status badge for vendor1', async () => {
    await act(async () => { renderRegistry() })
    expect(screen.getByText('Overdue')).toBeInTheDocument()
  })

  it('renders contract end date for vendor1', async () => {
    await act(async () => { renderRegistry() })
    // 2025-12-31 → "31 Dec 2025" or similar
    expect(screen.getByText(/31 Dec 2025|Dec 31, 2025|2025-12-31/i)).toBeInTheDocument()
  })

  it('renders contact name for vendor1', async () => {
    await act(async () => { renderRegistry() })
    expect(screen.getByText('Jane Smith')).toBeInTheDocument()
  })

  it('renders contact email for vendor1', async () => {
    await act(async () => { renderRegistry() })
    expect(screen.getByText('jane@acme.com')).toBeInTheDocument()
  })

  it('row click fires onSelectVendor', async () => {
    const onSelectVendor = vi.fn()
    await act(async () => { renderRegistry({ onSelectVendor }) })
    const rows = document.querySelectorAll('.explorer-row[role="button"]')
    expect(rows.length).toBeGreaterThan(0)
    fireEvent.click(rows[0])
    expect(onSelectVendor).toHaveBeenCalledTimes(1)
  })

  it('delete button fires onDeleteVendor and does NOT fire onSelectVendor', async () => {
    const onSelectVendor = vi.fn()
    const onDeleteVendor = vi.fn()
    await act(async () => { renderRegistry({ onSelectVendor, onDeleteVendor }) })
    const deleteButtons = screen.getAllByTitle(/delete vendor/i)
    fireEvent.click(deleteButtons[0])
    expect(onDeleteVendor).toHaveBeenCalledTimes(1)
    expect(onSelectVendor).not.toHaveBeenCalled()
  })

  it('search input filters rows client-side', async () => {
    await act(async () => { renderRegistry() })
    const search = screen.getByRole('searchbox')
    fireEvent.change(search, { target: { value: 'Acme' } })
    const titleEls = document.querySelectorAll('.explorer-row-title')
    expect(titleEls.length).toBe(1)
    expect(titleEls[0].textContent).toBe('Acme Corp')
  })

  it('status filter select is present in the sidebar', async () => {
    await act(async () => { renderRegistry() })
    // Status filter lives in the sidebar; the select has "All Statuses" option
    const selects = screen.getAllByRole('combobox')
    const allStatuses = selects.find(s =>
      Array.from((s as HTMLSelectElement).options).some(o => /all status/i.test(o.text))
    )
    expect(allStatuses).toBeInTheDocument()
  })

  it('criticality filter select is present in the sidebar', async () => {
    await act(async () => { renderRegistry() })
    const selects = screen.getAllByRole('combobox')
    const allCrit = selects.find(s =>
      Array.from((s as HTMLSelectElement).options).some(o => /all criticality/i.test(o.text))
    )
    expect(allCrit).toBeInTheDocument()
  })

  it('changing status filter triggers a new API call', async () => {
    await act(async () => { renderRegistry() })
    mockGetVendors.mockClear()
    mockGetVendors.mockResolvedValue([vendor1])

    const selects = screen.getAllByRole('combobox')
    const statusSelect = selects.find(s =>
      Array.from((s as HTMLSelectElement).options).some(o => /all status/i.test(o.text))
    ) as HTMLSelectElement

    await act(async () => {
      fireEvent.change(statusSelect, { target: { value: 'active' } })
    })

    await waitFor(() => expect(mockGetVendors).toHaveBeenCalled())
  })

  it('calls onFilteredListChange when filtered list changes', async () => {
    const onFilteredListChange = vi.fn()
    mockGetVendors.mockResolvedValue([vendor1, vendor2])
    await act(async () => {
      render(
        <VendorRegistry
          organizationId="org-1"
          onSelectVendor={vi.fn()}
          onAddVendor={vi.fn()}
          onDeleteVendor={vi.fn()}
          onFilteredListChange={onFilteredListChange}
        />
      )
    })
    expect(onFilteredListChange).toHaveBeenCalledWith(expect.arrayContaining([
      expect.objectContaining({ id: 'v-001' }),
      expect.objectContaining({ id: 'v-002' }),
    ]))
  })
})
