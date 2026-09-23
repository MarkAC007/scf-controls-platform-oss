/**
 * Decoding for the before/after values held in the audit log.
 *
 * Those columns are plain text carrying a serialised value, and more than one
 * code path writes them: most go through a JSON encoder — so a date arrives
 * carrying its own quote characters, and an unset value arrives as the four
 * characters `null` — while a few writers store the value verbatim. The log is
 * append-only, so both encodings are permanent and a reader has to cope with
 * either one. Decoding is therefore the reader's job, not something a writer
 * change could retire.
 */

/**
 * Returns the human-meaningful value, or null when there is none.
 *
 * Callers render a single em-dash for null, so every flavour of empty — an
 * absent column, an encoded null, and the legacy `str(None)` form — reads the
 * same way instead of leaking its encoding onto the screen.
 */
export function decodeAuditValue(raw: string | null | undefined): string | null {
  if (raw === null || raw === undefined || raw === '') return null

  let decoded: unknown = raw
  try {
    decoded = JSON.parse(raw)
  } catch {
    // Not encoded — a value a writer stored as it stood. Keep it as it stands.
  }

  if (decoded === null || decoded === undefined) return null
  if (typeof decoded === 'string') {
    return decoded === '' || decoded === 'None' ? null : decoded
  }
  if (typeof decoded === 'object') return JSON.stringify(decoded)
  return String(decoded)
}

/**
 * True when the field name's final segment is `date`.
 *
 * Anchored on purpose: a substring test also catches `updated_at`,
 * `last_updated` and `validated`, none of which hold a date, and sends them
 * through date formatting that can only mangle them.
 */
export function isDateField(field: string): boolean {
  return /(^|_)date$/.test(field)
}

/**
 * Formats a decoded date for display.
 *
 * A date-only string is pinned to local midnight — the house convention —
 * because a bare UTC-midnight parse shifts the day backwards west of
 * Greenwich. A single space is normalised to `T` so the form a Python
 * `str(datetime)` produces parses in Safari as well as V8.
 *
 * Anything unparseable falls back to the value itself. It must never fall
 * through to `toLocaleDateString`, which formats a NaN time value as the
 * literal string "Invalid Date" rather than throwing — so a try/catch around
 * it catches nothing and the user reads "Invalid Date" as if it were data.
 */
export function formatAuditDate(value: string): string {
  const candidate = /^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00` : value.replace(' ', 'T')
  const parsed = new Date(candidate)
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
}
