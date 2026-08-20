/**
 * NotificationBell 'catalog' branch: a catalog reconciliation notification
 * navigates to the catalog changelog and is marked read.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { NotificationBell } from '../NotificationBell'
import { apiClient } from '../../data/apiClient'

vi.mock('../../data/apiClient', () => ({
  apiClient: {
    get: vi.fn(),
    patch: vi.fn(),
  },
}))

const mockGet = vi.mocked(apiClient.get)
const mockPatch = vi.mocked(apiClient.patch)

beforeEach(() => {
  vi.clearAllMocks()
  mockPatch.mockResolvedValue({})
})

function withNotifications(notifications: unknown[]) {
  mockGet.mockResolvedValue({
    unread_count: notifications.length,
    notifications,
  })
}

describe('NotificationBell catalog branch', () => {
  it('navigates to the changelog for catalog notifications', async () => {
    withNotifications([
      {
        id: 'notif-1',
        reference_type: 'catalog',
        reference_id: 'recon-run-1',
        message: 'Your organisation was reconciled to catalog 2026.2',
        is_read: false,
        created_at: '2026-08-20T10:00:00Z',
      },
    ])
    const onNavigateToChangelog = vi.fn()
    render(<NotificationBell onNavigateToChangelog={onNavigateToChangelog} />)

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith('/notifications?limit=10')
    })

    fireEvent.click(screen.getByRole('button'))
    const item = await screen.findByText(/reconciled to catalog 2026\.2/)
    fireEvent.click(item)

    await waitFor(() => {
      expect(onNavigateToChangelog).toHaveBeenCalledTimes(1)
    })
    // unread → marked as read first
    expect(mockPatch).toHaveBeenCalledWith('/notifications/notif-1/read', {})
  })

  it('leaves other reference types on their existing branches', async () => {
    withNotifications([
      {
        id: 'notif-2',
        reference_type: 'task',
        reference_id: 'task-1',
        message: 'Task assigned',
        is_read: true,
        created_at: '2026-08-20T10:00:00Z',
      },
    ])
    const onNavigateToChangelog = vi.fn()
    const onNavigateToTask = vi.fn()
    render(
      <NotificationBell
        onNavigateToChangelog={onNavigateToChangelog}
        onNavigateToTask={onNavigateToTask}
      />
    )

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalled()
    })

    fireEvent.click(screen.getByRole('button'))
    fireEvent.click(await screen.findByText('Task assigned'))

    await waitFor(() => {
      expect(onNavigateToTask).toHaveBeenCalledTimes(1)
    })
    expect(onNavigateToChangelog).not.toHaveBeenCalled()
  })
})
