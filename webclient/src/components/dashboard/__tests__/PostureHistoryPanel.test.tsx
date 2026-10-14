import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ConfirmProvider, answerConfirm } from '../../../__tests__/confirmDialog'
import PostureHistoryPanel from '../PostureHistoryPanel'
import type { PostureSnapshot } from '../../../types'

const state = vi.hoisted(() => ({ editor: true }))
const api = vi.hoisted(() => ({ listPostureSnapshots: vi.fn(), createPostureSnapshot: vi.fn() }))

vi.mock('../../../data/apiClient', () => api)
vi.mock('../../../hooks/useHasOrgRole', () => ({ useIsOrgEditor: () => state.editor }))
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const snap = (period: string, over: Partial<PostureSnapshot['counts']> = {}, trigger: PostureSnapshot['trigger'] = 'scheduled'): PostureSnapshot => ({
  id: `snap-${period}`,
  organization_id: 'org-one',
  period,
  taken_at: `${period.slice(0, 7)}-01T00:05:00+00:00`,
  trigger,
  taken_by_user_id: null,
  counts: {
    in_scope: 40,
    status: { implemented: 10, in_progress: 20, not_started: 10 },
    basis: { mcr: 12, dsr: 8, none: 20 },
    materiality: { material: 6, not_material: 34 },
    deficiencies: { open: 5, material_open: 2, overdue: 1, without_due_date: 1 },
    ...over,
  },
})

/** Sets the browser's zone and clock. Node re-reads TZ when it is assigned; the check proves Date follows it. */
function clockAt(zone: string, utc: string) {
  vi.stubEnv('TZ', zone)
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(utc))
  const hour = new Intl.DateTimeFormat('en-US', { timeZone: zone, hour: 'numeric', hourCycle: 'h23' }).format(new Date())
  expect(new Date().getHours()).toBe(Number(hour))
}

function renderPanel(onOpenDocuments = vi.fn(), client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const view = render(
    <QueryClientProvider client={client}>
      <ConfirmProvider>
        <PostureHistoryPanel organizationId="org-one" onOpenDocuments={onOpenDocuments} />
      </ConfirmProvider>
    </QueryClientProvider>,
  )
  return { invalidate, onOpenDocuments, unmount: view.unmount }
}

