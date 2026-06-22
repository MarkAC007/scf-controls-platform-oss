import { useQuery } from '@tanstack/react-query'
import {
  fetchOrganizationSettings,
  type OrganizationSettingsResponse,
} from '../data/apiClient'

/**
 * Hook to fetch organization-level settings (owner teams, etc.).
 * Returns the settings data and loading state.
 */
export function useOrganizationSettings(orgId?: string) {
  return useQuery<OrganizationSettingsResponse>({
    queryKey: ['organization-settings', orgId],
    queryFn: () => fetchOrganizationSettings(orgId),
    staleTime: 5 * 60 * 1000, // 5 minutes
    refetchOnWindowFocus: false,
  })
}
