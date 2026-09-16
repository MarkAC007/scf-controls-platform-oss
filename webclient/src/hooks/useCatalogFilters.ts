import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchCatalogDomains } from '../data/catalogApi'

/** The `ABBR - Name` label every domain filter in the app renders. */
export const domainFilterLabel = (identifier: string, name: string) => `${identifier} - ${name}`

/**
 * Catalog domain NAME -> identifier, for filters that derive their own option
 * list but still want `domainFilterLabel`. The Evidence filter is the case:
 * it lists only the domains present in the evidence set, with counts, so it
 * cannot use `useCatalogFilters().domains` as its source — but it only holds
 * the domain name and needs the abbreviation looked up.
 *
 * Shares the `catalog-domains` query key with `useCatalogFilters`, so this
 * costs no extra request. Callers MUST fall back to the bare name on a miss:
 * the map is keyed on the catalog domain name, and not every domain string in
 * the app comes from that vocabulary (the ERL's `area_of_focus` is its own).
 */
export function useDomainIdentifiers(): Map<string, string> {
  const { data } = useQuery({
    queryKey: ['catalog-domains'],
    queryFn: fetchCatalogDomains,
    staleTime: Infinity,
  })
  return useMemo(() => new Map((data ?? []).map((d) => [d.name, d.identifier])), [data])
}

/**
 * Hook to fetch catalog filter options (domains and NIST CSF functions).
 * Uses React Query with infinite stale time since domains don't change.
 */
export function useCatalogFilters() {
  const { data: domains, isLoading } = useQuery({
    queryKey: ['catalog-domains'],
    queryFn: fetchCatalogDomains,
    staleTime: Infinity, // Domains never change
  })

  // Extract domain identifiers for the filter dropdown
  const domainOptions = domains?.map((d) => ({
    value: d.identifier,
    label: domainFilterLabel(d.identifier, d.name),
  })) ?? []

  // Extract unique NIST CSF functions
  // These are: Identify, Protect, Detect, Respond, Recover, Govern
  const nistCsfFunctions = [
    { value: 'Identify', label: 'Identify' },
    { value: 'Protect', label: 'Protect' },
    { value: 'Detect', label: 'Detect' },
    { value: 'Respond', label: 'Respond' },
    { value: 'Recover', label: 'Recover' },
    { value: 'Govern', label: 'Govern' },
  ]

  // Control weighting options (0-10 scale)
  const controlWeights = [
    { value: '0', label: '0 - Minimal' },
    { value: '1', label: '1' },
    { value: '2', label: '2' },
    { value: '3', label: '3' },
    { value: '4', label: '4' },
    { value: '5', label: '5 - Medium' },
    { value: '6', label: '6' },
    { value: '7', label: '7' },
    { value: '8', label: '8' },
    { value: '9', label: '9' },
    { value: '10', label: '10 - Critical' },
  ]

  return {
    domains: domainOptions,
    nistCsfFunctions,
    controlWeights,
    isLoading,
  }
}
