/**
 * BusinessSizeGuidance.test.tsx — hover-only right-sizing pills.
 *
 * Coverage:
 *   - Five size pills render, org default (medium) selected
 *   - No inline guidance paragraph anywhere — guidance is hover-only,
 *     matching the Risk & Threat badge pattern
 *   - Hovering any pill (selected or not) reveals that size's guidance
 *   - Pills with no guidance reveal nothing
 *   - Renders nothing without guidance
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import BusinessSizeGuidance from '../BusinessSizeGuidance'
import type { BusinessSizeGuidance as BusinessSizeGuidanceType } from '../../types'

const guidance: BusinessSizeGuidanceType = {
  micro_small: 'Owner-operated: a one-page note is enough.',
  small: 'Assign a named owner and review annually.',
  medium: 'Formal procedure with quarterly review.',
  large: 'Dedicated function with automated monitoring.',
}

describe('BusinessSizeGuidance', () => {
  it('renders nothing when no guidance is supplied', () => {
    const { container } = render(<BusinessSizeGuidance />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when every size is empty', () => {
    const { container } = render(<BusinessSizeGuidance guidance={{}} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders five size pills with medium selected by default', () => {
    const { container } = render(<BusinessSizeGuidance guidance={guidance} />)
    expect(container.querySelectorAll('.size-pill')).toHaveLength(5)
    expect(screen.getByRole('button', { name: 'Medium' }).className).toContain('active')
  })

  it('renders no inline guidance text', () => {
    render(<BusinessSizeGuidance guidance={guidance} />)
    expect(screen.queryByText(guidance.medium!)).not.toBeInTheDocument()
    expect(screen.queryByText(guidance.small!)).not.toBeInTheDocument()
  })

  it('reveals guidance for the hovered pill and hides it on leave', () => {
    const { container } = render(<BusinessSizeGuidance guidance={guidance} />)
    const wraps = container.querySelectorAll('.size-pill-wrap')

    fireEvent.mouseEnter(wraps[0])
    expect(screen.getByText(guidance.micro_small!)).toBeInTheDocument()
    expect(container.querySelector('.guidance-popover')).not.toBeNull()

    fireEvent.mouseLeave(wraps[0])
    expect(screen.queryByText(guidance.micro_small!)).not.toBeInTheDocument()
  })

  it('reveals guidance on hover even when the pill is not the selected one', () => {
    const { container } = render(<BusinessSizeGuidance guidance={guidance} />)
    const wraps = container.querySelectorAll('.size-pill-wrap')

    fireEvent.mouseEnter(wraps[3])
    expect(screen.getByText(guidance.large!)).toBeInTheDocument()
  })

  it('shows no popover for a size that has no guidance', () => {
    const { container } = render(<BusinessSizeGuidance guidance={guidance} />)
    const wraps = container.querySelectorAll('.size-pill-wrap')

    fireEvent.mouseEnter(wraps[4])
    expect(container.querySelector('.guidance-popover')).toBeNull()
  })
})
