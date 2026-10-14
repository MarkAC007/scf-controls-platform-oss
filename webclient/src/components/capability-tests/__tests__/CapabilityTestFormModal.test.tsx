import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ConfirmProvider, answerConfirm, findConfirm } from '../../../__tests__/confirmDialog'
import CapabilityTestFormModal from '../CapabilityTestFormModal'
import type { CapabilityTest } from '../../../types'

const api = vi.hoisted(() => ({
  createCapabilityTest: vi.fn(),
  updateCapabilityTest: vi.fn(),
  getOrgMembers: vi.fn(),
  getRiskAssessments: vi.fn(),
  fetchScopedControlsPage: vi.fn(),
}))

vi.mock('../../../data/apiClient', () => api)
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

/** Sets the browser's zone and clock. Node re-reads TZ when it is assigned; the check proves Date follows it. */
function clockAt(zone: string, utc: string) {
  vi.stubEnv('TZ', zone)
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(utc))
  const hour = new Intl.DateTimeFormat('en-US', { timeZone: zone, hour: 'numeric', hourCycle: 'h23' }).format(new Date())
  expect(new Date().getHours()).toBe(Number(hour))
}

const TEST: CapabilityTest = {
  id: 'ct-1',
  organization_id: 'org-one',
  test_type: 'continuity',
  title: 'DR failover',
  tested_on: '2026-10-01',
  scenario: 'Fail over to the second region',
  result: 'passed',
  outcome: 'Failed over in 40 minutes',
  recorded_by_user_id: null,
  finding_count: 0,
  open_finding_count: 0,
  created_at: '2026-10-01T09:00:00Z',
  updated_at: '2026-10-01T09:00:00Z',
}

function renderModal(props: Partial<Parameters<typeof CapabilityTestFormModal>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const onClose = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <ConfirmProvider>
        <CapabilityTestFormModal organizationId="org-one" onClose={onClose} {...props} />
      </ConfirmProvider>
    </QueryClientProvider>,
  )
  return { invalidate, onClose }
}

const field = (name: string) => screen.getByLabelText(name)
const type = (name: string, value: string) => fireEvent.change(field(name), { target: { value } })

function fillTest() {
  type('Title', '  Ransomware tabletop  ')
  type('Type', 'incident_response')
  type('Tested on', '2026-10-10')
  type('Scenario', ' Encrypted file share ')
  type('Outcome', ' Contained in 3h ')
}

