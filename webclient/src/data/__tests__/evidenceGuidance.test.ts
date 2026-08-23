/**
 * Evidence Guidance stops answering "GENERIC" for nine items in ten (#789).
 *
 * The tier is the thing under test. A caller that inferred the badge from
 * "did I get a template?" is how the panel came to label catalogue-derived text
 * as generic in the first place, so `resolveEvidenceGuidance` reports the tier
 * and these cases pin what each one is allowed to contain.
 */
import { describe, it, expect } from 'vitest'
import { resolveEvidenceGuidance, GENERIC_GUIDANCE, GUIDANCE_TIER_BADGE } from '../evidenceGuidance'
import { AMBER_GRACE_MULTIPLIER } from '../freshnessRule'
import type { ERLFile, EvidenceTemplatesFile, EvidenceTracking } from '../../types'

const ID = 'E-IAM-99'

const erl: ERLFile = {
  [ID]: {
    evidence_id: ID,
    area_of_focus: 'Identity & Access Management',
    artifact_title: 'Privileged Access Review Records',
    artifact_description:
      'Documented evidence of periodic reviews of privileged account entitlements.',
    control_mappings: ['IAC-01', 'IAC-17', 'IAC-21'],
  },
}

const templates: EvidenceTemplatesFile = {
  [ID]: {
    evidence_id: ID,
    title: 'Privileged Access Review Records',
    guidance: { ...GENERIC_GUIDANCE, summary: 'Hand-written summary for this artifact.' },
  },
}

describe('tier selection', () => {
  it('prefers a hand-written template over anything derivable', () => {
    const { tier, guidance } = resolveEvidenceGuidance(ID, { templates, erl })
    expect(tier).toBe('template')
    expect(guidance.summary).toBe('Hand-written summary for this artifact.')
  })

  it('derives from the catalogue when no template exists', () => {
    const { tier, guidance } = resolveEvidenceGuidance(ID, { erl })
    expect(tier).toBe('derived')
    expect(guidance.summary).toBe(erl[ID].artifact_description)
  })

  it('falls back to generic when the catalogue has no entry either', () => {
    const { tier, guidance } = resolveEvidenceGuidance('E-NOT-REAL', { templates, erl })
    expect(tier).toBe('generic')
    expect(guidance).toBe(GENERIC_GUIDANCE)
  })

  it('treats a catalogue entry with no title or description as absent', () => {
    // Derived guidance whose summary is empty is worse than the generic
    // sentence: the panel's first line would simply be blank.
    const empty: ERLFile = { [ID]: { evidence_id: ID, control_mappings: ['IAC-01'] } }
    expect(resolveEvidenceGuidance(ID, { erl: empty }).tier).toBe('generic')
  })

  it('uses the artifact title when only the description is missing', () => {
    const titleOnly: ERLFile = { [ID]: { evidence_id: ID, artifact_title: 'Backup Test Logs' } }
    const { tier, guidance } = resolveEvidenceGuidance(ID, { erl: titleOnly })
    expect(tier).toBe('derived')
    expect(guidance.summary).toBe('Backup Test Logs')
  })

  it('labels the tiers distinguishably, and labels a template not at all', () => {
    expect(GUIDANCE_TIER_BADGE.template).toBeNull()
    expect(GUIDANCE_TIER_BADGE.derived).not.toBe(GUIDANCE_TIER_BADGE.generic)
  })
})

