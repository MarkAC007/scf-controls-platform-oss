/**
 * AddSystemModal — PIG boundary & recovery fields (issue #1124, backend PR #1135)
 *
 * Pins:
 *  - Create form renders the "Boundary & Recovery" fields
 *  - Create payload carries in_scope / criticality / rto_hours / rpo_hours /
 *    data_classification / hosting_location with the backend's exact values
 *  - "Undecided" boundary and blank fields are sent as null, not "" or omitted
 *  - Edit form pre-fills the stored values
 *  - Clearing a stored value sends null so the PATCH actually clears it
 *  - A rejected save shows the API's detail message, not a generic string
 */
import { render, screen, fireEvent, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import AddSystemModal from '../AddSystemModal'
import type { System } from '../../types'

vi.mock('../../data/apiClient', () => ({
  createSystem: vi.fn(),
  updateSystem: vi.fn(),
}))

vi.mock('../SystemTemplatePicker', () => ({
  SystemTemplatePicker: ({ onCustom }: { onCustom: () => void }) => (
    <button type="button" onClick={onCustom}>Add a custom system</button>
  ),
}))

vi.mock('../VendorPicker', () => ({
  VendorPicker: () => <div data-testid="vendor-picker" />,
}))

vi.mock('../MarkdownField', () => ({
  default: ({ id, value, onChange }: { id: string; value: string; onChange: (v: string) => void }) => (
    <textarea id={id} value={value} onChange={e => onChange(e.target.value)} />
  ),
}))

import { createSystem, updateSystem } from '../../data/apiClient'

const mockCreateSystem = vi.mocked(createSystem)
const mockUpdateSystem = vi.mocked(updateSystem)

const STORED: System = {
  id: 'sys-001',
  organization_id: 'org-1',
  name: 'Google Workspace',
  system_type: 'cloud_provider',
  status: 'active',
  in_scope: true,
  criticality: 'high',
  rto_hours: 4,
  rpo_hours: 1.5,
  data_classification: 'confidential',
  hosting_location: 'europe-west2',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
}

function openCreateForm() {
  render(<AddSystemModal organizationId="org-1" onClose={vi.fn()} onSuccess={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: 'Add a custom system' }))
}

function change(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } })
}

beforeEach(() => {
  vi.clearAllMocks()
  mockCreateSystem.mockResolvedValue(STORED)
  mockUpdateSystem.mockResolvedValue(STORED)
})

describe('AddSystemModal — boundary & recovery (create)', () => {
  it('renders the new fields with their labels', () => {
    openCreateForm()
    expect(screen.getByText('Boundary & Recovery')).toBeInTheDocument()
    expect(screen.getByLabelText('Assessment Boundary')).toBeInTheDocument()
    expect(screen.getByLabelText('Criticality')).toBeInTheDocument()
    expect(screen.getByLabelText('RTO (hours)')).toBeInTheDocument()
    expect(screen.getByLabelText('RPO (hours)')).toBeInTheDocument()
    expect(screen.getByLabelText('Data Classification')).toBeInTheDocument()
    expect(screen.getByLabelText('Hosting Location')).toBeInTheDocument()
  })

  it('offers the backend enum values with human labels', () => {
    openCreateForm()
    const boundary = screen.getByLabelText('Assessment Boundary') as HTMLSelectElement
    expect(Array.from(boundary.options).map(o => [o.value, o.text])).toEqual([
      ['', 'Undecided'],
      ['true', 'In scope'],
      ['false', 'Out of scope'],
    ])
    const criticality = screen.getByLabelText('Criticality') as HTMLSelectElement
    expect(Array.from(criticality.options).map(o => [o.value, o.text])).toEqual([
      ['', '-- Select criticality --'],
      ['low', 'Low'],
      ['medium', 'Medium'],
      ['high', 'High'],
      ['critical', 'Critical'],
    ])
    const classification = screen.getByLabelText('Data Classification') as HTMLSelectElement
    expect(Array.from(classification.options).map(o => [o.value, o.text])).toEqual([
      ['', '-- Select classification --'],
      ['public', 'Public'],
      ['internal', 'Internal'],
      ['confidential', 'Confidential'],
      ['restricted', 'Restricted'],
    ])
  })

  it('sends the filled-in values with the exact field names and types', async () => {
    openCreateForm()
    change('System Name *', 'AWS Production')
    change('Assessment Boundary', 'false')
    change('Criticality', 'critical')
    change('RTO (hours)', '24')
    change('RPO (hours)', '0.5')
    change('Data Classification', 'restricted')
    change('Hosting Location', '  eu-west-2  ')

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /add system/i }))
    })

    expect(mockCreateSystem).toHaveBeenCalledTimes(1)
    const [payload, orgId] = mockCreateSystem.mock.calls[0]
    expect(orgId).toBe('org-1')
    expect(payload).toMatchObject({
      name: 'AWS Production',
      in_scope: false,
      criticality: 'critical',
      rto_hours: 24,
      rpo_hours: 0.5,
      data_classification: 'restricted',
      hosting_location: 'eu-west-2',
    })
  })

  it('sends null for an undecided boundary and for every blank field', async () => {
    openCreateForm()
    change('System Name *', 'AWS Production')

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /add system/i }))
    })

    const [payload] = mockCreateSystem.mock.calls[0]
    expect(payload).toMatchObject({
      in_scope: null,
      criticality: null,
      rto_hours: null,
      rpo_hours: null,
      data_classification: null,
      hosting_location: null,
    })
  })

  it('keeps zero hours as 0, not null', async () => {
    openCreateForm()
    change('System Name *', 'AWS Production')
    change('RTO (hours)', '0')
    change('RPO (hours)', '0')

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /add system/i }))
    })

    const [payload] = mockCreateSystem.mock.calls[0]
    expect(payload.rto_hours).toBe(0)
    expect(payload.rpo_hours).toBe(0)
  })
})

