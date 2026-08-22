/**
 * A line-level diff, computed in the browser.
 *
 * There is no diff library in `webclient/package.json` and this does not
 * justify adding one: the inputs are single policy sections — tens of lines,
 * not thousands — so the O(n·m) LCS table below costs nothing measurable and
 * carries no supply-chain weight. If a whole-document diff ever appears, that
 * is the point to reach for a real Myers implementation, not before.
 *
 * The diff is deliberately line-level rather than word-level. The decision the
 * reader is making is "which of these two paragraphs is the policy", not
 * "which word changed", and a word-level diff of two independently written
 * paragraphs is confetti.
 */

export type DiffOp = 'equal' | 'removed' | 'added'

export interface DiffRow {
  op: DiffOp
  /** The line as it appears in the operative document. Null when added. */
  left: string | null
  /** The line as the generator wrote it. Null when removed. */
  right: string | null
  /** 1-based line numbers, for the gutters. Null on the side that has no line. */
  leftNo: number | null
  rightNo: number | null
}

/** Rows plus the counts the caller needs for a "N added, M removed" summary. */
export interface DiffResult {
  rows: DiffRow[]
  added: number
  removed: number
}

/**
 * Longest common subsequence over lines, then walked back into aligned rows.
 *
 * Trailing whitespace is ignored when matching but preserved in the output:
 * two lines that differ only by a trailing space are the same policy text, and
 * showing that as a change would bury the changes that matter.
 */
export function lineDiff(left: string, right: string): DiffResult {
  const a = left.split('\n')
  const b = right.split('\n')
  const key = (s: string) => s.replace(/\s+$/, '')

  // lcs[i][j] = length of the LCS of a[i…] and b[j…]. Built from the end so the
  // walk below can go forwards, which is what keeps the row order natural.
  const lcs: number[][] = Array.from({ length: a.length + 1 }, () =>
    new Array<number>(b.length + 1).fill(0)
  )
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i][j] =
        key(a[i]) === key(b[j])
          ? lcs[i + 1][j + 1] + 1
          : Math.max(lcs[i + 1][j], lcs[i][j + 1])
    }
  }

  const rows: DiffRow[] = []
  let added = 0
  let removed = 0
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (key(a[i]) === key(b[j])) {
      rows.push({ op: 'equal', left: a[i], right: b[j], leftNo: i + 1, rightNo: j + 1 })
      i++
      j++
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      rows.push({ op: 'removed', left: a[i], right: null, leftNo: i + 1, rightNo: null })
      removed++
      i++
    } else {
      rows.push({ op: 'added', left: null, right: b[j], leftNo: null, rightNo: j + 1 })
      added++
      j++
    }
  }
  while (i < a.length) {
    rows.push({ op: 'removed', left: a[i], right: null, leftNo: i + 1, rightNo: null })
    removed++
    i++
  }
  while (j < b.length) {
    rows.push({ op: 'added', left: null, right: b[j], leftNo: null, rightNo: j + 1 })
    added++
    j++
  }

  return { rows, added, removed }
}
