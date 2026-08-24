/**
 * Cadence vs coverage — the words themselves (#789, ISC-66).
 *
 * The helpers are trivial. They are tested anyway because two components render
 * the same card and the whole point of extracting these was that both say the
 * same thing; a test is the only thing that keeps them honest.
 */
import { describe, it, expect } from 'vitest'
import {
  ageLabel,
  basisLabel,
  basisTitle,
  coverageLabel,
  stalenessSortKey,
  uploadLabel,
  type FreshnessFields,
} from '../evidenceFreshness'

function item(overrides: Partial<FreshnessFields> = {}): FreshnessFields {
  return {
    days_since_upload: 3,
    coverage_through: '2026-06-30',
    days_since_coverage: 54,
    staleness_basis: 'asserted_period',
    ...overrides,
  }
}

describe('ageLabel', () => {
  it('says Today rather than 0d ago', () => {
    expect(ageLabel(0)).toBe('Today')
  })

  it('says Never when there is no date at all', () => {
    expect(ageLabel(null)).toBe('Never')
  })

  it('counts days otherwise', () => {
    expect(ageLabel(12)).toBe('12d ago')
  })

  it('does not render a negative day count as "-4d ago"', () => {
    // A period asserted to run past today is legitimate — a quarterly report
    // filed mid-quarter covers dates that have not happened yet.
    expect(ageLabel(-4)).toBe('Future')
  })
})

describe('coverage and upload are different numbers', () => {
  it('reads coverage off days_since_coverage, not days_since_upload', () => {
    const i = item({ days_since_upload: 0, days_since_coverage: 54 })
    expect(coverageLabel(i)).toBe('54d ago')
    expect(uploadLabel(i)).toBe('Today')
  })

  it('is the case that made the old card unreadable', () => {
    // Uploaded today, covers a period that ended two months ago. The card is
    // red; the old card captioned it "Last upload: Today".
    const i = item({ days_since_upload: 0, days_since_coverage: 61 })
    expect(coverageLabel(i)).not.toBe(uploadLabel(i))
  })
})

describe('basis disclosure', () => {
  it('names an asserted period as asserted', () => {
    expect(basisLabel(item({ staleness_basis: 'asserted_period' }))).toBe('asserted')
  })

  it('names the proxy as a proxy', () => {
    expect(basisLabel(item({ staleness_basis: 'upload_date' }))).toBe('from upload date')
  })

  it('discloses nothing when there is no coverage to qualify', () => {
    const empty = item({ days_since_coverage: null, coverage_through: null })
    expect(basisLabel(empty)).toBeNull()
    expect(basisTitle(empty)).toBeNull()
  })

  it('spells the distinction out in the long form too', () => {
    expect(basisTitle(item({ staleness_basis: 'asserted_period' }))).toContain('asserted')
    expect(basisTitle(item({ staleness_basis: 'upload_date' }))).toContain('stands in')
  })
})

describe('stalenessSortKey', () => {
  it('sorts never-collected worse than anything with a date', () => {
    const never = stalenessSortKey(item({ days_since_coverage: null }))
    expect(never).toBeGreaterThan(stalenessSortKey(item({ days_since_coverage: 10_000 })))
  })

  it('orders by coverage age, not upload age', () => {
    const staleCoverage = item({ days_since_upload: 0, days_since_coverage: 90 })
    const staleUpload = item({ days_since_upload: 90, days_since_coverage: 1 })
    expect(stalenessSortKey(staleCoverage)).toBeGreaterThan(stalenessSortKey(staleUpload))
  })
})
