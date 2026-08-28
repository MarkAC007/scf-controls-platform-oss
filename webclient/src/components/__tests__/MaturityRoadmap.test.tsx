/**
 * MaturityRoadmap.test.tsx — two render variants.
 *
 * `full` (default, library control detail page):
 *   - Renders a `.roadmap-stepper` (NOT `.maturity-stepper` — that class belongs
 *     to components/maturity/MaturityStepper and collided, forcing a vertical stack)
 *   - Six L0–L5 chips in a single row
 *   - Per-level guidance is hover-only: no inline guidance text on first paint
 *   - Target level chip carries the `target` modifier
 *
 * `duo` (scoping control detail top badge row):
 *   - Two chips only — current and next (current+1) — with a `›` separator
 *   - Level unset falls back to L0 current / L1 next
 *   - L5 current renders alone: no separator, no phantom L6
 *   - Both chips carry hover popovers reusing `.guidance-popover`
 *
 * Both variants render nothing without guidance.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import MaturityRoadmap from '../MaturityRoadmap'
import type { CMMaturityGuidance } from '../../types'

const maturity: CMMaturityGuidance = {
  level_0: 'Nothing is performed.',
  level_1: 'Performed informally by individuals.',
  level_2: 'Planned and tracked against a schedule.',
  level_3: 'Well defined and documented org-wide.',
  level_4: 'Quantitatively controlled with metrics.',
  level_5: 'Continuously improving.',
}

describe('MaturityRoadmap — full variant (default)', () => {
  it('renders nothing when no maturity guidance is supplied', () => {
    const { container } = render(<MaturityRoadmap />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when every level is empty', () => {
    const { container } = render(<MaturityRoadmap maturity={{}} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders a roadmap-scoped stepper, not the colliding maturity-stepper class', () => {
    const { container } = render(<MaturityRoadmap maturity={maturity} />)
    expect(container.querySelector('.roadmap-stepper')).not.toBeNull()
    expect(container.querySelector('.maturity-stepper')).toBeNull()
    expect(container.querySelector('.maturity-step')).toBeNull()
  })

  it('renders six level chips L0 through L5', () => {
    const { container } = render(<MaturityRoadmap maturity={maturity} />)
    expect(container.querySelectorAll('.roadmap-step')).toHaveLength(6)
    for (const label of ['L0', 'L1', 'L2', 'L3', 'L4', 'L5']) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
  })

  it('does not render the duo presentation', () => {
    const { container } = render(<MaturityRoadmap maturity={maturity} level="L2" />)
    expect(container.querySelector('.roadmap-duo')).toBeNull()
  })

  it('does not render any guidance text inline', () => {
    render(<MaturityRoadmap maturity={maturity} level="L2" />)
    expect(screen.queryByText(maturity.level_2!)).not.toBeInTheDocument()
    expect(screen.queryByText(maturity.level_5!)).not.toBeInTheDocument()
  })

  it('reveals the hovered level guidance in a popover and hides it on leave', () => {
    const { container } = render(<MaturityRoadmap maturity={maturity} level="L2" />)
    const steps = container.querySelectorAll('.roadmap-step')

    fireEvent.mouseEnter(steps[3])
    expect(screen.getByText(maturity.level_3!)).toBeInTheDocument()
    expect(container.querySelector('.guidance-popover')).not.toBeNull()

    fireEvent.mouseLeave(steps[3])
    expect(screen.queryByText(maturity.level_3!)).not.toBeInTheDocument()
    expect(container.querySelector('.guidance-popover')).toBeNull()
  })

  it('marks the target level chip and fills the levels up to it', () => {
    const { container } = render(<MaturityRoadmap maturity={maturity} level="L2" />)
    const circles = container.querySelectorAll('.roadmap-circle')
    expect(circles[2].className).toContain('target')
    expect(circles[1].className).toContain('active')
    expect(circles[5].className).toContain('inactive')
  })
})

describe('MaturityRoadmap — duo variant', () => {
  it('renders nothing when no maturity guidance is supplied', () => {
    const { container } = render(<MaturityRoadmap variant="duo" level="L2" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders current and next chips with their sublabels', () => {
    const { container } = render(
      <MaturityRoadmap maturity={maturity} variant="duo" level="L2" />
    )
    const chips = container.querySelectorAll('.roadmap-duo-chip')
    expect(chips).toHaveLength(2)

    expect(chips[0].className).toContain('current')
    expect(chips[0].textContent).toContain('L2')
    expect(chips[0].textContent).toContain('current')

    expect(chips[1].className).toContain('next')
    expect(chips[1].textContent).toContain('L3')
    expect(chips[1].textContent).toContain('next')
  })

  it('renders the Maturity label, the chevron separator and the hover hint', () => {
    const { container } = render(
      <MaturityRoadmap maturity={maturity} variant="duo" level="L2" />
    )
    expect(screen.getByText('Maturity')).toBeInTheDocument()
    expect(screen.getByText('hover for guidance')).toBeInTheDocument()
    expect(container.querySelector('.roadmap-duo-arrow')?.textContent).toBe('›')
  })

  it('does not render the full six-chip stepper', () => {
    const { container } = render(
      <MaturityRoadmap maturity={maturity} variant="duo" level="L2" />
    )
    expect(container.querySelector('.roadmap-stepper')).toBeNull()
    expect(container.querySelector('.maturity-stepper')).toBeNull()
  })

  it('falls back to L0 current / L1 next when the level is unset', () => {
    const { container } = render(<MaturityRoadmap maturity={maturity} variant="duo" />)
    const chips = container.querySelectorAll('.roadmap-duo-chip')
    expect(chips).toHaveLength(2)
    expect(chips[0].textContent).toContain('L0')
    expect(chips[1].textContent).toContain('L1')
  })

  it('falls back to L0 current when the level is explicitly null', () => {
    const { container } = render(
      <MaturityRoadmap maturity={maturity} variant="duo" level={null} />
    )
    expect(container.querySelectorAll('.roadmap-duo-chip')[0].textContent).toContain('L0')
  })

  it('renders the current chip alone at L5 — no separator, no phantom L6', () => {
    const { container } = render(
      <MaturityRoadmap maturity={maturity} variant="duo" level="L5" />
    )
    const chips = container.querySelectorAll('.roadmap-duo-chip')
    expect(chips).toHaveLength(1)
    expect(chips[0].className).toContain('current')
    expect(chips[0].textContent).toContain('L5')
    expect(container.querySelector('.roadmap-duo-arrow')).toBeNull()
    expect(screen.queryByText('L6')).not.toBeInTheDocument()
    expect(screen.queryByText('next')).not.toBeInTheDocument()
  })

  it('reveals the current level guidance on hover', () => {
    const { container } = render(
      <MaturityRoadmap maturity={maturity} variant="duo" level="L2" />
    )
    const chips = container.querySelectorAll('.roadmap-duo-chip')

    fireEvent.mouseEnter(chips[0])
    expect(container.querySelector('.guidance-popover')).not.toBeNull()
    expect(screen.getByText(maturity.level_2!)).toBeInTheDocument()

    fireEvent.mouseLeave(chips[0])
    expect(container.querySelector('.guidance-popover')).toBeNull()
  })

  it('reveals the next level guidance on hover', () => {
    const { container } = render(
      <MaturityRoadmap maturity={maturity} variant="duo" level="L2" />
    )
    const chips = container.querySelectorAll('.roadmap-duo-chip')

    fireEvent.mouseEnter(chips[1])
    expect(screen.getByText(maturity.level_3!)).toBeInTheDocument()
  })

  it('renders no guidance text inline', () => {
    render(<MaturityRoadmap maturity={maturity} variant="duo" level="L2" />)
    expect(screen.queryByText(maturity.level_2!)).not.toBeInTheDocument()
    expect(screen.queryByText(maturity.level_3!)).not.toBeInTheDocument()
  })
})
