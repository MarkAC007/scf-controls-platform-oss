/**
 * Integrations is platform-wide, not org-scoped: a tenant admin must not see
 * the section or its nav entry at all (issue #947). The gate is
 * `canManageIntegrations` from AuthContext — isPlatformAdmin for signed-in
 * principals, and true in API-key mode, where the static key is platform
 * admin server-side and the UI is the only credential write path a no-IdP
 * install has. The gate lives in App.tsx's settings layout, which needs the whole provider stack to render, so this
 * asserts the guard at the source level instead — the same approach
 * styles.filterRail.test.ts takes for an invariant that lives in a file rather
 * than in a component's output.
 *
 * Source access: read through node:fs at runtime. The specifier is built by
 * concatenation because this tsconfig has no node types — a literal 'node:fs'
 * import is a TS2307 (the OrgSettings.restyle lesson); a non-literal one types
 * as `any` and type-checks clean while resolving fine under vitest's node
 * runtime.
 */
import { beforeAll, describe, expect, it } from 'vitest'

let APP = ''

/** The section anchor and the section body, as they appear in App.tsx. */
const NAV_ANCHOR = 'href="#settings-integrations"'
const SECTION = '<div id="settings-integrations">'
const GUARD = '{canManageIntegrations && ('

beforeAll(async () => {
  const fs = await import('node' + ':fs')
  // Relative to process.cwd(), which vitest pins to the webclient root.
  APP = fs.readFileSync('src/App.tsx', 'utf-8')
})

/**
 * True when `needle` sits inside an open `isPlatformAdmin` guard: the nearest
 * guard opener before it is not already closed by the time we reach it.
 */
function isAdminGuarded(source: string, needle: string): boolean {
  const at = source.indexOf(needle)
  if (at < 0) return false
  const guardAt = source.lastIndexOf(GUARD, at)
  if (guardAt < 0) return false
  return !source.slice(guardAt + GUARD.length, at).includes(')}')
}

describe('Settings → Integrations is platform-admin only', () => {
  it('loads App.tsx', () => {
    expect(APP.length).toBeGreaterThan(1000)
    expect(APP).toContain("import IntegrationsSettings from './components/IntegrationsSettings'")
  })

  it('renders the section only behind the canManageIntegrations guard', () => {
    expect(APP.split(SECTION)).toHaveLength(2) // exactly one occurrence
    expect(isAdminGuarded(APP, SECTION)).toBe(true)
  })

  it('renders the nav entry only behind the canManageIntegrations guard', () => {
    expect(APP.split(NAV_ANCHOR)).toHaveLength(2) // exactly one occurrence
    expect(isAdminGuarded(APP, NAV_ANCHOR)).toBe(true)
  })

  it('takes canManageIntegrations from useAuth(), next to the isPlatformAdmin Sidebar gets', () => {
    // useAuth() is the single source for both signals.
    expect(APP).toMatch(/const \{[^}]*isPlatformAdmin[^}]*canManageIntegrations[^}]*\} = useAuth\(\)/)
    expect(APP).toContain('isPlatformAdmin={isPlatformAdmin}')
  })

  it('AuthContext derives canManageIntegrations from the profile flag, and grants it in API-key mode', async () => {
    const fs = await import('node' + ':fs')
    const ctx: string = fs.readFileSync('src/contexts/AuthContext.tsx', 'utf-8')
    // Both signed-in providers (OIDC and Google) derive it from the backend profile.
    expect(ctx.split('canManageIntegrations: user?.is_platform_admin === true,')).toHaveLength(3)
    // API-key mode: the Platform pages stay hidden, Integrations stays reachable.
    const apiKeyBlock = ctx.slice(ctx.indexOf('const noAuthContextValue'), ctx.indexOf('login: () => {}'))
    expect(apiKeyBlock).toContain('isPlatformAdmin: false')
    expect(apiKeyBlock).toContain('canManageIntegrations: true')
  })

  it('does not hand the section an organizationId — it is not org-scoped', () => {
    const at = APP.indexOf(SECTION)
    const block = APP.slice(at, at + 200)
    expect(block).toContain('<IntegrationsSettings />')
    expect(block).not.toContain('organizationId')
  })
})
