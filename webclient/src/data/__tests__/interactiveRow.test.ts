import { describe, it, expect, vi } from 'vitest'
import type { KeyboardEvent } from 'react'
import { interactiveRowProps } from '../interactiveRow'

/** Minimal stand-in for React's synthetic keyboard event. */
function keyEvent(key: string) {
  return { key, preventDefault: vi.fn() } as unknown as KeyboardEvent & {
    preventDefault: ReturnType<typeof vi.fn>
  }
}

describe('interactiveRowProps', () => {
  it('marks the row as a button', () => {
    expect(interactiveRowProps(() => {})).toMatchObject({ role: 'button' })
  })

  it('puts the row in the tab order', () => {
    expect(interactiveRowProps(() => {})).toMatchObject({ tabIndex: 0 })
  })

  it('passes the activation handler through as onClick', () => {
    const onActivate = vi.fn()
    const props = interactiveRowProps(onActivate)
    ;(props as { onClick: () => void }).onClick()
    expect(onActivate).toHaveBeenCalledTimes(1)
  })

  it('activates on Enter', () => {
    const onActivate = vi.fn()
    const props = interactiveRowProps(onActivate) as {
      onKeyDown: (e: KeyboardEvent) => void
    }
    props.onKeyDown(keyEvent('Enter'))
    expect(onActivate).toHaveBeenCalledTimes(1)
  })

  it('activates on Space', () => {
    const onActivate = vi.fn()
    const props = interactiveRowProps(onActivate) as {
      onKeyDown: (e: KeyboardEvent) => void
    }
    props.onKeyDown(keyEvent(' '))
    expect(onActivate).toHaveBeenCalledTimes(1)
  })

  it('stops Space scrolling the page', () => {
    const props = interactiveRowProps(vi.fn()) as {
      onKeyDown: (e: KeyboardEvent) => void
    }
    const event = keyEvent(' ')
    props.onKeyDown(event)
    expect(event.preventDefault).toHaveBeenCalledTimes(1)
  })

  it('ignores keys that are not Enter or Space', () => {
    const onActivate = vi.fn()
    const props = interactiveRowProps(onActivate) as {
      onKeyDown: (e: KeyboardEvent) => void
    }
    for (const key of ['a', 'Tab', 'ArrowDown', 'Escape', 'Shift']) {
      const event = keyEvent(key)
      props.onKeyDown(event)
      expect(event.preventDefault).not.toHaveBeenCalled()
    }
    expect(onActivate).not.toHaveBeenCalled()
  })

  it('adds no tab stop when there is nothing to activate', () => {
    // Several callers take navigation as an optional prop. A row that claims to
    // be a button and then does nothing is worse than a plain div: it costs a
    // keyboard user a tab stop to discover that.
    expect(interactiveRowProps(undefined)).toEqual({})
  })
})
