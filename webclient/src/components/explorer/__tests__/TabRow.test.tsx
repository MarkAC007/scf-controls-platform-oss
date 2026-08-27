import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import TabRow from '../TabRow'

const tabs = [
  { id: 'alpha', label: 'Alpha' },
  { id: 'beta', label: 'Beta', count: 12 },
  { id: 'gamma', label: 'Gamma' },
]

describe('TabRow', () => {
  it('renders all tab labels', () => {
    render(
      <TabRow
        tabs={tabs}
        activeId="alpha"
        onSelect={vi.fn()}
        aria-label="Test tabs"
      />,
    )

    expect(screen.getByText('Alpha')).toBeInTheDocument()
    expect(screen.getByText('Beta')).toBeInTheDocument()
    expect(screen.getByText('Gamma')).toBeInTheDocument()
  })

  it('active tab has aria-selected="true" and class explorer-tab--active', () => {
    render(
      <TabRow
        tabs={tabs}
        activeId="beta"
        onSelect={vi.fn()}
        aria-label="Test tabs"
      />,
    )

    const activeBtn = screen.getByRole('tab', { name: /Beta/ })
    expect(activeBtn).toHaveAttribute('aria-selected', 'true')
    expect(activeBtn).toHaveClass('explorer-tab--active')
  })

  it('inactive tabs have aria-selected="false"', () => {
    render(
      <TabRow
        tabs={tabs}
        activeId="alpha"
        onSelect={vi.fn()}
        aria-label="Test tabs"
      />,
    )

    const inactiveBtn = screen.getByRole('tab', { name: /Beta/ })
    expect(inactiveBtn).toHaveAttribute('aria-selected', 'false')
    expect(inactiveBtn).not.toHaveClass('explorer-tab--active')
  })

  it('click calls onSelect with the tab id', () => {
    const onSelect = vi.fn()
    render(
      <TabRow
        tabs={tabs}
        activeId="alpha"
        onSelect={onSelect}
        aria-label="Test tabs"
      />,
    )

    fireEvent.click(screen.getByRole('tab', { name: /Beta/ }))
    expect(onSelect).toHaveBeenCalledWith('beta')
  })

  it('ArrowRight from active tab calls onSelect with next id', () => {
    const onSelect = vi.fn()
    render(
      <TabRow
        tabs={tabs}
        activeId="alpha"
        onSelect={onSelect}
        aria-label="Test tabs"
      />,
    )

    const activeBtn = screen.getByRole('tab', { name: /Alpha/ })
    fireEvent.keyDown(activeBtn, { key: 'ArrowRight' })
    expect(onSelect).toHaveBeenCalledWith('beta')
  })

  it('ArrowRight moves DOM focus to the next tab button', () => {
    const onSelect = vi.fn()
    render(
      <TabRow
        tabs={tabs}
        activeId="alpha"
        onSelect={onSelect}
        aria-label="Test tabs"
      />,
    )

    const alphaBtn = screen.getByRole('tab', { name: /Alpha/ })
    const betaBtn = screen.getByRole('tab', { name: /Beta/ })
    alphaBtn.focus()
    fireEvent.keyDown(alphaBtn, { key: 'ArrowRight' })
    expect(document.activeElement).toBe(betaBtn)
  })

  it('ArrowLeft from active tab calls onSelect with previous id', () => {
    const onSelect = vi.fn()
    render(
      <TabRow
        tabs={tabs}
        activeId="beta"
        onSelect={onSelect}
        aria-label="Test tabs"
      />,
    )

    const activeBtn = screen.getByRole('tab', { name: /Beta/ })
    fireEvent.keyDown(activeBtn, { key: 'ArrowLeft' })
    expect(onSelect).toHaveBeenCalledWith('alpha')
  })

  it('renders optional count alongside label', () => {
    render(
      <TabRow
        tabs={tabs}
        activeId="alpha"
        onSelect={vi.fn()}
        aria-label="Test tabs"
      />,
    )

    // count "12" should be visible within the Beta tab
    expect(screen.getByText('12')).toBeInTheDocument()
  })

  it('renders a tablist role with the given aria-label', () => {
    render(
      <TabRow
        tabs={tabs}
        activeId="alpha"
        onSelect={vi.fn()}
        aria-label="My tab list"
      />,
    )

    expect(screen.getByRole('tablist', { name: 'My tab list' })).toBeInTheDocument()
  })
})
