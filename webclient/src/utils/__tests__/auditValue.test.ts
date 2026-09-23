/**
 * auditValue: audit_log stores serialised before/after values as plain text,
 * and more than one writer feeds that column — most values arrive JSON-encoded
 * (a date therefore carries its own quote characters), a few arrive verbatim.
 * The table is append-only, so both encodings are permanent and the reader has
 * to decode either one without ever surfacing a broken date to the user.
 */
import { describe, expect, it } from 'vitest'

import { decodeAuditValue, formatAuditDate, isDateField } from '../auditValue'

// Every raw value this suite decodes or formats, collected so the final test can
// assert the one outcome the whole module exists to prevent.
const ALL_RAW_VALUES = [
  '"2026-09-22"', 'null', '', 'None', 'pending', 'true', 'false', '{"a":1}',
  '2026-09-20', '"2026-09-22T00:00:00"', '2026-09-22T10:54:26+00:00',
  '2026-09-22 10:54:26.604292+00:00', 'not-a-date', '0', '[]',
]

describe('decodeAuditValue', () => {
  it('strips the JSON quotes a json.dumps writer added', () => {
    expect(decodeAuditValue('"2026-09-22"')).toBe('2026-09-22')
    expect(decodeAuditValue('"at_risk"')).toBe('at_risk')
  })

  it('treats every flavour of empty as no value', () => {
    expect(decodeAuditValue('null')).toBeNull()
    expect(decodeAuditValue(null)).toBeNull()
    expect(decodeAuditValue(undefined)).toBeNull()
    expect(decodeAuditValue('')).toBeNull()
    expect(decodeAuditValue('None')).toBeNull()
  })

  it('keeps a bare string written by a writer that did not encode it', () => {
    expect(decodeAuditValue('pending')).toBe('pending')
    expect(decodeAuditValue('skipped')).toBe('skipped')
  })

  it('renders JSON scalars as their text, not as objects', () => {
    expect(decodeAuditValue('true')).toBe('true')
    expect(decodeAuditValue('false')).toBe('false')
    expect(decodeAuditValue('3')).toBe('3')
  })

  it('renders a JSON object as JSON rather than [object Object]', () => {
    const decoded = decodeAuditValue('{"a":1}')
    expect(decoded).toBe('{"a":1}')
    expect(decoded).not.toContain('[object Object]')
  })
})

describe('isDateField', () => {
  // The six tracked field names that genuinely hold a date.
  it.each([
    'completion_date',
    'target_date',
    'start_date',
    'end_date',
    'treatment_due_date',
    'next_review_date',
  ])('routes %s through date formatting', field => {
    expect(isDateField(field)).toBe(true)
  })

  // An unanchored `field.includes('date')` passes every case above and fails
  // every case below — which is the whole point of testing these.
  it.each(['updated_at', 'last_updated', 'validated', 'date_of_birth_note', 'candidate'])(
    'does not mistake %s for a date field',
    field => {
      expect(isDateField(field)).toBe(false)
    },
  )
})

describe('formatAuditDate', () => {
  it('formats a date-only value at local midnight', () => {
    expect(formatAuditDate('2026-09-22')).toBe('Sep 22, 2026')
    expect(formatAuditDate('2026-09-20')).toBe('Sep 20, 2026')
  })

  it('formats a naive timestamp on its own calendar day', () => {
    expect(formatAuditDate('2026-09-22T00:00:00')).toBe('Sep 22, 2026')
  })

  it('formats an offset-aware timestamp without producing a broken date', () => {
    expect(formatAuditDate('2026-09-22T10:54:26+00:00')).toMatch(/^\w{3} \d{1,2}, 2026$/)
  })

  it('formats the space-separated form a Python str(datetime) produces', () => {
    // Safari will not parse the space; normalising it keeps the render portable.
    expect(formatAuditDate('2026-09-22 10:54:26.604292+00:00')).toMatch(/^\w{3} \d{1,2}, 2026$/)
  })

  it('falls back to the raw text rather than a broken date', () => {
    // Raw text is informative; "Invalid Date" is not. Values that no engine
    // parses are asserted here — how leniently a given engine treats a
    // half-recognisable string is its own business, and callers decode first.
    expect(formatAuditDate('not-a-date')).toBe('not-a-date')
    expect(formatAuditDate('n/a')).toBe('n/a')
    expect(formatAuditDate('2026-13-45')).toBe('2026-13-45')
  })
})

describe('the literal string a NaN date formats to', () => {
  it('is never produced for any value this module can receive', () => {
    for (const raw of ALL_RAW_VALUES) {
      const decoded = decodeAuditValue(raw)
      expect(decoded ?? '').not.toContain('Invalid Date')
      if (decoded !== null) {
        expect(formatAuditDate(decoded)).not.toContain('Invalid Date')
      }
    }
  })
})
