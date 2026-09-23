import type { JourneyStage } from '../../data/apiClient'

/**
 * What a journey with no current stage actually means.
 *
 * The API reports `current_stage_key: null` when no stage is ACTIVE or
 * AWAITING_ATTESTATION. That is accurate, and it is also what an unprovisioned
 * preview reports — so the field alone cannot tell "finished" from "never
 * started". The difference lives in the stages themselves: a finished journey
 * has a signature on every one of them.
 *
 * Everything here is derived from the payload. No stage count, no ordering
 * assumption, no date, no title is written into this module; `ordinal` is the
 * only thing that decides order.
 */
export interface JourneyCompletion {
  /** Provisioned, activated, no current stage, and every stage signed. */
  complete: boolean
  /**
   * Running, but no stage is current and signatures are missing. A data
   * anomaly, not a completion — and emphatically not "not started", which is
   * only ever `!provisioned || !activated`.
   */
  noActiveStage: boolean
  /**
   * Signed knowing items were left open. Kept apart from `regressedStages`
   * because a conditional pass is *expected* to show unmet checks; calling
   * that a regression would raise a false alarm on every one of them.
   */
  conditionalStages: JourneyStage[]
  /**
   * Signed unconditionally, but today's live evaluation no longer agrees. The
   * API re-evaluates preconditions for every stage on every request, whatever
   * its state, so this is a fresh reading of the same checks rather than a
   * frozen snapshot.
   */
  regressedStages: JourneyStage[]
  /** Earliest outstanding due date across the conditional stages, if any. */
  nextDue: string | null
  /** Highest-ordinal signed stage — the last signature on the path. */
  lastSigned: JourneyStage | null
  /** Stages with no signature. Zero whenever `complete` is true. */
  unsignedCount: number
}

function byOrdinal(a: JourneyStage, b: JourneyStage): number {
  return a.ordinal - b.ordinal
}

export function deriveJourneyCompletion(
  stages: JourneyStage[],
  journey: { provisioned: boolean; activated: boolean },
  currentStageKey: string | null,
): JourneyCompletion {
  const running = journey.provisioned && journey.activated
  const current = currentStageKey ? stages.find(s => s.key === currentStageKey) ?? null : null

  const signed = stages.filter(s => !!s.attested_at)
  const unsignedCount = stages.length - signed.length

  const complete = running && stages.length > 0 && unsignedCount === 0 && !current
  const noActiveStage = running && !current && !complete

  const conditionalStages = stages
    .filter(s => !!s.attested_at && s.state === 'passed_conditional')
    .sort(byOrdinal)

  // `total_count > 0` matters: a stage that declares no mechanical checks
  // cannot regress, and `all_met` on an empty check set is not a verdict.
  const regressedStages = stages
    .filter(
      s =>
        !!s.attested_at &&
        s.state === 'passed' &&
        s.preconditions.total_count > 0 &&
        !s.preconditions.all_met,
    )
    .sort(byOrdinal)

  const nextDue =
    conditionalStages
      .map(s => s.target_date)
      .filter((d): d is string => !!d)
      .sort()[0] ?? null

  const lastSigned = signed.length > 0 ? [...signed].sort(byOrdinal)[signed.length - 1] : null

  return {
    complete,
    noActiveStage,
    conditionalStages,
    regressedStages,
    nextDue,
    lastSigned,
    unsignedCount,
  }
}
