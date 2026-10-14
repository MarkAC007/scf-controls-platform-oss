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
  describe("the organisation's recorded size", () => {
    it('selects and marks the pill for the recorded size', () => {
      const { container } = render(<BusinessSizeGuidance guidance={guidance} orgSize="small" />)
      const pill = screen.getByRole('button', { name: 'Small (your organisation)' })
      expect(pill.className).toContain('active')
      expect(pill.className).toContain('org-size')
      expect(container.querySelectorAll('.org-size')).toHaveLength(1)
      expect(screen.getByRole('button', { name: 'Medium' }).className).not.toContain('active')
    })

    it('follows the size when settings arrive after the first render', () => {
      const { rerender } = render(<BusinessSizeGuidance guidance={guidance} />)
      expect(screen.getByRole('button', { name: 'Medium' }).className).toContain('active')
      rerender(<BusinessSizeGuidance guidance={guidance} orgSize="large" />)
      expect(screen.getByRole('button', { name: 'Large (your organisation)' }).className).toContain('active')
    })

    it("keeps the user's own pick and still marks the organisation's size", () => {
      render(<BusinessSizeGuidance guidance={guidance} orgSize="small" />)
      fireEvent.click(screen.getByRole('button', { name: 'Large' }))
      expect(screen.getByRole('button', { name: 'Large' }).className).toContain('active')
      const org = screen.getByRole('button', { name: 'Small (your organisation)' })
      expect(org.className).not.toContain('active')
      expect(org.className).toContain('org-size')
    })

    it('a fresh mount for the next control starts from the organisation size again', () => {
      const { rerender } = render(<BusinessSizeGuidance key="a" guidance={guidance} orgSize="small" />)
      fireEvent.click(screen.getByRole('button', { name: 'Large' }))
      rerender(<BusinessSizeGuidance key="b" guidance={guidance} orgSize="small" />)
      expect(screen.getByRole('button', { name: 'Small (your organisation)' }).className).toContain('active')
    })

    it('marks nothing when no size is recorded', () => {
      const { container } = render(<BusinessSizeGuidance guidance={guidance} orgSize={null} />)
      expect(container.querySelector('.org-size')).toBeNull()
    })
  })
})
