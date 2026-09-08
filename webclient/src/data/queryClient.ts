import { QueryClient } from '@tanstack/react-query'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000, // 5 minutes
      gcTime: 10 * 60 * 1000, // 10 minutes (formerly cacheTime)
      // Refetch when the user comes back to the tab. This is the whole
      // "I drove Claude over MCP in another window, now I'm looking at the
      // browser" scenario, and it costs nothing: React Query keeps the old
      // data on screen until the new data lands. Per-hook overrides that
      // turned this off were removed with it — a single default is the point.
      refetchOnWindowFocus: true,
      retry: 1,
    },
  },
})
