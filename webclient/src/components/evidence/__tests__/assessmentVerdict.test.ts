/**
 * assessmentVerdict — the wording rule, tested as a rule (#881 WS3).
 *
 * The product commitment is that an AI verdict reads as a suggestion until a
 * person confirms it. That commitment lives in one function, so it can be
 * asserted once here rather than re-asserted at every surface that draws a
 * chip. What the surfaces then have to prove is only that they call it.
 */
import { describe, it, expect } from 'vitest'

import {
  verdictPresentation,
  designationLabel,
  designationClass,
  AO_DESIGNATIONS,
} from '../assessmentVerdict'

describe('verdictPresentation', () => {
  it('words an unconfirmed verdict as a suggestion', () => {
    const v = verdictPresentation('partial', null)
    expect(v.text).toBe('AI suggests: Partial')
    expect(v.isReviewed).toBe(false)
    expect(v.className).toContain('ai-chip-suggested')
  })

  it('never uses confirmed-state language before a human decision', () => {
    for (const status of ['sufficient', 'partial', 'insufficient', 'unassessable']) {
      const v = verdictPresentation(status, null)
      expect(v.text.startsWith('AI suggests:')).toBe(true)
      expect(v.text).not.toContain('Confirmed')
    }
  })

  it('says Confirmed only once a person has confirmed it', () => {
    const v = verdictPresentation('sufficient', 'confirmed')
    expect(v.text).toBe('Confirmed: Sufficient')
    expect(v.isReviewed).toBe(true)
    expect(v.className).toContain('ai-chip-reviewed')
  })

  it('distinguishes a correction from an endorsement', () => {
    // A reviewer who disagreed did not confirm the model. Collapsing the two
    // would lose the one fact a later reader most needs.
    const v = verdictPresentation('insufficient', 'overridden')
    expect(v.text).toBe('Corrected: Insufficient')
    expect(v.qualifier).toBe('Corrected')
    expect(v.isReviewed).toBe(true)
  })

  it('treats an unrecognised decision as not reviewed rather than as confirmed', () => {
    const v = verdictPresentation('partial', 'something_new')
    expect(v.isReviewed).toBe(false)
    expect(v.text).toBe('AI suggests: Partial')
  })

  it('does not call a failed run a suggestion about the evidence', () => {
    expect(verdictPresentation('error', null).text).toBe('AI: Error')
    expect(verdictPresentation('pending', null).text).toBe('AI: Assessing...')
    expect(verdictPresentation('error', null).className).not.toContain('suggested')
  })

  it('separates confirmed from suggested by more than colour', () => {
    // The class names carry the distinction; the CSS attached to them is a
    // dashed versus solid edge. A test cannot read the stylesheet, but it can
    // insist the two states are not the same class.
    const suggested = verdictPresentation('partial', null)
    const confirmed = verdictPresentation('partial', 'confirmed')
    expect(suggested.className).not.toBe(confirmed.className)
    expect(suggested.text).not.toBe(confirmed.text)
  })

  it('falls back to the raw status rather than rendering nothing', () => {
    expect(verdictPresentation('some_new_status', null).text).toBe('AI: some_new_status')
    expect(verdictPresentation(null, null).text).toBe('AI: unknown')
  })
})

describe('designations', () => {
  it('labels every designation the reviewer may choose', () => {
    for (const designation of AO_DESIGNATIONS) {
      expect(designationLabel(designation)).not.toBe(designation)
      expect(designationClass(designation)).toContain('ao-designation-')
    }
  })

  it('uses advisory wording, never an assessor determination', () => {
    const labels = AO_DESIGNATIONS.map(designationLabel).join(' ').toLowerCase()
    for (const forbidden of ['pass', 'fail', 'compliant', 'non-compliant', 'effective']) {
      expect(labels).not.toContain(forbidden)
    }
    expect(designationLabel('appears_satisfied')).toBe('Appears satisfied')
  })
})
