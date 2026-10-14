/**
 * targetMaturity.test.ts — the client twin of the backend resolver (PIG R5).
 *
 * The override wins, then the domain target by scf_id prefix, then nothing.
 * Anything that is not L0–L5 is ignored rather than shown as a target.
 */
import { describe, expect, it } from 'vitest'
import { resolveTargetMaturity } from '../targetMaturity'

describe('resolveTargetMaturity', () => {
  it("uses the control's own override first", () => {
    expect(resolveTargetMaturity('GOV-a', 'L4', { GOV: 'L2' })).toEqual({ level: 'L4', source: 'control' })
  })

  it("falls back to the control's domain target, matched on the prefix", () => {
    expect(resolveTargetMaturity('gov-a.b', null, { GOV: 'L2' })).toEqual({ level: 'L2', source: 'domain' })
  })

  it('has no target when neither is set', () => {
    expect(resolveTargetMaturity('RSK-a', undefined, { GOV: 'L2' })).toEqual({ level: null, source: null })
    expect(resolveTargetMaturity('RSK-a', null, null)).toEqual({ level: null, source: null })
  })

  it('ignores values that are not CMM levels', () => {
    expect(resolveTargetMaturity('GOV-a', 'L9', { GOV: 'high' })).toEqual({ level: null, source: null })
    expect(resolveTargetMaturity('GOV-a', ' l3 ', {})).toEqual({ level: 'L3', source: 'control' })
  })
})
