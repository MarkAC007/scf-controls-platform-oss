/**
 * The deficiency lifecycle as a move between statuses (PIG R8, #1155).
 *
 * The board's drag and its Move to… menu, and the list's Cancel and Reopen
 * buttons, all decide through `planMove`, so each asks the same question.
 */
import { describe, expect, it, vi } from 'vitest'

import { planMove } from '../deficiencyMove'
import { cancelConfirm, reopenConfirm } from '../deficiencyText'
import type { ActionItemStatus, Deficiency } from '../../../types'

vi.mock('../../../data/apiClient', () => ({ updateDeficiency: vi.fn() }))

const d = (status: ActionItemStatus): Pick<Deficiency, 'title' | 'status'> => ({ title: 'Gap', status })

describe('planMove', () => {
  it('does nothing for a move to the same status', () => {
    for (const s of ['open', 'in_progress', 'completed', 'cancelled'] as const) {
      expect(planMove(d(s), s)).toEqual({ kind: 'none' })
    }
  })

  it('moves between Open and In progress with a single write and no question', () => {
    expect(planMove(d('open'), 'in_progress')).toEqual({ kind: 'write', confirm: null, done: 'Moved to In progress' })
    expect(planMove(d('in_progress'), 'open')).toEqual({ kind: 'write', confirm: null, done: 'Moved to Open' })
  })

  it('completes only through the completion form, from any status', () => {
    for (const s of ['open', 'in_progress', 'cancelled'] as const) {
      expect(planMove(d(s), 'completed')).toEqual({ kind: 'complete' })
    }
  })

  it('cancels after the cancel question', () => {
    for (const s of ['open', 'in_progress', 'completed'] as const) {
      expect(planMove(d(s), 'cancelled')).toEqual({ kind: 'write', confirm: cancelConfirm(d(s)), done: 'Deficiency cancelled' })
    }
  })

  it('reopens out of Completed or Cancelled after the reopen question, to either open column', () => {
    for (const from of ['completed', 'cancelled'] as const) {
      for (const to of ['open', 'in_progress'] as const) {
        expect(planMove(d(from), to)).toEqual({ kind: 'write', confirm: reopenConfirm(d(from)), done: 'Deficiency reopened' })
      }
    }
  })

  it('asks the questions the list has always asked', () => {
    expect(cancelConfirm(d('open'))).toEqual({
      title: 'Cancel deficiency',
      message: 'Cancel "Gap"? It stops counting as an open deficiency. Deficiencies are never deleted; it can be reopened.',
      confirmLabel: 'Cancel deficiency',
      cancelLabel: 'Keep it',
    })
    expect(reopenConfirm(d('completed'))).toEqual({
      title: 'Reopen deficiency',
      message: 'Reopen "Gap"? Its retest note and completion date are cleared, and completing it again needs a new retest.',
      confirmLabel: 'Reopen',
    })
  })
})
