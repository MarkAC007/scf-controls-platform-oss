/**
 * Test helpers for the in-app ConfirmDialog (#1157).
 *
 * The component tests that used to stub `window.confirm` / `window.prompt`
 * render inside `ConfirmProvider` and answer the real dialog instead. These
 * helpers find the open dialog, read its consequence text exactly as the user
 * sees it (the element `aria-describedby` points at), and answer it.
 *
 * Not a test file itself: vitest collects only `*.test.ts(x)`.
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'

import { ConfirmProvider } from '../contexts/ConfirmContext'

export { ConfirmProvider }

/** Wrap a tree in the provider. */
export function withConfirm(ui: ReactNode) {
  return <ConfirmProvider>{ui}</ConfirmProvider>
}

/** A `wrapper` for `render` that adds the provider inside another wrapper. */
export function confirmWrapper(Inner?: (props: { children: ReactNode }) => ReactNode) {
  return function ConfirmTestWrapper({ children }: { children: ReactNode }) {
    return <ConfirmProvider>{Inner ? <Inner>{children}</Inner> : children}</ConfirmProvider>
  }
}

export interface OpenConfirm {
  dialog: HTMLElement
  /** The consequence text, verbatim. */
  message: string
  /** The dialog's title (its accessible name). */
  title: string
  /** Click the confirming button (the one that is not Cancel). */
  accept: () => void
  /** Click the cancelling button. */
  cancel: () => void
  /** Type into the reason field. */
  type: (text: string) => void
}

/** Wait for the confirmation dialog to open and return a handle on it. */
export async function findConfirm(): Promise<OpenConfirm> {
  const dialog = await screen.findByRole('alertdialog')
  const describedBy = dialog.getAttribute('aria-describedby')
  const labelledBy = dialog.getAttribute('aria-labelledby')
  const message = describedBy ? (document.getElementById(describedBy)?.textContent ?? '') : ''
  const title = labelledBy ? (document.getElementById(labelledBy)?.textContent ?? '') : ''
  const buttons = within(dialog).getAllByRole('button')
  // The dialog renders [cancel, confirm], in that order.
  const [cancelButton, confirmButton] = buttons
  return {
    dialog,
    message,
    title,
    accept: () => fireEvent.click(confirmButton),
    cancel: () => fireEvent.click(cancelButton),
    type: (text: string) => fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: text } }),
  }
}

/** Wait for the dialog to close. */
export async function confirmClosed(): Promise<void> {
  await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
}

/** Wait for the dialog, answer it, wait for it to close. Returns its message for the caller to assert. */
export async function answerConfirm(answer: boolean): Promise<string> {
  const open = await findConfirm()
  if (answer) open.accept()
  else open.cancel()
  await confirmClosed()
  return open.message
}
