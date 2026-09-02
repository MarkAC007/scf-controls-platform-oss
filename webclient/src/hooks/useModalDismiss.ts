/**
 * useModalDismiss — Escape closes the modal, and the page behind it stops
 * scrolling while it is open.
 *
 * Every overlay in this app was built the same way — a `modal-overlay` div
 * whose backdrop click closes it and a ✕ in the corner — and none of them
 * answered the Escape key or stopped the list behind them scrolling under the
 * dialog. This hook is the one place that behaviour lives, so a new modal gets
 * it by calling one line rather than by remembering two effects.
 *
 * `active` is a parameter rather than the hook being called conditionally
 * because most overlays here are inline conditional JSX inside the parent
 * screen (`{showModal && <div className="modal-overlay">…}`), and a hook cannot
 * be called inside that condition. A component that *is* the modal — mounted
 * only while open — passes `true`.
 *
 * Registrations form a stack, which is what makes a confirm dialog stacked on
 * top of a management modal behave: Escape closes only the topmost, and the
 * body stays locked when that inner dialog unmounts while the outer modal is
 * still open.
 */
import { useEffect, useRef } from 'react'

/** One open overlay's registration. Identity is what unregisters it. */
interface DismissRegistration {
  close: () => void
}

/** Open overlays, oldest first. The last entry owns the Escape key. */
const stack: DismissRegistration[] = []

/**
 * `document.body.style.overflow` as it was before the first overlay opened.
 *
 * Captured once on the empty→non-empty transition and restored once on the way
 * back, so nested dialogs cannot leave the page permanently unscrollable — nor
 * unlock it while an outer modal is still covering the screen.
 */
let overflowBeforeLock: string | null = null

function handleKeyDown(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return
  const top = stack[stack.length - 1]
  if (!top) return

  // The open modal consumes the key outright. Every detail page in this app
  // already listens for Escape to navigate back — some on `window`, some on
  // `document` — so without this, closing a dialog over one of those screens
  // would also leave the screen behind it. Capture phase plus
  // `stopImmediatePropagation` is what puts the topmost overlay first.
  event.preventDefault()
  event.stopImmediatePropagation()
  top.close()
}

function register(entry: DismissRegistration): void {
  stack.push(entry)
  if (stack.length === 1) {
    overflowBeforeLock = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', handleKeyDown, true)
  }
}

function unregister(entry: DismissRegistration): void {
  const index = stack.indexOf(entry)
  // An entry already gone is not an error: StrictMode runs effects twice, and
  // splicing on a stale index would evict somebody else's overlay.
  if (index === -1) return
  stack.splice(index, 1)
  if (stack.length === 0) {
    document.removeEventListener('keydown', handleKeyDown, true)
    document.body.style.overflow = overflowBeforeLock ?? ''
    overflowBeforeLock = null
  }
}

export function useModalDismiss(active: boolean, onClose: () => void): void {
  // Callers declare `onClose` inline, so its identity changes on every render.
  // Held in a ref, the registration below can key on `active` alone instead of
  // tearing the overlay off the stack and pushing it back on each render.
  const onCloseRef = useRef(onClose)
  useEffect(() => {
    onCloseRef.current = onClose
  })

  useEffect(() => {
    if (!active) return
    const entry: DismissRegistration = { close: () => onCloseRef.current() }
    register(entry)
    return () => unregister(entry)
  }, [active])
}

export default useModalDismiss
