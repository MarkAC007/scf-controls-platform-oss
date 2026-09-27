/**
 * Cover for UIP-015 — Shift+Tab from the New engagement drawer's first field
 * moved focus to the "+ New Engagement" button behind the open overlay.
 */
import { useRef, useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'

import { useFocusTrap } from '../useFocusTrap'

// jsdom lays nothing out, so every element reports zero client rects; the hook
// skips invisible elements by that test. Report one rect so the fields count.
const originalRects = HTMLElement.prototype.getClientRects
beforeAll(() => {
  HTMLElement.prototype.getClientRects = function () {
    return [{}] as unknown as DOMRectList
  }
})
afterAll(() => {
  HTMLElement.prototype.getClientRects = originalRects
})

function Dialog() {
  const ref = useRef<HTMLDivElement>(null)
  useFocusTrap(ref, true)
  return (
    <div ref={ref} role="dialog" aria-modal="true" tabIndex={-1}>
      <input aria-label="first" autoFocus />
      <button type="button">middle</button>
      <button type="button">last</button>
    </div>
  )
}

function Page() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>opener</button>
      {open && <Dialog />}
      {open && <button type="button" onClick={() => setOpen(false)}>close</button>}
    </>
  )
}

function openDialog() {
  const opener = screen.getByRole('button', { name: 'opener' })
  opener.focus()
  fireEvent.click(opener)
  return opener
}

describe('useFocusTrap', () => {
  it('keeps autoFocus on the first field', () => {
    render(<Page />)
    openDialog()
    expect(document.activeElement).toBe(screen.getByLabelText('first'))
  })

  it('wraps Shift+Tab from the first field to the last control', () => {
    render(<Page />)
    openDialog()
    const first = screen.getByLabelText('first')
    fireEvent.keyDown(first, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'last' }))
  })

  it('wraps Tab from the last control to the first field', () => {
    render(<Page />)
    openDialog()
    const last = screen.getByRole('button', { name: 'last' })
    last.focus()
    fireEvent.keyDown(last, { key: 'Tab' })
    expect(document.activeElement).toBe(screen.getByLabelText('first'))
  })

  it('leaves Tab between inner controls to the browser', () => {
    render(<Page />)
    openDialog()
    const middle = screen.getByRole('button', { name: 'middle' })
    middle.focus()
    const notPrevented = fireEvent.keyDown(middle, { key: 'Tab' })
    expect(notPrevented).toBe(true)
  })

  it('returns focus to the opener when the dialog closes', () => {
    render(<Page />)
    const opener = openDialog()
    // Closing from inside the dialog: focus is on a dialog control, then the
    // dialog unmounts and the browser drops focus to <body>.
    screen.getByRole('button', { name: 'last' }).focus()
    fireEvent.click(screen.getByRole('button', { name: 'close' }))
    expect(document.activeElement).toBe(opener)
  })
})
