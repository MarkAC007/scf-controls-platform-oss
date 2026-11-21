// SPDX-License-Identifier: AGPL-3.0-only
/**
 * A small RFC 4180 CSV reader for the evidence viewer (#1309).
 *
 * Handles what real exports contain: quoted fields, doubled quotes inside
 * them, commas and line breaks inside quotes, CRLF or LF line endings and a
 * leading byte order mark. Lenient where the RFC is strict, because this only
 * draws a preview: an unterminated quote runs to the end of the text instead
 * of throwing, and blank lines are skipped rather than drawn as empty rows. A
 * trailing newline therefore never adds a row.
 */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = []
  let row: string[] = []
  let field = ''
  let inQuotes = false
  let i = text.charCodeAt(0) === 0xfeff ? 1 : 0

  const endRow = () => {
    row.push(field)
    // A blank line is one empty field; it is not a row of data.
    if (!(row.length === 1 && row[0] === '')) rows.push(row)
    row = []
    field = ''
  }

  while (i < text.length) {
    const ch = text[i]
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"'
          i += 2
          continue
        }
        inQuotes = false
        i += 1
        continue
      }
      field += ch
      i += 1
      continue
    }
    if (ch === '"') {
      inQuotes = true
      i += 1
    } else if (ch === ',') {
      row.push(field)
      field = ''
      i += 1
    } else if (ch === '\r' || ch === '\n') {
      endRow()
      i += ch === '\r' && text[i + 1] === '\n' ? 2 : 1
    } else {
      field += ch
      i += 1
    }
  }
  if (field !== '' || row.length > 0) endRow()
  return rows
}
