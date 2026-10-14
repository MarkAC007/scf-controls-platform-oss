/**
 * DeficienciesPage — the organisation's plan of action and milestones (PIG R8).
 *
 * Every control deficiency, whoever or whatever raised it: by hand, a control
 * going at risk, insufficient evidence, a rejected file, or a capability test.
 * The counts come from the whole record (status and overdue) and the server's
 * summary (open and material); the domain and risk filters go to the server.
 *
 * Two views of the same record (#1155): the List, and a Board with a column
 * per status. Both read one query, and apply Status, overdue and search in the
 * browser, so switching views refetches nothing and, for any filters, both
 * show the same deficiencies. On the board, Status picks the columns, and the
 * toolbar count leaves out the cards a collapsed Cancelled column hides.
 * The view is the viewer's choice: `?view=` in the URL wins, then the one
 * they last chose (local storage), then the list; the page writes it back
 * into a deficiencies URL that arrives without one.
 */
import { useEffect, useMemo, useState, type JSX } from 'react'
import { readAppLocation, replaceSearch, withDeficiencyView, type DeficiencyView } from '../../data/appUrl'
import { useIsOrgEditor } from '../../hooks/useHasOrgRole'
import {
  isOpenDeficiency,
  isOverdue,
  localIsoDate,
  useDeficiencies,
  useDeficiencySummary,
  useOrgMembersQuery,
} from '../../hooks/useDeficiencies'
import type { ActionItemStatus, Deficiency, DeficiencyFilters } from '../../types'
import FilterRadio from '../explorer/FilterRadio'
import FilterSidebar, {
  FilterCheckbox,
  FilterGroup,
  FilterSelect,
  defaultFiltersCollapsed,
} from '../explorer/FilterSidebar'
import ListToolbar from '../explorer/ListToolbar'
import DeficiencyBoard, { isColumnCollapsed, useCancelledCollapsed } from './DeficiencyBoard'
import DeficiencyCompleteModal from './DeficiencyCompleteModal'
import DeficiencyFormModal from './DeficiencyFormModal'
import DeficiencyList from './DeficiencyList'
import { STATUSES } from './deficiencyText'

type StatusFilter = 'all' | 'active' | ActionItemStatus

const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'active', label: 'Open or in progress' },
  { value: 'open', label: 'Open' },
  { value: 'in_progress', label: 'In progress' },
  { value: 'completed', label: 'Completed' },
  { value: 'cancelled', label: 'Cancelled' },
]

const ACTIVE_STATUSES = STATUSES.filter((s) => isOpenDeficiency({ status: s }))

/** The statuses a Status filter choice shows: the board's columns, the list's rows. */
function boardStatuses(status: StatusFilter): ActionItemStatus[] {
  if (status === 'all') return STATUSES
  if (status === 'active') return ACTIVE_STATUSES
  return [status]
}

/** Local storage: the view this viewer last chose. */
export const DEFICIENCY_VIEW_STORAGE_KEY = 'scf.deficiencies.view'

function storedView(): DeficiencyView | null {
  try {
    const v = window.localStorage.getItem(DEFICIENCY_VIEW_STORAGE_KEY)
    return v === 'list' || v === 'board' ? v : null
  } catch {
    return null
  }
}

/** The URL's view, else the viewer's last, else the list. */
const initialView = (): DeficiencyView =>
  readAppLocation(window.location.search).deficiencyView ?? storedView() ?? 'list'

/**
 * Take the view from the address bar, and name it there when the address is
 * this page's but says no view (an in-app link, or Back to an entry made
 * before views existed). Leaves any other page's address alone.
 */
function syncViewFromUrl(setView: (v: DeficiencyView) => void) {
  const loc = readAppLocation(window.location.search)
  if (loc.tab !== 'deficiencies') return
  const next = loc.deficiencyView ?? storedView() ?? 'list'
  setView(next)
  if (!loc.deficiencyView) replaceSearch(withDeficiencyView(window.location.search, next))
}

