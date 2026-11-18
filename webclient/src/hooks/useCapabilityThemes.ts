// SPDX-License-Identifier: AGPL-3.0-only
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
  })
}

// The server's upper bound for one page of theme controls.
const THEME_CONTROLS_PAGE = 200

/**
 * Every control mapped to a theme, scoped or not, read page by page.
 *
 * The theme detail shows two lists from one read: the in-scope controls and
 * the whole catalogue for the theme. The route lists in-scope controls only
 * by default, so the catalogue list has to ask for `all`, and a theme can
 * hold more controls than one page carries.
 */
export async function fetchAllThemeControls(
  themeCode: string,
  orgId?: string
): Promise<CapabilityThemeControlsResponse> {
  const first = await getCapabilityThemeControls(
    themeCode,
    { limit: THEME_CONTROLS_PAGE, offset: 0, scope_status: 'all' },
    orgId
  )
  const controls = [...(first.controls ?? [])]
  while (controls.length < (first.total ?? 0)) {
    const page = await getCapabilityThemeControls(
      themeCode,
      { limit: THEME_CONTROLS_PAGE, offset: controls.length, scope_status: 'all' },
      orgId
    )
    if (!page.controls?.length) break // never loop on a short answer
    controls.push(...page.controls)
  }
  return { ...first, controls, offset: 0, limit: controls.length }
}

/**
 * Hook to fetch every control of a capability theme (scoped and unscoped).
 * Only enabled when themeCode is provided.
 */
export function useAllCapabilityThemeControls(themeCode: string | null, orgId?: string) {
  return useQuery<CapabilityThemeControlsResponse>({
    queryKey: ['capability-theme-controls', themeCode, 'all', orgId],
    queryFn: () => fetchAllThemeControls(themeCode!, orgId),
    enabled: !!themeCode,
    staleTime: 5 * 60 * 1000,
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
  })
}
