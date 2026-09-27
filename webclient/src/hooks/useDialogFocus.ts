/**
 * useDialogFocus — keyboard focus belongs to the open dialog.
 *
 * On mount, focus moves to the element carrying `data-autofocus` inside the
 * dialog (else its first focusable element). While mounted, Tab and Shift+Tab
 * wrap inside the dialog instead of walking into the page behind the overlay.
 * On unmount, focus returns to whatever held it when the dialog opened —
 * normally the button that opened it.
 *
 * Escape and scroll locking stay with `useModalDismiss`; the two compose.
 * Call this from a component that is mounted only while its dialog is open.
 */
import { useEffect, type RefObject } from 'react'

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function focusableIn(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE))
}

export function useDialogFocus(dialogRef: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    const returnTo = document.activeElement as HTMLElement | null

    const initial = dialog.querySelector<HTMLElement>('[data-autofocus]') ?? focusableIn(dialog)[0]
    initial?.focus()

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return
      // Queried per keypress: the set changes as buttons enable and disable.
      const items = focusableIn(dialog)
      if (items.length === 0) {
        event.preventDefault()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      const active = document.activeElement
      if (event.shiftKey && (active === first || !dialog.contains(active))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (active === last || !dialog.contains(active))) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      // The opener may have unmounted with the dialog (e.g. a view switch).
      if (returnTo?.isConnected) returnTo.focus()
    }
  }, [dialogRef])
}

export default useDialogFocus
