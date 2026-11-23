// SPDX-License-Identifier: AGPL-3.0-only
/**
 * VendorModal — public subprocessor listing (issue #1321)
 *
 * Pins:
 *  - The switch "Listed publicly as a subprocessor" is off for a new vendor and
 *    its help text says turning it on publishes the vendor's name on the trust portal
 *  - The two public fields sit beside it and are sent trimmed, blanks as null
 *  - Create sends is_subprocessor false unless the switch is turned on
 *  - Edit pre-fills the stored switch and fields; turning it off sends false
 */
import { render, screen, fireEvent, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import VendorModal from '../VendorModal'
import type { Vendor } from '../../types'

vi.mock('../../data/apiClient', () => ({
  createVendor: vi.fn(),
  updateVendor: vi.fn(),
}))

import { createVendor, updateVendor } from '../../data/apiClient'

const mockCreateVendor = vi.mocked(createVendor)
const mockUpdateVendor = vi.mocked(updateVendor)

const LISTED: Vendor = {
  id: 'v-101',
  organization_id: 'org-1',
  name: 'Acme Cloud',
  status: 'active',
  criticality: 'high',
  is_subprocessor: true,
  subprocessor_purpose: 'Cloud hosting',
  subprocessor_location: 'EU (Frankfurt)',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
}

const SWITCH = { name: 'Listed publicly as a subprocessor' }

function change(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } })
}

beforeEach(() => {
  vi.clearAllMocks()
  mockCreateVendor.mockResolvedValue(LISTED)
  mockUpdateVendor.mockResolvedValue(LISTED)
})

describe('VendorModal — subprocessor listing (create)', () => {
  it('shows the switch off, with help text about publishing the name', () => {
    render(<VendorModal organizationId="org-1" onClose={vi.fn()} onSuccess={vi.fn()} />)
    const toggle = screen.getByRole('switch', SWITCH)
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByText(/publishes this vendor's name on your trust portal/i)).toBeInTheDocument()
    expect(screen.getByLabelText('Public purpose')).toBeInTheDocument()
    expect(screen.getByLabelText('Public processing location')).toBeInTheDocument()
  })

  it('sends is_subprocessor false and null public fields when left alone', async () => {
    render(<VendorModal organizationId="org-1" onClose={vi.fn()} onSuccess={vi.fn()} />)
    change('Vendor Name *', 'Quiet Vendor')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /add vendor/i }))
    })
    const [payload] = mockCreateVendor.mock.calls[0]
    expect(payload).toMatchObject({
      is_subprocessor: false,
      subprocessor_purpose: null,
      subprocessor_location: null,
    })
  })

  it('sends the switch on and the two public fields trimmed', async () => {
    render(<VendorModal organizationId="org-1" onClose={vi.fn()} onSuccess={vi.fn()} />)
    change('Vendor Name *', 'Acme Cloud')
    fireEvent.click(screen.getByRole('switch', SWITCH))
    expect(screen.getByRole('switch', SWITCH)).toHaveAttribute('aria-checked', 'true')
    change('Public purpose', '  Cloud hosting ')
    change('Public processing location', ' EU (Frankfurt) ')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /add vendor/i }))
    })
    const [payload] = mockCreateVendor.mock.calls[0]
    expect(payload).toMatchObject({
      is_subprocessor: true,
      subprocessor_purpose: 'Cloud hosting',
      subprocessor_location: 'EU (Frankfurt)',
    })
  })
})

describe('VendorModal — subprocessor listing (edit)', () => {
  it('pre-fills the stored switch and fields', () => {
    render(<VendorModal organizationId="org-1" editVendor={LISTED} onClose={vi.fn()} onSuccess={vi.fn()} />)
    expect(screen.getByRole('switch', SWITCH)).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByLabelText('Public purpose')).toHaveValue('Cloud hosting')
    expect(screen.getByLabelText('Public processing location')).toHaveValue('EU (Frankfurt)')
  })

  it('turning the switch off sends false and keeps the text', async () => {
    render(<VendorModal organizationId="org-1" editVendor={LISTED} onClose={vi.fn()} onSuccess={vi.fn()} />)
    fireEvent.click(screen.getByRole('switch', SWITCH))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save changes/i }))
    })
    const [vendorId, payload] = mockUpdateVendor.mock.calls[0]
    expect(vendorId).toBe('v-101')
    expect(payload).toMatchObject({
      is_subprocessor: false,
      subprocessor_purpose: 'Cloud hosting',
      subprocessor_location: 'EU (Frankfurt)',
    })
  })

  it('reads a vendor stored before the field existed as off', () => {
    const legacy: Vendor = { ...LISTED, is_subprocessor: undefined, subprocessor_purpose: undefined, subprocessor_location: undefined }
    render(<VendorModal organizationId="org-1" editVendor={legacy} onClose={vi.fn()} onSuccess={vi.fn()} />)
    expect(screen.getByRole('switch', SWITCH)).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByLabelText('Public purpose')).toHaveValue('')
  })
})
