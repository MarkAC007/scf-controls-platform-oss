import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AddFindingModal from '../AddFindingModal'
import type { CapabilityTest } from '../../../types'

const api = vi.hoisted(() => ({
  addCapabilityTestFinding: vi.fn(),
  updateCapabilityTest: vi.fn(),
  getOrgMembers: vi.fn(),
  getRiskAssessments: vi.fn(),
  fetchScopedControlsPage: vi.fn(),
}))

vi.mock('../../../data/apiClient', () => api)
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const make = (over: Partial<CapabilityTest> = {}): CapabilityTest => ({
  id: 'ct-1',
  organization_id: 'org-one',
  test_type: 'incident_response',
  title: 'Ransomware tabletop',
  tested_on: '2026-10-01',
  scenario: 's',
  result: 'failed',
  outcome: 'o',
  finding_count: 1,
  open_finding_count: 1,
  created_at: '2026-10-01T09:00:00Z',
  updated_at: '2026-10-01T09:00:00Z',
  ...over,
})

function renderModal(test: CapabilityTest) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const onClose = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <AddFindingModal organizationId="org-one" test={test} onClose={onClose} />
    </QueryClientProvider>,
  )
  return { invalidate, onClose }
}

const type = (name: string, value: string) => fireEvent.change(screen.getByLabelText(name), { target: { value } })

describe('AddFindingModal (PIG R11)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.addCapabilityTestFinding.mockResolvedValue({})
    api.updateCapabilityTest.mockResolvedValue({})
    api.getOrgMembers.mockResolvedValue([])
    api.getRiskAssessments.mockResolvedValue([{ risk_code: 'R-TEST-1' }])
    api.fetchScopedControlsPage.mockResolvedValue({ controls: [], total: 0, offset: 0 })
  })

  it('adds a finding to a test that found something, then refreshes tests, deficiencies and the journey', async () => {
    const { onClose, invalidate } = renderModal(make())
    expect(screen.getByRole('dialog', { name: 'Add finding' })).toHaveTextContent('Ransomware tabletop')
    expect(screen.getByLabelText('Title')).toHaveFocus()
    expect(screen.queryByLabelText("The test's result becomes")).toBeNull()

    type('Title', ' Backups untested ')
    await waitFor(() => expect(screen.getByLabelText('Risk')).not.toBeDisabled())
    type('Risk', 'R-TEST-1')
    type('Priority', 'high')
    type('Due date', '2026-11-30')
    type('Root cause', ' Nobody owned restores ')
    fireEvent.click(screen.getByRole('button', { name: 'Add finding' }))

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    expect(api.addCapabilityTestFinding).toHaveBeenCalledWith('org-one', 'ct-1', {
      title: 'Backups untested',
      priority: 'high',
      risk_code: 'R-TEST-1',
      due_date: '2026-11-30',
      root_cause: 'Nobody owned restores',
    })
    expect(api.updateCapabilityTest).not.toHaveBeenCalled()
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['capability-tests', 'org-one'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['deficiencies', 'org-one'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['journey', 'org-one'] })
  })

  it('corrects a test recorded as passed with the finding, in one request', async () => {
    const { onClose } = renderModal(make({ result: 'passed', finding_count: 0, open_finding_count: 0 }))
    type('Title', 'Late finding')
    const add = screen.getByRole('button', { name: 'Add finding' })
    expect(add).toBeDisabled()

    type("The test's result becomes", 'failed')
    fireEvent.click(add)

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    expect(api.updateCapabilityTest).toHaveBeenCalledWith('org-one', 'ct-1', {
      result: 'failed',
      findings: [{ title: 'Late finding', priority: 'medium' }],
    })
    expect(api.addCapabilityTestFinding).not.toHaveBeenCalled()
  })

  it('offers only the results a finding allows', () => {
    renderModal(make({ result: 'passed', finding_count: 0, open_finding_count: 0 }))
    const options = Array.from((screen.getByLabelText("The test's result becomes") as HTMLSelectElement).options).map((o) => o.value)
    expect(options).toEqual(['', 'passed_with_findings', 'failed'])
  })

  it('needs a title', () => {
    renderModal(make())
    const add = screen.getByRole('button', { name: 'Add finding' })
    type('Title', '   ')
    expect(add).toBeDisabled()
    fireEvent.submit(add.closest('form')!)
    expect(api.addCapabilityTestFinding).not.toHaveBeenCalled()
  })

  it("shows the server's refusal (a viewer's 403 included) and keeps the form", async () => {
    api.addCapabilityTestFinding.mockRejectedValue(new Error("Access denied: API key role 'viewer' insufficient, requires 'editor'"))
    const { onClose } = renderModal(make())
    type('Title', 'Gap')
    fireEvent.click(screen.getByRole('button', { name: 'Add finding' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("Access denied: API key role 'viewer' insufficient, requires 'editor'")
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Title')).toHaveValue('Gap')
  })

  it('is read-only and cannot be closed while it saves', async () => {
    let resolve!: (v: unknown) => void
    api.addCapabilityTestFinding.mockReturnValue(new Promise((r) => (resolve = r)))
    const { onClose } = renderModal(make())
    type('Title', 'Gap')
    fireEvent.click(screen.getByRole('button', { name: 'Add finding' }))
    await screen.findByRole('button', { name: 'Saving…' })
    expect(screen.getByLabelText('Root cause')).toHaveAttribute('readonly')
    expect(screen.getByLabelText('Description')).toHaveAttribute('readonly')
    expect(screen.getByRole('button', { name: 'Close' })).toBeDisabled()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
    resolve({})
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
  })

  it('ignores Escape and a backdrop click once anything is typed', () => {
    const { onClose } = renderModal(make())
    type('Description', 'x')
    fireEvent.keyDown(document, { key: 'Escape' })
    fireEvent.click(document.querySelector('.modal-overlay')!)
    expect(onClose).not.toHaveBeenCalled()
  })
})
