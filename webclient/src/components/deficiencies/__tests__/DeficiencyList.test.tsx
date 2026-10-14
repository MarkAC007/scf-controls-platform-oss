import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ConfirmProvider, answerConfirm, confirmClosed, findConfirm } from '../../../__tests__/confirmDialog'
import DeficiencyList from '../DeficiencyList'
import DeficiencyCompleteModal from '../DeficiencyCompleteModal'
import type { Deficiency } from '../../../types'

const api = vi.hoisted(() => ({
  updateDeficiency: vi.fn(),
  createDeficiency: vi.fn(),
  getOrgMembers: vi.fn(),
  getRiskAssessments: vi.fn(),
  fetchScopedControlsPage: vi.fn(),
}))

vi.mock('../../../data/apiClient', () => api)
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const USERS = [{ id: 'user-ann', email: 'ann@example.test', display_name: 'Ann' }]

const base: Deficiency = {
  id: 'def-open',
  organization_id: 'org-one',
  title: 'Backups untested',
  priority: 'high',
  status: 'open',
  owner_user_id: 'user-ann',
  due_date: '2020-01-31',
  scf_id: 'TESTCONTROL-01',
  domain: 'TESTCONTROL',
  risk_code: 'R-TEST-1',
  auto_generated: false,
  source: null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
}
const completed: Deficiency = {
  ...base,
  id: 'def-done',
  title: 'Old gap',
  status: 'completed',
  completed_date: '2026-10-02',
  retest_note: 'Restored on 2 Oct',
  source: 'control_at_risk',
  auto_generated: true,
}

function renderList(canEdit = true, deficiencies = [base, completed]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const onEdit = vi.fn()
  const onComplete = vi.fn()
  const ui = (rows: Deficiency[]) => (
    <QueryClientProvider client={client}>
      <ConfirmProvider>
        <DeficiencyList
          organizationId="org-one"
          deficiencies={rows}
          users={USERS}
          canEdit={canEdit}
          onEdit={onEdit}
          onComplete={onComplete}
        />
      </ConfirmProvider>
    </QueryClientProvider>
  )
  const { rerender } = render(ui(deficiencies))
  return { invalidate, onEdit, onComplete, rerender: (rows: Deficiency[]) => rerender(ui(rows)) }
}

const row = (id: string) => within(screen.getByTestId(`deficiency-${id}`))

