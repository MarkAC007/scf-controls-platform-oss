/**
 * The completed-journey derivation.
 *
 * A journey with every stage signed reports `current_stage_key: null` — and so
 * does a journey that was never started. The client used to read both the same
 * way and print "not started" over a finished path. These cases pin the
 * distinction, and pin the second one that matters: a stage signed *with
 * conditions* is not a stage whose checks have regressed, and conflating them
 * would cry regression over every conditional signature ever made.
 *
 * Fixtures build their own stage arrays at differing lengths on purpose. No
 * case encodes how many stages a template happens to have.
 */
import { describe, expect, it } from 'vitest'

import { deriveJourneyCompletion } from '../journeyCompletion'
import type { JourneyStage, JourneyStageState } from '../../../data/apiClient'

interface StageSpec {
  state?: JourneyStageState
  signed?: boolean
  met?: number
  total?: number
  due?: string | null
  attestedAt?: string
}

function stage(ordinal: number, spec: StageSpec = {}): JourneyStage {
  const state = spec.state ?? 'passed'
  const signed = spec.signed ?? (state === 'passed' || state === 'passed_conditional')
  const total = spec.total ?? 0
  const met = spec.met ?? total
  return {
    id: `id-${ordinal}`,
    ordinal,
    key: `stage-${ordinal}`,
    title: `Stage ${ordinal}`,
    summary: null,
    expect_next: null,
    state,
    started_at: null,
    attested_at: signed ? spec.attestedAt ?? `2026-0${(ordinal % 9) + 1}-01T00:00:00Z` : null,
    attested_by_user_id: null,
    attested_by_name: signed ? 'A signer' : null,
    attestation_note: null,
    target_date: spec.due ?? null,
    preconditions: {
      checks: [],
      met_count: met,
      total_count: total,
      unknown_count: 0,
      all_met: total === 0 ? true : met === total,
    },
  }
}

const RUNNING = { provisioned: true, activated: true }

/** A short path, deliberately not the length of any real template. */
function threeSigned(): JourneyStage[] {
  return [stage(1), stage(2), stage(3)]
}

/** A longer one, so nothing can pass by assuming a fixed count. */
function elevenSigned(): JourneyStage[] {
  return Array.from({ length: 11 }, (_, i) => stage(i + 1))
}

describe('complete', () => {
  it('is true when every stage is signed and nothing is current', () => {
    expect(deriveJourneyCompletion(threeSigned(), RUNNING, null).complete).toBe(true)
    expect(deriveJourneyCompletion(elevenSigned(), RUNNING, null).complete).toBe(true)
  })

  it('is false, and does not become "not started", when a stage is unsigned', () => {
    const stages = [...threeSigned().slice(0, 2), stage(3, { state: 'locked', signed: false })]
    const result = deriveJourneyCompletion(stages, RUNNING, null)
    expect(result.complete).toBe(false)
    // The honest fallback: no stage is active, and signatures are missing.
    expect(result.noActiveStage).toBe(true)
    expect(result.unsignedCount).toBe(1)
  })

  it('is false while a stage is still current', () => {
    const stages = [stage(1), stage(2, { state: 'active', signed: false })]
    const result = deriveJourneyCompletion(stages, RUNNING, 'stage-2')
    expect(result.complete).toBe(false)
    expect(result.noActiveStage).toBe(false)
  })

  it('is false, with no anomaly claimed, before the journey is activated', () => {
    const result = deriveJourneyCompletion(threeSigned(), { provisioned: true, activated: false }, null)
    expect(result.complete).toBe(false)
    expect(result.noActiveStage).toBe(false)
  })

  it('is false on an empty path', () => {
    expect(deriveJourneyCompletion([], RUNNING, null).complete).toBe(false)
  })
})

describe('conditional and regressed are different things', () => {
  it('a conditional pass with unmet checks is conditional, never regressed', () => {
    const stages = [
      stage(1),
      stage(2, { state: 'passed_conditional', met: 1, total: 4, due: '2026-11-30' }),
      stage(3),
    ]
    const result = deriveJourneyCompletion(stages, RUNNING, null)
    expect(result.conditionalStages.map(s => s.key)).toEqual(['stage-2'])
    expect(result.regressedStages).toEqual([])
  })

  it('an unconditional pass whose live checks no longer agree is regressed', () => {
    const stages = [stage(1), stage(2, { state: 'passed', met: 1, total: 2 }), stage(3)]
    const result = deriveJourneyCompletion(stages, RUNNING, null)
    expect(result.regressedStages.map(s => s.key)).toEqual(['stage-2'])
    expect(result.conditionalStages).toEqual([])
  })

  it('a stage that declares no checks cannot regress', () => {
    const stages = [stage(1, { total: 0 }), stage(2, { total: 0 })]
    const result = deriveJourneyCompletion(stages, RUNNING, null)
    expect(result.regressedStages).toEqual([])
  })

  it('lists both in ordinal order, whatever order the payload arrived in', () => {
    const stages = [
      stage(5, { state: 'passed_conditional', met: 0, total: 1 }),
      stage(2, { state: 'passed', met: 1, total: 3 }),
      stage(1, { state: 'passed_conditional', met: 2, total: 3 }),
      stage(4, { state: 'passed', met: 0, total: 2 }),
      stage(3),
    ]
    const result = deriveJourneyCompletion(stages, RUNNING, null)
    expect(result.conditionalStages.map(s => s.ordinal)).toEqual([1, 5])
    expect(result.regressedStages.map(s => s.ordinal)).toEqual([2, 4])
  })
})

describe('nextDue', () => {
  it('is the earliest outstanding date among the conditional stages', () => {
    const stages = [
      stage(1, { state: 'passed_conditional', due: '2027-03-15' }),
      stage(2, { state: 'passed_conditional', due: '2026-10-22' }),
      stage(3, { state: 'passed_conditional', due: '2026-12-01' }),
    ]
    expect(deriveJourneyCompletion(stages, RUNNING, null).nextDue).toBe('2026-10-22')
  })

  it('is null when every conditional stage was signed without one', () => {
    const stages = [stage(1, { state: 'passed_conditional' }), stage(2)]
    expect(deriveJourneyCompletion(stages, RUNNING, null).nextDue).toBeNull()
  })
})

describe('lastSigned', () => {
  it('is the highest-ordinal signed stage, not the last array element', () => {
    const stages = [stage(3), stage(1), stage(2)]
    expect(deriveJourneyCompletion(stages, RUNNING, null).lastSigned?.ordinal).toBe(3)
  })

  it('ignores unsigned stages that sit above it', () => {
    const stages = [stage(1), stage(2), stage(3, { state: 'locked', signed: false })]
    expect(deriveJourneyCompletion(stages, RUNNING, null).lastSigned?.ordinal).toBe(2)
  })

  it('is null when nothing has been signed', () => {
    const stages = [stage(1, { state: 'active', signed: false })]
    expect(deriveJourneyCompletion(stages, RUNNING, null).lastSigned).toBeNull()
  })
})
