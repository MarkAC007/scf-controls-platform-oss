/**
 * WorkScopeContext.test.tsx — the header work scope (#1052).
 *
 * The one behaviour worth pinning hardest is the default. A scope that
 * defaults to 'my_teams' would hide most of an organisation's list from a
 * caller who never asked it to, and would do so behind a control they may
 * never look at. So: fresh localStorage means Everything, and only an explicit
 * choice moves it.
 */
import { act, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import {
  DEFAULT_WORK_SCOPE,
  WorkScopeProvider,
  useWorkScope,
} from '../WorkScopeContext'

function Probe() {
  const { scope, isMyTeams, setScope } = useWorkScope()
  return (
    <div>
      <span data-testid="scope">{scope}</span>
      <span data-testid="is-my-teams">{String(isMyTeams)}</span>
      <button onClick={() => setScope('my_teams')}>go my teams</button>
      <button onClick={() => setScope('everything')}>go everything</button>
    </div>
  )
}

describe('WorkScopeContext', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('defaults to Everything on a fresh localStorage', () => {
    render(
      <WorkScopeProvider>
        <Probe />
      </WorkScopeProvider>,
    )

    expect(DEFAULT_WORK_SCOPE).toBe('everything')
    expect(screen.getByTestId('scope')).toHaveTextContent('everything')
    expect(screen.getByTestId('is-my-teams')).toHaveTextContent('false')
  })

  it('persists an explicit choice under scf_work_scope and restores it', () => {
    const { unmount } = render(
      <WorkScopeProvider>
        <Probe />
      </WorkScopeProvider>,
    )

    act(() => {
      screen.getByText('go my teams').click()
    })
    expect(screen.getByTestId('scope')).toHaveTextContent('my_teams')
    expect(localStorage.getItem('scf_work_scope')).toBe('my_teams')

    unmount()
    render(
      <WorkScopeProvider>
        <Probe />
      </WorkScopeProvider>,
    )
    expect(screen.getByTestId('scope')).toHaveTextContent('my_teams')
  })

  it('resolves an unrecognised stored value to Everything, never to a narrowing', () => {
    localStorage.setItem('scf_work_scope', 'some_future_value')

    render(
      <WorkScopeProvider>
        <Probe />
      </WorkScopeProvider>,
    )

    expect(screen.getByTestId('scope')).toHaveTextContent('everything')
  })
})
