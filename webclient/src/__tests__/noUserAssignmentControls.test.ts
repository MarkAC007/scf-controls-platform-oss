/**
 * Class-level guard: no screen offers a person as the unit of assignment for a
 * control or an evidence task.
 *
 * Per-component tests pin the screens that exist today. They cannot fail when a
 * NEW screen reintroduces the pattern, and that is how this requirement was
 * lost the first time: an earlier sweep converted the control and evidence
 * owner fields to teams, carved out one picker as "a different concept", and
 * never covered tasks at all. Both carve-outs then shipped as the thing the
 * sweep had removed everywhere else.
 *
 * This reads the source tree rather than rendering, because the failure it is
 * looking for is a file nobody thought to write a test for.
 *
 * Risks are deliberately out of scope here, and that exemption is a product
 * decision rather than an oversight: there is no team-assignment capability for
 * a risk at all, so removing its owner picker would leave risks with no way to
 * record ownership. The allowance is narrow, named and asserted below, so that
 * it stays one file rather than becoming a habit.
 */
import { describe, expect, it } from 'vitest'

/**
 * Raw source of every component and module in the tree, keyed by path relative
 * to `src/`. `import.meta.glob` rather than `node:fs` because the project's
 * typecheck has no Node types -- a green vitest run with a red build is not a
 * green build here -- and because the bundler resolves the glob the same way in
 * CI as it does locally.
 */
const RAW = import.meta.glob('../**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

/**
 * Files allowed to keep a per-user assignment control, each with the reason.
 * Adding an entry here is a decision somebody has to defend in review; that is
 * the point of the list.
 */
const EXEMPT: Record<string, string> = {
  'components/RiskDetailPage.tsx':
    'Risks have no team-assignment capability, so removing this leaves them unassignable.',
  'components/RiskAssessmentList.tsx':
    'Reads the same risk owner the picker above writes.',
  'components/evidence/EvidenceAssigneeSelect.tsx':
    'Read-and-clear only, so a row stamped before the rule can be handed back.',
}

const FILES = Object.entries(RAW)
  .map(([key, text]) => ({ rel: key.replace(/^\.\.\//, ''), text }))
  .filter(
    (f) => !f.rel.includes('__tests__/') && !/\.test\.tsx?$/.test(f.rel)
  )

/** The patterns that make a screen a direct-assignment screen. */
const SIGNATURES: { name: string; pattern: RegExp }[] = [
  { name: 'an "+ Assign User" button', pattern: /\+\s*Assign User/ },
  {
    name: 'a label reading exactly "Assign To" or "Assigned To"',
    pattern: />\s*Assigned?\s+To\s*</i,
  },
  {
    name: 'a <select> bound to an assignee state variable',
    pattern: /value=\{assign(ed)?UserId\}/i,
  },
  {
    // A BARE identifier only. `row.assigned_user_id ?? null` and
    // `tracking.assigned_user_id === '' ? null : ...` are pass-through and
    // clear idioms on the evidence-tracking field, which is deliberately kept
    // readable and clearable so a row stamped before the rule can be handed
    // back. What this catches is the other shape: a value held in local state
    // because a picker on the screen just produced it.
    name: 'a request body key fed from an assignee picker\'s state',
    pattern: /assigned_user_id:\s*(?!null\b)[A-Za-z_$][\w$]*\s*(\|\|[^,}]*)?[,}]/,
  },
]

describe('no surface offers a user picker for control or evidence assignment', () => {
  it('finds source files to scan at all', () => {
    // Without this the sweep below is vacuous: an empty file list passes every
    // assertion in it, and a broken glob looks exactly like a clean tree.
    expect(FILES.length).toBeGreaterThan(100)
    expect(FILES.map((f) => f.rel)).toContain('components/EvidenceTaskList.tsx')
  })

  for (const { name, pattern } of SIGNATURES) {
    it(`no non-exempt file contains ${name}`, () => {
      const offenders = FILES.filter(
        (f) => !(f.rel in EXEMPT) && pattern.test(f.text)
      ).map((f) => f.rel)

      expect(offenders).toEqual([])
    })
  }

  it('the exemption list still describes files that exist', () => {
    // A stale exemption silently widens the guard: the file it names is gone,
    // but a new file at the same path would inherit the allowance.
    const present = new Set(FILES.map((f) => f.rel))
    for (const rel of Object.keys(EXEMPT)) {
      expect(present.has(rel), `exempt file no longer exists: ${rel}`).toBe(true)
    }
  })

  it('the polymorphic per-user picker is gone from the tree', () => {
    expect(FILES.map((f) => f.rel)).not.toContain('components/AssignmentPicker.tsx')
  })
})