describe('DeficiencyList (PIG R8)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getOrgMembers.mockResolvedValue(USERS)
    api.getRiskAssessments.mockResolvedValue([])
  })
  afterEach(() => vi.restoreAllMocks())

  it('shows each deficiency with its status, priority, subject, owner, overdue flag and source', () => {
    renderList()
    const open = row('def-open')
    expect(open.getByText('Backups untested')).toBeInTheDocument()
    expect(open.getByText('Open')).toBeInTheDocument()
    expect(open.getByText('High')).toBeInTheDocument()
    expect(open.getByText('Overdue')).toBeInTheDocument()
    expect(open.getByRole('link', { name: 'TESTCONTROL-01' })).toHaveAttribute('href', expect.stringContaining('item=TESTCONTROL-01'))
    expect(open.getByRole('link', { name: 'R-TEST-1' })).toHaveAttribute('href', expect.stringContaining('risk=R-TEST-1'))
    expect(open.getByText('Owner: Ann')).toBeInTheDocument()
    expect(open.getByText('Raised by hand')).toBeInTheDocument()
    const done = row('def-done')
    expect(done.queryByText('Overdue')).not.toBeInTheDocument()
    expect(done.getByText('Raised automatically: control at risk')).toBeInTheDocument()
    expect(done.getByText(/Restored on 2 Oct/)).toBeInTheDocument()
  })

  it('offers no actions to someone who cannot edit', () => {
    renderList(false)
    expect(screen.queryByRole('button', { name: /^Edit/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Complete/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Reopen/ })).not.toBeInTheDocument()
  })

  it('offers complete and cancel on an open deficiency, reopen on a closed one', () => {
    renderList()
    expect(row('def-open').getByRole('button', { name: 'Complete "Backups untested"' })).toBeInTheDocument()
    expect(row('def-open').getByRole('button', { name: 'Cancel deficiency "Backups untested"' })).toBeInTheDocument()
    expect(row('def-open').queryByRole('button', { name: /^Reopen/ })).not.toBeInTheDocument()
    expect(row('def-done').getByRole('button', { name: 'Reopen "Old gap"' })).toBeInTheDocument()
    expect(row('def-done').queryByRole('button', { name: /^Complete/ })).not.toBeInTheDocument()
  })

  it('names every row action after its deficiency, so no two read alike (review LOW-1)', () => {
    renderList()
    const names = screen.getAllByRole('button').map((b) => b.getAttribute('aria-label') ?? b.textContent)
    expect(names).toEqual([
      'Edit "Backups untested"',
      'Complete "Backups untested"',
      'Cancel deficiency "Backups untested"',
      'Edit "Old gap"',
      'Reopen "Old gap"',
    ])
  })

  it('cancels only after confirmation, then refreshes the lists and the journey', async () => {
    api.updateDeficiency.mockResolvedValue({ ...base, status: 'cancelled' })
    const { invalidate } = renderList()
    fireEvent.click(row('def-open').getByRole('button', { name: /^Cancel deficiency/ }))
    await answerConfirm(false)
    expect(api.updateDeficiency).not.toHaveBeenCalled()
    fireEvent.click(row('def-open').getByRole('button', { name: /^Cancel deficiency/ }))
    const message = await answerConfirm(true)
    await waitFor(() => expect(api.updateDeficiency).toHaveBeenCalledWith('org-one', 'def-open', { status: 'cancelled' }))
    expect(message).toMatch(/never deleted/)
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['deficiencies', 'org-one'] }))
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['journey', 'org-one'] })
  })

  it('asks in the page, word for word, with "Keep it" as the way out so there is no second "Cancel"', async () => {
    renderList()
    fireEvent.click(row('def-open').getByRole('button', { name: /^Cancel deficiency/ }))
    const open = await findConfirm()
    expect(open.title).toBe('Cancel deficiency')
    expect(open.message).toBe(
      `Cancel "${base.title}"? It stops counting as an open deficiency. Deficiencies are never deleted; it can be reopened.`,
    )
    expect(within(open.dialog).getByRole('button', { name: 'Keep it' })).toBeInTheDocument()
    expect(within(open.dialog).getByRole('button', { name: 'Cancel deficiency' })).toBeInTheDocument()
    open.cancel()
    await confirmClosed()
    expect(api.updateDeficiency).not.toHaveBeenCalled()
  })

  it('reopens a completed deficiency after warning that its retest note is cleared, and sends no note', async () => {
    api.updateDeficiency.mockResolvedValue({ ...completed, status: 'open' })
    renderList()
    fireEvent.click(row('def-done').getByRole('button', { name: /^Reopen/ }))
    const message = await answerConfirm(true)
    await waitFor(() => expect(api.updateDeficiency).toHaveBeenCalledWith('org-one', 'def-done', { status: 'open' }))
    expect(message).toMatch(/retest note and completion date are cleared/)
  })

  it("shows the server's refusal on the row it concerns", async () => {
    api.updateDeficiency.mockRejectedValue(new Error('This finding cannot be restored: its test has passed'))
    renderList()
    fireEvent.click(row('def-done').getByRole('button', { name: /^Reopen/ }))
    await answerConfirm(true)
    expect(await row('def-done').findByRole('alert')).toHaveTextContent('its test has passed')
    expect(row('def-open').queryByRole('alert')).not.toBeInTheDocument()
  })

  it("drops a row's refusal once a later write changes that row (review LOW-4)", async () => {
    api.updateDeficiency.mockRejectedValue(new Error('This finding cannot be restored'))
    const { rerender } = renderList()
    fireEvent.click(row('def-done').getByRole('button', { name: /^Reopen/ }))
    await answerConfirm(true)
    await row('def-done').findByRole('alert')
    // Edited elsewhere (the edit form, another tab): the refetched row is newer.
    rerender([base, { ...completed, title: 'Old gap, retested', updated_at: '2026-10-03T00:00:00Z' }])
    expect(row('def-done').queryByRole('alert')).not.toBeInTheDocument()
  })

  it("clears a row's refusal when its edit or complete form is opened", async () => {
    api.updateDeficiency.mockRejectedValue(new Error('This finding cannot be restored'))
    renderList()
    fireEvent.click(row('def-done').getByRole('button', { name: /^Reopen/ }))
    await answerConfirm(true)
    await row('def-done').findByRole('alert')
    fireEvent.click(row('def-done').getByRole('button', { name: /^Edit/ }))
    expect(row('def-done').queryByRole('alert')).not.toBeInTheDocument()
  })

  it('hands the row to the edit and complete forms, which its owner mounts (review MED-2)', () => {
    const { onEdit, onComplete } = renderList()
    fireEvent.click(row('def-open').getByRole('button', { name: /^Complete/ }))
    expect(onComplete).toHaveBeenCalledWith(base)
    fireEvent.click(row('def-done').getByRole('button', { name: /^Edit/ }))
    expect(onEdit).toHaveBeenCalledWith(completed)
    // The list mounts no dialog of its own: one that lived here died with it.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

describe('DeficiencyCompleteModal (PIG R8)', () => {
  beforeEach(() => vi.clearAllMocks())

  function renderComplete() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const onClose = vi.fn()
    render(
      <QueryClientProvider client={client}>
        <DeficiencyCompleteModal organizationId="org-one" deficiency={base} onClose={onClose} />
      </QueryClientProvider>,
    )
    return { invalidate, onClose }
  }

  it('needs a retest note, and sends it trimmed with the completion', async () => {
    api.updateDeficiency.mockResolvedValue({ ...base, status: 'completed' })
    const { invalidate, onClose } = renderComplete()
    expect(screen.getByText('Backups untested')).toBeInTheDocument()
    const complete = screen.getByRole('button', { name: 'Complete' })
    expect(complete).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Retest note'), { target: { value: '   ' } })
    expect(complete).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Retest note'), { target: { value: '  Restore tested: 40 min  ' } })
    fireEvent.click(complete)
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.updateDeficiency).toHaveBeenCalledWith('org-one', 'def-open', {
      status: 'completed',
      retest_note: 'Restore tested: 40 min',
    })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['deficiencies', 'org-one'] })
  })

  it('stays open while the completion is in flight, and ignores Escape once a note is typed (review MED-3)', async () => {
    let refuse: (err: Error) => void = () => {}
    api.updateDeficiency.mockReturnValue(new Promise((_, reject) => { refuse = reject }))
    const { onClose } = renderComplete()
    fireEvent.change(screen.getByLabelText('Retest note'), { target: { value: 'Restore tested' } })
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Complete' }))
    await screen.findByRole('button', { name: 'Saving…' })
    fireEvent.click(document.querySelector('.modal-overlay')!)
    expect(screen.getByRole('button', { name: 'Close' })).toBeDisabled()
    expect(onClose).not.toHaveBeenCalled()
    refuse(new Error('Insufficient permissions'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Insufficient permissions')
    expect(onClose).not.toHaveBeenCalled()
  })

  it('closes on Escape before anything is typed', () => {
    const { onClose } = renderComplete()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('ignores a backdrop click once a note is typed, and closes on one before', () => {
    const { onClose } = renderComplete()
    fireEvent.change(screen.getByLabelText('Retest note'), { target: { value: 'Restore tested' } })
    fireEvent.click(document.querySelector('.modal-overlay')!)
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('Retest note'), { target: { value: '  ' } })
    fireEvent.click(document.querySelector('.modal-overlay')!)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("shows the server's refusal and stays open", async () => {
    api.updateDeficiency.mockRejectedValue(new Error('Completing a deficiency needs a retest note'))
    const { onClose } = renderComplete()
    fireEvent.change(screen.getByLabelText('Retest note'), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: 'Complete' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('needs a retest note')
    expect(onClose).not.toHaveBeenCalled()
  })
})

describe('DeficiencyList: a capability test finding (PIG R11)', () => {
  const findings: Deficiency[] = [
    { ...base, id: 'f-1', title: 'No out-of-band comms', source: 'capability_test', capability_test_id: 'ct-1' },
    { ...base, id: 'f-2', title: 'Backups untested', source: 'capability_test', capability_test_id: 'ct-1' },
  ]
  const renderFindings = (showTest?: boolean) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <DeficiencyList
          organizationId="org-one"
          deficiencies={findings}
          users={USERS}
          canEdit
          onEdit={vi.fn()}
          onComplete={vi.fn()}
          showTest={showTest}
        />
      </QueryClientProvider>,
    )
  }

  beforeEach(() => {
    vi.clearAllMocks()
    api.getOrgMembers.mockResolvedValue(USERS)
    api.getRiskAssessments.mockResolvedValue([])
  })

  it('links its source to the test, with a name of its own', () => {
    renderFindings()
    const link = screen.getByRole('link', { name: 'Capability test finding "No out-of-band comms": open its test' })
    expect(link).toHaveTextContent('Capability test finding')
    const href = new URL(link.getAttribute('href')!, 'http://x')
    expect(href.searchParams.get('tab')).toBe('capability-tests')
    expect(href.searchParams.get('test')).toBe('ct-1')
    expect(screen.getByRole('link', { name: 'Capability test finding "Backups untested": open its test' })).toBeInTheDocument()
  })

  it('names the source without a link on the test itself', () => {
    renderFindings(false)
    expect(screen.queryByRole('link', { name: /open its test/ })).toBeNull()
    expect(row('f-1').getByText('Capability test finding')).toBeInTheDocument()
  })
})
