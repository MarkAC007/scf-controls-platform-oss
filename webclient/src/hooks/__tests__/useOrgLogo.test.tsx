/**
 * Regression cover for #807 — the header logo rendered as a broken image.
 *
 * ``useOrgLogo`` used to blob every OK response body. A Blob is always truthy,
 * so a zero-byte logo row or an error page the edge answered with 200 became an
 * object URL the <img> could never decode (naturalWidth 0, complete true).
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useOrgLogo } from '../useOrgLogo'
import { fetchOrganizationLogoBlob } from '../../data/apiClient'

vi.mock('../../data/apiClient', () => ({
  fetchOrganizationLogoBlob: vi.fn(),
}))

const mockFetchLogo = vi.mocked(fetchOrganizationLogoBlob)

const ORG_ID = '11111111-1111-4111-8111-111111111111'

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.clearAllMocks()
  // jsdom has no object-URL store; stub it so we can assert on the call.
  vi.stubGlobal('URL', Object.assign(Object.create(URL), URL, {
    createObjectURL: vi.fn(() => 'blob:https://scf.test/64b14db8'),
    revokeObjectURL: vi.fn(),
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useOrgLogo', () => {
  it('returns null instead of an undecodable object URL for a zero-byte body', async () => {
    mockFetchLogo.mockResolvedValue(new Blob([], { type: 'image/png' }))

    const { result } = renderHook(() => useOrgLogo(ORG_ID), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toBeNull()
    expect(URL.createObjectURL).not.toHaveBeenCalled()
  })

  it('returns null instead of an undecodable object URL for a non-image body', async () => {
    mockFetchLogo.mockResolvedValue(
      new Blob(['<html>503 Service Unavailable</html>'], { type: 'text/html' })
    )

    const { result } = renderHook(() => useOrgLogo(ORG_ID), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toBeNull()
    expect(URL.createObjectURL).not.toHaveBeenCalled()
  })

  it('still mints an object URL for a real image body', async () => {
    mockFetchLogo.mockResolvedValue(new Blob(['\x89PNG\r\n'], { type: 'image/png' }))

    const { result } = renderHook(() => useOrgLogo(ORG_ID), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toBe('blob:https://scf.test/64b14db8')
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1)
  })
})
