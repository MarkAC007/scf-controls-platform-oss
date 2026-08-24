import { useQuery } from '@tanstack/react-query'
import { fetchOrganizationLogoBlob } from '../data/apiClient'

export const ORG_LOGO_QUERY_KEY = 'organization-logo'

/**
 * Fetch the current organization's logo and expose it as an object URL
 * (null when the org has no uploaded logo). Object URLs are intentionally
 * not revoked: the query cache shares them across consumers and a logo is
 * at most 1 MB.
 *
 * Only a non-empty image body becomes an object URL. A Blob is always truthy —
 * including a zero-byte one and an error page the edge answered with 200 — so
 * blobbing every OK response minted URLs no <img> could decode, which is the
 * broken-image icon the header showed on every route (#807).
 */
export function useOrgLogo(orgId?: string) {
  return useQuery<string | null>({
    queryKey: [ORG_LOGO_QUERY_KEY, orgId],
    queryFn: async () => {
      const blob = await fetchOrganizationLogoBlob(orgId!)
      if (!blob || blob.size === 0 || !blob.type.toLowerCase().startsWith('image/')) {
        return null
      }
      return URL.createObjectURL(blob)
    },
    enabled: !!orgId,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })
}
