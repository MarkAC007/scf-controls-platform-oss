/**
 * Build-time feature flags, and the parity check against the backend's
 * runtime view of the same flags (#787, ISC-80).
 *
 * `VITE_ENABLE_PER_WINDOW_REVIEW` is compiled into the bundle; the backend
 * reads `ENABLE_PER_WINDOW_REVIEW` from its environment at request time.
 * Two switches for one behaviour, set in two different places, is a
 * divergence waiting to happen — and the dangerous direction is silent:
 * a bundle built without the flag keeps showing per-file Approve buttons
 * against a backend that answers `410 Gone`, so a reviewer is left with no
 * working way to review anything at all.
 *
 * What this module does NOT do: switch what renders. The components still
 * gate on the compiled constant, because moving them onto an async runtime
 * value would mean every gated surface needs a loading state and a
 * re-render path, which is a larger change than the divergence warrants.
 * What it does do is make the divergence loud instead of silent — the
 * mismatch is reported the first time the app talks to the backend.
 */

export const PER_WINDOW_REVIEW_ENABLED =
  import.meta.env.VITE_ENABLE_PER_WINDOW_REVIEW === 'true'

export interface ServerFeatureFlags {
  per_window_review: boolean
  window_assessment_ksi: boolean
  composite_ksi: boolean
}

/**
 * Describe a build-vs-runtime disagreement, or `null` when they agree.
 *
 * Pure so the wording — the part a confused operator actually reads — can
 * be asserted without a network stub.
 */
export function featureFlagMismatch(
  server: Pick<ServerFeatureFlags, 'per_window_review'>,
  compiled: boolean = PER_WINDOW_REVIEW_ENABLED,
): string | null {
  if (server.per_window_review === compiled) return null
  if (server.per_window_review) {
    return (
      'Feature flag mismatch: the backend has ENABLE_PER_WINDOW_REVIEW=true ' +
      'but this build was made without VITE_ENABLE_PER_WINDOW_REVIEW=true. ' +
      'Per-file review requests will be refused with 410 Gone and the ' +
      'per-window review panel is not in this bundle, so evidence cannot be ' +
      'reviewed. Rebuild the webclient with the flag set, or turn the ' +
      'backend flag off.'
    )
  }
  return (
    'Feature flag mismatch: this build has VITE_ENABLE_PER_WINDOW_REVIEW=true ' +
    'but the backend has ENABLE_PER_WINDOW_REVIEW off. The per-window review ' +
    'panel is showing while the backend still expects per-file review. Set ' +
    'ENABLE_PER_WINDOW_REVIEW=true on the backend and celery-worker, or ' +
    'rebuild the webclient without the flag.'
  )
}

/**
 * Fetch the backend's flags and report any disagreement.
 *
 * Deliberately best-effort: a failure here must never stop the app
 * booting, so every error path resolves to `null`. `fetchFlags` is
 * injectable for tests.
 */
export async function checkFeatureFlagParity(
  fetchFlags: () => Promise<ServerFeatureFlags> = defaultFetchFlags,
  report: (message: string) => void = (m) => console.error(m),
): Promise<string | null> {
  let server: ServerFeatureFlags
  try {
    server = await fetchFlags()
  } catch {
    return null
  }
  if (!server || typeof server.per_window_review !== 'boolean') return null
  const message = featureFlagMismatch(server)
  if (message) report(message)
  return message
}

async function defaultFetchFlags(): Promise<ServerFeatureFlags> {
  const response = await fetch('/api/features')
  if (!response.ok) throw new Error(`/api/features returned ${response.status}`)
  return (await response.json()) as ServerFeatureFlags
}
