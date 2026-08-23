/**
 * The app's URL vocabulary — every query parameter that names a location.
 *
 * This app selects screens from `activeTab` state; `react-router-dom` is a
 * dependency that drives nothing, and giving it the wheel would touch all
 * twenty-two screens. So the address bar reflects the two workspaces that need
 * to be linkable — documents and evidence — and nothing else (#785).
 *
 * Everything here is pure: takes a search string, returns data or a new search
 * string. No `window`, no React. The components decide *when* to write and
 * whether to push or replace; this module decides only *what* the URL says.
 */

/**
 * Tabs whose name is honoured in `?tab=`.
 *
 * Closed on purpose. A `tab` naming anything else is ignored rather than
 * obeyed: several screens receive one-shot navigation signals through
 * sessionStorage and would arrive without them, landing in a state nobody has
 * checked. Evidence qualifies for this list precisely because #785 removed its
 * one-shot signal — the URL now carries what sessionStorage used to.
 */
export const SYNCED_TABS = ['documents', 'evidence'] as const
export type SyncedTab = (typeof SYNCED_TABS)[number]

/** The evidence workspace's two sub-screens. */
export const EVIDENCE_VIEWS = ['dashboard', 'workspace'] as const
export type EvidenceView = (typeof EVIDENCE_VIEWS)[number]

/** Where the app lands when the URL names nothing it recognises. */
export const DEFAULT_TAB = 'dashboard'
export const DEFAULT_EVIDENCE_VIEW: EvidenceView = 'dashboard'

export const PARAM_TAB = 'tab'
export const PARAM_EVIDENCE_VIEW = 'view'
export const PARAM_EVIDENCE_ITEM = 'item'

/**
 * Parameters that belong to a tab and must leave with it.
 *
 * A `doc` left behind on the dashboard's URL reopens that document on the next
 * reload; an `item` left behind does the same to an evidence item.
 */
export const TAB_OWNED_PARAMS: Record<SyncedTab, readonly string[]> = {
  documents: ['doc', 'mode'],
  evidence: [PARAM_EVIDENCE_VIEW, PARAM_EVIDENCE_ITEM],
}

export interface AppLocation {
  /** The synced tab the URL names, or null for "the usual landing screen". */
  tab: SyncedTab | null
  /** Which evidence sub-screen, resolved. Only meaningful when tab is evidence. */
  evidenceView: EvidenceView
  /** The evidence item to select, or null. */
  evidenceItem: string | null
}

function params(search: string): URLSearchParams {
  // `URLSearchParams` does not throw on malformed input — it drops what it
  // cannot parse — so a hand-mangled address bar degrades to "no parameters"
  // rather than a white screen.
  return new URLSearchParams(search)
}

/** What the query string asks for, with every unknown value resolved away. */
export function readAppLocation(search: string): AppLocation {
  const p = params(search)

  const requestedTab = p.get(PARAM_TAB)
  const tab = SYNCED_TABS.find(t => t === requestedTab) ?? null

  const item = p.get(PARAM_EVIDENCE_ITEM)?.trim() || null

  const requestedView = p.get(PARAM_EVIDENCE_VIEW)
  const namedView = EVIDENCE_VIEWS.find(v => v === requestedView)
  // An item with no view still means "show me this item", and the item only
  // exists on the workspace sub-screen. Honouring the item while landing on
  // the dashboard would be the worst outcome: a link that looks like it worked.
  const evidenceView = namedView ?? (item ? 'workspace' : DEFAULT_EVIDENCE_VIEW)

  return { tab, evidenceView, evidenceItem: item }
}

/** The search string with `tab` set, leaving every other parameter alone. */
export function withTab(search: string, tab: SyncedTab): string {
  const p = params(search)
  p.set(PARAM_TAB, tab)
  return p.toString()
}

/**
 * The search string with `tab` and everything that tab owned removed.
 *
 * Called when leaving a synced tab. Unrelated parameters — `invite_type`, an
 * invite token — survive: they describe the session, not the screen.
 */
export function withoutTab(search: string): string {
  const p = params(search)
  const leaving = SYNCED_TABS.find(t => t === p.get(PARAM_TAB))
  p.delete(PARAM_TAB)
  for (const owned of leaving ? TAB_OWNED_PARAMS[leaving] : []) {
    p.delete(owned)
  }
  return p.toString()
}

/** The search string with the evidence sub-screen named. */
export function withEvidenceView(search: string, view: EvidenceView): string {
  const p = params(search)
  p.set(PARAM_EVIDENCE_VIEW, view)
  // Leaving the workspace takes its selection with it — the dashboard has no
  // selected item, and a stale one would reappear on the next reload.
  if (view !== 'workspace') p.delete(PARAM_EVIDENCE_ITEM)
  return p.toString()
}

/** The search string with the selected evidence item named, or cleared. */
export function withEvidenceItem(search: string, item: string | null): string {
  const p = params(search)
  if (item) p.set(PARAM_EVIDENCE_ITEM, item)
  else p.delete(PARAM_EVIDENCE_ITEM)
  return p.toString()
}

/** A full deep link to one evidence item, for handing to `history`. */
export function evidenceItemSearch(search: string, item: string): string {
  return withEvidenceItem(
    withEvidenceView(withTab(search, 'evidence'), 'workspace'),
    item,
  )
}

/**
 * `?a=b`, or `''` when there is nothing to ask for.
 *
 * Kept here so no caller has to remember that a bare `?` is a URL change the
 * history API will happily record.
 */
export function toSearchString(search: string): string {
  return search ? `?${search}` : ''
}

// ---- The only two functions here that touch the browser ----
//
// Everything above is pure so it can be tested without a DOM. These two exist
// so no caller has to reassemble `pathname + search` by hand — get that wrong
// once and every navigation silently drops the path.

function href(search: string): string {
  return `${window.location.pathname}${toSearchString(search)}`
}

/** Same page, new query string, no new history entry. */
export function replaceSearch(search: string): void {
  window.history.replaceState(window.history.state, '', href(search))
}

/** Same page, new query string, and an entry for Back to return to. */
export function pushSearch(search: string): void {
  window.history.pushState(window.history.state, '', href(search))
}
