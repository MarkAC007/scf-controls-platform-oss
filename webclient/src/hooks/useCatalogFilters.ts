import { useQuery } from '@tanstack/react-query'
import { fetchCatalogDomains } from '../data/catalogApi'

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
    label: `${d.identifier} - ${d.name}`,
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
