/**
 * The line diff behind "mine vs generated".
 *
 * The reader decides which of two texts is the policy by looking at this, so
 * the alignment matters as much as the counts: a row is a pair, and the side
 * with no line on it must be null rather than an empty string, or the two panes
 * drift apart by a line and the diff stops meaning anything.
 */
import { describe, expect, it } from 'vitest'

import { lineDiff } from '../lineDiff'

describe('lineDiff', () => {
  it('reports no changes for identical input', () => {
    const text = ['Access is reviewed quarterly.', 'Owners are named.'].join('\n')
    const { rows, added, removed } = lineDiff(text, text)

    expect(added).toBe(0)
    expect(removed).toBe(0)
    expect(rows.every((r) => r.op === 'equal')).toBe(true)
    expect(rows).toHaveLength(2)
  })

  it('treats a line differing only by trailing whitespace as unchanged', () => {
    // Two paragraphs that differ by a trailing space are the same policy text.
    // Showing that as a change buries the changes that matter.
    const { added, removed } = lineDiff('Access is reviewed.  ', 'Access is reviewed.')
    expect(added).toBe(0)
    expect(removed).toBe(0)
  })

  it('reports a pure insertion', () => {
    const mine = ['First.', 'Third.'].join('\n')
    const generated = ['First.', 'Second.', 'Third.'].join('\n')
    const { rows, added, removed } = lineDiff(mine, generated)

    expect(added).toBe(1)
    expect(removed).toBe(0)

    const inserted = rows.find((r) => r.op === 'added')
    expect(inserted?.right).toBe('Second.')
    expect(inserted?.left).toBeNull()
    expect(inserted?.leftNo).toBeNull()
    expect(inserted?.rightNo).toBe(2)
  })

  it('reports a pure deletion', () => {
    const mine = ['First.', 'Second.', 'Third.'].join('\n')
    const generated = ['First.', 'Third.'].join('\n')
    const { rows, added, removed } = lineDiff(mine, generated)

    expect(added).toBe(0)
    expect(removed).toBe(1)

    const deleted = rows.find((r) => r.op === 'removed')
    expect(deleted?.left).toBe('Second.')
    expect(deleted?.right).toBeNull()
    expect(deleted?.leftNo).toBe(2)
    expect(deleted?.rightNo).toBeNull()
  })

  it('reports a replacement as one removal and one addition', () => {
    // The real case: a preserved human edit written against an earlier scope.
    const mine = ['Scope covers 1390 controls.', 'Owners are named.'].join('\n')
    const generated = ['Scope covers 345 controls.', 'Owners are named.'].join('\n')
    const { rows, added, removed } = lineDiff(mine, generated)

    expect(added).toBe(1)
    expect(removed).toBe(1)
    expect(rows.find((r) => r.op === 'removed')?.left).toBe('Scope covers 1390 controls.')
    expect(rows.find((r) => r.op === 'added')?.right).toBe('Scope covers 345 controls.')
    expect(rows.filter((r) => r.op === 'equal')).toHaveLength(1)
  })

  it('keeps the two panes alignable: every row has a line on at least one side', () => {
    const { rows } = lineDiff('A\nB\nC', 'A\nX\nC\nD')
    expect(rows.every((r) => r.left !== null || r.right !== null)).toBe(true)
    // Line numbers are per-side and only present where that side has a line.
    expect(rows.filter((r) => r.left !== null).map((r) => r.leftNo)).toEqual([1, 2, 3])
    expect(rows.filter((r) => r.right !== null).map((r) => r.rightNo)).toEqual([1, 2, 3, 4])
  })

  it('handles one side being empty', () => {
    const { added, removed } = lineDiff('', 'Generated line.')
    expect(added).toBe(1)
    // The empty string splits to one empty line, which the generator does not
    // have — so it counts as removed. The panes still align.
    expect(removed).toBe(1)
  })
})
