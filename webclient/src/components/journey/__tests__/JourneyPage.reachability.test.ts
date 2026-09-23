/**
 * The Journey screen is on a screen.
 *
 * Rendering `JourneyPage` in a test proves it renders. It does not prove the
 * app mounts it, and this repo has already shipped a complete, correct,
 * fully-unit-tested component that nothing imported. So the property here is
 * reachability from the app root by imports, plus the two conditions the mount
 * is actually gated on — the tab, and a non-null `scopingData`, which is the
 * easy one to miss.
 *
 * There is no router in this client; the current screen is a piece of `App`
 * state. Nothing below reasons about a path.
 *
 * Sources come from `import.meta.glob`, not `node:fs`, because this file is
 * type-checked by the webclient's tsconfig, which has no node types.
 */
import { describe, expect, it } from 'vitest'

const SOURCES = import.meta.glob('../../../**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

/** Glob keys are relative to this file; resolve them to paths under `src/`. */
function underSrc(key: string): string {
  const segments = 'src/components/journey/__tests__'.split('/')
  for (const segment of key.split('/')) {
    if (segment === '.') continue
    else if (segment === '..') segments.pop()
    else segments.push(segment)
  }
  return segments.join('/').replace(/^src\//, '')
}

const BY_PATH = new Map<string, string>(
  Object.entries(SOURCES)
    .filter(([key]) => !key.includes('__tests__') && !key.includes('.test.'))
    .map(([key, source]) => [underSrc(key), source]),
)

function resolveImport(fromPath: string, specifier: string): string | undefined {
  if (!specifier.startsWith('.')) return undefined
  const segments = fromPath.split('/')
  segments.pop()
  for (const segment of specifier.split('/')) {
    if (segment === '.') continue
    else if (segment === '..') segments.pop()
    else segments.push(segment)
  }
  const base = segments.join('/')
  for (const candidate of [`${base}.tsx`, `${base}.ts`, `${base}/index.tsx`, `${base}/index.ts`]) {
    if (BY_PATH.has(candidate)) return candidate
  }
  return undefined
}

const IMPORT_SPECIFIER = /(?:from|import)\s*\(?\s*['"]([^'"]+)['"]/g

/** Every module reachable from `App.tsx` by following imports. */
function reachableFromApp(): Set<string> {
  const seen = new Set<string>(['App.tsx'])
  const queue = ['App.tsx']
  while (queue.length > 0) {
    const current = queue.shift() as string
    const source = BY_PATH.get(current)
    if (!source) continue
    for (const match of source.matchAll(IMPORT_SPECIFIER)) {
      const target = resolveImport(current, match[1])
      if (target && !seen.has(target)) {
        seen.add(target)
        queue.push(target)
      }
    }
  }
  return seen
}

describe('the fixture itself', () => {
  // A glob that silently matched nothing would make every case below vacuous.
  it('loaded App and the rest of the client', () => {
    expect(BY_PATH.get('App.tsx')).toBeTypeOf('string')
    expect(BY_PATH.size).toBeGreaterThan(50)
  })

  it('the import walk reaches a broad slice of the client, not just App', () => {
    expect(reachableFromApp().size).toBeGreaterThan(50)
  })
})

describe('the Journey screen is reachable from the app root', () => {
  const REACHABLE = reachableFromApp()

  it('mounts the page itself', () => {
    expect(REACHABLE.has('components/journey/JourneyPage.tsx')).toBe(true)
  })

  it('pulls in the completion derivation the page now branches on', () => {
    expect(REACHABLE.has('components/journey/journeyCompletion.ts')).toBe(true)
  })

  /**
   * The invalidation subscriber is registered as a side effect of loading the
   * hook module. If nothing on the boot path imports it, it never runs and the
   * Journey goes stale again with every test still green.
   */
  it('loads the hook that registers Journey invalidation', () => {
    expect(REACHABLE.has('hooks/useJourney.ts')).toBe(true)
    expect(BY_PATH.get('hooks/useJourney.ts')).toMatch(/registerJourneyInvalidation\(/)
  })
})

describe('the mount is gated on the tab and on scoping data', () => {
  const APP = BY_PATH.get('App.tsx') as string

  it('renders the page behind both conditions', () => {
    const mount = APP.slice(APP.indexOf('<JourneyPage') - 200, APP.indexOf('<JourneyPage'))
    expect(mount).toMatch(/activeTab === 'journey'/)
    // The condition that is easy to miss: no scoping data, no Journey screen.
    expect(mount).toMatch(/scopingData\s*&&/)
  })

  it('keeps journey in the tab vocabulary the app switches on', () => {
    expect(APP).toMatch(/type Tab =[^\n]*'journey'/)
  })
})