const VIEW_OPTIONS: { value: DeficiencyView; label: string }[] = [
  { value: 'list', label: 'List' },
  { value: 'board', label: 'Board' },
]

const emptyText = (filtered: boolean) =>
  filtered
    ? 'No deficiencies match these filters.'
    : 'No deficiencies recorded. They are raised by hand here or on a control, and automatically when a control goes at risk, its evidence is insufficient or rejected, or a capability test finds a gap.'

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err))

function matchesSearch(d: Deficiency, term: string): boolean {
  if (!term) return true
  return [d.title, d.scf_id, d.risk_code, d.description]
    .some((field) => field?.toLowerCase().includes(term))
}

export default function DeficienciesPage({ organizationId }: { organizationId: string }): JSX.Element {
  const canEdit = useIsOrgEditor(organizationId)
  const [view, setView] = useState<DeficiencyView>(initialView)
  const [filtersCollapsed, setFiltersCollapsed] = useState(defaultFiltersCollapsed)
  const [status, setStatus] = useState<StatusFilter>('all')
  const [domain, setDomain] = useState('')
  const [riskCode, setRiskCode] = useState('')
  const [overdueOnly, setOverdueOnly] = useState(false)
  const [search, setSearch] = useState('')
  const [raising, setRaising] = useState(false)
  // Owned here, not by the list, so a refetch that fails or empties the list
  // cannot unmount a form someone is typing in.
  const [editing, setEditing] = useState<Deficiency | null>(null)
  const [completing, setCompleting] = useState<Deficiency | null>(null)
  const [cancelledCollapsed, toggleCancelled] = useCancelledCollapsed()

  // `replaceState`: List and Board are one screen shown two ways, not two places.
  const chooseView = (next: DeficiencyView) => {
    setView(next)
    replaceSearch(withDeficiencyView(window.location.search, next))
    try {
      window.localStorage.setItem(DEFICIENCY_VIEW_STORAGE_KEY, next)
    } catch {
      // Private mode or storage full: the URL still holds the choice.
    }
  }

  // On arrival and on Back or Forward. Writes only `view`: App owns `tab`.
  // Arrival waits a microtask: App names the tab in an effect that runs after
  // this one, and its write would otherwise land on top of ours.
  useEffect(() => {
    let live = true
    const sync = () => syncViewFromUrl(setView)
    queueMicrotask(() => {
      if (live) sync()
    })
    window.addEventListener('popstate', sync)
    return () => {
      live = false
      window.removeEventListener('popstate', sync)
    }
  }, [])

  const board = view === 'board'
  // Status is not sent: both views read this one query, so switching views
  // or statuses never waits on a fetch.
  const filters = useMemo<DeficiencyFilters>(() => {
    const f: DeficiencyFilters = {}
    if (domain) f.domain = domain
    if (riskCode) f.risk_code = riskCode
    return f
  }, [domain, riskCode])
  const columns = boardStatuses(status)

  // The whole record, for the counts and the filter options; the same query
  // as the list when no domain or risk is set.
  const all = useDeficiencies(organizationId)
  const listed = useDeficiencies(organizationId, filters)
  const summary = useDeficiencySummary(organizationId)
  const members = useOrgMembersQuery(organizationId)

  const today = localIsoDate()
  const counts = useMemo(() => {
    const rows = all.data ?? []
    const by = (s: ActionItemStatus) => rows.filter((d) => d.status === s).length
    return {
      inProgress: by('in_progress'),
      completed: by('completed'),
      cancelled: by('cancelled'),
      overdue: rows.filter((d) => isOverdue(d, today)).length,
    }
  }, [all.data, today])

  const domainOptions = useMemo(() => {
    const open = new Map((summary.data?.by_domain ?? []).map((d) => [d.domain, d.open_count]))
    const codes = [...new Set((all.data ?? []).map((d) => d.domain).filter((d): d is string => !!d))].sort()
    return [
      { value: '', label: 'All domains' },
      ...codes.map((code) => ({ value: code, label: `${code} · ${open.get(code) ?? 0} open` })),
    ]
  }, [all.data, summary.data])

  const riskOptions = useMemo(() => {
    const codes = [...new Set((all.data ?? []).map((d) => d.risk_code).filter((r): r is string => !!r))].sort()
    return [{ value: '', label: 'All risks' }, ...codes.map((code) => ({ value: code, label: code }))]
  }, [all.data])

  const term = search.trim().toLowerCase()
  // Every filter, in both views; on the board, Status picks the columns.
  const visible = (listed.data ?? []).filter(
    (d) => columns.includes(d.status) && (!overdueOnly || isOverdue(d, today)) && matchesSearch(d, term),
  )
  // What the viewer can see: on the board, not the cards a collapsed column hides.
  const shown = board ? visible.filter((d) => !isColumnCollapsed(d.status, columns, cancelledCollapsed)) : visible
  const filtered = status !== 'all' || domain || riskCode || overdueOnly || term

  const statValue = (n: number | undefined, loading: boolean) => (loading ? '…' : n ?? '–')

  return (
    <div className="tasks-page tasks-explorer-page deficiencies-page">
      <div className="tasks-explorer-shell">
        <FilterSidebar
          collapsed={filtersCollapsed}
          onToggleCollapsed={() => setFiltersCollapsed((c) => !c)}
          aria-label="Deficiency filters"
        >
          <FilterGroup label="STATUS">
            <FilterRadio
              label="STATUS"
              name="deficiency-status-filter"
              options={STATUS_OPTIONS}
              value={status}
              onChange={(v) => setStatus(v as StatusFilter)}
            />
          </FilterGroup>
          <FilterGroup label="DUE">
            <FilterCheckbox label="Overdue only" checked={overdueOnly} onChange={setOverdueOnly} count={counts.overdue} />
          </FilterGroup>
          <FilterGroup label="CONTROL DOMAIN">
            <FilterSelect ariaLabel="Domain" value={domain} onChange={setDomain} options={domainOptions} />
          </FilterGroup>
          <FilterGroup label="RISK">
            <FilterSelect ariaLabel="Risk" value={riskCode} onChange={setRiskCode} options={riskOptions} />
          </FilterGroup>
        </FilterSidebar>

        <div className="tasks-explorer-main">
          <ListToolbar
            search={search}
            onSearchChange={setSearch}
            searchPlaceholder="Search deficiencies — title, control, risk…"
            count={listed.isSuccess ? `${shown.length} deficienc${shown.length === 1 ? 'y' : 'ies'}` : undefined}
            actions={
              <>
                <div className="view-toggle deficiency-view-toggle" role="group" aria-label="View">
                  {VIEW_OPTIONS.map((o) => (
                    <button
                      key={o.value}
                      type="button"
                      className={view === o.value ? 'active' : undefined}
                      aria-pressed={view === o.value}
                      onClick={() => chooseView(o.value)}
                    >
                      {o.label}
                    </button>
                  ))}
                </div>
                {canEdit && (
                  <button type="button" className="btn-primary btn-small" onClick={() => setRaising(true)}>
                    Raise deficiency
                  </button>
                )}
              </>
            }
          />

          <div className="tasks-stats-strip deficiency-stats" role="group" aria-label="Deficiency counts">
            <div className="tasks-stat-item" data-testid="deficiency-stat-open">
              <span className="tasks-stat-value tasks-stat-total">{statValue(summary.data?.open_total, summary.isLoading)}</span>
              <span className="tasks-stat-label">OPEN</span>
            </div>
            <div className="tasks-stat-item" data-testid="deficiency-stat-material">
              <span className="tasks-stat-value tasks-stat-overdue">{statValue(summary.data?.material_open_total, summary.isLoading)}</span>
              <span className="tasks-stat-label">MATERIAL OPEN</span>
            </div>
            <div className="tasks-stat-item" data-testid="deficiency-stat-in-progress">
              <span className="tasks-stat-value tasks-stat-in-progress">{statValue(all.isSuccess ? counts.inProgress : undefined, all.isLoading)}</span>
              <span className="tasks-stat-label">IN PROGRESS</span>
            </div>
            <div className="tasks-stat-item" data-testid="deficiency-stat-overdue">
              <span className="tasks-stat-value tasks-stat-overdue">{statValue(all.isSuccess ? counts.overdue : undefined, all.isLoading)}</span>
              <span className="tasks-stat-label">OVERDUE</span>
            </div>
            <div className="tasks-stat-item" data-testid="deficiency-stat-completed">
              <span className="tasks-stat-value tasks-stat-completed">{statValue(all.isSuccess ? counts.completed : undefined, all.isLoading)}</span>
              <span className="tasks-stat-label">COMPLETED</span>
            </div>
            <div className="tasks-stat-item" data-testid="deficiency-stat-cancelled">
              <span className="tasks-stat-value tasks-stat-not-started">{statValue(all.isSuccess ? counts.cancelled : undefined, all.isLoading)}</span>
              <span className="tasks-stat-label">CANCELLED</span>
            </div>
          </div>

          <div className="deficiency-page-body">
            {summary.data && summary.data.unlinked_open > 0 && (
              <p className="gov-muted">
                {summary.data.unlinked_open} open deficienc{summary.data.unlinked_open === 1 ? 'y is' : 'ies are'} not raised
                against a control (a risk alone, or a capability test finding), so {summary.data.unlinked_open === 1 ? 'it has' : 'they have'} no
                control domain.
              </p>
            )}
            {!canEdit && (
              <p className="gov-muted">Only an editor or admin can raise, edit, complete or cancel a deficiency.</p>
            )}
            {summary.isError && (
              <div role="alert" className="gov-error">
                Could not load the open counts: {errorText(summary.error)}
              </div>
            )}
            {members.isError && (
              <div role="alert" className="gov-error">
                Could not load the members, so owners show as unknown: {errorText(members.error)}
              </div>
            )}

            {listed.isError && listed.data && (
              <div role="alert" className="gov-error">
                Could not refresh the deficiencies: {errorText(listed.error)}. Showing the last list loaded.
              </div>
            )}
            {listed.isLoading ? (
              <p className="tasks-loading">Loading deficiencies…</p>
            ) : !listed.data ? (
              <div role="alert" className="gov-error">
                Could not load the deficiencies: {errorText(listed.error)}
              </div>
            ) : board ? (
              <>
                {/* The board stays: its columns are still where a card can be moved to. */}
                {visible.length === 0 && <p className="deficiency-empty">{emptyText(!!filtered)}</p>}
                <DeficiencyBoard
                  organizationId={organizationId}
                  deficiencies={visible}
                  statuses={columns}
                  users={members.data}
                  canEdit={canEdit}
                  cancelledCollapsed={cancelledCollapsed}
                  onToggleCancelled={toggleCancelled}
                  onEdit={setEditing}
                  onComplete={setCompleting}
                />
              </>
            ) : visible.length === 0 ? (
              <p className="deficiency-empty">{emptyText(!!filtered)}</p>
            ) : (
              <DeficiencyList
                organizationId={organizationId}
                deficiencies={visible}
                users={members.data}
                canEdit={canEdit}
                onEdit={setEditing}
                onComplete={setCompleting}
              />
            )}
          </div>
        </div>
      </div>
      {raising && <DeficiencyFormModal organizationId={organizationId} onClose={() => setRaising(false)} />}
      {editing && (
        <DeficiencyFormModal
          key={editing.id}
          organizationId={organizationId}
          deficiency={editing}
          onClose={() => setEditing(null)}
        />
      )}
      {completing && (
        <DeficiencyCompleteModal
          key={completing.id}
          organizationId={organizationId}
          deficiency={completing}
          onClose={() => setCompleting(null)}
        />
      )}
    </div>
  )
}
