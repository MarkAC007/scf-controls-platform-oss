/**
 * The work-scope wiring contract (#1052), asserted against the source.
 *
 * This file exists because of a real miss. The Controls half of #1052 was built
 * correctly — chip, count line, `my_teams` on the query, team-aware empty states
 * — into `components/scoping/ScopingList.tsx`, which **nothing imports**. Its
 * unit tests rendered it directly and passed, `tsc` was clean and the build
 * succeeded, because none of those things asks whether the component is on a
 * screen. Meanwhile the header rendered a "Showing: Everything | My teams"
 * switch over `UnifiedLibraryList`, which ignored it. A control that labels a
 * list it does not govern is worse than no control at all.
 *
 * So the property under test is reachability, not behaviour: every file that
 * sends `my_teams` must be reachable from `App.tsx` by imports, and the tabs the
 * header shows the switch on must be exactly the tabs whose list sends it.
 * Rendering cannot prove either — only the import graph can.
 *
 * Sources come from `import.meta.glob`, not `node:fs`, for the reason given in
 * `EvidenceReview.deeplink.test.ts`: this file is type-checked by the
 * webclient's tsconfig, which has no node types.
 */
import { describe, expect, it } from 'vitest'

const SOURCES = import.meta.glob('../../**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

/** Glob keys are relative to this file; resolve them to paths under `src/`. */
function underSrc(key: string): string {
  const segments = 'src/components/__tests__'.split('/')
  for (const segment of key.split('/')) {
    if (segment === '.') continue
    else if (segment === '..') segments.pop()
    else segments.push(segment)
  }
  return segments.join('/').replace(/^src\//, '')
}

/** Every production module, keyed by its path under `src/`. */
const BY_PATH = new Map<string, string>(
  Object.entries(SOURCES)
    .filter(([key]) => !key.includes('__tests__') && !key.includes('.test.'))
    .map(([key, source]) => [underSrc(key), source]),
)

/** Resolve a relative specifier against the importing module's path. */
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

/** Files that put `my_teams` on a request. Type-only mentions do not count. */
const SENDERS = [...BY_PATH.entries()]
  .filter(([, source]) => /my_teams:\s*(?!boolean)/.test(source))
  .map(([path]) => path)
  .sort()

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

describe('every my_teams sender is on a screen', () => {
  /**
   * The one module that sends `my_teams` from nowhere, named so that a *new*
   * orphan still fails this test.
   *
   * `components/scoping/ScopingList.tsx` (and its container `ScopingPage.tsx`)
   * hold a complete, working Controls implementation — chip, `Organisation
   * totals`, `aria-live` count, `mine=true` picker, team-aware empty copy — that
   * no screen mounts. `App.tsx` routes the `scoping` tab to
   * `scoping/FrameworkScopingPage` and the Controls list to
   * `library/UnifiedLibraryPage`; neither imports it.
   *
   * It is listed rather than deleted because #1049 is live in this directory and
   * ripping out a built component is not this change's call to make. Deleting it
   * or wiring it up should delete this entry with it.
   */
  const KNOWN_ORPHANS = ['components/scoping/ScopingList.tsx']

  // THE case. `ScopingList.tsx` passed its own unit tests while unreachable;
  // this is the assertion that would have failed at the moment it was written.
  it('is reachable from App.tsx by imports, or is a named known orphan', () => {
    const reachable = reachableFromApp()
    const orphans = SENDERS.filter((path) => !reachable.has(path))
    expect(orphans).toEqual(KNOWN_ORPHANS)
  })

  // Closed on purpose: a new list that narrows to the caller's teams must also
  // get a scope chip and team-aware empty copy, which is a review decision
  // rather than a detail to notice later.
  it('the senders are the live Controls list, the shared hooks, and the orphan', () => {
    expect(SENDERS).toEqual([
      'components/library/UnifiedLibraryList.tsx',
      'components/scoping/ScopingList.tsx',
      'hooks/useScopedControlsQuery.ts',
      'hooks/useTeamFilteredEvidence.ts',
    ])
  })

  // The Evidence list reaches the same parameter through the hook's `myTeams`
  // argument rather than spelling it itself, so it is absent above by design —
  // pinned here so a silent removal of that wiring does not read as a rename.
  it('the Evidence list still hands the scope to its hook', () => {
    const review = BY_PATH.get('components/EvidenceReview.tsx') as string
    expect(review).toMatch(/useTeamFilteredEvidence\(/)
    expect(review).toMatch(/isMyTeams/)
  })
})

describe('the header shows the switch where the list honours it', () => {
  const HEADER = BY_PATH.get('components/Header.tsx') as string

  it('gates on exactly the Controls and Evidence tabs', () => {
    const declaration = HEADER.slice(HEADER.indexOf('const WORK_SCOPE_TABS'))
    expect(declaration.slice(0, 200)).toMatch(/new Set<Tab>\(\['library', 'evidence'\]\)/)
  })

  it('renders the control behind that gate and nowhere else', () => {
    const uses = HEADER.match(/<WorkScopeControl\s*\/>/g) ?? []
    expect(uses).toHaveLength(1)
    expect(HEADER).toMatch(/WORK_SCOPE_TABS\.has\(activeTab\)\s*&&\s*<WorkScopeControl\s*\/>/)
  })
})
