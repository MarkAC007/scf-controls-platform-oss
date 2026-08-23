import { describe, it, expect } from 'vitest'

/**
 * Source assertions over the navigational-row class.
 *
 * These components mount six services between them and rendering one to press
 * Tab at it would be testing the mocks. What matters is structural: the rows
 * that navigate go through the shared helper, so none of them can quietly lose
 * its keyboard path again.
 */
const sources = import.meta.glob('../../**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

/** Glob keys arrive relative to this file with redundant segments collapsed. */
function underSrc(relative: string): string {
  const from = ['src', 'data', '__tests__']
  const parts = relative.split('/')
  const out = [...from]
  for (const part of parts) {
    if (part === '.' || part === '') continue
    if (part === '..') out.pop()
    else out.push(part)
  }
  return out.join('/')
}

function source(path: string): string {
  const key = Object.keys(sources).find(k => underSrc(k) === `src/${path}`)
  if (!key) {
    throw new Error(
      `no source loaded for src/${path} — the glob matched ${Object.keys(sources).length} files`,
    )
  }
  return sources[key]
}

/** Every row whose click IS the navigation. */
const NAVIGATIONAL_ROWS: { file: string; rows: number }[] = [
  { file: 'components/dashboard/WorkQueuePanel.tsx', rows: 3 },
  { file: 'components/evidence/EvidenceDashboardTab.tsx', rows: 2 },
  { file: 'components/SidebarControlCard.tsx', rows: 1 },
  { file: 'components/NotificationBell.tsx', rows: 1 },
  { file: 'components/EvidenceReview.tsx', rows: 2 },
]

/**
 * Rows whose click reveals something in place rather than navigating.
 *
 * A second population, not a second name for the first. The keyboard contract is
 * identical — Enter and Space activate, Space must not scroll — but a disclosure
 * also owns `aria-expanded`, and a reader that conflates the two will eventually
 * "fix" a disclosure by giving it a destination. Declared separately so the
 * distinction survives the next edit (#789).
 */
const DISCLOSURE_HEADERS: { file: string; rows: number }[] = [
  { file: 'components/evidence/EvidenceTemplateGuidance.tsx', rows: 1 },
  { file: 'components/maturity/MaturityDistributionWidget.tsx', rows: 1 },
]

const HELPER_USERS = [...NAVIGATIONAL_ROWS, ...DISCLOSURE_HEADERS]

describe('navigational rows are keyboard reachable', () => {
  it('loaded the fixtures it is asserting on', () => {
    // Without this, a glob that silently matched nothing would make every other
    // case in this file pass vacuously.
    expect(Object.keys(sources).length).toBeGreaterThan(50)
    expect(source('components/dashboard/WorkQueuePanel.tsx')).toContain('wq-item')
  })

  for (const { file, rows } of HELPER_USERS) {
    it(`${file} routes all ${rows} of its rows through the helper`, () => {
      const text = source(file)
      const uses = text.match(/interactiveRowProps\(/g) ?? []
      expect(uses.length).toBe(rows)
    })
  }

  it('leaves no row hand-rolling the keyboard contract', () => {
    for (const { file } of HELPER_USERS) {
      const text = source(file)
      expect(text).not.toMatch(/tabIndex=\{0\}/)
      expect(text).not.toMatch(/role="button"/)
    }
  })

  it('gives every disclosure an aria-expanded state', () => {
    // The one thing a disclosure needs that a navigational row does not. The
    // guidance header used to declare `role="button"` with neither a tab stop
    // nor a key handler, so it announced a control that could not be operated.
    for (const { file } of DISCLOSURE_HEADERS) {
      expect(source(file)).toMatch(/aria-expanded=\{/)
    }
  })

  it('never puts a tab stop on a modal backdrop', () => {
    // Backdrops are the other big population of clickable divs. A backdrop that
    // takes focus lands the user between the trigger and the dialog content,
    // which is worse than leaving it alone -- so the helper must not reach them.
    for (const [key, text] of Object.entries(sources)) {
      if (!/interactiveRowProps\(/.test(text)) continue
      const path = underSrc(key)
      if (path.includes('__tests__')) continue // the tests name it to assert on it
      const known = HELPER_USERS.some(r => path === `src/${r.file}`)
      const isHelper = path === 'src/data/interactiveRow.ts'
      expect(known || isHelper, `${path} uses the helper but is not a declared row`).toBe(
        true,
      )
    }
  })
})
