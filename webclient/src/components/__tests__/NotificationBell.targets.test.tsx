/**
 * The bell opens the item a notification names.
 *
 * Before `reference_key`, an evidence notification carried the evidence_tracking
 * row UUID and the bell fetched /evidence-tasks to recover the real id. When
 * that row had no tasks the fetch returned [], `tasks[0]` was undefined, and the
 * click did nothing at all — no navigation, no message, dropdown closed.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { NotificationBell } from '../NotificationBell'
import { apiClient } from '../../data/apiClient'

vi.mock('../../data/apiClient', () => ({
  apiClient: { get: vi.fn(), patch: vi.fn() },
}))

const mockGet = vi.mocked(apiClient.get)
const mockPatch = vi.mocked(apiClient.patch)

beforeEach(() => {
  vi.clearAllMocks()
  mockPatch.mockResolvedValue({})
})

function withNotifications(notifications: unknown[]) {
  mockGet.mockResolvedValue({ unread_count: notifications.length, notifications })
}

async function openAndClick(message: string) {
  await waitFor(() => expect(mockGet).toHaveBeenCalled())
  fireEvent.click(screen.getByRole('button', { name: /🔔/ }))
  const row = await screen.findByText(message)
  fireEvent.click(row)
}

describe('NotificationBell target resolution', () => {
  it('opens the evidence item named by an evidence notification', async () => {
    const onNavigateToEvidence = vi.fn()
    withNotifications([
      {
        id: 'n1',
        reference_type: 'evidence',
        reference_id: 'a2c1d0e4-0000-0000-0000-000000000001',
        reference_key: 'E-HRS-16',
        message: 'Alex rejected evidence E-HRS-16',
        is_read: false,
        created_at: new Date().toISOString(),
      },
    ])
    render(<NotificationBell onNavigateToEvidence={onNavigateToEvidence} />)
    await openAndClick('Alex rejected evidence E-HRS-16')
    await waitFor(() => expect(onNavigateToEvidence).toHaveBeenCalledWith('E-HRS-16'))
  })

  it('opens the evidence item named by an overdue-task notification', async () => {
    // The message always named the item; the reference never could reach it.
    const onNavigateToEvidence = vi.fn()
    const onNavigateToTask = vi.fn()
    withNotifications([
      {
        id: 'n2',
        reference_type: 'task',
        reference_id: 'b2c1d0e4-0000-0000-0000-000000000002',
        reference_key: 'E-HRS-16',
        message: 'Evidence collection task for E-HRS-16 is overdue by 4 day(s)',
        is_read: false,
        created_at: new Date().toISOString(),
      },
    ])
    render(
      <NotificationBell
        onNavigateToEvidence={onNavigateToEvidence}
        onNavigateToTask={onNavigateToTask}
      />,
    )
    await openAndClick('Evidence collection task for E-HRS-16 is overdue by 4 day(s)')
    await waitFor(() => expect(onNavigateToEvidence).toHaveBeenCalledWith('E-HRS-16'))
    expect(onNavigateToTask).not.toHaveBeenCalled()
  })

  it('still sends a task with no evidence key to the task list', async () => {
    // Not every task notification is about evidence. This path worked and must
    // keep working.
    const onNavigateToTask = vi.fn()
    withNotifications([
      {
        id: 'n3',
        reference_type: 'task',
        reference_id: 'c2c1d0e4-0000-0000-0000-000000000003',
        reference_key: null,
        message: 'You were assigned a task',
        is_read: false,
        created_at: new Date().toISOString(),
      },
    ])
    render(<NotificationBell onNavigateToTask={onNavigateToTask} />)
    await openAndClick('You were assigned a task')
    await waitFor(() => expect(onNavigateToTask).toHaveBeenCalled())
  })

  it('never fetches evidence-tasks to work out where to go', async () => {
    const onNavigateToEvidence = vi.fn()
    withNotifications([
      {
        id: 'n4',
        reference_type: 'evidence',
        reference_id: 'd2c1d0e4-0000-0000-0000-000000000004',
        reference_key: 'E-BCD-02',
        message: 'Alex rejected evidence E-BCD-02',
        is_read: false,
        created_at: new Date().toISOString(),
      },
    ])
    render(<NotificationBell onNavigateToEvidence={onNavigateToEvidence} />)
    await openAndClick('Alex rejected evidence E-BCD-02')
    await waitFor(() => expect(onNavigateToEvidence).toHaveBeenCalled())
    const paths = mockGet.mock.calls.map(c => String(c[0]))
    expect(paths.some(p => p.includes('/evidence-tasks'))).toBe(false)
  })

  it('says so when the evidence item no longer exists', async () => {
    // This is the case that used to close the dropdown and do nothing.
    withNotifications([
      {
        id: 'n5',
        reference_type: 'evidence',
        reference_id: 'e2c1d0e4-0000-0000-0000-000000000005',
        reference_key: null,
        message: 'Alex rejected evidence E-GONE-01',
        is_read: false,
        created_at: new Date().toISOString(),
      },
    ])
    render(<NotificationBell onNavigateToEvidence={vi.fn()} />)
    await openAndClick('Alex rejected evidence E-GONE-01')
    expect(await screen.findByRole('alert')).toHaveTextContent(/no longer exists/i)
  })

  it('says so for a reference type it has no destination for', async () => {
    // engagement_query fell past every branch: no navigation, dropdown left open,
    // indistinguishable from a missed tap.
    withNotifications([
      {
        id: 'n6',
        reference_type: 'engagement_query',
        reference_id: 'f2c1d0e4-0000-0000-0000-000000000006',
        reference_key: null,
        message: 'A query was raised on your engagement',
        is_read: false,
        created_at: new Date().toISOString(),
      },
    ])
    render(<NotificationBell />)
    await openAndClick('A query was raised on your engagement')
    expect(await screen.findByRole('alert')).toHaveTextContent(/nowhere to open/i)
  })

  it('gives each notification row a keyboard path', async () => {
    const onNavigateToEvidence = vi.fn()
    withNotifications([
      {
        id: 'n7',
        reference_type: 'evidence',
        reference_id: 'g2c1d0e4-0000-0000-0000-000000000007',
        reference_key: 'E-KEY-01',
        message: 'Alex rejected evidence E-KEY-01',
        is_read: false,
        created_at: new Date().toISOString(),
      },
    ])
    render(<NotificationBell onNavigateToEvidence={onNavigateToEvidence} />)
    await waitFor(() => expect(mockGet).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('button', { name: /🔔/ }))
    const row = (await screen.findByText('Alex rejected evidence E-KEY-01')).closest(
      '.notification-item',
    )!
    expect(row).toHaveAttribute('tabindex', '0')
    fireEvent.keyDown(row, { key: 'Enter' })
    await waitFor(() => expect(onNavigateToEvidence).toHaveBeenCalledWith('E-KEY-01'))
  })
})
