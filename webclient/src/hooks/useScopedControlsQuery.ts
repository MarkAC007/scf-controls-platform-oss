import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
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
