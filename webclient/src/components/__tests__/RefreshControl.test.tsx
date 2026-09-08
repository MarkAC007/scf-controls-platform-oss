import { fireEvent, render, screen, type RenderResult } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import RefreshControl from '../RefreshControl'
import {
  useDataRefresh,
  type DataRefreshValue,
} from '../../contexts/DataRefreshContext'

vi.mock('../../contexts/DataRefreshContext', () => ({ useDataRefresh: vi.fn() }))

const mockUseDataRefresh = vi.mocked(useDataRefresh)

function renderRefreshControl(value: Partial<DataRefreshValue> = {}): RenderResult {
  mockUseDataRefresh.mockReturnValue({
    epoch: 0,
    lastUpdatedAt: Date.now(),
    updatesAvailable: false,
    isRefreshing: false,
    refresh: vi.fn(),
    ...value,
  })

  return render(<RefreshControl />)
}

describe('RefreshControl', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-08T12:00:00Z'))
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders just now when the last update is current', () => {
    renderRefreshControl({ lastUpdatedAt: Date.now() })

    expect(screen.getByText('Updated just now')).toBeInTheDocument()
  })

  it('renders seconds when the last update was under a minute ago', () => {
    renderRefreshControl({ lastUpdatedAt: Date.now() - 45000 })

    expect(screen.getByText('Updated 45s ago')).toBeInTheDocument()
  })

  it('renders the updates available state with dot and root modifier', () => {
    const { container } = renderRefreshControl({
      updatesAvailable: true,
    })

    const caption = screen.getByText('Updates available')
    expect(caption).toBeInTheDocument()
    expect(container.querySelector('.refresh-control-dot')).not.toBeNull()
    expect(caption.closest('.refresh-control')).toHaveClass('refresh-control--updates')
  })

  it('calls refresh when the button is clicked', () => {
    const refresh = vi.fn()
    renderRefreshControl({ refresh })

    fireEvent.click(screen.getByRole('button', { name: 'Refresh data' }))

    expect(refresh).toHaveBeenCalledTimes(1)
  })

  it('stays clickable while refreshing, and says so', () => {
    const refresh = vi.fn()
    renderRefreshControl({ isRefreshing: true, refresh })

    const button = screen.getByRole('button', { name: 'Refresh data' })

    // Not disabled on purpose. With refetchOnWindowFocus on globally,
    // useIsFetching() is non-zero the moment a user returns to the tab —
    // exactly when they reach for this button. Disabling it there would make
    // the control feel broken in its main scenario.
    expect(button).toBeEnabled()
    expect(button).toHaveAttribute('aria-busy', 'true')
    expect(button).toHaveAttribute('data-refreshing', 'true')

    fireEvent.click(button)
    expect(refresh).toHaveBeenCalledTimes(1)
  })

  it('exposes the refresh button by accessible name', () => {
    renderRefreshControl()

    expect(screen.getByRole('button', { name: 'Refresh data' })).toBeInTheDocument()
  })
})
