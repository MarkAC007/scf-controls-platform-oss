/**
 * useDialogFocus — keyboard focus belongs to the open dialog.
 *
 * On mount, focus moves to the element carrying `data-autofocus` inside the
 * dialog (else its first focusable element). While mounted, Tab and Shift+Tab
 * wrap inside the dialog instead of walking into the page behind the overlay.
 * On unmount, focus returns to whatever held it when the dialog opened —
 * normally the button that opened it. If that element is gone or disabled —
 * on close, or just after, when the opener reacts to the answer by removing
 * the row or going busy — focus goes to the dialog it sat in, else to the
 * topmost dialog still open, rather than falling to <body> under a modal.
 * The same holds when the opener was already gone as the dialog opened (it
 * was removed in the render that opened it, so focus sat on <body>).
 *
 * Escape and scroll locking stay with `useModalDismiss`; the two compose.
 * Call this from a component that is mounted only while its dialog is open.
 *
 * Open traps form a stack, as `useModalDismiss` registrations do: only the
 * most recently opened dialog acts on Tab. Every trap listens on `document`,
 * so without this a confirmation raised from inside a modal (#1157) would be
 * fought over — the outer trap sees focus outside itself and pulls it back.
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

/** Open dialogs, oldest first. Only the last one traps Tab. */
const openDialogs: HTMLElement[] = []

const DIALOG_SELECTOR = '[role="dialog"], [role="alertdialog"], [aria-modal="true"]'

/** How long after closing to keep watching the opener for removal or disabling. */
const RETURN_WATCH_MS = 2000

function canTakeFocus(element: HTMLElement): boolean {
  return element.isConnected && !(element as HTMLButtonElement).disabled
}

/** Focus a container that may not be focusable itself, without adding it to the Tab order. */
function focusContainer(element: HTMLElement): void {
  if (!element.hasAttribute('tabindex')) element.setAttribute('tabindex', '-1')
  element.focus()
}

/**
 * Hand focus back to `returnTo`, or to a dialog that still holds the user,
 * and keep doing so briefly: the opener usually reacts to the answer in a
 * render that lands after this dialog has gone.
 */
function returnFocus(returnTo: HTMLElement | null, returnScope: HTMLElement | null): void {
  const fallback = () => {
    const scope = returnScope?.isConnected ? returnScope : null
    const top = [...openDialogs].reverse().find((dialog) => dialog.isConnected) ?? null
    const target = scope ?? top
    if (target) focusContainer(target)
  }
  if (!returnTo || returnTo === document.body) {
    // No opener to go back to. Under another modal, focus belongs in it; on a
    // bare page, leave focus where it is.
    const active = document.activeElement
    if (active === null || active === document.body) fallback()
    return
  }
  if (canTakeFocus(returnTo)) returnTo.focus()
  else {
    fallback()
    return
  }

  const stop = () => {
    observer.disconnect()
    window.clearTimeout(timer)
    document.removeEventListener('focusin', onFocusIn, true)
  }
  const check = () => {
    if (canTakeFocus(returnTo)) return
    stop()
    const active = document.activeElement
    if (active === returnTo || active === document.body || active === null) fallback()
  }
  // Focus moved on (another dialog opened, the user clicked elsewhere): done.
  const onFocusIn = (event: FocusEvent) => {
    if (event.target !== returnTo) stop()
  }
  const observer = new MutationObserver(check)
  observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['disabled'] })
  document.addEventListener('focusin', onFocusIn, true)
  const timer = window.setTimeout(stop, RETURN_WATCH_MS)
}

export function useDialogFocus(dialogRef: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    const returnTo = document.activeElement as HTMLElement | null
    const returnScope = returnTo?.closest<HTMLElement>(DIALOG_SELECTOR) ?? null
    openDialogs.push(dialog)

    const initial = dialog.querySelector<HTMLElement>('[data-autofocus]') ?? focusableIn(dialog)[0]
    initial?.focus()

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return
      if (openDialogs[openDialogs.length - 1] !== dialog) return
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
      // By identity, not position: StrictMode re-runs effects, and a dialog
      // under the top can close first.
      const index = openDialogs.lastIndexOf(dialog)
      if (index !== -1) openDialogs.splice(index, 1)
      returnFocus(returnTo, returnScope)
    }
  }, [dialogRef])
}

export default useDialogFocus