describe('AddSystemModal — boundary & recovery (edit)', () => {
  function openEditForm(system: System = STORED) {
    render(
      <AddSystemModal organizationId="org-1" editSystem={system} onClose={vi.fn()} onSuccess={vi.fn()} />
    )
  }

  it('pre-fills the stored values', () => {
    openEditForm()
    expect((screen.getByLabelText('Assessment Boundary') as HTMLSelectElement).value).toBe('true')
    expect((screen.getByLabelText('Criticality') as HTMLSelectElement).value).toBe('high')
    expect((screen.getByLabelText('RTO (hours)') as HTMLInputElement).value).toBe('4')
    expect((screen.getByLabelText('RPO (hours)') as HTMLInputElement).value).toBe('1.5')
    expect((screen.getByLabelText('Data Classification') as HTMLSelectElement).value).toBe('confidential')
    expect((screen.getByLabelText('Hosting Location') as HTMLInputElement).value).toBe('europe-west2')
  })

  it('pre-fills an out-of-scope system as "false", not undecided', () => {
    openEditForm({ ...STORED, in_scope: false })
    expect((screen.getByLabelText('Assessment Boundary') as HTMLSelectElement).value).toBe('false')
  })

  it('sends null for each value the user clears, so the PATCH clears it', async () => {
    openEditForm()
    change('Assessment Boundary', '')
    change('Criticality', '')
    change('RTO (hours)', '')
    change('RPO (hours)', '')
    change('Data Classification', '')
    change('Hosting Location', '   ')

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save changes/i }))
    })

    expect(mockUpdateSystem).toHaveBeenCalledTimes(1)
    const [systemId, payload, orgId] = mockUpdateSystem.mock.calls[0]
    expect(systemId).toBe('sys-001')
    expect(orgId).toBe('org-1')
    expect(payload).toMatchObject({
      in_scope: null,
      criticality: null,
      rto_hours: null,
      rpo_hours: null,
      data_classification: null,
      hosting_location: null,
    })
  })

  it('does not submit a negative RTO (min=0 matches the backend ge=0)', async () => {
    render(
      <AddSystemModal organizationId="org-1" editSystem={STORED} onClose={vi.fn()} onSuccess={vi.fn()} />
    )
    change('RTO (hours)', '-1')

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save changes/i }))
    })

    expect(mockUpdateSystem).not.toHaveBeenCalled()
  })

  it('shows the API detail message when the save is rejected', async () => {
    mockUpdateSystem.mockRejectedValue(
      new Error("System with name 'Okta' already exists in this organization")
    )
    const onSuccess = vi.fn()
    render(
      <AddSystemModal organizationId="org-1" editSystem={STORED} onClose={vi.fn()} onSuccess={onSuccess} />
    )
    change('Hosting Location', 'eu-west-1')

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save changes/i }))
    })

    expect(mockUpdateSystem).toHaveBeenCalledTimes(1)
    expect(screen.getByText("System with name 'Okta' already exists in this organization")).toBeInTheDocument()
    expect(onSuccess).not.toHaveBeenCalled()
  })
})