describe('PostureHistoryPanel (PIG R10)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state.editor = true
    api.listPostureSnapshots.mockResolvedValue([
      snap('2025-10', { in_scope: 42, status: { not_started: 28, implemented: 14 } }),
      snap('2025-09'),
    ])
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllEnvs()
    vi.restoreAllMocks()
  })

  it('lists the snapshots newest first, one row a month, with the figures that moved', async () => {
    renderPanel()
    const table = await screen.findByRole('table', { name: 'Posture by month' })
    expect(api.listPostureSnapshots).toHaveBeenCalledWith('org-one')
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((r) => within(r).getAllByRole('cell')[0].textContent)).toEqual(['October 2025', 'September 2025'])
    expect(within(rows[0]).getAllByRole('cell').map((c) => c.textContent)).toEqual([
      'October 2025', '42', '14 (33%)', '6', '5 (2 material)', '1',
    ])
    expect(within(rows[1]).getByText('10 (25%)')).toBeInTheDocument()
  })

  it('breaks the latest snapshot down in full', async () => {
    renderPanel()
    const latest = await screen.findByRole('group', { name: 'Latest: October 2025' })
    expect(within(latest).getByText('October 2025')).toBeInTheDocument()
    expect(within(latest).getByText(/taken Oct 1, 2025 by the monthly schedule/)).toBeInTheDocument()
    expect(within(latest).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Implemented: 14',
      'Not started: 28',
    ])
    expect(within(latest).getByText('MCR 12 · DSR 8 · none 20')).toBeInTheDocument()
    expect(within(latest).getByText('5 open · 2 material · 1 overdue · 1 without a due date')).toBeInTheDocument()
  })

  it('reads a period the server sends as a full date the same way', async () => {
    api.listPostureSnapshots.mockResolvedValue([snap('2025-10-01')])
    renderPanel()
    expect(await screen.findByRole('group', { name: 'Latest: October 2025' })).toBeInTheDocument()
  })

  it('says when there is no snapshot yet', async () => {
    api.listPostureSnapshots.mockResolvedValue([])
    renderPanel()
    expect(await screen.findByText(/No posture snapshot yet/)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('gives the date it was taken as the server dated it, wherever the reader is', async () => {
    clockAt('America/New_York', '2025-10-15T12:00:00Z')
    renderPanel()
    const latest = await screen.findByRole('group', { name: 'Latest: October 2025' })
    // Taken 00:05 UTC on 1 October, which is the evening of 30 September in New York.
    expect(within(latest).getByText(/taken Oct 1, 2025 by the monthly schedule/)).toBeInTheDocument()
  })

  it("lets an editor take this month's snapshot, once confirmed, and refreshes the list", async () => {
    clockAt('UTC', '2026-11-15T12:00:00Z')
    api.createPostureSnapshot.mockResolvedValue(snap('2026-11', {}, 'manual'))
    const { invalidate } = renderPanel()
    const take = await screen.findByRole('button', { name: "Take this month's snapshot" })

    fireEvent.click(take)
    expect(await answerConfirm(false)).toMatch(/November 2026 now\? .*cannot be replaced or removed/)
    expect(api.createPostureSnapshot).not.toHaveBeenCalled()

    fireEvent.click(take)
    await answerConfirm(true)
    await waitFor(() => expect(api.createPostureSnapshot).toHaveBeenCalledWith('org-one'))
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['posture-snapshots', 'org-one'] }))
  })

  it('does not offer a second snapshot for a month that has one', async () => {
    clockAt('UTC', '2026-11-15T12:00:00Z')
    api.listPostureSnapshots.mockResolvedValue([snap('2026-11')])
    renderPanel()
    await screen.findByRole('table', { name: 'Posture by month' })
    expect(screen.queryByRole('button', { name: "Take this month's snapshot" })).not.toBeInTheDocument()
  })

  it("knows this month's snapshot when the server sends its period as a full date", async () => {
    clockAt('UTC', '2026-11-15T12:00:00Z')
    api.listPostureSnapshots.mockResolvedValue([snap('2026-11-01')])
    renderPanel()
    await screen.findByRole('table', { name: 'Posture by month' })
    expect(screen.queryByRole('button', { name: "Take this month's snapshot" })).not.toBeInTheDocument()
  })

  // The server takes the month it is in UTC; the button and its confirm go by the same month.
  it('at 00:30 BST on 1 October, knows the server is still in September', async () => {
    clockAt('Europe/London', '2026-09-30T23:30:00Z')
    api.listPostureSnapshots.mockResolvedValue([snap('2026-09')])
    renderPanel()
    await screen.findByRole('table', { name: 'Posture by month' })
    expect(screen.queryByRole('button', { name: "Take this month's snapshot" })).not.toBeInTheDocument()
  })

  it('on the evening of 31 October in New York, offers November, the month the server is in', async () => {
    clockAt('America/New_York', '2026-11-01T00:30:00Z')
    api.listPostureSnapshots.mockResolvedValue([snap('2026-10')])
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: "Take this month's snapshot" }))
    expect(await answerConfirm(false)).toMatch(/^Take the posture snapshot for November 2026 now\?/)
  })

  it('reads the list again when the server refuses because the month is taken, so the offer goes', async () => {
    clockAt('UTC', '2026-11-15T12:00:00Z')
    api.listPostureSnapshots
      .mockResolvedValueOnce([snap('2025-10')])
      .mockResolvedValue([snap('2026-11', {}, 'scheduled'), snap('2025-10')])
    api.createPostureSnapshot.mockRejectedValue(new Error('A posture snapshot for 2026-11 already exists'))
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: "Take this month's snapshot" }))
    await answerConfirm(true)
    expect(await screen.findByRole('alert')).toHaveTextContent('already exists')
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: "Take this month's snapshot" })).not.toBeInTheDocument(),
    )
    expect(await screen.findByRole('group', { name: 'Latest: November 2026' })).toBeInTheDocument()
  })

  it('lets the keyboard scroll the table, in a region that says what it holds', async () => {
    renderPanel()
    const scroller = await screen.findByRole('region', { name: 'Posture by month, scrollable' })
    expect(scroller).toHaveAttribute('tabindex', '0')
    expect(within(scroller).getByRole('table', { name: 'Posture by month' })).toBeInTheDocument()
  })

  it('does not offer a viewer the snapshot', async () => {
    state.editor = false
    renderPanel()
    await screen.findByRole('table', { name: 'Posture by month' })
    expect(screen.queryByRole('button', { name: "Take this month's snapshot" })).not.toBeInTheDocument()
  })

  it("shows the server's refusal of a snapshot", async () => {
    clockAt('UTC', '2026-11-15T12:00:00Z')
    // Held open, so the button can be seen disabled while the request is in flight.
    let refuse: (error: Error) => void = () => {}
    api.createPostureSnapshot.mockReturnValue(new Promise((_, reject) => { refuse = reject }))
    renderPanel()
    const take = await screen.findByRole('button', { name: "Take this month's snapshot" })
    fireEvent.click(take)
    await answerConfirm(true)
    await waitFor(() => expect(take).toBeDisabled())
    refuse(new Error('API key role viewer insufficient'))
    expect(await screen.findByRole('alert')).toHaveTextContent('API key role viewer insufficient')
    expect(take).toBeEnabled()
  })

  it('says when the snapshots cannot be read', async () => {
    api.listPostureSnapshots.mockRejectedValue(new Error('Not a member of this organization'))
    renderPanel()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not load the posture snapshots: Not a member of this organization',
    )
  })

  it('points to the Governance Oversight Report in Generated Documents rather than generating it here', async () => {
    const { onOpenDocuments } = renderPanel()
    await screen.findByRole('table', { name: 'Posture by month' })
    expect(screen.getByText(/Governance Oversight Report/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Open Generated Documents' }))
    expect(onOpenDocuments).toHaveBeenCalledTimes(1)
  })

  it('reads the snapshots afresh on every visit, since the schedule takes them unseen', async () => {
    // The app's own default: data stays fresh for five minutes unless a query says otherwise.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 5 * 60_000 } } })
    const first = renderPanel(vi.fn(), client)
    await screen.findByRole('table', { name: 'Posture by month' })
    first.unmount()
    renderPanel(vi.fn(), client)
    await waitFor(() => expect(api.listPostureSnapshots).toHaveBeenCalledTimes(2))
  })
})
