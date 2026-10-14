export type JourneyCheckStateKey = 'met' | 'unmet' | 'unknown'

/**
 * How a precondition check's `met` reads on screen. The label carries the
 * state; the glyph is decoration. `null` is a check this deployment could not
 * evaluate, which is neither a pass nor a fail.
 */
export function checkState(met: boolean | null): { key: JourneyCheckStateKey; glyph: string; label: string } {
  if (met === true) return { key: 'met', glyph: '✓', label: 'Met' }
  if (met === null) return { key: 'unknown', glyph: '?', label: 'Not evaluated' }
  return { key: 'unmet', glyph: '○', label: 'Not met' }
}
