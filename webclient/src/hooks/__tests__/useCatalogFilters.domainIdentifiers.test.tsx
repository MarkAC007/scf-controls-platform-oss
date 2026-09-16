/**
 * The Evidence page's domain filter lists only the domains present in the
 * evidence set, with counts, so it cannot take its options from
 * `useCatalogFilters().domainOptions`. It holds a domain NAME and needs the
 * catalogue abbreviation looked up so it can render `ABBR - Name (count)` the
 * way Control Scoping and the Library already do. `useDomainIdentifiers` is
 * that lookup, and `domainFilterLabel` is the one label format both share.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { domainFilterLabel, useCatalogFilters, useDomainIdentifiers } from '../useCatalogFilters'
import { fetchCatalogDomains } from '../../data/catalogApi'

vi.mock('../../data/catalogApi', () => ({
  fetchCatalogDomains: vi.fn(),
  fetchNistCsfFunctions: vi.fn(),
}))

const mockFetchDomains = vi.mocked(fetchCatalogDomains)

const DOMAINS = [
  { identifier: 'ABC', order: 1, name: 'Alpha Domain', principle: '', principle_intent: null },
  { identifier: 'XYZ', order: 2, name: 'Zulu Domain', principle: '', principle_intent: null },
]

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.clearAllMocks()
  mockFetchDomains.mockResolvedValue(DOMAINS)
})

describe('domainFilterLabel', () => {
  it('renders the abbreviation before the name', () => {
    expect(domainFilterLabel('ABC', 'Alpha Domain')).toBe('ABC - Alpha Domain')
  })
})

describe('useDomainIdentifiers', () => {
  it('maps each catalogue domain NAME to its identifier', async () => {
    const { result } = renderHook(() => useDomainIdentifiers(), { wrapper })

    await waitFor(() => expect(result.current.size).toBe(2))
    expect(result.current.get('Alpha Domain')).toBe('ABC')
    expect(result.current.get('Zulu Domain')).toBe('XYZ')
  })

  // The ERL's `area_of_focus` is its own vocabulary; about half of its names
  // have no catalogue domain. A miss must be a miss, so the caller can keep
  // the bare name rather than render "undefined - Name".
  it('returns undefined for a name the catalogue does not know', async () => {
    const { result } = renderHook(() => useDomainIdentifiers(), { wrapper })

    await waitFor(() => expect(result.current.size).toBe(2))
    expect(result.current.get('Not A Catalogue Domain')).toBeUndefined()
  })

  it('is empty, not undefined, before the catalogue has loaded', () => {
    mockFetchDomains.mockReturnValue(new Promise(() => {}))
    const { result } = renderHook(() => useDomainIdentifiers(), { wrapper })
    expect(result.current).toBeInstanceOf(Map)
    expect(result.current.size).toBe(0)
  })
})

describe('useCatalogFilters', () => {
  // The scoping/library filters and the evidence filter must agree on the
  // format, or the relabel has just moved the inconsistency.
  it('labels its domain options with domainFilterLabel', async () => {
    const { result } = renderHook(() => useCatalogFilters(), { wrapper })

    await waitFor(() => expect(result.current.domains.length).toBe(2))
    expect(result.current.domains[0]).toEqual({
      value: 'ABC',
      label: domainFilterLabel('ABC', 'Alpha Domain'),
    })
  })
})
