/**
 * ConfirmContext — promise-returning confirmation, in the page (#1157).
 *
 * `ConfirmProvider` is mounted once at the app root. Any component below it
 * asks for a confirmation with `useConfirm()`:
 *
 *   const confirm = useConfirm()
 *
 *   // a plain confirmation resolves true (confirmed) or false (cancelled)
 *   if (!(await confirm({ title: 'Delete engagement', message, confirmLabel: 'Delete', destructive: true }))) return
 *
 *   // with `reason` it resolves the trimmed reason, or null when cancelled
 *   const why = await confirm({ title: 'Remove from scope', message, reason: { label: 'Reason' } })
 *   if (why === null) return
 *
 * Choices, deliberately:
 * - One dialog at a time, requests queued. A request made while another is
 *   open waits and is shown when that one settles, in the order asked, so
 *   every caller gets a real answer and "declined" never stands in for
 *   "never asked".
 * - A request belongs to the component that made it. If that component
 *   unmounts (an org switch or session expiry swaps the tree under this
 *   provider), its request settles as a cancel: an open dialog closes and a
 *   queued one is never shown, so no answer can run a stale action.
 * - Outside a provider the hook still returns a function, so a component can
 *   render anywhere (including tests that never open the dialog), but calling
 *   it rejects with an error rather than resolving: a missing provider must
 *   not read as either "yes" or "no".
 * - If the provider unmounts with requests open or queued, they all settle
 *   as a cancel, so no caller is left awaiting forever.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import ConfirmDialog, { type ConfirmOptions, type ConfirmReasonField } from '../components/ConfirmDialog'

export type { ConfirmOptions, ConfirmReasonField } from '../components/ConfirmDialog'

/** Options for a confirmation that asks for a reason (what replaced the native prompt dialog). */
export type ConfirmWithReasonOptions = ConfirmOptions & { reason: ConfirmReasonField }

export interface ConfirmFn {
  /** Resolves `true` when confirmed, `false` when cancelled. */
  (options: ConfirmOptions & { reason?: undefined }): Promise<boolean>
  /** Resolves the accepted reason (trimmed, or exactly `mustEqual`), `null` when cancelled. */
  (options: ConfirmWithReasonOptions): Promise<string | null>
}

type AnyOptions = ConfirmOptions & { reason?: ConfirmReasonField }
type Answer = boolean | string | null

interface Request {
  id: number
  options: AnyOptions
  resolve: (value: Answer) => void
}

interface ConfirmApi {
  /** Queue a request; it is shown when every earlier one has settled. */
  request: (options: AnyOptions) => { id: number; answer: Promise<Answer> }
  /** Settle a request as a cancel, open or queued. Unknown ids are ignored. */
  withdraw: (id: number) => void
}

const ConfirmContext = createContext<ConfirmApi | null>(null)

const cancelValue = (options: AnyOptions): Answer => (options.reason ? null : false)

export function ConfirmProvider({ children }: { children: ReactNode }) {
  // The queue is the source of truth, held in a ref so requests made in the
  // same tick see each other. Its head is the open dialog; `open` mirrors it
  // for rendering.
  const queue = useRef<Request[]>([])
  const [open, setOpen] = useState<Request | null>(null)
  const nextId = useRef(1)

  const settle = useCallback((id: number, value: (request: Request) => Answer) => {
    const index = queue.current.findIndex((request) => request.id === id)
    if (index === -1) return
    const [request] = queue.current.splice(index, 1)
    if (index === 0) setOpen(queue.current[0] ?? null)
    request.resolve(value(request))
  }, [])

  const api = useMemo<ConfirmApi>(
    () => ({
      request: (options) => {
        const id = nextId.current++
        let resolve!: (value: Answer) => void
        const answer = new Promise<Answer>((settleWith) => {
          resolve = settleWith
        })
        const request: Request = { id, options, resolve }
        queue.current.push(request)
        if (queue.current.length === 1) setOpen(request)
        return { id, answer }
      },
      withdraw: (id) => settle(id, (request) => cancelValue(request.options)),
    }),
    [settle],
  )

  useEffect(
    () => () => {
      const pending = queue.current
      queue.current = []
      for (const request of pending) request.resolve(cancelValue(request.options))
    },
    [],
  )

  return (
    <ConfirmContext.Provider value={api}>
      {children}
      {open && (
        <ConfirmDialog
          // A new request is a new dialog: fresh field, fresh focus capture.
          key={open.id}
          {...open.options}
          onConfirm={(reason) => settle(open.id, (request) => (request.options.reason ? (reason ?? null) : true))}
          onCancel={() => settle(open.id, (request) => cancelValue(request.options))}
        />
      )}
    </ConfirmContext.Provider>
  )
}

function withoutProvider(): Promise<never> {
  return Promise.reject(
    new Error('useConfirm: no <ConfirmProvider> is mounted above this component, so nothing can be confirmed'),
  )
}

/** The app's confirmation step. See the module comment for the contract. */
export function useConfirm(): ConfirmFn {
  const api = useContext(ConfirmContext)
  // The requests this component made and that have not settled yet.
  const mine = useRef(new Set<number>())

  useEffect(() => {
    const requests = mine.current
    return () => {
      for (const id of requests) api?.withdraw(id)
      requests.clear()
    }
  }, [api])

  return useMemo(() => {
    if (!api) return withoutProvider as ConfirmFn
    return ((options: AnyOptions) => {
      const { id, answer } = api.request(options)
      mine.current.add(id)
      return answer.then((value) => {
        mine.current.delete(id)
        return value
      })
    }) as ConfirmFn
  }, [api])
}
