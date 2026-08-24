import { describe, it, expect, vi } from 'vitest'
import {
  featureFlagMismatch,
  checkFeatureFlagParity,
  type ServerFeatureFlags,
} from '../featureFlags'

const flags = (per_window_review: boolean): ServerFeatureFlags => ({
  per_window_review,
  window_assessment_ksi: false,
  composite_ksi: false,
})

describe('featureFlagMismatch', () => {
  it('is silent when both sides agree it is off', () => {
    expect(featureFlagMismatch(flags(false), false)).toBeNull()
  })

  it('is silent when both sides agree it is on', () => {
    expect(featureFlagMismatch(flags(true), true)).toBeNull()
  })

  it('names the 410 trap when the backend is ahead of the bundle', () => {
    const message = featureFlagMismatch(flags(true), false)
    expect(message).toContain('410 Gone')
    expect(message).toContain('Rebuild the webclient')
  })

  it('names the backend variable when the bundle is ahead of the backend', () => {
    const message = featureFlagMismatch(flags(false), true)
    expect(message).toContain('ENABLE_PER_WINDOW_REVIEW=true on the backend')
    expect(message).toContain('celery-worker')
  })

  it('tells the operator which side to change, not merely that it differs', () => {
    // A mismatch warning that says only "these disagree" leaves the reader
    // exactly where they started. Both directions must name a fix.
    for (const compiled of [true, false]) {
      const message = featureFlagMismatch(flags(!compiled), compiled)
      expect(message).toBeTruthy()
      expect(message!.toLowerCase()).toMatch(/rebuild|set enable_per_window_review/)
    }
  })
})

describe('checkFeatureFlagParity', () => {
  it('reports the mismatch through the reporter', async () => {
    const report = vi.fn()
    const result = await checkFeatureFlagParity(
      async () => flags(true),
      report,
    )
    // The compiled flag is false under test (no VITE_ var set in vitest env).
    expect(result).toContain('410 Gone')
    expect(report).toHaveBeenCalledOnce()
  })

  it('says nothing when the backend agrees', async () => {
    const report = vi.fn()
    expect(await checkFeatureFlagParity(async () => flags(false), report)).toBeNull()
    expect(report).not.toHaveBeenCalled()
  })

  it('swallows a fetch failure rather than breaking boot', async () => {
    const report = vi.fn()
    const result = await checkFeatureFlagParity(async () => {
      throw new Error('network down')
    }, report)
    expect(result).toBeNull()
    expect(report).not.toHaveBeenCalled()
  })

  it('ignores a malformed payload instead of warning on undefined', async () => {
    const report = vi.fn()
    const result = await checkFeatureFlagParity(
      async () => ({} as ServerFeatureFlags),
      report,
    )
    expect(result).toBeNull()
    expect(report).not.toHaveBeenCalled()
  })
})
