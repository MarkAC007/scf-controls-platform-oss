import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from 'react'
import { useIsFetching, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { apiClient, clearCurrentOrganizationCache } from '../data/apiClient'
import { useOrganization } from './OrganizationContext'

export interface DataRefreshValue {
  epoch: number
  lastUpdatedAt: number | null
  updatesAvailable: boolean
  isRefreshing: boolean
  refresh: () => void
}

interface CursorSnapshot {
  cursor: string | null
  count: number
}

const DataRefreshContext = createContext<DataRefreshValue | undefined>(undefined)

function isCursorSnapshot(value: unknown): value is CursorSnapshot {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return false
  }

  const payload = value as { cursor?: unknown; count?: unknown }
  const cursorIsValid = typeof payload.cursor === 'string' || payload.cursor === null
  const countIsValid = typeof payload.count === 'number' && Number.isFinite(payload.count)

  return cursorIsValid && countIsValid
}

function isMissingCursorEndpointError(error: unknown): boolean {
  if (typeof error !== 'object' || error === null) {
    return false
  }

  const status = (error as { status?: unknown }).status
  if (status === 404) {
    return true
  }

  const message = (error as { message?: unknown }).message
  if (typeof message === 'string') {
    return /\b404\b/.test(message)
  }

  return false
}

function targetBlocksShortcutRefresh(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false
  }

  const tagName = target.tagName.toLowerCase()
  if (tagName === 'input' || tagName === 'textarea' || tagName === 'select') {
    return true
  }

  return target.isContentEditable
}

function invalidateAllQueries(queryClient: QueryClient): void {
  void queryClient.invalidateQueries().catch(() => {
    // Refresh is user-facing state, while query invalidation has its own retry/refetch paths.
    // Keep the synchronous refresh action from throwing on a cache-layer failure.
  })
}

export function DataRefreshProvider({
  children,
  pollIntervalMs = 20000,
}: {
  children: ReactNode
  pollIntervalMs?: number
}): ReactElement {
  const queryClient = useQueryClient()
  const isRefreshing = useIsFetching() > 0
  const { currentOrg } = useOrganization()
  const currentOrgId = currentOrg?.id ?? null

  const [epoch, setEpoch] = useState(0)
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null)
  const [updatesAvailable, setUpdatesAvailable] = useState(false)

  const currentOrgIdRef = useRef<string | null>(currentOrgId)
  const baselineCursorRef = useRef<string | null>(null)
  const baselineCountRef = useRef<number | null>(null)
  const latestSeenRef = useRef<CursorSnapshot | null>(null)

  const refresh = useCallback(() => {
    setEpoch(previousEpoch => previousEpoch + 1)
    clearCurrentOrganizationCache()
    invalidateAllQueries(queryClient)
    setLastUpdatedAt(currentOrgIdRef.current === null ? null : Date.now())
    setUpdatesAvailable(false)

    const latestSeen = latestSeenRef.current
    if (latestSeen === null) {
      baselineCursorRef.current = null
      baselineCountRef.current = null
      return
    }

    baselineCursorRef.current = latestSeen.cursor
    baselineCountRef.current = latestSeen.count
  }, [queryClient])

  useEffect(() => {
    currentOrgIdRef.current = currentOrgId
    baselineCursorRef.current = null
    baselineCountRef.current = null
    latestSeenRef.current = null
    setUpdatesAvailable(false)
    setLastUpdatedAt(currentOrgId === null ? null : Date.now())
  }, [currentOrgId])

  useEffect(() => {
    if (currentOrgId === null) {
      return () => {
        // No interval or listener is registered without an org.
      }
    }

    let isDisposed = false
    let stoppedForMissingEndpoint = false
    let intervalId: ReturnType<typeof setInterval> | null = null

    const clearPollInterval = (): void => {
      if (intervalId !== null) {
        clearInterval(intervalId)
        intervalId = null
      }
    }

    const applySnapshot = (snapshot: CursorSnapshot): void => {
      latestSeenRef.current = snapshot

      if (baselineCountRef.current === null) {
        baselineCursorRef.current = snapshot.cursor
        baselineCountRef.current = snapshot.count
        setUpdatesAvailable(false)
        return
      }

      if (
        snapshot.cursor !== baselineCursorRef.current ||
        snapshot.count !== baselineCountRef.current
      ) {
        setUpdatesAvailable(true)
        invalidateAllQueries(queryClient)
      }
    }

    const pollCursor = async (): Promise<void> => {
      try {
        // apiClient.get does not expose AbortSignal or a timeout. The await can last
        // as long as the browser request lasts, so stale guards below prevent late writes.
        const payload = await apiClient.get<unknown>(
          `/organizations/${currentOrgId}/changes/cursor`
        )

        if (isDisposed || currentOrgIdRef.current !== currentOrgId) {
          return
        }

        if (!isCursorSnapshot(payload)) {
          // The endpoint returns untrusted JSON; malformed payloads are ignored so one
          // bad response cannot corrupt the acknowledgement baseline.
          return
        }

        applySnapshot(payload)
      } catch (error) {
        if (isDisposed || currentOrgIdRef.current !== currentOrgId) {
          return
        }

        if (isMissingCursorEndpointError(error)) {
          stoppedForMissingEndpoint = true
          clearPollInterval()
          return
        }

        // Transient network and 5xx errors are deliberately swallowed; the next bounded
        // interval tick is the retry mechanism.
      }
    }

    const runPoll = (): void => {
      if (
        isDisposed ||
        stoppedForMissingEndpoint ||
        document.visibilityState !== 'visible'
      ) {
        return
      }

      void pollCursor()
    }

    const startPollInterval = (): void => {
      if (intervalId !== null || stoppedForMissingEndpoint) {
        return
      }

      intervalId = setInterval(runPoll, pollIntervalMs)
    }

    const handleVisibilityChange = (): void => {
      if (document.visibilityState === 'visible') {
        runPoll()
        startPollInterval()
        return
      }

      clearPollInterval()
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)

    if (document.visibilityState === 'visible') {
      runPoll()
      startPollInterval()
    }

    return () => {
      isDisposed = true
      clearPollInterval()
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [currentOrgId, pollIntervalMs, queryClient])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent): void => {
      if (
        event.key !== 'r' &&
        event.key !== 'R'
      ) {
        return
      }

      if (event.ctrlKey || event.metaKey || event.altKey) {
        return
      }

      if (targetBlocksShortcutRefresh(event.target)) {
        return
      }

      event.preventDefault()
      refresh()
    }

    window.addEventListener('keydown', handleKeyDown)

    return () => {
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [refresh])

  const value = useMemo<DataRefreshValue>(() => ({
    epoch,
    lastUpdatedAt,
    updatesAvailable,
    isRefreshing,
    refresh,
  }), [epoch, lastUpdatedAt, updatesAvailable, isRefreshing, refresh])

  return (
    <DataRefreshContext.Provider value={value}>
      {children}
    </DataRefreshContext.Provider>
  )
}

export function useDataRefresh(): DataRefreshValue {
  const context = useContext(DataRefreshContext)
  if (context === undefined) {
    throw new Error('useDataRefresh must be used within DataRefreshProvider')
  }

  return context
}
