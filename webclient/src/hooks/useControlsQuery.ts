import { useInfiniteQuery } from '@tanstack/react-query'
import { fetchControlsPage, type PaginatedControlsResponse } from '../data/catalogApi'

const PAGE_SIZE = 50

export interface ControlFilters {
  search?: string
  domain?: string
  csf_function?: string
  control_weighting?: number
}

/**
 * Infinite query hook for paginated controls with filtering.
 * Automatically loads more pages as the user scrolls.
 *
 * @param filters - Optional search and filter parameters
 * @returns React Query infinite query result
 */
export function useControlsQuery(filters: ControlFilters = {}) {
  return useInfiniteQuery<PaginatedControlsResponse>({
    queryKey: ['controls', filters],
    queryFn: async ({ pageParam }) => {
      const offset = typeof pageParam === 'number' ? pageParam : 0
      return fetchControlsPage({
        limit: PAGE_SIZE,
        offset,
        search: filters.search || undefined,
        domain: filters.domain || undefined,
        csf_function: filters.csf_function || undefined,
        control_weighting: filters.control_weighting,
      })
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
 * Helper to flatten paginated results into a single array
 */
export function flattenControlPages(
  pages: PaginatedControlsResponse[] | undefined
): { controls: PaginatedControlsResponse['controls']; total: number } {
  if (!pages || pages.length === 0) {
    return { controls: [], total: 0 }
  }

  const controls = pages.flatMap((page) => page.controls)
  const total = pages[0].total

  return { controls, total }
}
