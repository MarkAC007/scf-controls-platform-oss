/**
 * VendorManagement — the open detail page reloads after a successful edit.
 *
 * Without this the detail page kept the values it fetched on open, so the
 * data location & responsibility fields (and every other field) looked
 * unsaved until the user navigated away and back.
 */
import { useEffect } from 'react'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import VendorManagement from '../VendorManagement'

const detailMounts = vi.fn()

vi.mock('../../data/apiClient', () => ({
  deleteVendor: vi.fn(),
}))

vi.mock('../VendorRegistry', () => ({
  default: () => <div data-testid="vendor-registry" />,
}))

vi.mock('../VendorDetailPage', () => ({
  default: ({ vendorId, onEdit }: { vendorId: string; onEdit: (v: { id: string }) => void }) => {
    useEffect(() => { detailMounts(vendorId) }, [vendorId])
    return <button onClick={() => onEdit({ id: vendorId })}>Edit</button>
  },
}))

vi.mock('../VendorModal', () => ({
  default: ({ onSuccess, onClose }: { onSuccess: () => void; onClose: () => void }) => (
    <div>
      <button onClick={onSuccess}>Save Changes</button>
      <button onClick={onClose}>Cancel</button>
    </div>
  ),
}))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('VendorManagement — detail refresh after edit', () => {
  it('remounts the detail page (so it refetches) after a successful save', async () => {
    await act(async () => {
      render(<VendorManagement organizationId="org-1" vendorItem="v-001" onVendorItemChange={vi.fn()} />)
    })
    expect(detailMounts).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }))
    })

    expect(detailMounts).toHaveBeenCalledTimes(2)
    expect(detailMounts).toHaveBeenLastCalledWith('v-001')
  })

  it('does not reload the detail page when the edit is cancelled', async () => {
    await act(async () => {
      render(<VendorManagement organizationId="org-1" vendorItem="v-001" onVendorItemChange={vi.fn()} />)
    })
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    })

    expect(detailMounts).toHaveBeenCalledTimes(1)
  })
})
