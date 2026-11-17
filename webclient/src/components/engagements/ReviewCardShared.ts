// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Small helpers shared by the review card panel, its discussion thread and the
 * query list. Kept free of React so each piece formats dates and errors the
 * same way.
 */

/** The server's own message from a thrown apiFetch error. */
export function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message
  return fallback
}

/** HTTP status carried by an apiFetch error, when there is one. */
export function errorStatus(err: unknown): number | undefined {
  const status = (err as { status?: unknown } | null)?.status
  return typeof status === 'number' ? status : undefined
}

/**
 * A date-only value ("2026-01-01") is a calendar date. `new Date()` reads it as
 * UTC midnight, which shows the day before west of UTC, so parse it as local.
 */
export function parseDate(value: string): Date {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(value)
}

export function fmtDate(value: string | null | undefined): string | null {
  if (!value) return null
  const d = parseDate(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

export function fmtDateTime(value: string | null | undefined): string | null {
  if (!value) return null
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString(undefined, {
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

/**
 * Escape inside an inline form cancels that form; it must not also close the
 * panel. The panel's document listener skips events that were prevented.
 */
export function cancelOnEscape(cancel: () => void) {
  return (e: { key: string; preventDefault: () => void }) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      cancel()
    }
  }
}
