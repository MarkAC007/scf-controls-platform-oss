import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { queryClient as appQueryClient } from '../data/queryClient'
import {
  attestJourneyStage,
  getJourney,
  importJourney,
  type JourneyResponse,
} from '../data/apiClient'

/** The prefix every journey query shares. Invalidating it needs no org id. */
export const JOURNEY_QUERY_ROOT = 'journey'

/** One registration per mutation cache, so a double import cannot double-fire. */
const wired = new WeakSet<object>()

/**
 * Invalidate the journey whenever *any* mutation in the app succeeds.
 *
 * Every journey gate is a live SQL count over the operational tables — scoped
 * controls, approved documents, evidence tracking, evidence files, engagements
 * — recomputed per request. So any successful write can move one, and asking
 * each write to remember the journey exists is exactly what failed: of the
 * write paths that move a gate, only the journey's own two invalidated it.
 *
 * This is registered once, against the mutation cache rather than against a
 * list of keys, so a mutation written next year is covered without its author
 * knowing this screen exists. It is the one shape that cannot be forgotten.
 *
 * Returns an unsubscribe so tests can register against their own client.
 */
export function registerJourneyInvalidation(client: QueryClient): () => void {
  const cache = client.getMutationCache()
  if (wired.has(cache)) return () => {}
  wired.add(cache)

  const unsubscribe = cache.subscribe(event => {
    if (event.type !== 'updated') return
    if (event.action.type !== 'success') return
    void client.invalidateQueries({ queryKey: [JOURNEY_QUERY_ROOT] })
  })

  return () => {
    wired.delete(cache)
    unsubscribe()
  }
}

// Registered at module load. `App.tsx` imports `JourneyPage`, which imports
// this file, so the subscriber is live from app boot — not only while the
// Journey screen is mounted, which is precisely when the stale writes happen.
//
// That makes the static import a load-bearing part of the fix: lazy-loading
// `JourneyPage` would defer this registration until the screen is first
// opened, and every write before that would go unnoticed again. If the page is
// ever code-split, move this call to where the client is constructed.
registerJourneyInvalidation(appQueryClient)

/**
 * The organisation's journey.
 *
 * Always enabled once an org is known, including for organisations that have
 * never imported one: the API answers with the default template rendered as an
 * unlit map rather than a 404, so the screen has something honest to show.
 *
 * `staleTime` and `refetchOnWindowFocus` are deliberately absent: the app-wide
 * defaults apply, and freshness comes from invalidation above rather than from
 * a shorter expiry window. `refetchOnMount: 'always'` is a backstop, not the
 * fix — writes made through bare `apiClient` calls rather than `useMutation`
 * are invisible to the subscriber, and this screen unmounts on navigation, so
 * arriving at it always asks the server again.
 */
export function useJourney(orgId?: string) {
  return useQuery<JourneyResponse>({
    queryKey: [JOURNEY_QUERY_ROOT, orgId],
    queryFn: () => getJourney(orgId!),
    enabled: !!orgId,
    refetchOnMount: 'always',
  })
}

export function useImportJourney(orgId?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { template_key?: string; activate?: boolean; practitioner_name?: string }) =>
      importJourney(orgId!, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: [JOURNEY_QUERY_ROOT, orgId] }) },
  })
}

export function useAttestStage(orgId?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ stageId, ...body }: { stageId: string; note?: string; conditional?: boolean; target_date?: string }) =>
      attestJourneyStage(orgId!, stageId, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: [JOURNEY_QUERY_ROOT, orgId] }) },
  })
}
