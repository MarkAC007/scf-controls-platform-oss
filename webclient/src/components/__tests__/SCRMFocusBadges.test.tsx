/**
 * SCRMFocusBadges.test.tsx — two render variants over one tier table.
 *
 * `card` (default, scoping control detail page): unchanged full-width section.
 * `bar`  (library header badge row): compact T1/T2/T3 chips, active tiers ticked
 *        and success-toned, inactive tiers dimmed but never hidden.
 *
 * Tier semantics (which tiers are active) must not fork between the two.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import SCRMFocusBadges from '../SCRMFocusBadges'
import type { SCRMFocus } from '../../types'

const focus: SCRMFocus = {
  tier1_strategic: true,
  tier2_operational: true,
  tier3_tactical: false,
}

describe('SCRMFocusBadges — card variant (default)', () => {
  it('renders nothing without focus', () => {
    const { container } = render(<SCRMFocusBadges />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when no tier is active', () => {
    const { container } = render(
      <SCRMFocusBadges focus={{ tier1_strategic: false, tier2_operational: false, tier3_tactical: false }} />
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('still renders the full-width tier cards section', () => {
    const { container } = render(<SCRMFocusBadges focus={focus} />)
    expect(container.querySelector('.scrm-tier-cards')).not.toBeNull()
    expect(container.querySelectorAll('.scrm-tier-card')).toHaveLength(3)
    expect(screen.getByText('Supply Chain Focus')).toBeInTheDocument()
    expect(container.querySelector('.scrm-bar')).toBeNull()
  })

  it('marks active and inactive tier cards', () => {
    const { container } = render(<SCRMFocusBadges focus={focus} />)
    const cards = container.querySelectorAll('.scrm-tier-card')
    expect(cards[0].classList.contains('active')).toBe(true)
    expect(cards[1].classList.contains('active')).toBe(true)
    expect(cards[2].classList.contains('inactive')).toBe(true)
    expect(cards[2].classList.contains('active')).toBe(false)
  })
})

describe('SCRMFocusBadges — bar variant', () => {
  it('renders nothing without focus', () => {
    const { container } = render(<SCRMFocusBadges variant="bar" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders the Supply Chain label and three tier chips', () => {
    const { container } = render(<SCRMFocusBadges focus={focus} variant="bar" />)
    expect(container.querySelector('.scrm-bar')).not.toBeNull()
    expect(screen.getByText('Supply Chain')).toBeInTheDocument()

    const tiles = container.querySelectorAll('.scrm-bar-tile')
    expect(tiles).toHaveLength(3)
    expect(tiles[0].textContent).toContain('T1')
    expect(tiles[1].textContent).toContain('T2')
    expect(tiles[2].textContent).toContain('T3')
  })

  it('does not render the full-width card section', () => {
    const { container } = render(<SCRMFocusBadges focus={focus} variant="bar" />)
    expect(container.querySelector('.scrm-tier-cards')).toBeNull()
    expect(container.querySelector('.detail-section-container')).toBeNull()
    expect(screen.queryByText('Supply Chain Focus')).not.toBeInTheDocument()
  })

  it('ticks active tiers and dims inactive ones without hiding them', () => {
    const { container } = render(<SCRMFocusBadges focus={focus} variant="bar" />)
    const tiles = container.querySelectorAll('.scrm-bar-tile')

    expect(tiles[0].className).toContain('active')
    expect(tiles[0].textContent).toContain('✓')
    expect(tiles[1].className).toContain('active')

    expect(tiles[2].className).toContain('off')
    expect(tiles[2].textContent).not.toContain('✓')
    // dimmed, but still present and readable
    expect(tiles[2].textContent).toContain('T3')
  })

  it('gives every tier a provider sublabel', () => {
    const { container } = render(<SCRMFocusBadges focus={focus} variant="bar" />)
    expect(container.querySelectorAll('.scrm-bar-sub')).toHaveLength(3)
    for (const sub of container.querySelectorAll('.scrm-bar-sub')) {
      expect(sub.textContent).toBe('provider')
    }
  })

  it('derives the same active tiers as the card variant', () => {
    const onlyTier3: SCRMFocus = {
      tier1_strategic: false,
      tier2_operational: false,
      tier3_tactical: true,
    }
    // NB: classList.contains, not className.includes — "inactive" contains "active"
    // as a substring, so a naive check reports every card as active.
    const card = render(<SCRMFocusBadges focus={onlyTier3} />)
    const cardActive = [...card.container.querySelectorAll('.scrm-tier-card')]
      .map(el => el.classList.contains('active'))
    card.unmount()

    const bar = render(<SCRMFocusBadges focus={onlyTier3} variant="bar" />)
    const barActive = [...bar.container.querySelectorAll('.scrm-bar-tile')]
      .map(el => el.classList.contains('active'))

    expect(barActive).toEqual(cardActive)
    expect(barActive).toEqual([false, false, true])
  })
})
