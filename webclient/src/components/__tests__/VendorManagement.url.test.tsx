/**
 * VendorManagement URL behaviour tests — Phase 4 Task 5
 *
 * Pins:
 *  - LIST-FIRST: vendorItem=null renders the registry list, not the detail page
 *  - DETAIL: vendorItem="v-001" renders the detail page, not the registry list
 *  - onVendorItemChange called with vendor id when a registry row is clicked
 *  - onVendorItemChange called with null when back/breadcrumb clicked from detail
 *  - Registry list stays mounted (hidden) when detail is open
 */
import { render, screen, fireEvent, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import VendorManagement from '../VendorManagement'
import type { Vendor } from '../../types'

// ── Mocks ────────────────────────────────────────────────────────────────────

vi.mock('../../data/apiClient', () => ({
  getVendors: vi.fn(),
  getVendor: vi.fn(),
  getVendorAssessments: vi.fn(),
  getVendorCertifications: vi.fn(),
  getSystemsFiltered: vi.fn(),
  getVendorAssessmentStatus: vi.fn(),
  deleteVendor: vi.fn(),
}))

vi.mock('../VendorModal', () => ({
  default: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="vendor-modal">
      <button onClick={onClose}>Close modal</button>
    </div>
  ),
}))

vi.mock('../VendorDetailPage', () => ({
  default: ({
    vendorId,
    onVendorItemChange,
  }: {
    vendorId: string
    onVendorItemChange: (id: string | null) => void
  }) => (
    <div data-testid="vendor-detail-page" data-vendor-id={vendorId}>
      <button onClick={() => onVendorItemChange(null)}>Back to list</button>
    </div>
  ),
}))

import { getVendors, getVendor, getVendorAssessments, getVendorCertifications, getSystemsFiltered } from '../../data/apiClient'

const mockGetVendors = vi.mocked(getVendors)
const mockGetVendor = vi.mocked(getVendor)
const mockGetVendorAssessments = vi.mocked(getVendorAssessments)
const mockGetVendorCertifications = vi.mocked(getVendorCertifications)
const mockGetSystemsFiltered = vi.mocked(getSystemsFiltered)

// ── Fixtures ─────────────────────────────────────────────────────────────────

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
} as unknown as Vendor

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
} as unknown as Vendor

beforeEach(() => {
  vi.clearAllMocks()
  mockGetVendors.mockResolvedValue([vendor1, vendor2])
  mockGetVendor.mockResolvedValue(vendor1)
  mockGetVendorAssessments.mockResolvedValue([])
  mockGetVendorCertifications.mockResolvedValue([])
  mockGetSystemsFiltered.mockResolvedValue([])
})

// ── URL behaviour tests ───────────────────────────────────────────────────────

describe('VendorManagement — URL behaviour', () => {
  it('LIST-FIRST: vendorItem=null renders registry list, not detail page', async () => {
    await act(async () => {
      render(
        <VendorManagement
          organizationId="org-1"
          vendorItem={null}
          onVendorItemChange={vi.fn()}
        />
      )
    })
    // Registry is visible; detail page is not rendered
    expect(screen.getByRole('searchbox')).toBeInTheDocument()
    expect(screen.queryByTestId('vendor-detail-page')).not.toBeInTheDocument()
  })

  it('DETAIL: vendorItem="v-001" renders detail page, not the list in visible state', async () => {
    await act(async () => {
      render(
        <VendorManagement
          organizationId="org-1"
          vendorItem="v-001"
          onVendorItemChange={vi.fn()}
        />
      )
    })
    expect(screen.getByTestId('vendor-detail-page')).toBeInTheDocument()
    expect(screen.getByTestId('vendor-detail-page')).toHaveAttribute('data-vendor-id', 'v-001')
  })

  it('registry list stays mounted (hidden) when detail is open', async () => {
    await act(async () => {
      render(
        <VendorManagement
          organizationId="org-1"
          vendorItem="v-001"
          onVendorItemChange={vi.fn()}
        />
      )
    })
    // The list is mounted but hidden — searchbox is in the DOM but not visible
    const searchbox = screen.getByRole('searchbox', { hidden: true })
    expect(searchbox).toBeInTheDocument()
    // The wrapper div has display:none when vendorItem is set
    const wrapper = searchbox.closest('div')
    // Walk up until we find the wrapper that has display none
    let el: HTMLElement | null = wrapper
    let foundHidden = false
    while (el && !foundHidden) {
      const style = el.getAttribute('style') ?? ''
      if (style.includes('display: none')) {
        foundHidden = true
      }
      el = el.parentElement
    }
    expect(foundHidden).toBe(true)
  })

  it('onVendorItemChange called with vendor id when registry row clicked', async () => {
    const onVendorItemChange = vi.fn()
    await act(async () => {
      render(
        <VendorManagement
          organizationId="org-1"
          vendorItem={null}
          onVendorItemChange={onVendorItemChange}
        />
      )
    })
    // Click first explorer row
    const rows = document.querySelectorAll('.explorer-row[role="button"]')
    expect(rows.length).toBeGreaterThan(0)
    fireEvent.click(rows[0])
    expect(onVendorItemChange).toHaveBeenCalledWith(expect.any(String))
    expect(onVendorItemChange).toHaveBeenCalledWith(expect.stringMatching(/^v-/))
  })

  it('onVendorItemChange called with null when Back to list clicked from detail', async () => {
    const onVendorItemChange = vi.fn()
    await act(async () => {
      render(
        <VendorManagement
          organizationId="org-1"
          vendorItem="v-001"
          onVendorItemChange={onVendorItemChange}
        />
      )
    })
    fireEvent.click(screen.getByRole('button', { name: /back to list/i }))
    expect(onVendorItemChange).toHaveBeenCalledWith(null)
  })

  it('no vendorItem prop defaults to list view', async () => {
    await act(async () => {
      render(
        <VendorManagement
          organizationId="org-1"
        />
      )
    })
    expect(screen.getByRole('searchbox')).toBeInTheDocument()
    expect(screen.queryByTestId('vendor-detail-page')).not.toBeInTheDocument()
  })
})
