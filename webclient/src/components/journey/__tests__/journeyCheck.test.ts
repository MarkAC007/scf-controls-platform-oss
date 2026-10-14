import { describe, expect, it } from 'vitest'

import { checkState } from '../journeyCheck'

describe('checkState', () => {
  it('spells out a met check', () => {
    expect(checkState(true)).toEqual({ key: 'met', glyph: '✓', label: 'Met' })
  })

  it('spells out an unmet check', () => {
    expect(checkState(false)).toEqual({ key: 'unmet', glyph: '○', label: 'Not met' })
  })

  it('never reads a check the deployment could not evaluate as a pass or a fail', () => {
    expect(checkState(null)).toEqual({ key: 'unknown', glyph: '?', label: 'Not evaluated' })
  })
})
