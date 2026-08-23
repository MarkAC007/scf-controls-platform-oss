/**
 * The item half of the URL contract (#785), asserted against the source.
 *
 * `EvidenceReview` is a 1,400-line screen that loads scoped controls, evidence
 * tracking, systems, suggestions and collection guidance on mount. Rendering it
 * to prove three structural properties would mean mocking six modules, and the
 * test would then pass or fail on the shape of those mocks rather than on the
 * property under test.
 *
 * So these read the source. The properties are structural — *where* the seed
 * happens, *what* the auto-select guards on, *who* imports the URL module — and
 * structure is what a source assertion can pin and a render assertion cannot.
 * Same technique as `test_upcoming_evidence_no_sentinel.py` on the backend.
 *
 * Sources come from `import.meta.glob`, not `node:fs`: this file is type-checked
 * by the webclient's tsconfig, which has no node types, and adding them to reach
 * the filesystem in one test would widen the whole project's ambient types.
 */
import { describe, expect, it } from 'vitest'

const SOURCES = import.meta.glob('../../**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

/**
 * Glob keys arrive relative to this file, with redundant segments already
 * collapsed by Vite — `../EvidenceReview.tsx`, `../../App.tsx`. Resolve them
 * back to a path under `src/` so the assertions below read as file names.
 */
function underSrc(key: string): string {
  const segments = 'src/components/__tests__'.split('/')
  for (const segment of key.split('/')) {
    if (segment === '.') continue
    else if (segment === '..') segments.pop()
    else segments.push(segment)
  }
  return segments.join('/').replace(/^src\//, '')
}

const REVIEW = SOURCES['../EvidenceReview.tsx']

const PRODUCTION = Object.entries(SOURCES).filter(([key]) => !key.includes('__tests__'))

describe('the fixture itself', () => {
  // A glob that silently matched nothing would make every case below vacuous.
  it('loaded the screen under test and the rest of the client', () => {
    expect(REVIEW).toBeTypeOf('string')
    expect(PRODUCTION.length).toBeGreaterThan(50)
  })
})

describe('one carrier, not two', () => {
  // The key had two jobs: carry the target id, and suppress the auto-select
  // effect. Only the first was written down. Deleting it means the suppression
  // has to come from somewhere visible — see the seeding case below.
  it('the sessionStorage navigation key is gone from the whole client', () => {
    const offenders = PRODUCTION.filter(([, source]) => source.includes('navigate_to_evidence'))
      .map(([key]) => underSrc(key))
    expect(offenders).toEqual([])
  })

  it('EvidenceReview reads its selection from the URL module', () => {
    expect(REVIEW).toMatch(/from '\.\.\/data\/appUrl'/)
  })
})

describe('a deep-linked item wins', () => {
  // In the initialiser, not an effect. An effect would run after the
  // auto-select effect had already claimed the selection, and the fix for that
  // is exactly what the deleted sessionStorage flag used to be.
  it('seeds the selection in the useState initialiser', () => {
    expect(REVIEW).toMatch(
      /useState<EvidenceId \| undefined>\(\s*\(\) => readAppLocation\(window\.location\.search\)\.evidenceItem/,
    )
  })

  it('auto-selects only when nothing is already selected', () => {
    const autoSelect = REVIEW.slice(REVIEW.indexOf('uniqueEvidenceItems.length > 0'))
    expect(autoSelect).toMatch(/!selectedEvidenceId \|\| !known/)
  })

  // Member access, not the bare word: the comments explaining what the URL
  // replaced still say "sessionStorage", and should — a reader deleting the
  // popstate handler needs to know what used to do that job.
  it('no longer reads or writes sessionStorage', () => {
    expect(REVIEW).not.toMatch(/sessionStorage\s*\./)
  })
})

describe('selection is a place', () => {
  // The card handler must go through `selectEvidence`, which pushes. Calling
  // the setter directly would update the screen and leave the URL — and so
  // Back, reload and any pasted link — describing the previous item.
  it('the evidence card writes through the pushing helper', () => {
    // Asserted on the call, not on the attribute that carries it: the card now
    // receives its handler through `interactiveRowProps` so the row is keyboard
    // reachable. Pinning `onClick={...}` pinned the syntax rather than the
    // behaviour, and broke on a refactor that kept the behaviour intact.
    expect(REVIEW).toMatch(/selectEvidence\(evidenceItem\.id\)/)
    expect(REVIEW).not.toMatch(/setSelectedEvidenceId\(evidenceItem\.id\)/)
  })

  it('selectEvidence pushes rather than replaces', () => {
    const start = REVIEW.indexOf('const selectEvidence')
    expect(REVIEW.slice(start, start + 300)).toMatch(/pushSearch\(withEvidenceItem\(/)
  })

  // The fallback for an id this org does not have, and the auto-select, both
  // correct the URL without inventing a history entry the user did not create.
  it('the auto-select fallback replaces rather than pushes', () => {
    const start = REVIEW.indexOf('const known = uniqueEvidenceItems.some')
    expect(REVIEW.slice(start, start + 400)).toMatch(/replaceSearch\(withEvidenceItem\(/)
  })
})

describe('no other screen was quietly made URL-aware', () => {
  // The allow-list is closed because the other screens read one-shot navigation
  // signals out of sessionStorage and would arrive without them. This fails the
  // moment a fourth file starts writing the address bar.
  it('only the three intended components import the URL module', () => {
    const importers = PRODUCTION.filter(([, source]) => /from '.*data\/appUrl'/.test(source))
      .map(([key]) => underSrc(key))
      .sort()
    expect(importers).toEqual([
      'App.tsx',
      'components/EvidenceReview.tsx',
      'components/EvidenceWorkspace.tsx',
    ])
  })
})
