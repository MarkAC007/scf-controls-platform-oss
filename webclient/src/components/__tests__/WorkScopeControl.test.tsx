/**
 * WorkScopeControl.test.tsx — the header control, and the thing it is for:
 * selecting "My teams" must put ``my_teams=true`` on the controls request.
 *
 * The second test goes all the way to the URL rather than stopping at the
 * hook. A scope control that updates a context nobody forwards is a control
 * that lies, and it would pass any test that only checked the context.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { WorkScopeProvider, useWorkScope } from '../../contexts/WorkScopeContext'
import { fetchScopedControlsPage } from '../../data/apiClient'
import WorkScopeControl from '../WorkScopeControl'

/** Re-reads the controls list whenever the scope changes, like the list does. */
function ControlsProbe() {
  const { isMyTeams } = useWorkScope()
  return (
    <button
      onClick={() => {
        void fetchScopedControlsPage(
          { limit: 50, offset: 0, my_teams: isMyTeams || undefined },
          'org-1',
        )
      }}
    >
      load controls
    </button>
  )
}

function renderControl() {
  return render(
    <WorkScopeProvider>
      <WorkScopeControl />
      <ControlsProbe />
    </WorkScopeProvider>,
  )
}

/** The path the fetch mock was last called with. */
function lastRequestedPath(): string {
  const calls = vi.mocked(globalThis.fetch).mock.calls
  return String(calls[calls.length - 1][0])
}

describe('WorkScopeControl', () => {
  beforeEach(() => {
    localStorage.clear()
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

  it('renders two exclusive options and defaults to Everything', () => {
    renderControl()

    expect(screen.getByText('Showing:')).toBeInTheDocument()

    const everything = screen.getByRole('radio', { name: 'Everything' })
    const myTeams = screen.getByRole('radio', { name: 'My teams' })

    expect(everything).toHaveAttribute('aria-checked', 'true')
    expect(myTeams).toHaveAttribute('aria-checked', 'false')
    expect(screen.getAllByRole('radio')).toHaveLength(2)
  })

  it('puts my_teams=true on the controls request once My teams is selected', async () => {
    renderControl()

    // Baseline: nothing is narrowed until the caller asks.
    fireEvent.click(screen.getByText('load controls'))
    await vi.waitFor(() => expect(globalThis.fetch).toHaveBeenCalled())
    expect(lastRequestedPath()).not.toContain('my_teams')

    fireEvent.click(screen.getByRole('radio', { name: 'My teams' }))
    expect(screen.getByRole('radio', { name: 'My teams' })).toHaveAttribute(
      'aria-checked',
      'true',
    )

    const before = vi.mocked(globalThis.fetch).mock.calls.length
    fireEvent.click(screen.getByText('load controls'))
    await vi.waitFor(() =>
      expect(vi.mocked(globalThis.fetch).mock.calls.length).toBeGreaterThan(before),
    )

    expect(lastRequestedPath()).toContain('my_teams=true')
  })
})
