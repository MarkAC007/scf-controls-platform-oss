/**
 * Assessment timestamps are UTC instants, not local ones (#881).
 *
 * `assessed_at` is stored as `DateTime(timezone=False)` and serialises with no
 * zone designator — "2026-09-01T12:00:00". Handed straight to `new Date()`,
 * that is read as *local* time, so the provenance line in the preview modal
 * would report an assessment as having run an hour ago in British Summer Time
 * and eight hours out in California. Silently: nothing throws, the date just
 * quietly disagrees with the audit record it is supposed to evidence.
 *
 * The fix normalises at the API seam so no render site has to remember the
 * quirk, and it has to be idempotent — the backend may yet start emitting
 * zone-aware timestamps, and this must not then corrupt them.
 */
import { describe, it, expect } from 'vitest'

import { asUtcIso } from '../apiClient'

describe('asUtcIso', () => {
  it('reads a naive backend timestamp as UTC', () => {
    expect(asUtcIso('2026-09-01T12:00:00')).toBe('2026-09-01T12:00:00Z')
  })

  it('produces the instant the backend actually meant', () => {
    // The assertion that matters: the parsed instant is noon UTC regardless of
    // the machine's timezone. Comparing epoch millis keeps this true on any
    // developer's laptop and in CI.
    const parsed = new Date(asUtcIso('2026-09-01T12:00:00') as string)
    expect(parsed.getTime()).toBe(Date.UTC(2026, 8, 1, 12, 0, 0))
  })

  it('leaves an already-UTC timestamp alone', () => {
    expect(asUtcIso('2026-09-01T12:00:00Z')).toBe('2026-09-01T12:00:00Z')
  })

  it('leaves an explicit offset alone rather than double-stamping it', () => {
    expect(asUtcIso('2026-09-01T12:00:00+01:00')).toBe('2026-09-01T12:00:00+01:00')
    expect(asUtcIso('2026-09-01T12:00:00-0800')).toBe('2026-09-01T12:00:00-0800')
  })

  it('handles fractional seconds, which Python emits', () => {
    expect(asUtcIso('2026-09-01T12:00:00.123456')).toBe('2026-09-01T12:00:00.123456Z')
  })

  it('passes null through — a never-assessed row has no timestamp', () => {
    expect(asUtcIso(null)).toBeNull()
  })
})
