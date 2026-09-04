/**
 * The app's URL vocabulary — every query parameter that names a location.
 *
 * This app selects screens from `activeTab` state; `react-router-dom` is a
 * dependency that drives nothing. Rather than give it the wheel, the address
 * bar mirrors that state: `?tab=` names the screen, and each screen that has
 * sub-state of its own adds parameters it alone owns.
 *
 * Everything here is pure except the three helpers at the bottom: takes a
 * search string, returns data or a new search string. No React. The components
 * decide *when* to write and whether to push or replace; this module decides
 * only *what* the URL says.
 */

/**
 * Tabs whose name is honoured in `?tab=` — every destination the sidebar
 * offers, except the one the bare path already names (see `DEFAULT_TAB`).
 *
 * #785 opened this list with two entries and left the rest of the app writing
 * nothing to the address bar: eleven screens that refresh back to the
 * dashboard, cannot be bookmarked and cannot be sent to a colleague (#810).
 *
 * A `tab` naming anything not on this list is still ignored rather than
 * obeyed, so a mangled address bar lands on the dashboard rather than on a
 * blank screen. The two platform tabs are here because both pages gate
 * themselves on `is_platform_admin` and refuse to render for anyone else —
 * naming them in the URL is not a way past the sidebar's filter.
 *
 * Kept in sidebar order so a reader can check it against `Sidebar.tsx`.
 */
export const SYNCED_TABS = [
  // Overview
  'capability-posture',
  // Controls & Frameworks
  'library',
  'mapping-matrix',
  'scoping',
  // Risk & Third Party
  'risk-register',
  'vendors',
  // Evidence
  'evidence',
  // Documents
  'documents',
  // Operations
  'tasks',
  'systems',
  'users',
  // Admin
  'engagements',
  'webhooks',
  'audit-log',
  'catalog-changelog',
  'consultant-portal',
  'settings',
  // Platform (each page gates itself on is_platform_admin)
  'platform-catalog',
  'platform-tenants',
] as const
export type SyncedTab = (typeof SYNCED_TABS)[number]

/** The evidence workspace's two sub-screens. */
export const EVIDENCE_VIEWS = ['dashboard', 'workspace'] as const
export type EvidenceView = (typeof EVIDENCE_VIEWS)[number]

/** Where the app lands when the URL names nothing it recognises. */
export const DEFAULT_TAB = 'dashboard'
export const DEFAULT_EVIDENCE_VIEW: EvidenceView = 'dashboard'

/**
 * Every screen the app can be on: the synced tabs plus the landing screen,
 * which is addressed by the bare path and so never appears in `?tab=`.
 *
 * `App` passes its own `Tab` union here, so a destination added to the sidebar
 * without being added to `SYNCED_TABS` fails to compile rather than silently
 * joining the set of screens with no address.
 */
export type AppTab = SyncedTab | typeof DEFAULT_TAB

/**
 * The full `Tab` union — every screen id the app recognises.
 * Identical to `AppTab`; exported under this alias so Header and test files can
 * import the type without importing from App.tsx (which would create a cycle).
 */
export type Tab = AppTab

/**
 * Human-readable display label for each tab.
 *
 * Titles match the nav display labels shown in the Sidebar so the utility bar
 * and the sidebar always agree. The `satisfies` assertion is the exhaustiveness
 * check: adding a new tab to the `Tab` union without adding it here is a
 * compile error.
 */
export const TAB_TITLES = {
  'dashboard':          'Dashboard',
  'capability-posture': 'Analytics',
  'library':            'Control Library',
  'mapping-matrix':     'Framework Mappings',
  'scoping':            'Control Scoping',
  'risk-register':      'Risk Register',
  'vendors':            'Vendor Inventory',
  'evidence':           'Evidence',
  'documents':          'Generated Documents',
  'tasks':              'Task Management',
  'systems':            'Systems Registry',
  'users':              'User Management',
  'engagements':        'Engagements',
  'webhooks':           'Webhooks',
  'audit-log':          'Audit Log',
  'catalog-changelog':  'Catalog Changelog',
  'consultant-portal':  'Consultant Portal',
  'settings':           'Org Settings',
  'platform-catalog':   'Catalog',
  'platform-tenants':   'Tenants',
} satisfies Record<Tab, string>

