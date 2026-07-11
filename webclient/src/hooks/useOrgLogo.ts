import { useQuery } from '@tanstack/react-query'
import { fetchOrganizationLogoBlob } from '../data/apiClient'

export const ORG_LOGO_QUERY_KEY = 'organization-logo'

/**
 * Fetch the current organization's logo and expose it as an object URL
 * (null when the org has no uploaded logo). Object URLs are intentionally
 * not revoked: the query cache shares them across consumers and a logo is
 * at most 1 MB.
 */
export function useOrgLogo(orgId?: string) {
  return useQuery<string | null>({
    queryKey: [ORG_LOGO_QUERY_KEY, orgId],
    queryFn: async () => {
      const blob = await fetchOrganizationLogoBlob(orgId!)
      return blob ? URL.createObjectURL(blob) : null
    },
    enabled: !!orgId,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })
}
