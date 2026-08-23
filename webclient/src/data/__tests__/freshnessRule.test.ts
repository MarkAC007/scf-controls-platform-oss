import { describe, it, expect } from 'vitest'
import {
  AMBER_GRACE_MULTIPLIER,
  FRESHNESS_RULE,
  FRESHNESS_LEGEND,
} from '../freshnessRule'

describe('freshness rule copy', () => {
  it('states what Fresh means', () => {
    expect(FRESHNESS_RULE.green).toMatch(/within/i)
    expect(FRESHNESS_RULE.green).toMatch(/threshold/i)
  })

  it('states the grace band that makes an item Stale rather than Critical', () => {
    expect(FRESHNESS_RULE.amber).toContain(String(AMBER_GRACE_MULTIPLIER))
    expect(FRESHNESS_RULE.amber).toMatch(/threshold/i)
  })

  it('states what pushes an item to Critical', () => {
    expect(FRESHNESS_RULE.red).toContain(String(AMBER_GRACE_MULTIPLIER))
    expect(FRESHNESS_RULE.red).toMatch(/more than/i)
  })

  it('explains that No Data is not the same as fresh', () => {
    expect(FRESHNESS_RULE.unknown).toMatch(/never uploaded|no collection frequency/i)
  })

  it('carries the whole rule in the legend', () => {
    expect(FRESHNESS_LEGEND).toMatch(/fresh/i)
    expect(FRESHNESS_LEGEND).toMatch(/stale/i)
    expect(FRESHNESS_LEGEND).toMatch(/critical/i)
    expect(FRESHNESS_LEGEND).toContain(String(AMBER_GRACE_MULTIPLIER))
  })

  it('never names a threshold in days', () => {
    // The threshold is per item -- the row's own staleness_warning_days, or the
    // days its frequency implies. Any fixed number here would be right for some
    // rows and confidently wrong for others, which is the defect this copy
    // exists to fix. The 1.5 multiplier is the rule, not a threshold, so it is
    // stripped before the check rather than being allowed to fail it.
    const copy = [...Object.values(FRESHNESS_RULE), FRESHNESS_LEGEND]
      .join(' ')
      .split(String(AMBER_GRACE_MULTIPLIER))
      .join(' ')
    expect(copy).not.toMatch(/\d+\s*(d\b|day)/i)
    expect(copy).not.toMatch(/\d/)
  })
})