export const PARAM_TAB = 'tab'
export const PARAM_EVIDENCE_VIEW = 'view'
export const PARAM_EVIDENCE_ITEM = 'item'
export const PARAM_LIBRARY_ITEM = 'item'
export const PARAM_RISK_ITEM = 'risk'
export const PARAM_VENDOR_ITEM = 'vendor'
export const PARAM_SYSTEM_ITEM = 'system'
export const PARAM_TASK_ITEM = 'task'

/**
 * Parameters that belong to a tab and must leave with it.
 *
 * A `doc` left behind on another screen's URL reopens that document on the
 * next reload; an `item` left behind does the same to an evidence or library
 * item. Both evidence and library use the literal param name `item`, but they
 * are tab-scoped by this mechanism — switching tabs clears the outgoing tab's
 * params, so `item` from one tab never bleeds into the other. Tabs absent from
 * this map own no parameters beyond `tab` itself.
 */
export const TAB_OWNED_PARAMS: Partial<Record<SyncedTab, readonly string[]>> = {
  documents: ['doc', 'mode'],
  evidence: [PARAM_EVIDENCE_VIEW, PARAM_EVIDENCE_ITEM],
  library: [PARAM_LIBRARY_ITEM],
  'risk-register': [PARAM_RISK_ITEM],
  vendors: [PARAM_VENDOR_ITEM],
  systems: [PARAM_SYSTEM_ITEM],
  tasks: [PARAM_TASK_ITEM],
}

export interface AppLocation {
  /** The synced tab the URL names, or null for "the usual landing screen". */
  tab: SyncedTab | null
  /** Which evidence sub-screen, resolved. Only meaningful when tab is evidence. */
  evidenceView: EvidenceView
  /** The evidence item to select, or null. Only meaningful when tab is evidence. */
  evidenceItem: string | null
  /** The library control to deep-link, or null. Only meaningful when tab is library. */
  libraryItem: string | null
  /** The risk assessment to deep-link, or null. Only meaningful when tab is risk-register. */
  riskItem: string | null
  /** The vendor to deep-link, or null. Only meaningful when tab is vendors. */
  vendorItem: string | null
  /** The system to deep-link, or null. Only meaningful when tab is systems. */
  systemItem: string | null
  /** The task to deep-link, or null. Only meaningful when tab is tasks. */
  taskItem: string | null
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

  // `item` is the literal param name shared by both evidence and library, but
  // it is tab-scoped: we read it as an evidence item only when the tab is
  // evidence (or when evidence is inferred from the view), and as a library
  // item only when the tab is library.
  const rawItem = p.get(PARAM_EVIDENCE_ITEM)?.trim() || null

  const requestedView = p.get(PARAM_EVIDENCE_VIEW)
  const namedView = EVIDENCE_VIEWS.find(v => v === requestedView)
  // An item with no view still means "show me this item", and the item only
  // exists on the workspace sub-screen. Honouring the item while landing on
  // the dashboard would be the worst outcome: a link that looks like it worked.
  const evidenceView = namedView ?? (rawItem && tab === 'evidence' ? 'workspace' : DEFAULT_EVIDENCE_VIEW)

  // evidence item: only meaningful when on the evidence tab
  const evidenceItem = tab === 'evidence' ? rawItem : null

  // library item: only meaningful when on the library tab
  const libraryItem = tab === 'library' ? rawItem : null

  // detail params — each scoped to its own tab and its own param name
  const riskItem = tab === 'risk-register' ? (p.get(PARAM_RISK_ITEM)?.trim() || null) : null
  const vendorItem = tab === 'vendors' ? (p.get(PARAM_VENDOR_ITEM)?.trim() || null) : null
  const systemItem = tab === 'systems' ? (p.get(PARAM_SYSTEM_ITEM)?.trim() || null) : null
  const taskItem = tab === 'tasks' ? (p.get(PARAM_TASK_ITEM)?.trim() || null) : null

  return { tab, evidenceView, evidenceItem, libraryItem, riskItem, vendorItem, systemItem, taskItem }
}

/**
 * The search string with `tab` set, leaving every unrelated parameter alone.
 *
 * Setting a tab is also leaving whichever tab the URL named, so that tab's own
 * parameters go with it. Without that, walking evidence → scoping → evidence
 * carries `item` across the middle screen and reopens a stale selection on
 * arrival — invisible while only two tabs were synced, routine once every
 * destination is (#810).
 */
export function withTab(search: string, tab: SyncedTab): string {
  const p = params(withoutTab(search))
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
  for (const owned of (leaving && TAB_OWNED_PARAMS[leaving]) || []) {
    p.delete(owned)
  }
  return p.toString()
}

