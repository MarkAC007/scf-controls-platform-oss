/**
 * Filter-rail collapse guard.
 *
 * styles.css is append-only by convention, which creates a specific hazard:
 * a later `.explorer-filters { width: ... }` rule ties on specificity with
 * the much earlier `.explorer-filters--collapsed { width: 36px }` rule and
 * wins by source order — the rail then stays full-width when collapsed and
 * the list never gets the space back (shipped once, in the #840 polish).
 * Any restyle appended after the collapsed rule must scope itself with
 * :not(.explorer-filters--collapsed).
 *
 * Source access: import.meta.glob's ?raw is stubbed to '' for .css files by
 * vitest's `css: false`, so the stylesheet is read through node:fs at
 * runtime. The specifier is built by concatenation because this tsconfig has
 * no node types — a literal 'node:fs' import is a TS2307 (the
 * OrgSettings.restyle lesson); a non-literal one types as `any` and
 * type-checks clean while resolving fine under vitest's node runtime.
 */
import { beforeAll, describe, expect, it } from 'vitest'

let CSS = ''

beforeAll(async () => {
  const fs = await import('node' + ':fs')
  // Relative to process.cwd(), which vitest pins to the webclient root
  // (import.meta.url is not a file: URL inside vitest's transform pipeline).
  CSS = fs.readFileSync('src/styles.css', 'utf-8')
})

describe('filter-rail collapse is not overridden by later appends', () => {
  it('loads the stylesheet', () => {
    expect(CSS.length).toBeGreaterThan(1000)
    expect(CSS).toContain('.explorer-filters--collapsed')
  })

  it('no unscoped .explorer-filters rule appears after the collapsed rule', () => {
    const collapsedAt = CSS.indexOf('.explorer-filters--collapsed {')
    expect(collapsedAt).toBeGreaterThan(-1)
    const after = CSS.slice(collapsedAt)
    // A bare `.explorer-filters {` selector (not :not-guarded, not a
    // descendant/child selector) would win the source-order tie-break and
    // pin the collapsed rail at full width.
    expect(after).not.toMatch(/^\.explorer-filters\s*\{/m)
    expect(after).not.toMatch(/^\.explorer-filters\s*,/m)
  })

  it('the tightened rail rule carries the :not(--collapsed) guard', () => {
    expect(CSS).toMatch(/\.explorer-filters:not\(\.explorer-filters--collapsed\)\s*\{/)
  })
})
