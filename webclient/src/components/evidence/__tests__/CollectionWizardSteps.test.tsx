/**
 * Step 1 of the collection wizard stops being a dead end (#789).
 *
 * One sentence — "No systems found. Add one in the Systems tab first." — used to
 * cover two unrelated situations, and resolve neither:
 *
 *   - an organisation with no systems, told correctly what to do and given no
 *     way to do it, with `Continue` disabled behind it;
 *   - an organisation with systems and a mistyped search, told to go and add a
 *     system it already has.
 *
 * Each now says what is actually true and carries the control that fixes it.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { SystemSelectStep } from '../CollectionWizardSteps'
import type { System } from '../../../types'

afterEach(cleanup)

const system = (name: string, vendor?: string): System =>
  ({ id: name, name, vendor, system_type: 'identity_provider' }) as System

const noop = () => {}

describe('SystemSelectStep — empty registry', () => {
  it('says the registry is empty rather than that a search found nothing', () => {
    render(
      <SystemSelectStep
        systems={[]}
        selectedSystem={null}
        onSelect={noop}
        onNext={noop}
        onNavigateToSystems={noop}
      />,
    )
    expect(screen.getByText(/no systems registered yet/i)).toBeInTheDocument()
  })

  it('offers a control that opens the Systems Registry', () => {
    const onNavigateToSystems = vi.fn()
    render(
      <SystemSelectStep
        systems={[]}
        selectedSystem={null}
        onSelect={noop}
        onNext={noop}
        onNavigateToSystems={onNavigateToSystems}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /systems registry/i }))
    expect(onNavigateToSystems).toHaveBeenCalledTimes(1)
  })

  it('falls back to prose when no navigation handler was supplied', () => {
    // The handler is optional at all four hops between here and App. A button
    // that renders and does nothing is worse than no button, because the user
    // reads it as the way out and it isn't.
    render(
      <SystemSelectStep
        systems={[]}
        selectedSystem={null}
        onSelect={noop}
        onNext={noop}
      />,
    )
    expect(
      screen.queryByRole('button', { name: /systems registry/i }),
    ).not.toBeInTheDocument()
    expect(screen.getByText(/add one under systems/i)).toBeInTheDocument()
  })

  it('hides the search box, which can only ever match nothing', () => {
    render(
      <SystemSelectStep
        systems={[]}
        selectedSystem={null}
        onSelect={noop}
        onNext={noop}
      />,
    )
    expect(screen.queryByPlaceholderText(/search systems/i)).not.toBeInTheDocument()
  })
})

describe('SystemSelectStep — search matched nothing', () => {
  const systems = [system('Okta', 'Okta Inc'), system('Entra ID', 'Microsoft')]

  function renderWithQuery(query: string) {
    const view = render(
      <SystemSelectStep
        systems={systems}
        selectedSystem={null}
        onSelect={noop}
        onNext={noop}
        onNavigateToSystems={noop}
      />,
    )
    fireEvent.change(screen.getByPlaceholderText(/search systems/i), {
      target: { value: query },
    })
    return view
  }

  it('says how many systems exist rather than that none do', () => {
    renderWithQuery('zzzz')
    expect(screen.getByText(/none of this organisation/i)).toBeInTheDocument()
    expect(screen.getByText(/2 systems/i)).toBeInTheDocument()
  })

  it('offers clearing the search, not adding a system', () => {
    renderWithQuery('zzzz')
    expect(
      screen.queryByRole('button', { name: /systems registry/i }),
    ).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /clear the search/i })).toBeInTheDocument()
  })

  it('clearing the search restores the full list', () => {
    renderWithQuery('zzzz')
    fireEvent.click(screen.getByRole('button', { name: /clear the search/i }))
    expect(screen.getByText('Okta')).toBeInTheDocument()
    expect(screen.getByText('Entra ID')).toBeInTheDocument()
  })

  it('singularises the count for a one-system organisation', () => {
    render(
      <SystemSelectStep
        systems={[system('Okta')]}
        selectedSystem={null}
        onSelect={noop}
        onNext={noop}
      />,
    )
    fireEvent.change(screen.getByPlaceholderText(/search systems/i), {
      target: { value: 'zzzz' },
    })
    expect(screen.getByText(/1 system\b/i)).toBeInTheDocument()
  })
})
