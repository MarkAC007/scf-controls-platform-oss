/**
 * PIG R6: the paginated list's material filter and weighting order reach the
 * URL — a filter the request never carries would silently show everything.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchScopedControlsPage } from '../apiClient'

function lastRequestedPath(): string {
  const calls = vi.mocked(globalThis.fetch).mock.calls
  return String(calls[calls.length - 1][0])
}

describe('fetchScopedControlsPage materiality params', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        headers: { get: () => 'application/json' },
        json: async () => ({ controls: [], total: 0, offset: 0 }),
        text: async () => '{"controls":[],"total":0,"offset":0}',
      }),
    )
  })

  it('sends neither when not asked', async () => {
    await fetchScopedControlsPage({ limit: 50 }, 'org-1')
    expect(lastRequestedPath()).not.toContain('material=')
    expect(lastRequestedPath()).not.toContain('sort=')
  })

  it('sends material=true with the weighting order', async () => {
    await fetchScopedControlsPage({ limit: 50, material: true, sort: 'weighting' }, 'org-1')
    expect(lastRequestedPath()).toContain('material=true')
    expect(lastRequestedPath()).toContain('sort=weighting')
  })

  it('sends material=false rather than dropping it as falsy', async () => {
    await fetchScopedControlsPage({ limit: 50, material: false }, 'org-1')
    expect(lastRequestedPath()).toContain('material=false')
  })
})
