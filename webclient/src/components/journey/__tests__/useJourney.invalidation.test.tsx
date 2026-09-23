/**
 * The Journey is refreshed by writes it has never heard of.
 *
 * Every Journey gate is a live count over the operational tables, so any
 * successful write can move one. Asking each write to remember that had failed
 * almost everywhere: of the write paths that move a gate, only the Journey's
 * own two invalidated it, and the screen served a cached answer to anyone who
 * came back inside the stale window.
 *
 * So the property under test is structural, not per-callsite: an **arbitrary,
 * unrelated** mutation — one with no Journey key anywhere in it, standing in
 * for the mutation someone writes next year — must refresh the Journey. A test
 * that named the evidence upload would pass while the class stayed open.
 *
 * Note what is deliberately absent: no timers are advanced. The Journey query
 * has just resolved and the app's `staleTime` has not elapsed, so a refetch
 * here cannot be expiry. Only invalidation explains it.
 */
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider, useMutation } from '@tanstack/react-query'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getJourney = vi.fn(async () => ({
  provisioned: true,
  activated: true,
  practitioner: null,
  name: 'Your compliance journey',
  description: null,
  template_key: 'default',
  template_version: '1',
  current_stage_key: null,
  stages: [],
  focus: [],
}))

vi.mock('../../../data/apiClient', () => ({
  getJourney: (...args: unknown[]) => getJourney(...(args as [])),
  importJourney: vi.fn(),
  attestJourneyStage: vi.fn(),
}))

// Imported after the mock so the hook binds to it. `queryClient` is the client
// the app actually constructs — the production wiring, not a rebuild of it.
const { useJourney } = await import('../../../hooks/useJourney')
const { queryClient: appQueryClient } = await import('../../../data/queryClient')

function wrapperFor(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
}

/** The Journey, plus a mutation that knows nothing about it. */
function useProbe() {
  const journey = useJourney('org-1')
  const unrelated = useMutation({ mutationFn: async () => ({ ok: true }) })
  return { journey, unrelated }
}

async function runSequence(client: QueryClient) {
  const { result } = renderHook(() => useProbe(), { wrapper: wrapperFor(client) })
  await waitFor(() => expect(result.current.journey.isSuccess).toBe(true))
  expect(getJourney).toHaveBeenCalledTimes(1)
  await act(async () => {
    await result.current.unrelated.mutateAsync()
  })
  return result
}

beforeEach(() => {
  getJourney.mockClear()
  appQueryClient.clear()
})

afterEach(() => {
  cleanup()
  appQueryClient.clear()
})

describe('any successful mutation refreshes the Journey', () => {
  it('refetches after a mutation that names no Journey key', async () => {
    await runSequence(appQueryClient)
    await waitFor(() => expect(getJourney).toHaveBeenCalledTimes(2))
  })

  // The control. Without it the case above could pass for the wrong reason —
  // a remount refetch, a short staleTime, anything but the subscriber.
  it('does not refetch on a client the subscriber was never registered on', async () => {
    const bare = new QueryClient({
      defaultOptions: { queries: { staleTime: 5 * 60 * 1000, retry: false } },
    })
    await runSequence(bare)
    // Give an invalidation, if one were coming, every chance to land.
    await act(async () => {
      await Promise.resolve()
    })
    expect(getJourney).toHaveBeenCalledTimes(1)
    bare.clear()
  })
})

describe('the hook takes the app-wide cache policy', () => {
  // Read as text rather than through `node:fs`, which this tsconfig has no
  // types for — the same reason the reachability suites use a raw glob.
  const HOOK = Object.values(
    import.meta.glob('../../../hooks/useJourney.ts', {
      query: '?raw',
      import: 'default',
      eager: true,
    }) as Record<string, string>,
  )[0]

  it('loaded the hook source', () => {
    expect(HOOK).toMatch(/export function useJourney/)
  })

  it('declares no per-hook staleTime or refetchOnWindowFocus override', () => {
    const body = HOOK.slice(HOOK.indexOf('export function useJourney'))
    expect(body).not.toMatch(/staleTime:/)
    expect(body).not.toMatch(/refetchOnWindowFocus:/)
  })
})
