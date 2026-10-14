import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DeficiencyFormModal from '../DeficiencyFormModal'
import type { Deficiency } from '../../../types'

const api = vi.hoisted(() => ({
  createDeficiency: vi.fn(),
  updateDeficiency: vi.fn(),
  getOrgMembers: vi.fn(),
  getRiskAssessments: vi.fn(),
  fetchScopedControlsPage: vi.fn(),
}))

vi.mock('../../../data/apiClient', () => api)
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const MEMBERS = [
  { id: 'user-ann', email: 'ann@example.test', display_name: 'Ann' },
  { id: 'user-bob', email: 'bob@example.test' },
]
const RISKS = [{ risk_code: 'R-TEST-1' }, { risk_code: 'R-TEST-2' }]

const existing: Deficiency = {
  id: 'def-1',
  organization_id: 'org-one',
  title: 'Backups untested',
  description: 'No restore test this year',
  priority: 'high',
  status: 'open',
  owner_name: null,
  owner_user_id: 'user-ann',
  due_date: '2026-11-30',
  scf_id: 'TESTCONTROL-01',
  domain: 'TESTCONTROL',
  risk_code: null,
  auto_generated: false,
  source: null,
  root_cause: null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
}

function renderModal(props: Partial<Parameters<typeof DeficiencyFormModal>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const onClose = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <DeficiencyFormModal organizationId="org-one" onClose={onClose} {...props} />
    </QueryClientProvider>,
  )
  return { invalidate, onClose }
}