/**
 * The search string that should be showing while `tab` is the active screen,
 * or `null` when the address bar already says the right thing.
 *
 * `null` is not a detail: three other components own parameters on this same
 * URL — `DocumentsPage` has `doc`/`mode`, `EvidenceWorkspace` has `view`,
 * `EvidenceReview` has `item` — and a writer that rewrote the query string on
 * every render would race all three. This says "nothing to do" whenever the
 * URL and the active tab already agree, which is every render but the one
 * immediately after a navigation.
 */
export function searchForTab(search: string, tab: AppTab): string | null {
  const synced = SYNCED_TABS.find(t => t === tab)
  if (synced) {
    return readAppLocation(search).tab === synced ? null : withTab(search, synced)
  }
  // The landing screen is the bare path, so arriving there means taking the
  // previous screen's `tab` — and everything that tab owned — off the URL.
  return params(search).has(PARAM_TAB) ? withoutTab(search) : null
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
 * The search string with the selected library control named, or cleared.
 *
 * Sets `item` while ensuring `tab=library` (switching tabs also clears any
 * outgoing tab's owned params, so an evidence `item` present in the URL is
 * removed automatically via `withTab`). When `itemId` is null the `item`
 * param is removed, leaving the library list view.
 */
export function withLibraryItem(search: string, itemId: string | null): string {
  // First land on the library tab (which clears any outgoing tab's params,
  // including another tab's `item`), then set/clear the library's own item.
  const p = params(withTab(search, 'library'))
  if (itemId) p.set(PARAM_LIBRARY_ITEM, itemId)
  else p.delete(PARAM_LIBRARY_ITEM)
  return p.toString()
}

/**
 * The search string with the selected risk item named, or cleared.
 *
 * Sets `risk` while ensuring `tab=risk-register` (switching tabs also clears
 * any outgoing tab's owned params). When `itemId` is null the `risk` param is
 * removed, leaving the risk register list view.
 */
export function withRiskItem(search: string, itemId: string | null): string {
  const p = params(withTab(search, 'risk-register'))
  if (itemId) p.set(PARAM_RISK_ITEM, itemId)
  else p.delete(PARAM_RISK_ITEM)
  return p.toString()
}

/**
 * The search string with the selected vendor named, or cleared.
 *
 * Sets `vendor` while ensuring `tab=vendors`. When `itemId` is null the
 * `vendor` param is removed, leaving the vendor list view.
 */
export function withVendorItem(search: string, itemId: string | null): string {
  const p = params(withTab(search, 'vendors'))
  if (itemId) p.set(PARAM_VENDOR_ITEM, itemId)
  else p.delete(PARAM_VENDOR_ITEM)
  return p.toString()
}

/**
 * The search string with the selected system named, or cleared.
 *
 * Sets `system` while ensuring `tab=systems`. When `itemId` is null the
 * `system` param is removed, leaving the systems list view.
 */
export function withSystemItem(search: string, itemId: string | null): string {
  const p = params(withTab(search, 'systems'))
  if (itemId) p.set(PARAM_SYSTEM_ITEM, itemId)
  else p.delete(PARAM_SYSTEM_ITEM)
  return p.toString()
}

/**
 * The search string with the selected task named, or cleared.
 *
 * Sets `task` while ensuring `tab=tasks`. When `itemId` is null the `task`
 * param is removed, leaving the task list view.
 */
export function withTaskItem(search: string, itemId: string | null): string {
  const p = params(withTab(search, 'tasks'))
  if (itemId) p.set(PARAM_TASK_ITEM, itemId)
  else p.delete(PARAM_TASK_ITEM)
  return p.toString()
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

// ---- The only functions here that touch the browser ----
//
// Everything above is pure so it can be tested without a DOM. These exist so
// no caller has to reassemble `pathname + search` by hand — get that wrong
// once and every navigation silently drops the path.

function href(search: string): string {
  return `${window.location.pathname}${toSearchString(search)}`
}

/** The screen the address bar is currently asking for. */
export function readTabFromUrl(): AppTab {
  return readAppLocation(window.location.search).tab ?? DEFAULT_TAB
}

/** Same page, new query string, no new history entry. */
export function replaceSearch(search: string): void {
  window.history.replaceState(window.history.state, '', href(search))
}

/** Same page, new query string, and an entry for Back to return to. */
export function pushSearch(search: string): void {
  window.history.pushState(window.history.state, '', href(search))
}
