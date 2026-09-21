import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  attestJourneyStage,
  getJourney,
  importJourney,
  type JourneyResponse,
} from '../data/apiClient'

/**
 * The organisation's journey.
 *
 * Always enabled once an org is known, including for organisations that have
 * never imported one: the API answers with the default template rendered as an
 * unlit map rather than a 404, so the screen has something honest to show.
 */
export function useJourney(orgId?: string) {
  return useQuery<JourneyResponse>({
    queryKey: ['journey', orgId],
    queryFn: () => getJourney(orgId!),
    enabled: !!orgId,
    staleTime: 60_000,
    refetchOnWindowFocus: true,
  })
}

export function useImportJourney(orgId?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { template_key?: string; activate?: boolean; practitioner_name?: string }) =>
      importJourney(orgId!, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['journey', orgId] }) },
  })
}

export function useAttestStage(orgId?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ stageId, ...body }: { stageId: string; note?: string; conditional?: boolean; target_date?: string }) =>
      attestJourneyStage(orgId!, stageId, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['journey', orgId] }) },
  })
}
