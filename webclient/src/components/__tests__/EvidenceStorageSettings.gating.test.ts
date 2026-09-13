/**
 * Evidence storage is org-scoped — the mirror image of Integrations.
 *
 * Integrations is platform-wide and deliberately takes no organisation
 * (`IntegrationsSettings.gating.test.ts`). This card is the opposite: it
 * configures where *this organisation's* evidence goes, so it must be handed
 * the current organisation id, and it must NOT sit behind the platform-admin
 * guard — an organisation administrator who is not a platform administrator is
 * exactly who this screen is for (ISA D14).
 *
 * The gate that matters is the API's `require_org_role("admin")`. There is no
 * client-side org-role signal in App.tsx today, so the card is mounted for
 * every member and renders the 403 as a read-only explanation instead of a
 * blank card — that behaviour is pinned in EvidenceStorageSettings.test.tsx.
 *
 * Source access through node:fs, and the specifier built by concatenation, for
 * the reason the Integrations gating test gives: this tsconfig has no node
 * types, so a literal 'node:fs' import is a TS2307.
 */
import { beforeAll, describe, expect, it } from 'vitest'

let APP = ''

const NAV_ANCHOR = 'href="#settings-evidence-storage"'
const SECTION = '<div id="settings-evidence-storage">'
const PLATFORM_GUARD = '{canManageIntegrations && ('

beforeAll(async () => {
  const fs = await import('node' + ':fs')
  APP = fs.readFileSync('src/App.tsx', 'utf-8')
})

/** True when `needle` sits inside an open platform-admin guard. */
function isPlatformGuarded(source: string, needle: string): boolean {
  const at = source.indexOf(needle)
  if (at < 0) return false
  const guardAt = source.lastIndexOf(PLATFORM_GUARD, at)
  if (guardAt < 0) return false
  return !source.slice(guardAt + PLATFORM_GUARD.length, at).includes(')}')
}

describe('Settings → Evidence storage is organisation-scoped', () => {
  it('imports the card', () => {
    expect(APP).toContain(
      "import EvidenceStorageSettings from './components/EvidenceStorageSettings'"
    )
  })

  it('mounts the section and its nav entry exactly once', () => {
    expect(APP.split(SECTION)).toHaveLength(2)
    expect(APP.split(NAV_ANCHOR)).toHaveLength(2)
  })

  it('hands the card the current organisation id', () => {
    const at = APP.indexOf(SECTION)
    const block = APP.slice(at, at + 260)
    expect(block).toContain('<EvidenceStorageSettings')
    expect(block).toContain('organizationId={scopingData.organizationId!}')
  })

  it('does not hide the card behind the platform-admin guard', () => {
    expect(isPlatformGuarded(APP, SECTION)).toBe(false)
    expect(isPlatformGuarded(APP, NAV_ANCHOR)).toBe(false)
  })
})
