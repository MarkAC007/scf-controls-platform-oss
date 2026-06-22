import { useQuery } from '@tanstack/react-query'
import { getCapabilityThemes, getCapabilityThemeControls, getCapabilityThemeEvidencePosture } from '../data/apiClient'
import type { CapabilityThemeListResponse, CapabilityThemeControlsResponse, CapabilityThemeEvidencePostureResponse } from '../types'

/**
 * Hook to fetch all 11 capability themes with posture data.
 * Uses 5-minute staleTime since theme data is slow-moving.
 */
export function useCapabilityThemes(orgId?: string) {
  return useQuery<CapabilityThemeListResponse>({
    queryKey: ['capability-themes', orgId],
    queryFn: () => getCapabilityThemes(orgId),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })
}

/**
 * Hook to fetch evidence assessment posture per capability theme.
 * Fetched independently from main themes for parallel loading.
 */
export function useCapabilityThemeEvidencePosture(orgId?: string) {
  return useQuery<CapabilityThemeEvidencePostureResponse>({
    queryKey: ['capability-themes-evidence-posture', orgId],
    queryFn: () => getCapabilityThemeEvidencePosture(orgId),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })
}

/**
 * Hook to fetch paginated controls for a specific capability theme.
 * Only enabled when themeCode is provided.
 */
export function useCapabilityThemeControls(
  themeCode: string | null,
  params?: { limit?: number; offset?: number },
  orgId?: string
) {
  return useQuery<CapabilityThemeControlsResponse>({
    queryKey: ['capability-theme-controls', themeCode, params, orgId],
    queryFn: () => getCapabilityThemeControls(themeCode!, params, orgId),
    enabled: !!themeCode,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })
}
