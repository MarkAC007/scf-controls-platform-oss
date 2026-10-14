import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getCapabilityTest, listCapabilityTests } from '../data/apiClient'
import type { CapabilityTest, CapabilityTestFilters } from '../types'
import { capabilityTestsRoot, localIsoDate } from './useDeficiencies'

/**
 * A test's finding counts move whenever one of its deficiencies is closed,
 * cancelled or reopened, on any screen; so, like the deficiencies, these are
 * never fresh and refetch on every mount.
 */
const ALWAYS_REFETCH = { staleTime: 0 } as const

/** The organisation's capability tests, most recently run first (PIG R11). */
export function useCapabilityTests(orgId: string | undefined, filters: CapabilityTestFilters = {}) {
  return useQuery<CapabilityTest[]>({
    queryKey: [...capabilityTestsRoot(orgId ?? ''), 'list', filters],
    queryFn: () => listCapabilityTests(orgId!, filters),
    enabled: Boolean(orgId),
    ...ALWAYS_REFETCH,
  })
}

/** One test with its findings, as deficiencies. */
export function useCapabilityTest(orgId: string | undefined, testId: string, enabled = true) {
  return useQuery<CapabilityTest>({
    queryKey: [...capabilityTestsRoot(orgId ?? ''), 'detail', testId],
    queryFn: () => getCapabilityTest(orgId!, testId),
    enabled: Boolean(orgId) && enabled,
    ...ALWAYS_REFETCH,
  })
}

/**
 * The last day a test may be dated. The picker is in the reader's calendar,
 * but the server refuses a day after its own, which is UTC: east of
 * Greenwich, the reader's today starts before the server's, and until then
 * the latest day accepted is the server's.
 */
export function latestTestDay(now: Date = new Date()): string {
  const local = localIsoDate(now)
  const server = now.toISOString().slice(0, 10)
  return server < local ? server : local
}

/**
 * The test a finding's link names (`?test=`), kept in step with the address:
 * read on arrival and on Back or Forward, and dropped on leaving the tab, so
 * returning through the sidebar does not reopen it. `readItem` reads it from
 * a search string; App, which owns the URL, supplies it, defined once so the
 * listener is too.
 */
export function useFocusTestFromUrl(activeTab: string, readItem: (search: string) => string | null): string | null {
  const [item, setItem] = useState<string | null>(() => readItem(window.location.search))
  useEffect(() => {
    if (activeTab !== 'capability-tests') setItem(null)
  }, [activeTab])
  useEffect(() => {
    const onPopState = () => setItem(readItem(window.location.search))
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [readItem])
  return item
}
