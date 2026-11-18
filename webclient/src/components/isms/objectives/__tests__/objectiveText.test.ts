// SPDX-License-Identifier: AGPL-3.0-only
import { describe, expect, it } from 'vitest'
import { metric, objective, reading } from './fixtures'

import {
  completeBy,
  formatDateTime,
  kindLabel,
  numberOrNull,
  measuredCount,
  summaryLine,
  targetLine,
  validateTarget,
} from '../objectiveText'

describe('objectiveText', () => {
  it('says Ongoing with the review date for a maintained objective', () => {
    expect(completeBy({ objective_type: 'maintain', due_date: null, review_date: '2027-03-31' }))
      .toEqual({ main: 'Ongoing', sub: 'Review 31 Mar 2027', gap: false })
  })

  it('gives the due date for an objective to achieve, or says there is none', () => {
    expect(completeBy({ objective_type: 'achieve', due_date: '2027-03-31', review_date: null }).main).toBe('31 Mar 2027')
    expect(completeBy({ objective_type: 'achieve', due_date: null, review_date: null }))
      .toEqual({ main: 'No date', sub: null, gap: true })
  })

  it('summarises the register', () => {
    expect(summaryLine({
      objectives: 14, on_track: 2, at_risk: 2, off_track: 3, needs_attention: 5,
      in_progress: 2, proposed: 2, not_measured: 1, overdue: 1, insufficient_data: 1, achieved: 0, missed: 0,
    })).toBe('14 objectives · 9 measured · 2 proposed')
  })

  it('M1: counts as measured only objectives with a reading against a target, and takes the proposed count it is given', () => {
    const neverMeasured = {
      objectives: 4, on_track: 0, at_risk: 0, off_track: 0, needs_attention: 4,
      in_progress: 0, proposed: 0, not_measured: 0, overdue: 2, insufficient_data: 1, achieved: 0, missed: 0,
    }
    expect(measuredCount(neverMeasured)).toBe(0)
    expect(summaryLine(neverMeasured)).toBe('4 objectives · 0 measured')
    expect(summaryLine(neverMeasured, 1)).toBe('4 objectives · 0 measured · 1 proposed')
  })

  it('labels each kind, and no metric', () => {
    expect(kindLabel({ kind: 'live' })).toBe('Live')
    expect(kindLabel({ kind: 'logged' })).toBe('Logged')
    expect(kindLabel({ kind: 'milestone' })).toBe('Milestone')
    expect(kindLabel({ kind: 'unset' })).toBe('No metric')
    expect(kindLabel(null)).toBe('No metric')
  })

  it('reads a blank number as none, not 0', () => {
    expect(numberOrNull('')).toBeNull()
    expect(numberOrNull('  ')).toBeNull()
    expect(numberOrNull('0')).toBe(0)
    expect(numberOrNull('x')).toBeNull()
  })

  it('checks a target and its warning level', () => {
    expect(validateTarget('percent', 'at_least', '120', '')).toMatch(/between 0 and 100/)
    expect(validateTarget('count', 'at_most', '-1', '')).toMatch(/below 0/)
    expect(validateTarget('percent', 'at_least', '90', '95')).toMatch(/below the target/)
    expect(validateTarget('count', 'at_most', '0', '0')).toMatch(/above the target/)
    expect(validateTarget('percent', 'at_least', '90', '75')).toBeNull()
    expect(validateTarget('count', 'at_most', '0', '3')).toBeNull()
    expect(validateTarget('percent', 'at_least', '', '')).toBeNull()
  })

  it('M6: refuses a warning level with no target', () => {
    expect(validateTarget('percent', 'at_least', '', '80')).toBe('Set a target before a warning level.')
  })

  it('states the target of a closed objective without judging the value against it', () => {
    const closed = (lifecycle_status: 'achieved' | 'missed', over = {}) =>
      objective({ lifecycle_status, reading: reading({ status: lifecycle_status, target_label: 'at least 95%' }), ...over })
    expect(targetLine(closed('achieved'))).toBe('Target was at least 95%')
    expect(targetLine(closed('missed', { objective_type: 'achieve', due_date: '2026-10-31' }))).toBe('Target was at least 95%')
    expect(targetLine(closed('missed', {
      objective_type: 'achieve', due_date: '2026-10-31',
      metric: metric({ kind: 'milestone', unit: null, direction: null, target_value: null, warn_value: null }),
    }))).toBe('Was due 31 Oct 2026')
  })

  it('treats a timestamp with no zone as UTC', () => {
    expect(formatDateTime('2026-11-18T02:10:00')).toBe(formatDateTime('2026-11-18T02:10:00Z'))
    expect(formatDateTime('2026-11-18T02:10:00')).toBe(
      new Date(Date.UTC(2026, 10, 18, 2, 10)).toLocaleString('en-GB', {
        day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
      }),
    )
  })
})
