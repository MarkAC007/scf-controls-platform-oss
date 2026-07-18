/**
 * Shared authentication-token resolution for all API callers.
 *
 * The platform supports three auth modes, in precedence order:
 *   1. OIDC (VITE_OIDC_ENABLED) — redirect-based flow; id_token is the Bearer.
 *   2. Google (VITE_GOOGLE_AUTH_ENABLED) — legacy implicit-flow access_token.
 *   3. API key (VITE_API_KEY) — self-hosted single-tenant bypass.
 *
 * Every fetch site in the client resolves its Bearer token through
 * getAuthToken() so the mode logic lives in exactly one place.
 */

export const OIDC_ENABLED = import.meta.env.VITE_OIDC_ENABLED === 'true'
export const GOOGLE_AUTH_ENABLED = import.meta.env.VITE_GOOGLE_AUTH_ENABLED === 'true'
export const API_KEY = import.meta.env.VITE_API_KEY || ''

// localStorage keys
const GOOGLE_TOKEN_KEY = 'google_token'
export const OIDC_TOKEN_KEY = 'oidc_token'
export const OIDC_REFRESH_KEY = 'oidc_refresh_handle'
export const OIDC_EXPIRES_KEY = 'oidc_expires_at'

/** Read the raw Google token (null unless Google mode is active). */
export function getGoogleToken(): string | null {
  return GOOGLE_AUTH_ENABLED ? localStorage.getItem(GOOGLE_TOKEN_KEY) : null
}

/**
 * Resolve the Bearer token for the active auth mode.
 * OIDC → oidc_token; Google → google_token || API_KEY; else API_KEY.
 */
export function getAuthToken(): string {
  if (OIDC_ENABLED) {
    return localStorage.getItem(OIDC_TOKEN_KEY) || ''
  }
  return getGoogleToken() || API_KEY
}

/** Clear the stored session for the active mode (OIDC clears all three keys). */
export function clearAuthSession(): void {
  if (OIDC_ENABLED) {
    localStorage.removeItem(OIDC_TOKEN_KEY)
    localStorage.removeItem(OIDC_REFRESH_KEY)
    localStorage.removeItem(OIDC_EXPIRES_KEY)
  } else {
    localStorage.removeItem(GOOGLE_TOKEN_KEY)
  }
}

/** Persist the token trio the backend hands back on callback / refresh. */
export function storeOidcSession(idToken: string, expiresIn: number, refreshHandle: string): void {
  localStorage.setItem(OIDC_TOKEN_KEY, idToken)
  localStorage.setItem(OIDC_REFRESH_KEY, refreshHandle)
  localStorage.setItem(OIDC_EXPIRES_KEY, String(Date.now() + expiresIn * 1000))
}

/** True when the stored OIDC token expires within `withinMs` (default 60s). */
export function isOidcTokenExpiring(withinMs = 60_000): boolean {
  const expiresAt = Number(localStorage.getItem(OIDC_EXPIRES_KEY) || 0)
  if (!expiresAt) return false
  return expiresAt - Date.now() <= withinMs
}

// Single-flight guard so concurrent 401s / mount checks don't stampede refresh.
let refreshInFlight: Promise<string | null> | null = null

/**
 * Exchange the stored refresh_handle for a fresh id_token (rotated handle).
 * Returns the new id_token, or null if refresh is impossible/rejected (in which
 * case the session is cleared). Concurrent callers share one in-flight request.
 */
export function refreshOidcToken(): Promise<string | null> {
  if (!OIDC_ENABLED) return Promise.resolve(null)
  if (refreshInFlight) return refreshInFlight

  refreshInFlight = (async () => {
    const handle = localStorage.getItem(OIDC_REFRESH_KEY)
    if (!handle) return null
    try {
      const res = await fetch('/api/auth/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_handle: handle }),
      })
      if (!res.ok) {
        // Multi-tab safety: another tab may have already rotated the session
        // while this request was in flight. If the stored handle no longer
        // matches the one we sent, that tab succeeded and wrote a fresh token —
        // treat this failure as success and hand back the current token instead
        // of clearing the (now valid) shared session out from under it.
        const currentHandle = localStorage.getItem(OIDC_REFRESH_KEY)
        if (currentHandle && currentHandle !== handle) {
          return localStorage.getItem(OIDC_TOKEN_KEY)
        }
        // Transient IdP error (backend signals 503): the refresh handle is
        // still valid, so keep the session intact and let the caller retry
        // later rather than logging the user out.
        if (res.status === 503) {
          return null
        }
        // Genuine terminal failure (e.g. 401 with the handle unchanged): the
        // session is dead — clear it.
        clearAuthSession()
        return null
      }
      const data = await res.json()
      storeOidcSession(data.id_token, data.expires_in, data.refresh_handle)
      return data.id_token as string
    } catch {
      // Network error — keep the session; the caller surfaces the failure.
      return null
    }
  })()

  const pending = refreshInFlight
  pending.finally(() => {
    if (refreshInFlight === pending) refreshInFlight = null
  })
  return pending
}
