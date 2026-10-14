/**
 * Cover for D-09 — Escape closed no modal in this app, and the list behind the
 * users modal scrolled under the dialog.
 *
 * The stacking cases matter most: a confirm dialog opened on top of a
 * management modal must take the Escape press by itself, and must not unlock
 * the body on its way out while the modal underneath is still covering it.
 */
import { renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { keyEventBelongsToModal, useModalDismiss } from '../useModalDismiss'

function pressKey(key: string): void {
  document.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
}

afterEach(() => {
  document.body.style.overflow = ''
})

describe('useModalDismiss', () => {
  it('closes on Escape while active', () => {
    const onClose = vi.fn()
    renderHook(() => useModalDismiss(true, onClose))

    pressKey('Escape')

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('ignores Escape while inactive', () => {
    const onClose = vi.fn()
    renderHook(() => useModalDismiss(false, onClose))

    pressKey('Escape')

    expect(onClose).not.toHaveBeenCalled()
  })

  it('ignores keys other than Escape', () => {
    const onClose = vi.fn()
    renderHook(() => useModalDismiss(true, onClose))

    pressKey('Enter')
    pressKey('a')

    expect(onClose).not.toHaveBeenCalled()
  })

  it('locks body scroll while active and restores the previous value', () => {
    document.body.style.overflow = 'auto'
    const { unmount } = renderHook(() => useModalDismiss(true, vi.fn()))

    expect(document.body.style.overflow).toBe('hidden')

    unmount()

    expect(document.body.style.overflow).toBe('auto')
  })

  it('gives Escape to the topmost overlay only', () => {
    const outerClose = vi.fn()
    const innerClose = vi.fn()
    renderHook(() => useModalDismiss(true, outerClose))
    renderHook(() => useModalDismiss(true, innerClose))

    pressKey('Escape')

    expect(innerClose).toHaveBeenCalledTimes(1)
    expect(outerClose).not.toHaveBeenCalled()
  })

  it('keeps the body locked when an inner dialog closes over an open modal', () => {
    const outer = renderHook(() => useModalDismiss(true, vi.fn()))
    const inner = renderHook(() => useModalDismiss(true, vi.fn()))

    inner.unmount()
    expect(document.body.style.overflow).toBe('hidden')

    outer.unmount()
    expect(document.body.style.overflow).toBe('')
  })

  it('hands Escape back to the outer overlay once the inner one closes', () => {
    const outerClose = vi.fn()
    const innerClose = vi.fn()
    renderHook(() => useModalDismiss(true, outerClose))
    const inner = renderHook(() => useModalDismiss(true, innerClose))

    inner.unmount()
    pressKey('Escape')

    expect(outerClose).toHaveBeenCalledTimes(1)
    expect(innerClose).not.toHaveBeenCalled()
  })

  it('calls the latest onClose after a re-render, without re-registering', () => {
    const first = vi.fn()
    const second = vi.fn()
    const { rerender } = renderHook(({ onClose }) => useModalDismiss(true, onClose), {
      initialProps: { onClose: first },
    })

    rerender({ onClose: second })
    pressKey('Escape')

    expect(second).toHaveBeenCalledTimes(1)
    expect(first).not.toHaveBeenCalled()
  })

  it('keeps Escape from reaching the screen behind the modal', () => {
    // The detail pages navigate back on Escape — EvidenceDetailPage on
    // `window`, TaskDetailPage on `document`. An open dialog must swallow the
    // key rather than close itself and the page underneath it.
    const behindOnWindow = vi.fn()
    const behindOnDocument = vi.fn()
    window.addEventListener('keydown', behindOnWindow)
    document.addEventListener('keydown', behindOnDocument)

    const onClose = vi.fn()
    renderHook(() => useModalDismiss(true, onClose))

    pressKey('Escape')

    expect(onClose).toHaveBeenCalledTimes(1)
    expect(behindOnWindow).not.toHaveBeenCalled()
    expect(behindOnDocument).not.toHaveBeenCalled()

    window.removeEventListener('keydown', behindOnWindow)
    document.removeEventListener('keydown', behindOnDocument)
  })

  it('leaves other keys reaching the screen behind the modal', () => {
    const behind = vi.fn()
    window.addEventListener('keydown', behind)

    renderHook(() => useModalDismiss(true, vi.fn()))

    pressKey('ArrowRight')

    expect(behind).toHaveBeenCalledTimes(1)

    window.removeEventListener('keydown', behind)
  })

  it('unregisters when active goes false', () => {
    const onClose = vi.fn()
    const { rerender } = renderHook(({ active }) => useModalDismiss(active, onClose), {
      initialProps: { active: true },
    })

    rerender({ active: false })

    expect(document.body.style.overflow).toBe('')
    pressKey('Escape')
    expect(onClose).not.toHaveBeenCalled()
  })
})

describe('keyEventBelongsToModal', () => {
  const keyFrom = (target: EventTarget) => {
    let seen: KeyboardEvent | null = null
    const grab = (event: KeyboardEvent) => { seen = event }
    window.addEventListener('keydown', grab)
    target.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))
    window.removeEventListener('keydown', grab)
    return seen as unknown as KeyboardEvent
  }

  it('is false for a key on the page with no modal open', () => {
    expect(keyEventBelongsToModal(keyFrom(document.body))).toBe(false)
  })

  it('is true for a key from inside an aria-modal or dialog element', () => {
    for (const attrs of [{ 'aria-modal': 'true' }, { role: 'dialog' }, { role: 'alertdialog' }]) {
      const modal = document.createElement('div')
      for (const [k, v] of Object.entries(attrs)) modal.setAttribute(k, v)
      const button = document.createElement('button')
      modal.appendChild(button)
      document.body.appendChild(modal)
      try {
        expect(keyEventBelongsToModal(keyFrom(button))).toBe(true)
      } finally {
        modal.remove()
      }
    }
  })

  it('is true for any key while a modal is registered, even with focus on body', () => {
    const { unmount } = renderHook(() => useModalDismiss(true, vi.fn()))
    expect(keyEventBelongsToModal(keyFrom(document.body))).toBe(true)
    unmount()
    expect(keyEventBelongsToModal(keyFrom(document.body))).toBe(false)
  })
})