describe('DeficiencyFormModal — raise (PIG R8)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getOrgMembers.mockResolvedValue(MEMBERS)
    api.getRiskAssessments.mockResolvedValue(RISKS)
    api.fetchScopedControlsPage.mockResolvedValue({ controls: [], total: 0, offset: 0 })
  })

  it('needs a title and a control or a risk before it can be raised', async () => {
    renderModal()
    const submit = screen.getByRole('button', { name: 'Raise deficiency' })
    expect(submit).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Gap' } })
    expect(submit).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Control'), { target: { value: 'TESTCONTROL-01' } })
    expect(submit).toBeEnabled()
  })

  it('sends exactly what was entered against a control, then refreshes lists, counts and journey', async () => {
    api.createDeficiency.mockResolvedValue({ ...existing, id: 'def-new' })
    const { invalidate, onClose } = renderModal()
    await screen.findByRole('option', { name: 'Ann' })
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: '  Backups untested  ' } })
    fireEvent.change(screen.getByLabelText('Control'), { target: { value: ' testcontrol-01 ' } })
    fireEvent.change(screen.getByLabelText('Priority'), { target: { value: 'high' } })
    fireEvent.change(screen.getByLabelText('Owner'), { target: { value: 'user-ann' } })
    fireEvent.change(screen.getByLabelText('Due date'), { target: { value: '2026-11-30' } })
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: ' No restore test ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Raise deficiency' }))

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.createDeficiency).toHaveBeenCalledWith('org-one', {
      title: 'Backups untested',
      priority: 'high',
      scf_id: 'TESTCONTROL-01',
      owner_user_id: 'user-ann',
      due_date: '2026-11-30',
      description: 'No restore test',
    })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['deficiencies', 'org-one'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['journey', 'org-one'] })
  })

  it('raises against an assessed risk alone', async () => {
    api.createDeficiency.mockResolvedValue({ ...existing, scf_id: null, risk_code: 'R-TEST-2' })
    renderModal()
    await screen.findByRole('option', { name: 'R-TEST-2' })
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Supplier gap' } })
    fireEvent.change(screen.getByLabelText('Risk'), { target: { value: 'R-TEST-2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Raise deficiency' }))
    await waitFor(() => expect(api.createDeficiency).toHaveBeenCalled())
    expect(api.createDeficiency).toHaveBeenCalledWith('org-one', {
      title: 'Supplier gap',
      priority: 'medium',
      risk_code: 'R-TEST-2',
    })
  })

  it('fixes the control when raised from its detail page', async () => {
    api.createDeficiency.mockResolvedValue(existing)
    renderModal({ fixedScfId: 'TESTCONTROL-01' })
    expect(screen.queryByLabelText('Control')).not.toBeInTheDocument()
    expect(screen.getByText('TESTCONTROL-01')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Gap' } })
    fireEvent.click(screen.getByRole('button', { name: 'Raise deficiency' }))
    await waitFor(() => expect(api.createDeficiency).toHaveBeenCalled())
    expect(api.createDeficiency.mock.calls[0][1]).toEqual({ title: 'Gap', priority: 'medium', scf_id: 'TESTCONTROL-01' })
  })

  it("shows the server's refusal and stays open", async () => {
    api.createDeficiency.mockRejectedValue(new Error('TESTCONTROL-09 is not a control this organisation has in scope'))
    const { onClose } = renderModal()
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Gap' } })
    fireEvent.change(screen.getByLabelText('Control'), { target: { value: 'TESTCONTROL-09' } })
    fireEvent.click(screen.getByRole('button', { name: 'Raise deficiency' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('not a control this organisation has in scope')
    expect(onClose).not.toHaveBeenCalled()
  })

  it('says when the owners or the risks cannot be loaded', async () => {
    api.getOrgMembers.mockRejectedValue(new Error('members down'))
    api.getRiskAssessments.mockRejectedValue(new Error('risks down'))
    renderModal()
    expect(await screen.findByText(/Could not load the members: members down/)).toBeInTheDocument()
    expect(await screen.findByText(/Could not load the assessed risks: risks down/)).toBeInTheDocument()
  })

  it('offers in-scope controls matching what is typed', async () => {
    api.fetchScopedControlsPage.mockResolvedValue({
      controls: [{ scf_id: 'TESTCONTROL-02', control_name: 'Test control two' }],
      total: 1,
      offset: 0,
    })
    renderModal()
    fireEvent.change(screen.getByLabelText('Control'), { target: { value: 'TESTC' } })
    await waitFor(() =>
      expect(api.fetchScopedControlsPage).toHaveBeenCalledWith(
        expect.objectContaining({ search: 'TESTC', scope_status: 'in_scope' }),
        'org-one',
      ),
    )
    await waitFor(() =>
      expect(document.querySelector('datalist option[value="TESTCONTROL-02"]')).not.toBeNull(),
    )
  })
})

describe('DeficiencyFormModal — edit (PIG R8)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getOrgMembers.mockResolvedValue(MEMBERS)
    api.getRiskAssessments.mockResolvedValue(RISKS)
  })

  it('keeps Save disabled until something changes, and shows the subject read-only', async () => {
    renderModal({ deficiency: existing })
    await screen.findByRole('option', { name: 'Ann' })
    expect(screen.getByText('TESTCONTROL-01')).toBeInTheDocument()
    expect(screen.queryByLabelText('Control')).not.toBeInTheDocument()
    const save = screen.getByRole('button', { name: 'Save' })
    expect(save).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Backups untested ' } })
    expect(save).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Backups never restored' } })
    expect(save).toBeEnabled()
  })

  it('sends only the changed fields, with a cleared due date and owner as null', async () => {
    api.updateDeficiency.mockResolvedValue(existing)
    const { invalidate, onClose } = renderModal({ deficiency: existing })
    await screen.findByRole('option', { name: 'Ann' })
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'in_progress' } })
    fireEvent.change(screen.getByLabelText('Owner'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('Due date'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('Root cause'), { target: { value: 'No owner for restores' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.updateDeficiency).toHaveBeenCalledWith('org-one', 'def-1', {
      status: 'in_progress',
      owner_user_id: null,
      due_date: null,
      root_cause: 'No owner for restores',
    })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['deficiencies', 'org-one'] })
  })

  it('offers no status change on a closed deficiency', async () => {
    renderModal({ deficiency: { ...existing, status: 'completed', retest_note: 'Restored fine' } })
    await screen.findByRole('option', { name: 'Ann' })
    expect(screen.queryByLabelText('Status')).not.toBeInTheDocument()
  })

  it("shows the server's refusal of an edit", async () => {
    api.updateDeficiency.mockRejectedValue(new Error('Insufficient permissions'))
    renderModal({ deficiency: existing })
    fireEvent.change(screen.getByLabelText('Priority'), { target: { value: 'low' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Insufficient permissions')
  })
})

describe('DeficiencyFormModal — closing (review MED-3, QA Escape)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getOrgMembers.mockResolvedValue(MEMBERS)
    api.getRiskAssessments.mockResolvedValue(RISKS)
    api.fetchScopedControlsPage.mockResolvedValue({ controls: [], total: 0, offset: 0 })
  })

  it('stays open while a raise is in flight, and still shows its refusal', async () => {
    let refuse: (err: Error) => void = () => {}
    api.createDeficiency.mockReturnValue(new Promise((_, reject) => { refuse = reject }))
    const { onClose } = renderModal()
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Gap' } })
    fireEvent.change(screen.getByLabelText('Control'), { target: { value: 'TESTCONTROL-01' } })
    fireEvent.click(screen.getByRole('button', { name: 'Raise deficiency' }))
    await screen.findByRole('button', { name: 'Saving…' })

    fireEvent.keyDown(document, { key: 'Escape' })
    fireEvent.click(document.querySelector('.modal-overlay')!)
    const close = screen.getByRole('button', { name: 'Close' })
    expect(close).toBeDisabled()
    fireEvent.click(close)
    expect(onClose).not.toHaveBeenCalled()

    refuse(new Error('API key role viewer insufficient'))
    expect(await screen.findByRole('alert')).toHaveTextContent('API key role viewer insufficient')
    expect(onClose).not.toHaveBeenCalled()
  })

  it('ignores Escape once something is typed, so closing a control suggestion keeps the form', () => {
    const { onClose } = renderModal()
    const control = screen.getByLabelText('Control')
    fireEvent.change(control, { target: { value: 'TESTCONTROL' } })
    fireEvent.keyDown(control, { key: 'Escape' })
    fireEvent.keyDown(control, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Control')).toHaveValue('TESTCONTROL')
  })

  it('closes on Escape while untouched', () => {
    const first = renderModal()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(first.onClose).toHaveBeenCalledTimes(1)
  })

  it('ignores a backdrop click once something is typed, like Escape', () => {
    const { onClose } = renderModal()
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Gap' } })
    fireEvent.click(document.querySelector('.modal-overlay')!)
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Title')).toHaveValue('Gap')
  })

  it('closes on a backdrop click while untouched', () => {
    const { onClose } = renderModal()
    fireEvent.click(document.querySelector('.modal-overlay')!)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('ignores Escape on an edit with unsaved changes', () => {
    const { onClose } = renderModal({ deficiency: existing })
    fireEvent.change(screen.getByLabelText('Priority'), { target: { value: 'low' } })
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
