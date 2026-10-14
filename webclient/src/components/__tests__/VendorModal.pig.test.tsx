/**
 * VendorModal — PIG data location & responsibility fields (issue #1124, backend PR #1135)
 *
 * Pins:
 *  - The form renders Data Location, Flow-down Requirements, Shared Responsibility
 *  - Criticality and Data Classification are not duplicated (one control each)
 *  - Create payload carries data_location / flow_down_requirements /
 *    shared_responsibility_note, trimmed, with blanks sent as null
 *  - Edit form pre-fills the stored values; clearing sends null
 *  - A rejected save shows the API's detail message
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

const STORED: Vendor = {
  id: 'v-001',
  organization_id: 'org-1',
  name: 'JumpCloud',
  status: 'active',
  criticality: 'high',
  data_classification: 'confidential',
  data_location: 'EU (Frankfurt)',
  flow_down_requirements: 'Breach notice within 24 hours',
  shared_responsibility_note: 'They run the IdP; we own user lifecycle',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
}

function change(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } })
}

beforeEach(() => {
  vi.clearAllMocks()
  mockCreateVendor.mockResolvedValue(STORED)
  mockUpdateVendor.mockResolvedValue(STORED)
})

describe('VendorModal — data location & responsibilities (create)', () => {
  it('renders the new fields under their own section', () => {
    render(<VendorModal organizationId="org-1" onClose={vi.fn()} onSuccess={vi.fn()} />)
    expect(screen.getByText('Data Location & Responsibilities')).toBeInTheDocument()
    expect(screen.getByLabelText('Data Location')).toBeInTheDocument()
    expect(screen.getByLabelText('Flow-down Requirements')).toBeInTheDocument()
    expect(screen.getByLabelText('Shared Responsibility')).toBeInTheDocument()
  })

  it('keeps a single criticality and a single data classification control', () => {
    render(<VendorModal organizationId="org-1" onClose={vi.fn()} onSuccess={vi.fn()} />)
    expect(screen.getAllByLabelText(/criticality/i)).toHaveLength(1)
    expect(screen.getAllByLabelText(/classification/i)).toHaveLength(1)
  })

  it('sends the new values trimmed, with the exact field names', async () => {
    render(<VendorModal organizationId="org-1" onClose={vi.fn()} onSuccess={vi.fn()} />)
    change('Vendor Name *', 'Acme Cloud')
    change('Data Location', '  UK South  ')
    change('Flow-down Requirements', ' Annual pen test ')
    change('Shared Responsibility', ' Acme patches the OS ')

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /add vendor/i }))
    })

    expect(mockCreateVendor).toHaveBeenCalledTimes(1)
    const [payload, orgId] = mockCreateVendor.mock.calls[0]
    expect(orgId).toBe('org-1')
    expect(payload).toMatchObject({
      name: 'Acme Cloud',
      data_location: 'UK South',
      flow_down_requirements: 'Annual pen test',
      shared_responsibility_note: 'Acme patches the OS',
    })
  })

  it('sends null for each blank new field', async () => {
    render(<VendorModal organizationId="org-1" onClose={vi.fn()} onSuccess={vi.fn()} />)
    change('Vendor Name *', 'Acme Cloud')

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /add vendor/i }))
    })

    const [payload] = mockCreateVendor.mock.calls[0]
    expect(payload).toMatchObject({
      data_location: null,
      flow_down_requirements: null,
      shared_responsibility_note: null,
    })
  })
})

describe('VendorModal — data location & responsibilities (edit)', () => {
  it('pre-fills the stored values', () => {
    render(<VendorModal organizationId="org-1" editVendor={STORED} onClose={vi.fn()} onSuccess={vi.fn()} />)
    expect((screen.getByLabelText('Data Location') as HTMLInputElement).value).toBe('EU (Frankfurt)')
    expect((screen.getByLabelText('Flow-down Requirements') as HTMLTextAreaElement).value)
      .toBe('Breach notice within 24 hours')
    expect((screen.getByLabelText('Shared Responsibility') as HTMLTextAreaElement).value)
      .toBe('They run the IdP; we own user lifecycle')
  })

  it('sends null for each value the user clears, so the PATCH clears it', async () => {
    render(<VendorModal organizationId="org-1" editVendor={STORED} onClose={vi.fn()} onSuccess={vi.fn()} />)
    change('Data Location', '')
    change('Flow-down Requirements', '  ')
    change('Shared Responsibility', '')

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save changes/i }))
    })

    expect(mockUpdateVendor).toHaveBeenCalledTimes(1)
    const [vendorId, payload, orgId] = mockUpdateVendor.mock.calls[0]
    expect(vendorId).toBe('v-001')
    expect(orgId).toBe('org-1')
    expect(payload).toMatchObject({
      data_location: null,
      flow_down_requirements: null,
      shared_responsibility_note: null,
    })
  })

  it('shows the API detail message when the save is rejected', async () => {
    mockUpdateVendor.mockRejectedValue(new Error("Access denied: API key role 'viewer' insufficient, requires 'editor'"))
    const onSuccess = vi.fn()
    render(<VendorModal organizationId="org-1" editVendor={STORED} onClose={vi.fn()} onSuccess={onSuccess} />)
    change('Data Location', 'QA Frankfurt')

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save changes/i }))
    })

    expect(screen.getByText("Access denied: API key role 'viewer' insufficient, requires 'editor'")).toBeInTheDocument()
    expect(onSuccess).not.toHaveBeenCalled()
  })
})
