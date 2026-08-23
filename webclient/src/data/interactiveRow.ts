import type { KeyboardEvent } from 'react'

/**
 * Props that turn a clickable `<div>` row into something a keyboard can reach.
 *
 * The app has a recurring shape: a list row whose click IS the navigation —
 * work-queue items, evidence health cards, stale alerts, sidebar control cards.
 * Written as a bare `<div onClick>` it is unreachable by Tab, invisible to a
 * screen reader's control list, and inert to Enter and Space. There is no
 * mouse-free path to the dashboard's primary navigation.
 *
 * Wrapping each row in a `<button>` is the textbook fix and is wrong here: the
 * rows contain their own nested interactive elements and a grid layout that a
 * button's default box would have to fight. Supplying the four ARIA/keyboard
 * props instead leaves the markup alone.
 *
 * `preventDefault` on Space matters and is the reason this is one helper rather
 * than five copies. `SidebarControlCard` hand-rolled this pattern and omitted
 * it, so Space activated the card AND scrolled the page — the failure is silent
 * and nobody testing with a mouse can see it.
 */
export interface InteractiveRowProps {
  role: 'button'
  tabIndex: number
  onClick: () => void
  onKeyDown: (event: KeyboardEvent) => void
}

/**
 * Returns the props for a row that navigates when activated.
 *
 * Returns `{}` when `onActivate` is undefined. Several callers take their
 * navigation handler as an optional prop and render the same row with or
 * without it; a row that announces itself as a button and then does nothing is
 * worse for a keyboard user than a plain div, because it takes a tab stop to
 * say so.
 */
export function interactiveRowProps(
  onActivate: (() => void) | undefined,
): InteractiveRowProps | Record<string, never> {
  if (!onActivate) return {}
  return {
    role: 'button',
    tabIndex: 0,
    onClick: onActivate,
    onKeyDown: (event: KeyboardEvent) => {
      if (event.key === 'Enter' || event.key === ' ') {
        // Space scrolls the page by default; Enter submits an enclosing form.
        event.preventDefault()
        onActivate()
      }
    },
  }
}
