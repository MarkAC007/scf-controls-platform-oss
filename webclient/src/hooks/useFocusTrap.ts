/**
 * useFocusTrap — keeps Tab / Shift+Tab inside an open dialog and hands focus
 * back to whatever opened it when the dialog goes away.
 *
 * Companion to `useModalDismiss`, which owns Escape and the scroll lock. The
 * two are separate because most overlays here only need dismissal, while a
 * true modal (`aria-modal="true"`) also has to stop keyboard focus wandering
 * to the page behind it (UIP-015).
 *
 * The container is focused (it needs `tabIndex={-1}`) only when nothing inside
 * it already took focus — an `autoFocus` field keeps its focus.
 *
 * The opener is read during the first render, not in the effect: React applies
 * `autoFocus` while committing, before any effect runs, so by effect time
 * `document.activeElement` is already the dialog's own field.
 */
import { useEffect, useState, type RefObject } from 'react'

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function focusableWithin(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE))
    .filter(el => el.getClientRects().length > 0)
}

export function useFocusTrap(containerRef: RefObject<HTMLElement>, active: boolean): void {
  const [opener] = useState<HTMLElement | null>(() =>
    document.activeElement instanceof HTMLElement ? document.activeElement : null)

  useEffect(() => {
    const container = containerRef.current
    if (!active || !container) return

    if (!container.contains(document.activeElement)) container.focus()

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return
      const items = focusableWithin(container)
      if (items.length === 0) {
        event.preventDefault()
        container.focus()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      const current = document.activeElement
      if (event.shiftKey && (current === first || current === container)) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && current === last) {
        event.preventDefault()
        first.focus()
      }
    }

    container.addEventListener('keydown', onKeyDown)
    return () => {
      container.removeEventListener('keydown', onKeyDown)
      // Return focus to the opener only once the dialog has actually left the
      // page (StrictMode's simulated remount keeps it connected), the opener
      // is still there, and focus was not placed somewhere deliberate outside.
      if (!container.isConnected && opener && opener.isConnected && !container.contains(opener)) {
        const now = document.activeElement
        if (!now || now === document.body || container.contains(now)) opener.focus()
      }
    }
  }, [containerRef, active, opener])
}

export default useFocusTrap
