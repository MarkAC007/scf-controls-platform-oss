/**
 * Shared branding defaults (#864 follow-up).
 *
 * The logo/title fallback expressions were previously copy-pasted across
 * Sidebar, GoogleSignIn, and OidcSignIn — which is exactly how #864 happened:
 * one copy drifted to an asset that no longer existed. Keep the semantics in
 * one place.
 *
 * VITE_APP_LOGO semantics (all consumers must agree):
 *   - unset            → DEFAULT_APP_LOGO
 *   - empty string     → null (hide the logo entirely)
 *   - any other value  → used verbatim (path under public/ or a full URL)
 */
export const DEFAULT_APP_LOGO = '/compliancegenie-logo.png'
export const DEFAULT_APP_TITLE = 'SCF Controls Platform'

export function getAppLogo(): string | null {
  const env = import.meta.env.VITE_APP_LOGO
  return env === '' ? null : (env || DEFAULT_APP_LOGO)
}

export function getAppTitle(): string {
  return import.meta.env.VITE_APP_TITLE || DEFAULT_APP_TITLE
}
