import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CapabilityTestsPage from '../CapabilityTestsPage'
import type { CapabilityTest, Deficiency } from '../../../types'

const state = vi.hoisted(() => ({ editor: true }))
const api = vi.hoisted(() => ({
  listCapabilityTests: vi.fn(),
  getCapabilityTest: vi.fn(),
  createCapabilityTest: vi.fn(),
  updateCapabilityTest: vi.fn(),
  addCapabilityTestFinding: vi.fn(),
  updateDeficiency: vi.fn(),
  getOrgMembers: vi.fn(),
  getRiskAssessments: vi.fn(),
  fetchScopedControlsPage: vi.fn(),
}))

vi.mock('../../../data/apiClient', () => api)
vi.mock('../../../hooks/useHasOrgRole', () => ({ useIsOrgEditor: () => state.editor }))
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const make = (over: Partial<CapabilityTest>): CapabilityTest => ({
  id: 'x',
  organization_id: 'org-one',
  test_type: 'incident_response',
  title: 'x',
  tested_on: '2026-10-01',
  scenario: 'A scenario',
  result: 'passed',
  outcome: 'An outcome',
  recorded_by_user_id: 'u-1',
  finding_count: 0,
  open_finding_count: 0,
  created_at: '2026-10-01T23:30:00Z',
  updated_at: '2026-10-01T23:30:00Z',
  ...over,
})

const ALL = [
  make({ id: 't-1', title: 'Ransomware tabletop', result: 'failed', finding_count: 2, open_finding_count: 1, tested_on: '2026-10-10' }),
  make({ id: 't-2', title: 'DR failover', test_type: 'continuity', result: 'passed_with_findings', finding_count: 1, open_finding_count: 1 }),
  make({ id: 't-3', title: 'Phishing drill', test_type: 'other', result: 'passed' }),
]

const finding = (over: Partial<Deficiency>): Deficiency => ({
  id: 'f',
  organization_id: 'org-one',
  title: 'f',
  priority: 'medium',
  status: 'open',
  auto_generated: false,
  source: 'capability_test',
  capability_test_id: 't-1',
  created_at: '2026-10-10T00:00:00Z',
  updated_at: '2026-10-10T00:00:00Z',
  ...over,
})

const FINDINGS = [
  finding({ id: 'f-1', title: 'No out-of-band comms', root_cause: 'Plan assumed email', scf_id: 'TESTCONTROL-01' }),
  finding({ id: 'f-2', title: 'Backups untested', status: 'completed', retest_note: 'Restored' }),
]

function renderPage(props: Partial<Parameters<typeof CapabilityTestsPage>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <CapabilityTestsPage organizationId="org-one" {...props} />
    </QueryClientProvider>,
  )
  return { client }
}

const stat = (id: string) => screen.getByTestId(id)

