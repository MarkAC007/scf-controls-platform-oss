/**
 * Runtime configuration, injected by the container at start.
 *
 * Vite compiles `import.meta.env.VITE_*` into the bundle as literals, so a
 * value that varies per deployment used to mean a rebuild per deployment — and
 * a published image that fitted nobody. Anything read through here is instead
 * supplied at container start, so one image serves every deployment.
 *
 * Precedence: the injected value wins, then the build-time one. That fallback
 * is what keeps `vite dev` and `webclient/.env` working unchanged — in dev
 * nothing injects anything and every lookup lands on `import.meta.env`.
 *
 * `undefined` means "not configured" and an empty string means "configured to
 * empty". They are NOT the same: VITE_APP_LOGO distinguishes "use the default
 * logo" from "hide the logo entirely". The injector must therefore omit a
 * variable it was not given rather than writing an empty value for it.
 *
 * Auth stays compiled in on purpose. VITE_OIDC_ENABLED and
 * VITE_GOOGLE_AUTH_ENABLED drive dead-code elimination, which is what keeps the
 * unused sign-in paths out of the shipped bundle; moving them here would put
 * them back in.
 */

export interface RuntimeConfig {
  [key: string]: string | undefined
}

declare global {
  interface Window {
    __SCF_CONFIG__?: RuntimeConfig
  }
}

/**
 * The build-time value for a key.
 *
 * Every arm is a LITERAL `import.meta.env.VITE_X` access, because that is the
 * only form Vite substitutes. A computed `import.meta.env[name]` is left alone
 * at build time and reads as undefined in the bundle, which would silently
 * disable the fallback in production while working perfectly in dev.
 *
 * A function rather than a module-scope map, so the read happens per call.
 * Substitution is syntactic and happens either way, so the production bundle is
 * identical — but `vi.stubEnv` mutates `import.meta.env` after this module has
 * been imported, and a map captured at import time can never see it.
 */
function buildTimeValue(name: string): string | undefined {
  switch (name) {
    case 'APP_TITLE':
      return import.meta.env.VITE_APP_TITLE
    case 'APP_LOGO':
      return import.meta.env.VITE_APP_LOGO
    case 'MARKETING_WEBSITE_URL':
      return import.meta.env.VITE_MARKETING_WEBSITE_URL
    case 'ENABLE_PER_WINDOW_REVIEW':
      return import.meta.env.VITE_ENABLE_PER_WINDOW_REVIEW
    case 'DEBUG_API':
      return import.meta.env.VITE_DEBUG_API
    default:
      return undefined
  }
}

/** The raw value, or undefined when it was configured nowhere. */
export function getConfig(name: string): string | undefined {
  const injected =
    typeof window !== 'undefined' ? window.__SCF_CONFIG__?.[name] : undefined
  return injected !== undefined ? injected : buildTimeValue(name)
}

/** True only for the literal string 'true'. Anything unset reads false. */
export function getConfigFlag(name: string): boolean {
  return getConfig(name) === 'true'
}

/**
 * True unless the value is the literal string 'false'.
 *
 * For flags that default ON, where "never mentioned anywhere" has to agree with
 * a backend that was also never told about it.
 */
export function getConfigFlagDefaultOn(name: string): boolean {
  return getConfig(name) !== 'false'
}