describe('CapabilityTestFormModal: recording (PIG R11)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clockAt('Europe/London', '2026-10-13T12:00:00Z')
    api.createCapabilityTest.mockResolvedValue({})
    api.getOrgMembers.mockResolvedValue([{ id: 'u-1', email: 'ann@example.com', display_name: 'Ann' }])
    api.getRiskAssessments.mockResolvedValue([{ risk_code: 'R-TEST-1' }])
    api.fetchScopedControlsPage.mockResolvedValue({ controls: [], total: 0, offset: 0 })
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllEnvs()
  })

  it('records a test with its findings, trimmed, then refreshes tests, deficiencies and the journey', async () => {
    const { invalidate, onClose } = renderModal()
    fillTest()
    type('Result', 'failed')
    fireEvent.click(screen.getByRole('button', { name: 'Add a finding' }))
    type('Finding 1 Title', ' No out-of-band comms ')
    type('Finding 1 Control', 'testcontrol-01')
    type('Finding 1 Root cause', ' Comms plan assumed email ')
    await waitFor(() => expect(field('Finding 1 Owner')).not.toBeDisabled())
    type('Finding 1 Owner', 'u-1')
    fireEvent.click(screen.getByRole('button', { name: 'Record test' }))

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    expect(api.createCapabilityTest).toHaveBeenCalledWith('org-one', {
      test_type: 'incident_response',
      title: 'Ransomware tabletop',
      tested_on: '2026-10-10',
      scenario: 'Encrypted file share',
      result: 'failed',
      outcome: 'Contained in 3h',
      findings: [
        {
          title: 'No out-of-band comms',
          priority: 'medium',
          scf_id: 'TESTCONTROL-01',
          owner_user_id: 'u-1',
          root_cause: 'Comms plan assumed email',
        },
      ],
    })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['capability-tests', 'org-one'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['deficiencies', 'org-one'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['journey', 'org-one'] })
  })

  it("holds a pass with findings, and a failure without, to the server's rule", () => {
    renderModal()
    fillTest()
    const record = screen.getByRole('button', { name: 'Record test' })
    expect(record).not.toBeDisabled()

    type('Result', 'failed')
    expect(screen.getByText('A test that failed needs at least one finding')).toBeInTheDocument()
    expect(record).toBeDisabled()

    type('Result', 'passed')
    fireEvent.click(screen.getByRole('button', { name: 'Add a finding' }))
    type('Finding 1 Title', 'A gap')
    expect(
      screen.getByText('A test with 1 finding(s) did not simply pass: record it as passed_with_findings or failed'),
    ).toBeInTheDocument()
    expect(record).toBeDisabled()
    fireEvent.submit(record.closest('form')!)
    expect(api.createCapabilityTest).not.toHaveBeenCalled()

    type('Result', 'passed_with_findings')
    expect(record).not.toBeDisabled()
  })

  it('needs a title on every finding', () => {
    renderModal()
    fillTest()
    type('Result', 'failed')
    fireEvent.click(screen.getByRole('button', { name: 'Add a finding' }))
    expect(screen.getByText('Finding 1: title cannot be blank')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Record test' })).toBeDisabled()
  })

  it("refuses a day after the server's, as the server words it", () => {
    renderModal()
    fillTest()
    expect(field('Tested on')).toHaveAttribute('max', '2026-10-13')
    type('Tested on', '2026-10-14')
    expect(screen.getByText('tested_on cannot be in the future: record a test once it has run')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Record test' })).toBeDisabled()
    type('Tested on', '2026-10-13')
    expect(screen.getByRole('button', { name: 'Record test' })).not.toBeDisabled()
  })

  it('marks a refused day invalid and points it at the reason (QA1)', () => {
    renderModal()
    fillTest()
    expect(field('Tested on')).not.toHaveAttribute('aria-invalid')
    type('Tested on', '2026-10-14')
    const day = field('Tested on')
    expect(day).toHaveAttribute('aria-invalid', 'true')
    expect(day).toHaveAccessibleDescription(
      /at the latest\. tested_on cannot be in the future: record a test once it has run$/,
    )
    type('Tested on', '2026-10-13')
    expect(field('Tested on')).not.toHaveAttribute('aria-invalid')
    expect(field('Tested on')).toHaveAccessibleDescription('The day it was run; Oct 13, 2026 at the latest.')
  })

  it('keeps the problems region mounted, so its first problem is announced (L1)', () => {
    renderModal()
    const dialog = screen.getByRole('dialog', { name: 'Record capability test' })
    const region = dialog.querySelector('[aria-live="polite"]')
    expect(region).not.toBeNull()
    expect(region).toBeEmptyDOMElement()
    fillTest()
    type('Result', 'failed')
    expect(dialog.querySelector('[aria-live="polite"]')).toBe(region)
    expect(region).toHaveTextContent('A test that failed needs at least one finding')
  })

  it('names each empty required field once the form is touched (L2)', () => {
    renderModal()
    expect(screen.queryByText(/is required$/)).toBeNull()
    type('Title', 'Ransomware tabletop')
    expect(screen.getByText('Tested on is required')).toBeInTheDocument()
    expect(screen.getByText('Scenario is required')).toBeInTheDocument()
    expect(screen.getByText('Outcome is required')).toBeInTheDocument()
    expect(screen.queryByText('Title is required')).toBeNull()
    type('Tested on', '2026-10-10')
    type('Scenario', 'Encrypted file share')
    type('Outcome', '   ')
    expect(screen.getByText('Outcome is required')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Record test' })).toBeDisabled()
    type('Outcome', 'Contained')
    expect(screen.queryByText(/is required$/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Record test' })).not.toBeDisabled()
  })

  it("stops at the server's day when the reader's has already turned (00:30 BST)", () => {
    clockAt('Europe/London', '2026-10-13T23:30:00Z')
    renderModal()
    expect(field('Tested on')).toHaveAttribute('max', '2026-10-13')
    fillTest()
    type('Tested on', '2026-10-14')
    expect(screen.getByRole('button', { name: 'Record test' })).toBeDisabled()
  })

  it('names several findings apart', () => {
    renderModal()
    fireEvent.click(screen.getByRole('button', { name: 'Add a finding' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add a finding' }))
    expect(screen.getByRole('group', { name: 'Finding 1' })).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Finding 2' })).toBeInTheDocument()
    expect(screen.getByLabelText('Finding 2 Title')).toHaveFocus()
    expect(screen.getAllByLabelText(/Title$/)).toHaveLength(3)
    expect(screen.getByRole('button', { name: 'Remove finding 1' })).toBeInTheDocument()
  })

  it('confirms before dropping a finding that has anything typed in it', async () => {
    renderModal()
    fireEvent.click(screen.getByRole('button', { name: 'Add a finding' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add a finding' }))
    type('Finding 1 Title', 'Keep me')

    fireEvent.click(screen.getByRole('button', { name: 'Remove finding 1' }))
    expect(await answerConfirm(false)).toBe('Remove finding 1? What was typed in it is lost.')
    expect(screen.getByLabelText('Finding 1 Title')).toHaveValue('Keep me')

    // An empty one goes without asking, and the typed one keeps its text.
    fireEvent.click(screen.getByRole('button', { name: 'Remove finding 2' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(screen.queryByRole('group', { name: 'Finding 2' })).toBeNull()
    expect(screen.getByLabelText('Finding 1 Title')).toHaveValue('Keep me')
  })

  it('drops the typed finding once the removal is confirmed', async () => {
    renderModal()
    fireEvent.click(screen.getByRole('button', { name: 'Add a finding' }))
    type('Finding 1 Title', 'Drop me')
    fireEvent.click(screen.getByRole('button', { name: 'Remove finding 1' }))
    const open = await findConfirm()
    expect(open.title).toBe('Remove finding')
    open.accept()
    await waitFor(() => expect(screen.queryByRole('group', { name: 'Finding 1' })).toBeNull())
  })

  it("shows the server's refusal and keeps the form", async () => {
    api.createCapabilityTest.mockRejectedValue(new Error('TESTCONTROL-99 is not a control this organisation has in scope'))
    const { onClose } = renderModal()
    fillTest()
    type('Result', 'failed')
    fireEvent.click(screen.getByRole('button', { name: 'Add a finding' }))
    type('Finding 1 Title', 'Gap')
    fireEvent.click(screen.getByRole('button', { name: 'Record test' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('TESTCONTROL-99 is not a control this organisation has in scope')
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue('  Ransomware tabletop  ')
  })

  it('is read-only and cannot be closed while it saves', async () => {
    let resolve!: (v: unknown) => void
    api.createCapabilityTest.mockReturnValue(new Promise((r) => (resolve = r)))
    const { onClose } = renderModal()
    fillTest()
    fireEvent.click(screen.getByRole('button', { name: 'Record test' }))

    await screen.findByRole('button', { name: 'Saving…' })
    expect(field('Scenario')).toHaveAttribute('readonly')
    expect(field('Outcome')).toHaveAttribute('readonly')
    expect(screen.getByRole('button', { name: 'Close' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()

    resolve({})
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
  })

  it('ignores Escape and a backdrop click once anything is typed', () => {
    const { onClose } = renderModal()
    type('Title', 'x')
    fireEvent.keyDown(document, { key: 'Escape' })
    fireEvent.click(document.querySelector('.modal-overlay')!)
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('closes on Escape while untouched', () => {
    const { onClose } = renderModal()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

describe('CapabilityTestFormModal: correcting (PIG R11)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clockAt('Europe/London', '2026-10-13T12:00:00Z')
    api.updateCapabilityTest.mockResolvedValue({})
    api.getOrgMembers.mockResolvedValue([])
    api.getRiskAssessments.mockResolvedValue([])
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllEnvs()
  })

  it('sends only the fields that changed', async () => {
    const { onClose, invalidate } = renderModal({ test: { ...TEST, result: 'failed', finding_count: 2, open_finding_count: 1 } })
    const dialog = screen.getByRole('dialog', { name: 'Correct capability test' })
    expect(within(dialog).queryByRole('button', { name: 'Add a finding' })).toBeNull()
    const save = screen.getByRole('button', { name: 'Save' })
    expect(save).toBeDisabled()

    type('Outcome', ' Failed over in 55 minutes ')
    type('Result', 'passed_with_findings')
    fireEvent.click(save)

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    expect(api.updateCapabilityTest).toHaveBeenCalledWith('org-one', 'ct-1', {
      outcome: 'Failed over in 55 minutes',
      result: 'passed_with_findings',
    })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['capability-tests', 'org-one'] })
  })

  it('counts the findings recorded when the result changes', () => {
    renderModal({ test: { ...TEST, result: 'failed', finding_count: 2, open_finding_count: 2 } })
    type('Result', 'passed')
    expect(
      screen.getByText('A test with 2 finding(s) did not simply pass: record it as passed_with_findings or failed'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it('leaves the result rule alone when the result is not corrected', async () => {
    // Its findings all cancelled: the server checks the rule only when the result changes.
    const { onClose } = renderModal({ test: { ...TEST, result: 'failed', finding_count: 0, open_finding_count: 0 } })
    type('Scenario', 'Primary region lost')
    expect(screen.queryByText(/needs at least one finding/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.updateCapabilityTest).toHaveBeenCalledWith('org-one', 'ct-1', { scenario: 'Primary region lost' })
  })

  it('names a cleared field in a correction (L2)', () => {
    renderModal({ test: TEST })
    type('Scenario', '')
    expect(screen.getByText('Scenario is required')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it("words the hint for a test that failed but has no findings that count (L3)", () => {
    // Its only finding was cancelled.
    renderModal({ test: { ...TEST, result: 'failed', finding_count: 0, open_finding_count: 0 } })
    type('Result', 'passed_with_findings')
    expect(screen.getByText('A test that passed with findings needs at least one finding')).toBeInTheDocument()
    expect(screen.getByText('This test has no findings that count: add one with Add finding first.')).toBeInTheDocument()
    expect(screen.queryByText(/A test recorded as passed/)).toBeNull()
  })

  it('points a passed test with no findings to Add finding', () => {
    renderModal({ test: TEST })
    type('Result', 'failed')
    expect(screen.getByText('A test that failed needs at least one finding')).toBeInTheDocument()
    expect(screen.getByText(/corrected with its first finding: use Add finding/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it("does not hold an unchanged day against the reader's calendar", async () => {
    // Dated the server's 14th; read at 22:00 EDT on the 13th.
    clockAt('America/New_York', '2026-10-14T02:00:00Z')
    const { onClose } = renderModal({ test: { ...TEST, tested_on: '2026-10-14' } })
    type('Title', 'DR failover, corrected')
    expect(screen.queryByText(/cannot be in the future/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Save' })).not.toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.updateCapabilityTest).toHaveBeenCalledWith('org-one', 'ct-1', { title: 'DR failover, corrected' })
  })
})
