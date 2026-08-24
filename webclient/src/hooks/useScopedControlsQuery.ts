import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import type { MemberType } from '../types'
import {
  fetchScopedControlsPage,
  fetchScopedControlStats,
  type PaginatedScopedControlsResponse,
  type ScopedControlWithCatalog,
  type ScopedControlStatsResponse,
} from '../data/apiClient'

const PAGE_SIZE = 50

export interface ScopedControlFilters {
  search?: string
  domain?: string
  csf_function?: string
  control_weighting?: number
  framework?: string
  scope_status?: 'in_scope' | 'out_of_scope' | 'all'
  /**
   * Owning-team filters (#822 phase 3). Both are part of the query key via the
   * ``filters`` object, so changing either resets pagination like any other
   * filter rather than appending a differently-filtered page to the last one.
   */
  team_id?: string
  function_id?: string
  /**
   * Accountable owner's member type (#822 phase 2). Part of the ``filters``
   * object and so part of the query key, which is what makes changing it reset
   * pagination instead of appending a contractor-only page beneath an
   * unfiltered one.
   */
  accountable_owner_type?: MemberType
}

/**
 * Infinite query hook for paginated scoped controls with filtering.
 * Automatically loads more pages as the user scrolls.
 *
 * @param filters - Optional search and filter parameters
 * @param orgId - Optional organization ID (uses default from settings if not provided)
 * @returns React Query infinite query result
 */
export function useScopedControlsQuery(filters: ScopedControlFilters = {}, orgId?: string) {
  return useInfiniteQuery<PaginatedScopedControlsResponse>({
    queryKey: ['scoped-controls', orgId, filters],
    queryFn: async ({ pageParam }) => {
      const offset = typeof pageParam === 'number' ? pageParam : 0
      return fetchScopedControlsPage(
        {
          limit: PAGE_SIZE,
          offset,
          search: filters.search || undefined,
          domain: filters.domain || undefined,
          csf_function: filters.csf_function || undefined,
          control_weighting: filters.control_weighting,
          framework: filters.framework || undefined,
          scope_status: filters.scope_status || undefined,
          team_id: filters.team_id || undefined,
          function_id: filters.function_id || undefined,
          accountable_owner_type: filters.accountable_owner_type || undefined,
        },
        orgId
      )
    },
    initialPageParam: 0,
    getNextPageParam: (lastPage) => {
      const loaded = lastPage.offset + lastPage.controls.length
      // Return next offset if there are more items, otherwise undefined to stop loading
      return loaded < lastPage.total ? loaded : undefined
    },
    staleTime: 5 * 60 * 1000, // 5 minutes
    refetchOnWindowFocus: false,
  })
}

/**
 * Hook to fetch server-side aggregated stats for the Control Scoping stats bar.
 * Returns accurate totals regardless of pagination state.
 */
export function useScopedControlsStats(orgId?: string) {
  return useQuery<ScopedControlStatsResponse>({
    queryKey: ['scoped-controls-stats', orgId],
    queryFn: () => fetchScopedControlStats(orgId),
    staleTime: 30 * 1000, // 30 seconds - stats should be fresher than paginated data
    refetchOnWindowFocus: false,
  })
}

/**
 * Helper to flatten paginated scoped control results into a single array
 */
export function flattenScopedControlPages(
  pages: PaginatedScopedControlsResponse[] | undefined
): { controls: ScopedControlWithCatalog[]; total: number } {
  if (!pages || pages.length === 0) {
    return { controls: [], total: 0 }
  }

  const controls = pages.flatMap((page) => page.controls)
  const total = pages[0].total

  return { controls, total }
}