describe('CapabilityTestsPage (PIG R11)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state.editor = true
    api.listCapabilityTests.mockImplementation(async (_org: string, filters: { test_type?: string }) =>
      filters.test_type ? ALL.filter((t) => t.test_type === filters.test_type) : ALL,
    )
    api.getCapabilityTest.mockImplementation(async (_org: string, id: string) => ({
      ...ALL.find((t) => t.id === id)!,
      findings: id === 't-1' ? FINDINGS : [],
    }))
    api.getOrgMembers.mockResolvedValue([{ id: 'u-1', email: 'ann@example.com', display_name: 'Ann' }])
    api.getRiskAssessments.mockResolvedValue([])
    api.fetchScopedControlsPage.mockResolvedValue({ controls: [], total: 0, offset: 0 })
  })

  it('lists the tests with their type, result, day and findings, and counts them', async () => {
    renderPage()
    const row = await screen.findByTestId('capability-test-t-1')
    expect(row).toHaveTextContent('Ransomware tabletop')
    expect(row).toHaveTextContent('Incident response')
    expect(row).toHaveTextContent('Failed')
    expect(row).toHaveTextContent('Tested Oct 10, 2026')
    expect(row).toHaveTextContent('2 findings, 1 open')
    expect(screen.getByTestId('capability-test-t-3')).toHaveTextContent('No findings')

    expect(stat('capability-stat-total')).toHaveTextContent('3')
    expect(stat('capability-stat-passed')).toHaveTextContent('1')
    expect(stat('capability-stat-with-findings')).toHaveTextContent('1')
    expect(stat('capability-stat-failed')).toHaveTextContent('1')
    expect(stat('capability-stat-open-findings')).toHaveTextContent('2')
  })

  it('asks the server for a type, and filters the result and search on the list', async () => {
    renderPage()
    await screen.findByText('Ransomware tabletop')
    fireEvent.click(screen.getByRole('radio', { name: 'Continuity' }))
    await waitFor(() => expect(api.listCapabilityTests).toHaveBeenCalledWith('org-one', { test_type: 'continuity' }))
    await waitFor(() => expect(screen.queryByText('Ransomware tabletop')).toBeNull())
    expect(screen.getByText('DR failover')).toBeInTheDocument()
    // The counts stay the whole record's.
    expect(stat('capability-stat-total')).toHaveTextContent('3')

    // Back to every type (the unchecked "All" is TYPE's), then passed only.
    fireEvent.click(screen.getByRole('radio', { name: 'All', checked: false }))
    fireEvent.click(screen.getAllByRole('radio', { name: 'Passed' })[0])
    await waitFor(() => expect(screen.queryByText('DR failover')).toBeNull())
    expect(screen.getByText('Phishing drill')).toBeInTheDocument()
  })

  it('searches the title, scenario and outcome', async () => {
    renderPage()
    await screen.findByText('Ransomware tabletop')
    fireEvent.change(screen.getByPlaceholderText(/Search tests/), { target: { value: 'phishing' } })
    expect(screen.queryByText('Ransomware tabletop')).toBeNull()
    expect(screen.getByText('Phishing drill')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText(/Search tests/), { target: { value: 'nothing like this' } })
    expect(screen.getByText('No capability tests match these filters.')).toBeInTheDocument()
  })

  it("opens a test's details: scenario, outcome, who recorded it on the server's day, and its findings with their root cause", async () => {
    renderPage()
    await screen.findByText('Ransomware tabletop')
    const toggle = screen.getByRole('button', { name: 'Details of "Ransomware tabletop"' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')

    const row = screen.getByTestId('capability-test-t-1')
    await within(row).findByText('No out-of-band comms')
    expect(api.getCapabilityTest).toHaveBeenCalledWith('org-one', 't-1')
    expect(row).toHaveTextContent('A scenario')
    expect(row).toHaveTextContent('An outcome')
    // 23:30 UTC on the 1st is the server's 1st, whatever the reader's zone.
    expect(row).toHaveTextContent('Oct 1, 2026 by Ann')
    expect(row).toHaveTextContent('Root cause. Plan assumed email')
    expect(within(row).getByText('Backups untested')).toBeInTheDocument()
    // The finding is on its own test: no link back to it.
    expect(within(row).queryByRole('link', { name: /open its test/ })).toBeNull()
  })

  it('says a recorder who is not a member is outside the organisation, mid-sentence', async () => {
    api.getOrgMembers.mockResolvedValue([])
    renderPage()
    await screen.findByText('Phishing drill')
    fireEvent.click(screen.getByRole('button', { name: 'Details of "Phishing drill"' }))
    await screen.findByText('No findings recorded.')
    expect(screen.getByTestId('capability-test-t-3')).toHaveTextContent('Oct 1, 2026 by a user outside this organisation')
  })

  it('says when a test has no findings', async () => {
    renderPage()
    await screen.findByText('Phishing drill')
    fireEvent.click(screen.getByRole('button', { name: 'Details of "Phishing drill"' }))
    expect(await screen.findByText('No findings recorded.')).toBeInTheDocument()
  })

  it('opens the test a finding links to', async () => {
    renderPage({ focusTestId: 't-2' })
    await screen.findByText('DR failover')
    expect(screen.getByRole('button', { name: 'Details of "DR failover"' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('button', { name: 'Details of "Ransomware tabletop"' })).toHaveAttribute('aria-expanded', 'false')
    await waitFor(() => expect(api.getCapabilityTest).toHaveBeenCalledWith('org-one', 't-2'))
  })

  it('opens the record, correct and add-finding forms for an editor', async () => {
    renderPage()
    await screen.findByText('Ransomware tabletop')
    fireEvent.click(screen.getByRole('button', { name: 'Record test' }))
    expect(screen.getByRole('dialog', { name: 'Record capability test' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    fireEvent.click(screen.getByRole('button', { name: 'Correct "DR failover"' }))
    expect(screen.getByRole('dialog', { name: 'Correct capability test' })).toBeInTheDocument()
    expect(screen.getByLabelText('Title')).toHaveValue('DR failover')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    fireEvent.click(screen.getByRole('button', { name: 'Add finding to "Phishing drill"' }))
    const dialog = screen.getByRole('dialog', { name: 'Add finding' })
    expect(dialog).toHaveTextContent('Phishing drill')
    // Recorded as passed: the finding corrects the result.
    expect(within(dialog).getByLabelText("The test's result becomes")).toBeInTheDocument()
  })

  it("edits a finding with the deficiency's own form", async () => {
    renderPage()
    await screen.findByText('Ransomware tabletop')
    fireEvent.click(screen.getByRole('button', { name: 'Details of "Ransomware tabletop"' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Edit "No out-of-band comms"' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit deficiency' })
    expect(within(dialog).getByLabelText('Root cause')).toHaveValue('Plan assumed email')
  })

  it('shows a viewer the record, without the actions', async () => {
    state.editor = false
    renderPage()
    await screen.findByText('Ransomware tabletop')
    expect(screen.queryByRole('button', { name: 'Record test' })).toBeNull()
    expect(screen.queryByRole('button', { name: /^Correct / })).toBeNull()
    expect(screen.queryByRole('button', { name: /^Add finding/ })).toBeNull()
    expect(screen.getByText('Only an editor or admin can record a test, correct one or add a finding.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Details of "Ransomware tabletop"' }))
    await screen.findByText('No out-of-band comms')
    expect(screen.queryByRole('button', { name: 'Edit "No out-of-band comms"' })).toBeNull()
  })

  it('names tests that share a title apart (N2)', async () => {
    api.listCapabilityTests.mockResolvedValue([
      make({ id: 'd-1', title: 'DR failover', tested_on: '2026-10-10' }),
      make({ id: 'd-2', title: 'DR failover', tested_on: '2026-10-01' }),
      make({ id: 'd-3', title: 'DR failover', tested_on: '2026-10-01' }),
      make({ id: 'u-1', title: 'Phishing drill' }),
    ])
    renderPage()
    await screen.findByText('Phishing drill')
    for (const name of [
      'Details of "DR failover" (tested Oct 10, 2026)',
      'Correct "DR failover" (tested Oct 1, 2026, 1 of 2)',
      'Add finding to "DR failover" (tested Oct 1, 2026, 2 of 2)',
      'Details of "Phishing drill"',
    ]) {
      expect(screen.getByRole('button', { name })).toBeInTheDocument()
    }
    const names = screen.getAllByRole('button', { name: /^Details of / }).map((b) => b.getAttribute('aria-label'))
    expect(new Set(names).size).toBe(names.length)
  })

  it('scrolls to a linked test once, not again when a filter hides and shows it (L4)', async () => {
    // jsdom has no scrollIntoView; the page calls it only where it exists.
    const scroll = vi.fn()
    Element.prototype.scrollIntoView = scroll
    try {
      renderPage({ focusTestId: 't-1' })
      await screen.findByText('Ransomware tabletop')
      await waitFor(() => expect(scroll).toHaveBeenCalledTimes(1))
      expect(scroll.mock.contexts[0]).toBe(screen.getByTestId('capability-test-t-1'))
      fireEvent.click(screen.getByRole('radio', { name: 'Continuity' }))
      await waitFor(() => expect(screen.queryByText('Ransomware tabletop')).toBeNull())
      fireEvent.click(screen.getByRole('radio', { name: 'All', checked: false }))
      await screen.findByText('Ransomware tabletop')
      expect(scroll).toHaveBeenCalledTimes(1)
    } finally {
      delete (Element.prototype as Partial<Element>).scrollIntoView
    }
  })

  it('keeps the counts through a failed refresh, as the list keeps its rows (L5)', async () => {
    const { client } = renderPage()
    await screen.findByText('Ransomware tabletop')
    expect(stat('capability-stat-total')).toHaveTextContent('3')
    api.listCapabilityTests.mockRejectedValue(new Error('Network down'))
    await client.invalidateQueries({ queryKey: ['capability-tests', 'org-one'] })
    expect(await screen.findByText(/Could not refresh the capability tests: Network down/)).toBeInTheDocument()
    expect(stat('capability-stat-total')).toHaveTextContent('3')
    expect(stat('capability-stat-open-findings')).toHaveTextContent('2')
  })

  it('calls the recorder "someone" while the members are unknown (N6)', async () => {
    api.getOrgMembers.mockRejectedValue(new Error('Members down'))
    renderPage()
    await screen.findByText('Phishing drill')
    fireEvent.click(screen.getByRole('button', { name: 'Details of "Phishing drill"' }))
    await screen.findByText('No findings recorded.')
    expect(screen.getByTestId('capability-test-t-3')).toHaveTextContent('Oct 1, 2026 by someone')
  })

  it('says when the tests cannot be loaded', async () => {
    api.listCapabilityTests.mockRejectedValue(new Error('Access denied'))
    renderPage()
    const alerts = await screen.findAllByRole('alert')
    expect(alerts.some((a) => a.textContent?.includes('Could not load the capability tests: Access denied'))).toBe(true)
  })

  it('says when there are none', async () => {
    api.listCapabilityTests.mockResolvedValue([])
    renderPage()
    expect(await screen.findByText(/No capability tests recorded/)).toBeInTheDocument()
  })
})