describe('derived guidance says something the generic text cannot', () => {
  it('names the controls the artifact is mapped to', () => {
    const { guidance } = resolveEvidenceGuidance(ID, { erl })
    const good = guidance.good_examples.join(' ')
    expect(good).toContain('IAC-01')
    expect(good).toContain('IAC-21')
  })

  it('truncates a long control list instead of printing all of it', () => {
    const many: ERLFile = {
      [ID]: { ...erl[ID], control_mappings: ['A-1', 'A-2', 'A-3', 'A-4', 'A-5', 'A-6'] },
    }
    const good = resolveEvidenceGuidance(ID, { erl: many }).guidance.good_examples.join(' ')
    expect(good).toContain('and 2 more')
    expect(good).not.toContain('A-6')
  })

  it('states the area of focus in the auditor tip', () => {
    const { guidance } = resolveEvidenceGuidance(ID, { erl })
    expect(guidance.auditor_tip).toContain('Identity & Access Management')
  })

  it('warns against general material about the area of focus', () => {
    const { guidance } = resolveEvidenceGuidance(ID, { erl })
    expect(guidance.bad_examples.join(' ')).toMatch(/General material about Identity/)
  })

  it('names the collecting system this organisation recorded', () => {
    const tracking: EvidenceTracking = { collecting_system: 'Okta' }
    const { guidance } = resolveEvidenceGuidance(ID, { erl, tracking })
    expect(guidance.good_examples.join(' ')).toContain('Okta')
  })

  it('says nothing about a collecting system when none is set', () => {
    const { guidance } = resolveEvidenceGuidance(ID, { erl })
    expect(guidance.good_examples.join(' ')).not.toMatch(/system this organisation/)
  })
})

describe('derived freshness matches how freshness is actually judged', () => {
  it('states the item own cadence rather than "the current audit period"', () => {
    const { guidance } = resolveEvidenceGuidance(ID, { erl, tracking: { frequency: 'quarterly' } })
    expect(guidance.freshness).toContain('quarterly')
    expect(guidance.freshness).not.toContain('current audit period')
  })

  it('carries the grace band from the shared rule, not a copy of the number', () => {
    const { guidance } = resolveEvidenceGuidance(ID, { erl, tracking: { frequency: 'monthly' } })
    expect(guidance.freshness).toContain(`${AMBER_GRACE_MULTIPLIER}x`)
  })

  it('says the clock is not running when no frequency is set', () => {
    const { guidance } = resolveEvidenceGuidance(ID, { erl })
    expect(guidance.freshness).toMatch(/No Data/)
    expect(guidance.freshness).toMatch(/Set a frequency/)
  })

  it('renders an unrecognised stored frequency rather than dropping it', () => {
    // `frequencyLabel` falls back to the raw string for legacy free-text values.
    const { guidance } = resolveEvidenceGuidance(ID, { erl, tracking: { frequency: 'fortnightly' } })
    expect(guidance.freshness).toContain('fortnightly')
  })
})

describe('the generic tier is unchanged', () => {
  it('still offers the seven fixed sentences it always did', () => {
    // Moved between files, not rewritten. If this drifts it should be because
    // someone meant to change the fallback wording.
    expect(GENERIC_GUIDANCE.summary).toMatch(/^Upload documentation that demonstrates/)
    expect(GENERIC_GUIDANCE.acceptable_formats).toContain('PDF')
    expect(GENERIC_GUIDANCE.freshness).toBe('Within the current audit period')
  })

  it('offers the same upload formats in the derived tier', () => {
    // A property of the uploader, not of the artifact. Narrowing it per item
    // would be text that looks specific and is not.
    const { guidance } = resolveEvidenceGuidance(ID, { erl })
    expect(guidance.acceptable_formats).toEqual(GENERIC_GUIDANCE.acceptable_formats)
  })
})

describe('the join this panel used to reach for is gone', () => {
  // `/catalog/bulk/evidence` never emitted `collection_interfaces` — the backend
  // calls it a "Legacy CCF concept, not in SCF" — so `getInterfacesForEvidence`
  // and `EvidenceReview.getCollectionMethodsForEvidence` could only ever return
  // an empty list, and both had zero callers. Guidance is derived from fields
  // the catalogue actually serves instead. Pinned so the dead join is not
  // reintroduced the next time someone looks for per-item collection detail.
  const sources = import.meta.glob('../../**/*.{ts,tsx}', {
    query: '?raw',
    import: 'default',
    eager: true,
  }) as Record<string, string>

  it('loaded the fixtures it is asserting on', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(50)
  })

  it('has no consumer of the never-emitted field left', () => {
    const offenders = Object.entries(sources)
      .filter(([key]) => !key.includes('__tests__'))
      .filter(([, text]) => /\.collection_interfaces\b/.test(text))
      .map(([key]) => key)
    expect(offenders).toEqual([])
  })
})
