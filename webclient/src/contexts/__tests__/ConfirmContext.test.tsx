/**
 * #1157 — the in-app confirmation that replaces window.confirm / window.prompt.
 *
 * The dialog is driven through the promise `useConfirm()` hands back, the way
 * every call site uses it: render a trigger, click it, answer the dialog, and
 * read what the promise settled with.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useEffect, useRef, useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { useDialogFocus } from '../../hooks/useDialogFocus'
import { ConfirmProvider, useConfirm, type ConfirmOptions, type ConfirmReasonField } from '../ConfirmContext'

type Settled = boolean | string | null | 'rejected'

/** A trigger button that opens one confirmation and shows what it settled with. */
function Harness({ options, reason }: { options: ConfirmOptions; reason?: ConfirmReasonField }) {
  const confirm = useConfirm()
  const [settled, setSettled] = useState<Settled | undefined>(undefined)
  const open = async () => {
    try {
      setSettled(reason ? await confirm({ ...options, reason }) : await confirm(options))
    } catch {
      setSettled('rejected')
    }
  }
  return (
    <>
      <button type="button" onClick={() => void open()}>Open</button>
      <output data-testid="settled">{settled === undefined ? 'pending' : JSON.stringify(settled)}</output>
    </>
  )
}

const OPTIONS: ConfirmOptions = {
  title: 'Delete engagement',
  message: 'Delete engagement “Q3 audit”? This cannot be undone.',
  confirmLabel: 'Delete engagement',
  destructive: true,
}

function renderHarness(reason?: ConfirmReasonField, options: ConfirmOptions = OPTIONS) {
  render(
    <ConfirmProvider>
      <Harness options={options} reason={reason} />
    </ConfirmProvider>,
  )
  return screen.getByRole('button', { name: 'Open' })
}

const settled = () => screen.getByTestId('settled').textContent

