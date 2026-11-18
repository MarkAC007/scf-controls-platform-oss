// SPDX-License-Identifier: AGPL-3.0-only
/**
 * targetMaturity.test.ts — the client twin of the backend resolver (PIG R5).
 *
 * The override wins, then the domain target by scf_id prefix, then nothing.
 * Anything that is not L0–L5 is ignored rather than shown as a target.
 */
import { describe, expect, it } from 'vitest'
import { resolveTargetMaturity, targetStanding } from '../targetMaturity'

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

describe('targetStanding', () => {
  const control = (scf_id: string, maturity?: string | null, targetOverride?: string | null) => ({
    scf_id, domainName: `${scf_id.split('-')[0]} domain`, maturity, targetOverride,
  })

  it('counts a control at target when its level is at or above the target', () => {
    const standing = targetStanding(
      [control('GOV-01', 'L2'), control('GOV-02', 'L3'), control('GOV-03', 'L1')],
      { GOV: 'L2' },
    )
    expect(standing).toMatchObject({ withTarget: 3, atTarget: 2, noTarget: 0, domainsWithoutTarget: 0 })
    expect(standing.domains).toEqual([
      { code: 'GOV', name: 'GOV domain', target: 'L2', controls: 3, rated: 3, average: 2, withTarget: 3, atTarget: 2 },
    ])
  })

  it('counts a control with a target and no recorded level against, and leaves it out of the average', () => {
    const standing = targetStanding([control('GOV-01', 'L4'), control('GOV-02', null)], { GOV: 'L2' })
    expect(standing.domains[0]).toMatchObject({ rated: 1, average: 4, withTarget: 2, atTarget: 1 })
  })

  it("measures a control against its own override before its domain's target", () => {
    const standing = targetStanding(
      [control('GOV-01', 'L2', 'L4'), control('GOV-02', 'L2')],
      { GOV: 'L2' },
    )
    expect(standing.domains[0]).toMatchObject({ target: 'L2', withTarget: 2, atTarget: 1 })
  })

  it('counts controls with no target apart, never against', () => {
    const standing = targetStanding(
      [control('GOV-01', 'L3'), control('RSK-01', 'L0'), control('RSK-02', null), control('IAC-01', 'L1')],
      { GOV: 'L2' },
    )
    expect(standing).toMatchObject({ withTarget: 1, atTarget: 1, noTarget: 3, domainsWithoutTarget: 2 })
    expect(standing.domains.map(d => d.code)).toEqual(['GOV'])
  })

  it('lists a domain whose only targets are on single controls, with no domain target', () => {
    const standing = targetStanding([control('RSK-01', 'L1', 'L3'), control('RSK-02', 'L5')], {})
    expect(standing.domains).toEqual([
      { code: 'RSK', name: 'RSK domain', target: null, controls: 2, rated: 2, average: 3, withTarget: 1, atTarget: 0 },
    ])
    expect(standing).toMatchObject({ noTarget: 1, domainsWithoutTarget: 0 })
  })

  it('puts the domain with the most controls below target first', () => {
    const standing = targetStanding(
      [
        control('AST-01', 'L3'), control('AST-02', 'L3'),
        control('GOV-01', 'L1'), control('GOV-02', 'L3'),
        control('CLD-01', 'L0'), control('CLD-02', 'L1'),
      ],
      { AST: 'L2', GOV: 'L2', CLD: 'L3' },
    )
    expect(standing.domains.map(d => d.code)).toEqual(['CLD', 'GOV', 'AST'])
  })

  it('has nothing to show when no target is set anywhere', () => {
    expect(targetStanding([control('GOV-01', 'L3')], null)).toEqual({
      domains: [], withTarget: 0, atTarget: 0, noTarget: 1, domainsWithoutTarget: 1,
    })
  })
})
