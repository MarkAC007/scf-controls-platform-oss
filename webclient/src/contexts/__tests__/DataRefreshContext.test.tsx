import { act, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactElement, ReactNode } from 'react'

import {
  DataRefreshProvider,
  useDataRefresh,
  type DataRefreshValue,
} from '../DataRefreshContext'
import { apiClient, clearCurrentOrganizationCache } from '../../data/apiClient'

vi.mock('../../data/apiClient', () => ({
  apiClient: { get: vi.fn() },
  clearCurrentOrganizationCache: vi.fn(),
}))

vi.mock('../OrganizationContext', () => ({
  useOrganization: () => ({ currentOrg: { id: 'org-1', name: 'Org' } }),
}))

const mockGet = vi.mocked(apiClient.get)
const mockClearCurrentOrganizationCache = vi.mocked(clearCurrentOrganizationCache)

function setVisibilityState(visibilityState: DocumentVisibilityState): void {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => visibilityState,
  })
}

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
      mutations: {
        retry: false,
      },
    },
  })
}

function createWrapper(
  queryClient: QueryClient,
  pollIntervalMs = 1000
): ({ children }: { children: ReactNode }) => ReactElement {
  return function Wrapper({ children }: { children: ReactNode }): ReactElement {
    return (
      <QueryClientProvider client={queryClient}>
        <DataRefreshProvider pollIntervalMs={pollIntervalMs}>
          {children}
        </DataRefreshProvider>
      </QueryClientProvider>
    )
  }
}

async function flushAsyncWork(): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0)
  })
}

describe('DataRefreshProvider', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-08T12:00:00Z'))
    vi.clearAllMocks()
    setVisibilityState('visible')
    mockGet.mockResolvedValue({ cursor: 'cursor-1', count: 1 })
  })

  afterEach(() => {
    setVisibilityState('visible')
    vi.useRealTimers()
  })

  it('refresh bumps epoch, clears organization cache, and invalidates queries', async () => {
    const queryClient = createQueryClient()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    const { result } = renderHook((): DataRefreshValue => useDataRefresh(), {
      wrapper: createWrapper(queryClient),
    })

    expect(result.current.epoch).toBe(0)

    act(() => {
      result.current.refresh()
    })

    expect(result.current.epoch).toBe(1)
    expect(mockClearCurrentOrganizationCache).toHaveBeenCalledTimes(1)
    expect(invalidateSpy).toHaveBeenCalled()
    await flushAsyncWork()
  })

  it('uses the first successful poll as the baseline without raising updates', async () => {
    const queryClient = createQueryClient()
    const { result } = renderHook((): DataRefreshValue => useDataRefresh(), {
      wrapper: createWrapper(queryClient),
    })

    await flushAsyncWork()

    expect(mockGet).toHaveBeenCalledWith('/organizations/org-1/changes/cursor')
    expect(result.current.updatesAvailable).toBe(false)
  })

  it('sets updates available and invalidates queries when a later cursor changes', async () => {
    mockGet
      .mockResolvedValueOnce({ cursor: 'cursor-1', count: 1 })
      .mockResolvedValueOnce({ cursor: 'cursor-2', count: 1 })
    const queryClient = createQueryClient()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    const { result } = renderHook((): DataRefreshValue => useDataRefresh(), {
      wrapper: createWrapper(queryClient),
    })

    await flushAsyncWork()

    expect(result.current.updatesAvailable).toBe(false)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })

    expect(result.current.updatesAvailable).toBe(true)
    expect(invalidateSpy).toHaveBeenCalled()
  })

  it('clears updates available after refresh acknowledges the latest seen cursor', async () => {
    mockGet
      .mockResolvedValueOnce({ cursor: 'cursor-1', count: 1 })
      .mockResolvedValueOnce({ cursor: 'cursor-2', count: 1 })
    const queryClient = createQueryClient()
    const { result } = renderHook((): DataRefreshValue => useDataRefresh(), {
      wrapper: createWrapper(queryClient),
    })

    await flushAsyncWork()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })

    expect(result.current.updatesAvailable).toBe(true)

    act(() => {
      result.current.refresh()
    })

    expect(result.current.updatesAvailable).toBe(false)
  })

  it('stops polling after a 404 cursor endpoint error', async () => {
    const missingEndpointError = new Error('Not Found') as Error & { status?: number }
    missingEndpointError.status = 404
    mockGet.mockRejectedValue(missingEndpointError)
    const queryClient = createQueryClient()

    renderHook((): DataRefreshValue => useDataRefresh(), {
      wrapper: createWrapper(queryClient),
    })

    await flushAsyncWork()
    expect(mockGet).toHaveBeenCalledTimes(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000)
    })

    expect(mockGet).toHaveBeenCalledTimes(1)
  })

  it('does not poll while the document is hidden', async () => {
    setVisibilityState('hidden')
    const queryClient = createQueryClient()

    renderHook((): DataRefreshValue => useDataRefresh(), {
      wrapper: createWrapper(queryClient),
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })

    expect(mockGet).not.toHaveBeenCalled()
  })

  it('refreshes from the plain r keyboard shortcut', async () => {
    const queryClient = createQueryClient()
    const { result } = renderHook((): DataRefreshValue => useDataRefresh(), {
      wrapper: createWrapper(queryClient),
    })

    expect(result.current.epoch).toBe(0)

    act(() => {
      fireEvent.keyDown(window, { key: 'r' })
    })

    expect(result.current.epoch).toBe(1)
    await flushAsyncWork()
  })

  it('does not refresh from r when the event target is an input', async () => {
    const queryClient = createQueryClient()

    function Probe(): ReactElement {
      const { epoch } = useDataRefresh()

      return (
        <>
          <span data-testid="epoch">{epoch}</span>
          <input aria-label="Filter" />
        </>
      )
    }

    render(
      <QueryClientProvider client={queryClient}>
        <DataRefreshProvider pollIntervalMs={1000}>
          <Probe />
        </DataRefreshProvider>
      </QueryClientProvider>
    )

    fireEvent.keyDown(screen.getByLabelText('Filter'), { key: 'r' })

    expect(screen.getByTestId('epoch')).toHaveTextContent('0')
    await flushAsyncWork()
  })
})