describe('ConfirmDialog', () => {
  it('is an alertdialog labelled by its title and described by the consequence, word for word', async () => {
    const user = userEvent.setup()
    await user.click(renderHarness())

    const dialog = await screen.findByRole('alertdialog', { name: 'Delete engagement' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    const describedBy = dialog.getAttribute('aria-describedby')
    expect(describedBy).toBeTruthy()
    const consequence = document.getElementById(describedBy as string)
    expect(consequence?.textContent).toBe('Delete engagement “Q3 audit”? This cannot be undone.')
    expect(dialog).toHaveAccessibleDescription('Delete engagement “Q3 audit”? This cannot be undone.')
  })

  it('shows the destructive variant with the danger button class', async () => {
    const user = userEvent.setup()
    await user.click(renderHarness())
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByRole('button', { name: 'Delete engagement' })).toHaveClass('btn-danger')
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveClass('btn-secondary')
  })

  it('uses the primary button when the action is not destructive', async () => {
    const user = userEvent.setup()
    await user.click(renderHarness(undefined, { title: 'Take snapshot', message: 'Take it now?', confirmLabel: 'Take snapshot' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByRole('button', { name: 'Take snapshot' })).toHaveClass('btn-primary')
    expect(within(dialog).getByRole('button', { name: 'Take snapshot' })).not.toHaveClass('btn-danger')
  })

  it('resolves true on confirm and closes', async () => {
    const user = userEvent.setup()
    await user.click(renderHarness())
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Delete engagement' }))
    await waitFor(() => expect(settled()).toBe('true'))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })

  it('resolves false on Cancel', async () => {
    const user = userEvent.setup()
    await user.click(renderHarness())
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(settled()).toBe('false'))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })

  it('resolves false on Escape', async () => {
    const user = userEvent.setup()
    await user.click(renderHarness())
    await screen.findByRole('alertdialog')
    await user.keyboard('{Escape}')
    await waitFor(() => expect(settled()).toBe('false'))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })

  it('resolves false on a click on the backdrop', async () => {
    const user = userEvent.setup()
    await user.click(renderHarness())
    const dialog = await screen.findByRole('alertdialog')
    await user.click(dialog.parentElement as HTMLElement)
    await waitFor(() => expect(settled()).toBe('false'))
  })

  it('stays open when a press starts inside the dialog and is released on the backdrop', async () => {
    // Selecting text, or dragging out of the reason field, ends on the
    // backdrop; the click lands there too, and must not throw the text away.
    const user = userEvent.setup()
    await user.click(renderHarness({ label: 'Reason' }))
    const dialog = await screen.findByRole('alertdialog')
    const overlay = dialog.parentElement as HTMLElement
    const input = within(dialog).getByRole('textbox', { name: 'Reason' })
    await user.type(input, 'half a reason')
    fireEvent.mouseDown(input)
    fireEvent.mouseUp(overlay)
    fireEvent.click(overlay)
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    expect(input).toHaveValue('half a reason')
    expect(settled()).toBe('pending')
    // A press that starts and ends on the backdrop still dismisses.
    fireEvent.mouseDown(overlay)
    fireEvent.mouseUp(overlay)
    fireEvent.click(overlay)
    await waitFor(() => expect(settled()).toBe('null'))
  })

  it('focuses Cancel first on a destructive confirmation, and traps Tab inside the dialog', async () => {
    const user = userEvent.setup()
    await user.click(renderHarness())
    const dialog = await screen.findByRole('alertdialog')
    const cancel = within(dialog).getByRole('button', { name: 'Cancel' })
    const ok = within(dialog).getByRole('button', { name: 'Delete engagement' })
    expect(cancel).toHaveFocus()
    await user.tab()
    expect(ok).toHaveFocus()
    await user.tab()
    expect(cancel).toHaveFocus()
    await user.tab({ shift: true })
    expect(ok).toHaveFocus()
  })

  it('focuses the confirm button first when the action is not destructive, as the native dialog did', async () => {
    const user = userEvent.setup()
    await user.click(renderHarness(undefined, { title: 'Take snapshot', message: 'Take it now?', confirmLabel: 'Take snapshot' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByRole('button', { name: 'Take snapshot' })).toHaveFocus()
  })

  it('owns Tab when raised from inside a modal that has its own focus trap', async () => {
    // CapabilityTestFormModal (useDialogFocus) asks before removing a finding.
    // Both traps listen on document; only the topmost may act, or the outer
    // one pulls focus out of the confirmation on every Tab.
    function OuterModal() {
      const ref = useRef<HTMLDivElement>(null)
      useDialogFocus(ref)
      const confirm = useConfirm()
      return (
        <div ref={ref} role="dialog" aria-label="Outer">
          <button type="button" onClick={() => void confirm(OPTIONS)}>Remove finding</button>
          <button type="button">Outer last</button>
        </div>
      )
    }
    const user = userEvent.setup()
    render(
      <ConfirmProvider>
        <OuterModal />
      </ConfirmProvider>,
    )
    const trigger = screen.getByRole('button', { name: 'Remove finding' })
    await user.click(trigger)
    const dialog = await screen.findByRole('alertdialog')
    const cancel = within(dialog).getByRole('button', { name: 'Cancel' })
    const ok = within(dialog).getByRole('button', { name: 'Delete engagement' })
    expect(cancel).toHaveFocus()
    await user.tab()
    expect(ok).toHaveFocus()
    await user.tab()
    expect(cancel).toHaveFocus()

    // Closed, the outer trap is back in charge and focus is on its trigger.
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(trigger).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: 'Outer last' })).toHaveFocus()
    await user.tab()
    expect(trigger).toHaveFocus()
  })

  it('returns focus to the trigger after it closes', async () => {
    const user = userEvent.setup()
    const trigger = renderHarness()
    await user.click(trigger)
    await screen.findByRole('alertdialog')
    expect(trigger).not.toHaveFocus()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(trigger).toHaveFocus()
  })

  it('ignores a held Enter carried over from the trigger until a key is released', async () => {
    // Enter on a button activates it on keydown. Held on the trigger, the
    // auto-repeat lands on the dialog's focused Confirm and would answer the
    // question before the user has read it; the native dialog swallowed it.
    const user = userEvent.setup()
    await user.click(renderHarness(undefined, { title: 'Take snapshot', message: 'Take it now?', confirmLabel: 'Take snapshot' }))
    const dialog = await screen.findByRole('alertdialog')
    const ok = within(dialog).getByRole('button', { name: 'Take snapshot' })
    expect(ok).toHaveFocus()
    // fireEvent returns false when the default action (here: activation) was prevented.
    expect(fireEvent.keyDown(ok, { key: 'Enter', repeat: true })).toBe(false)
    expect(fireEvent.keyDown(ok, { key: 'Enter', repeat: true })).toBe(false)
    expect(fireEvent.keyDown(ok, { key: ' ', repeat: true })).toBe(false)
    expect(settled()).toBe('pending')
    fireEvent.keyUp(ok, { key: 'Enter' })
    // Released and pressed again: a deliberate answer goes through.
    expect(fireEvent.keyDown(ok, { key: 'Enter' })).toBe(true)
  })

  it('takes a fresh Enter at once when nothing was held (opened with the mouse)', async () => {
    const user = userEvent.setup()
    await user.click(renderHarness(undefined, { title: 'Take snapshot', message: 'Take it now?', confirmLabel: 'Take snapshot' }))
    const ok = within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Take snapshot' })
    expect(fireEvent.keyDown(ok, { key: 'Enter' })).toBe(true)
  })

  it('does not submit a reason on a held Enter either', async () => {
    const user = userEvent.setup()
    await user.click(renderHarness({ label: 'Reason' }))
    const input = within(await screen.findByRole('alertdialog')).getByRole('textbox', { name: 'Reason' })
    fireEvent.change(input, { target: { value: 'typed' } })
    fireEvent.keyDown(input, { key: 'Enter', repeat: true })
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    expect(settled()).toBe('pending')
  })

  it('lets arrow keys and other keys reach the page: shortcuts decide for themselves', async () => {
    // Page shortcuts skip events from inside a modal (keyEventBelongsToModal);
    // the dialog no longer swallows keys on their behalf.
    const onWindowKey = vi.fn()
    window.addEventListener('keydown', onWindowKey)
    try {
      const user = userEvent.setup()
      await user.click(renderHarness())
      await screen.findByRole('alertdialog')
      onWindowKey.mockClear()
      await user.keyboard('{ArrowRight}')
      expect(onWindowKey).toHaveBeenCalledTimes(1)
    } finally {
      window.removeEventListener('keydown', onWindowKey)
    }
  })

  it('renders a message with line breaks as separate lines', async () => {
    const user = userEvent.setup()
    await user.click(renderHarness(undefined, { title: 'Archive team', message: 'Archive "Ops"?\n\nNothing is deleted.' }))
    const dialog = await screen.findByRole('alertdialog')
    const consequence = document.getElementById(dialog.getAttribute('aria-describedby') as string)
    expect(consequence?.textContent).toBe('Archive "Ops"?\n\nNothing is deleted.')
    expect(consequence).toHaveClass('confirm-dialog-message')
  })
})

describe('ConfirmDialog with a required reason (replaces window.prompt)', () => {
  const reason: ConfirmReasonField = { label: 'Reason', required: true }

  it('blocks confirm until the reason is non-empty, then resolves the trimmed reason', async () => {
    const user = userEvent.setup()
    await user.click(renderHarness(reason))
    const dialog = await screen.findByRole('alertdialog')
    const input = within(dialog).getByRole('textbox', { name: 'Reason' })
    const ok = within(dialog).getByRole('button', { name: 'Delete engagement' })
    expect(input).toHaveFocus()
    expect(ok).toBeDisabled()

    await user.type(input, '   ')
    expect(ok).toBeDisabled()

    await user.type(input, '  out of scope for 2026  ')
    expect(ok).toBeEnabled()
    await user.click(ok)
    await waitFor(() => expect(settled()).toBe('"out of scope for 2026"'))
  })

  it('submits on Enter only when the reason is acceptable', async () => {
    const user = userEvent.setup()
    await user.click(renderHarness(reason))
    const input = within(await screen.findByRole('alertdialog')).getByRole('textbox', { name: 'Reason' })
    await user.type(input, '{Enter}')
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    expect(settled()).toBe('pending')
    await user.type(input, 'retired system{Enter}')
    await waitFor(() => expect(settled()).toBe('"retired system"'))
  })

  it('resolves null on cancel', async () => {
    const user = userEvent.setup()
    await user.click(renderHarness(reason))
    await screen.findByRole('alertdialog')
    await user.keyboard('{Escape}')
    await waitFor(() => expect(settled()).toBe('null'))
  })

  it('with mustEqual, confirm stays disabled until the exact text is typed', async () => {
    const user = userEvent.setup()
    await user.click(renderHarness({ label: 'Confirmation text', mustEqual: 'REMOVE ALL' }))
    const dialog = await screen.findByRole('alertdialog')
    const input = within(dialog).getByRole('textbox', { name: 'Confirmation text' })
    const ok = within(dialog).getByRole('button', { name: 'Delete engagement' })
    await user.type(input, 'remove all')
    expect(ok).toBeDisabled()
    await user.clear(input)
    await user.type(input, 'REMOVE ALL ')
    expect(ok).toBeDisabled()
    await user.clear(input)
    await user.type(input, 'REMOVE ALL')
    expect(ok).toBeEnabled()
    await user.click(ok)
    await waitFor(() => expect(settled()).toBe('"REMOVE ALL"'))
  })
})

describe('ConfirmProvider', () => {
  it('queues a request made while another is open, and shows each in turn', async () => {
    const results: unknown[] = []
    function Triple() {
      const confirm = useConfirm()
      useEffect(() => {
        void confirm({ title: 'First', message: 'First?' }).then((v) => results.push(['first', v]))
        void confirm({ title: 'Second', message: 'Second?' }).then((v) => results.push(['second', v]))
        void confirm({ title: 'Third', message: 'Third?', reason: { label: 'Why' } }).then((v) => results.push(['third', v]))
      }, [confirm])
      return null
    }
    // No StrictMode here: its double effect would itself be more requests.
    render(
      <ConfirmProvider>
        <Triple />
      </ConfirmProvider>,
    )
    const first = await screen.findByRole('alertdialog', { name: 'First' })
    expect(screen.getAllByRole('alertdialog')).toHaveLength(1)
    expect(results).toEqual([])
    fireEvent.click(within(first).getByRole('button', { name: 'Confirm' }))

    const second = await screen.findByRole('alertdialog', { name: 'Second' })
    expect(screen.getAllByRole('alertdialog')).toHaveLength(1)
    fireEvent.click(within(second).getByRole('button', { name: 'Cancel' }))

    const third = await screen.findByRole('alertdialog', { name: 'Third' })
    expect(screen.getAllByRole('alertdialog')).toHaveLength(1)
    fireEvent.change(within(third).getByRole('textbox', { name: 'Why' }), { target: { value: 'because' } })
    fireEvent.click(within(third).getByRole('button', { name: 'Confirm' }))

    await waitFor(() => expect(results).toEqual([['first', true], ['second', false], ['third', 'because']]))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })

  it('opens a fresh dialog for a request made after the previous one settled', async () => {
    const user = userEvent.setup()
    function Sequence() {
      const confirm = useConfirm()
      const [log, setLog] = useState<string[]>([])
      const run = async () => {
        const first = await confirm({ title: 'First', message: 'First?', reason: { label: 'Why' } })
        const second = await confirm({ title: 'Second', message: 'Second?', reason: { label: 'Why' } })
        setLog([String(first), String(second)])
      }
      return (
        <>
          <button type="button" onClick={() => void run()}>Run</button>
          <output data-testid="log">{log.join('|')}</output>
        </>
      )
    }
    render(
      <ConfirmProvider>
        <Sequence />
      </ConfirmProvider>,
    )
    await user.click(screen.getByRole('button', { name: 'Run' }))
    const first = await screen.findByRole('alertdialog', { name: 'First' })
    await user.type(within(first).getByRole('textbox', { name: 'Why' }), 'one{Enter}')
    const second = await screen.findByRole('alertdialog', { name: 'Second' })
    // A new dialog, not the old one with its text still in it.
    expect(within(second).getByRole('textbox', { name: 'Why' })).toHaveValue('')
    await user.type(within(second).getByRole('textbox', { name: 'Why' }), 'two{Enter}')
    await waitFor(() => expect(screen.getByTestId('log')).toHaveTextContent('one|two'))
  })

  it('settles an open request as a cancel when the provider unmounts', async () => {
    let result: unknown = 'pending'
    function Opener() {
      const confirm = useConfirm()
      useEffect(() => {
        void confirm({ title: 'T', message: 'M' }).then((v) => {
          result = v
        })
      }, [confirm])
      return null
    }
    const { unmount } = render(
      <ConfirmProvider>
        <Opener />
      </ConfirmProvider>,
    )
    await screen.findByRole('alertdialog')
    unmount()
    await waitFor(() => expect(result).toBe(false))
  })

  it('cancels an open request when the component that asked unmounts', async () => {
    // An org switch or session expiry swaps the tree under the provider; the
    // question must not outlive the screen whose action it would run.
    let result: unknown = 'pending'
    function Asker() {
      const confirm = useConfirm()
      useEffect(() => {
        void confirm({ title: 'Stale', message: 'Run the old action?' }).then((v) => {
          result = v
        })
      }, [confirm])
      return null
    }
    function Page() {
      const [show, setShow] = useState(true)
      return (
        <>
          <button type="button" onClick={() => setShow(false)}>Switch org</button>
          {show && <Asker />}
        </>
      )
    }
    render(
      <ConfirmProvider>
        <Page />
      </ConfirmProvider>,
    )
    await screen.findByRole('alertdialog', { name: 'Stale' })
    fireEvent.click(screen.getByRole('button', { name: 'Switch org' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    await waitFor(() => expect(result).toBe(false))
  })

  it('drops a queued request whose requester unmounted before its turn', async () => {
    const results: unknown[] = []
    function Asker({ title }: { title: string }) {
      const confirm = useConfirm()
      useEffect(() => {
        void confirm({ title, message: `${title}?`, reason: { label: 'Why' } }).then((v) => results.push([title, v]))
      }, [confirm, title])
      return null
    }
    function Page() {
      const [showSecond, setShowSecond] = useState(true)
      return (
        <>
          <Asker title="Kept" />
          {showSecond && <Asker title="Gone" />}
          <button type="button" onClick={() => setShowSecond(false)}>Drop second</button>
        </>
      )
    }
    render(
      <ConfirmProvider>
        <Page />
      </ConfirmProvider>,
    )
    const kept = await screen.findByRole('alertdialog', { name: 'Kept' })
    fireEvent.click(screen.getByRole('button', { name: 'Drop second', hidden: true }))
    await waitFor(() => expect(results).toEqual([['Gone', null]]))
    fireEvent.change(within(kept).getByRole('textbox', { name: 'Why' }), { target: { value: 'yes' } })
    fireEvent.click(within(kept).getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(results).toEqual([['Gone', null], ['Kept', 'yes']]))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })

  it('outside a provider, the hook renders but calling confirm rejects loudly', async () => {
    render(<Harness options={OPTIONS} />)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Open' }))
    })
    await waitFor(() => expect(settled()).toBe('"rejected"'))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })
})

describe('ConfirmDialog focus return when the trigger is gone', () => {
  // CapabilityTestFormModal removes the finding (and its button) once the
  // removal is confirmed; EngagementsPage disables its buttons while busy.
  // Focus must stay in the modal underneath rather than fall to <body>,
  // whether the trigger goes once the answer lands or while the question is
  // still open.
  function OuterModal({ after, when }: { after: 'remove' | 'disable'; when: 'answered' | 'asking' }) {
    const ref = useRef<HTMLDivElement>(null)
    useDialogFocus(ref)
    const confirm = useConfirm()
    const [present, setPresent] = useState(true)
    const [busy, setBusy] = useState(false)
    const lose = () => (after === 'remove' ? setPresent(false) : setBusy(true))
    const ask = async () => {
      if (when === 'asking') lose()
      if (!(await confirm(OPTIONS))) return
      if (when === 'answered') lose()
    }
    return (
      <div ref={ref} role="dialog" aria-label="Outer" data-testid="outer">
        <button type="button">Outer first</button>
        {present && (
          <button type="button" disabled={busy} onClick={() => void ask()}>
            Remove finding
          </button>
        )}
      </div>
    )
  }

  for (const when of ['answered', 'asking'] as const)
  for (const after of ['remove', 'disable'] as const) {
    it(`keeps focus in the outer modal when the trigger is ${after === 'remove' ? 'unmounted' : 'disabled'} ${when === 'answered' ? 'after the answer' : 'while the question is open'}`, async () => {
      const user = userEvent.setup()
      render(
        <ConfirmProvider>
          <OuterModal after={after} when={when} />
        </ConfirmProvider>,
      )
      await user.click(screen.getByRole('button', { name: 'Remove finding' }))
      const dialog = await screen.findByRole('alertdialog')
      await user.click(within(dialog).getByRole('button', { name: 'Delete engagement' }))
      await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
      const outer = screen.getByTestId('outer')
      await waitFor(() => expect(document.activeElement).toBe(outer))
    })
  }
})
