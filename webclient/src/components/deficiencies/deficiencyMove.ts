/**
 * Moving a deficiency between statuses, under the lifecycle rules (PIG R8).
 *
 * One decision for every way a status changes by hand: the board's drag and
 * its Move to… menu (#1155), and the list's Cancel and Reopen buttons. So a
 * move asks the same question wherever it starts.
 *
 * - Open ↔ In progress: one write, no question.
 * - To Completed: never a write here. The completion form takes it, because
 *   completing needs a retest note; the deficiency stays put until that form
 *   is submitted.
 * - To Cancelled: asked first. A cancelled deficiency stops counting; it is
 *   never deleted.
 * - Out of Completed or Cancelled: a reopen, asked first, because reopening a
 *   completed one clears its retest note and completion date.
 *
 * Nothing moves before the server agrees: the card or row stays where it was
 * until the refetch lands, and a refused write shows on it, pinned to the
 * version it was refused at so a later change clears it.
 */
import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import { updateDeficiency } from '../../data/apiClient'
import { useConfirm, type ConfirmOptions } from '../../contexts/ConfirmContext'
import { invalidateDeficiencies, isOpenDeficiency } from '../../hooks/useDeficiencies'
import type { ActionItemStatus, Deficiency } from '../../types'
import { STATUS_LABELS, cancelConfirm, reopenConfirm } from './deficiencyText'

export type MovePlan =
  | { kind: 'none' }
  | { kind: 'complete' }
  | { kind: 'write'; confirm: ConfirmOptions | null; done: string }

/** What moving `d` to `target` takes. Pure: the hook below carries it out. */
export function planMove(d: Pick<Deficiency, 'title' | 'status'>, target: ActionItemStatus): MovePlan {
  if (target === d.status) return { kind: 'none' }
  if (target === 'completed') return { kind: 'complete' }
  if (target === 'cancelled') return { kind: 'write', confirm: cancelConfirm(d), done: 'Deficiency cancelled' }
  if (!isOpenDeficiency(d)) return { kind: 'write', confirm: reopenConfirm(d), done: 'Deficiency reopened' }
  return { kind: 'write', confirm: null, done: `Moved to ${STATUS_LABELS[target]}` }
}

interface Refusal {
  updatedAt: string
  message: string
}

/**
 * Carries out `planMove`. `rows` are the deficiencies on screen, read again
 * once a question is answered, so a refusal is pinned to the row as it is
 * then, not as it was when the move began. `onComplete` opens the completion
 * form, which the page or panel owns so a refetch cannot unmount it half typed.
 *
 * Moves on different rows run side by side: each row has its own busy flag
 * and its own refusal. A second move of a row already being written is ignored.
 */
export function useDeficiencyMove(organizationId: string, rows: Deficiency[], onComplete: (d: Deficiency) => void) {
  const queryClient = useQueryClient()
  const confirm = useConfirm()
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(() => new Set())
  const [refusals, setRefusals] = useState<ReadonlyMap<string, Refusal>>(() => new Map())
  // The source of truth for "is this row being written": a ref, so two moves
  // started in the same tick both see the first.
  const inFlight = useRef(new Set<string>())
  const rowsRef = useRef(rows)
  useEffect(() => {
    rowsRef.current = rows
  }, [rows])

  const isBusy = (d: Pick<Deficiency, 'id'>) => busyIds.has(d.id)

  /** The refusal to show on `d`, while `d` is still the version it was refused at. */
  const errorFor = (d: Deficiency): string | null => {
    const r = refusals.get(d.id)
    return r && r.updatedAt === d.updated_at ? r.message : null
  }

  const clearError = (d: Pick<Deficiency, 'id'>) =>
    setRefusals((m) => {
      if (!m.has(d.id)) return m
      const next = new Map(m)
      next.delete(d.id)
      return next
    })

  const setBusy = (id: string, busy: boolean) => {
    if (busy) inFlight.current.add(id)
    else inFlight.current.delete(id)
    setBusyIds(new Set(inFlight.current))
  }

  /** Resolves true when the server took the new status, false otherwise. */
  const move = async (d: Deficiency, target: ActionItemStatus): Promise<boolean> => {
    if (inFlight.current.has(d.id)) return false
    const plan = planMove(d, target)
    if (plan.kind === 'none') return false
    if (plan.kind === 'complete') {
      clearError(d)
      onComplete(d)
      return false
    }
    if (plan.confirm && !(await confirm(plan.confirm))) return false
    if (inFlight.current.has(d.id)) return false
    setBusy(d.id, true)
    clearError(d)
    try {
      // The status alone: the audit log records one change, of `status`.
      await updateDeficiency(organizationId, d.id, { status: target })
      await invalidateDeficiencies(queryClient, organizationId)
      toast.success(plan.done)
      return true
    } catch (err) {
      // The row may have changed while the question was open: pin the
      // refusal to the version on screen now, or it would never show.
      const current = rowsRef.current.find((x) => x.id === d.id) ?? d
      setRefusals((m) =>
        new Map(m).set(d.id, {
          updatedAt: current.updated_at,
          message: err instanceof Error ? err.message : 'Could not update the deficiency',
        }),
      )
      return false
    } finally {
      setBusy(d.id, false)
    }
  }

  return { move, isBusy, errorFor, clearError }
}
