import { useMemo, useState, type JSX } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import {
  bulkScopeByFramework,
  bulkUnscopeByFramework,
  fetchFrameworkScopeSummary,
  previewFrameworkScopeChange,
  resetAllScope,
  type FrameworkScopePreview,
  type FrameworkScopeSummaryItem,
} from '../../data/apiClient'
import { useIsOrgEditor } from '../../hooks/useHasOrgRole'
import { useIsOrgAdmin } from '../../hooks/useIsOrgAdmin'
import { FrameworkLogo } from '../FrameworkLogo'
import { FRAMEWORK_GROUPS, OTHER_GROUP } from '../../data/frameworkGroups'

interface Props {
  organizationId: string
  onReviewControls?: (frameworkId?: string) => void
  onChanged?: () => void
}

const FAMILY_LABELS: Record<string, string> = {
  international: 'International Standards',
  us_federal: 'US Federal',
  us_state: 'US State Laws',
  emea: 'EMEA',
  apac: 'APAC',
  americas: 'Americas',
  industry: 'Industry Standards',
  other: 'Other',
}

/* Groups render in this order whatever order the API lists frameworks in —
   before, it was first-appearance, so the group holding the first selected
   framework led and "Other" landed mid-page. Unknown families go before Other. */
const FAMILY_ORDER = ['international', 'industry', 'us_federal', 'us_state', 'emea', 'apac', 'americas']

// The Dashboard's group icons, keyed by the same family ids.
const FAMILY_ICONS: Record<string, string> = Object.fromEntries(
  [...FRAMEWORK_GROUPS, OTHER_GROUP].map((group) => [group.id, group.emoji]),
)

type SelectionFilter = 'all' | 'selected' | 'available'

const SELECTION_FILTERS: { value: SelectionFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'selected', label: 'Selected' },
  { value: 'available', label: 'Available' },
]

function familyRank(family: string): number {
  const index = FAMILY_ORDER.indexOf(family)
  if (index >= 0) return index
  return family === 'other' ? FAMILY_ORDER.length + 1 : FAMILY_ORDER.length
}

function selectionStatus(framework: FrameworkScopeSummaryItem): { label: string; tone: string } {
  if (!framework.active) return { label: 'Not selected', tone: 'none' }
  return framework.partial
    ? { label: 'Partial', tone: 'partial' }
    : { label: 'Selected', tone: 'selected' }
}

export default function FrameworkScopingPage({
  organizationId,
  onReviewControls,
  onChanged,
}: Props): JSX.Element {
  const queryClient = useQueryClient()
  const canEdit = useIsOrgEditor(organizationId)
  const isAdmin = useIsOrgAdmin(organizationId)
  const [search, setSearch] = useState('')
  const [selectionFilter, setSelectionFilter] = useState<SelectionFilter>('all')
  // Families the user has opened or closed; null means "not touched yet".
  const [openFamilies, setOpenFamilies] = useState<Set<string> | null>(null)
  const [pending, setPending] = useState<{
    framework: FrameworkScopeSummaryItem
    operation: 'add' | 'remove'
  } | null>(null)
  const [preview, setPreview] = useState<FrameworkScopePreview | null>(null)
  const [reason, setReason] = useState('')
  // What happens to tracked evidence that no in-scope control will ask for once the
  // framework leaves. Default keeps the collector running; "untrack" stops it and closes
  // its open tasks. Files are never deleted either way. Asked at unscope time, per design.
  const [orphanAction, setOrphanAction] = useState<'keep' | 'untrack'>('keep')
  const [busy, setBusy] = useState(false)

  const summary = useQuery({
    queryKey: ['framework-scoping', organizationId],
    queryFn: () => fetchFrameworkScopeSummary(organizationId),
  })

  const selected = useMemo(
    () => (summary.data?.frameworks ?? []).filter((framework) => framework.active),
    [summary.data],
  )
  const needle = search.trim().toLowerCase()
  const groups = useMemo(() => {
    const byFamily = new Map<string, { all: number; selected: number; rows: FrameworkScopeSummaryItem[] }>()
    for (const framework of summary.data?.frameworks ?? []) {
      const group = byFamily.get(framework.family) ?? { all: 0, selected: 0, rows: [] }
      group.all += 1
      if (framework.active) group.selected += 1
      const matchesSearch =
        !needle ||
        framework.name.toLowerCase().includes(needle) ||
        framework.id.toLowerCase().includes(needle)
      const matchesFilter =
        selectionFilter === 'all' ||
        (selectionFilter === 'selected' ? framework.active : !framework.active)
      if (matchesSearch && matchesFilter) group.rows.push(framework)
      byFamily.set(framework.family, group)
    }
    return [...byFamily.entries()]
      .filter(([, group]) => group.rows.length > 0)
      .sort(([a], [b]) => familyRank(a) - familyRank(b) || a.localeCompare(b))
      .map(([family, group]) => ({ family, ...group }))
  }, [summary.data, needle, selectionFilter])

  // Until the user opens or closes one, a family is open when it holds a selected framework.
  const defaultOpenFamilies = useMemo(
    () => new Set(selected.map((framework) => framework.family)),
    [selected],
  )
  const openSet = openFamilies ?? defaultOpenFamilies

  const total = summary.data?.frameworks.length ?? 0
  const selectedTotal = selected.length
  const filterCounts: Record<SelectionFilter, number> = {
    all: total,
    selected: selectedTotal,
    available: total - selectedTotal,
  }
  // A search or a narrowing filter shows every match; collapsing would hide it.
  const narrowed = needle !== '' || selectionFilter !== 'all'
  const isOpen = (family: string) => narrowed || openSet.has(family)
  const allOpen = groups.length > 0 && groups.every((group) => isOpen(group.family))
  const toggleFamily = (family: string) =>
    setOpenFamilies(() => {
      const next = new Set(openSet)
      if (next.has(family)) next.delete(family)
      else next.add(family)
      return next
    })
  const toggleAllFamilies = () =>
    setOpenFamilies(allOpen ? new Set() : new Set(groups.map((group) => group.family)))

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['framework-scoping', organizationId] }),
      queryClient.invalidateQueries({ queryKey: ['scoped-controls', organizationId] }),
      queryClient.invalidateQueries({ queryKey: ['scoped-controls-stats', organizationId] }),
    ])
    onChanged?.()
  }

  const openPreview = async (
    framework: FrameworkScopeSummaryItem,
    operation: 'add' | 'remove',
  ) => {
    setPending({ framework, operation })
    setPreview(null)
    setReason('')
    try {
      setPreview(
        await previewFrameworkScopeChange(
          organizationId,
          operation,
          [framework.id],
        ),
      )
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not preview the change')
      setPending(null)
    }
  }

  const apply = async () => {
    if (!pending || !preview) return
    setBusy(true)
    try {
      if (pending.operation === 'add') {
        await bulkScopeByFramework(
          { frameworks: [pending.framework.id], selection_reason: reason || undefined },
          organizationId,
        )
      } else {
        await bulkUnscopeByFramework(
          {
            frameworks: [pending.framework.id],
            removal_reason: reason || undefined,
            orphan_evidence_action: orphanAction,
          },
          organizationId,
        )
      }
      toast.success(
        pending.operation === 'add'
          ? `${pending.framework.name} added to scope`
          : `${pending.framework.name} removed from scope`,
      )
      setPending(null)
      setPreview(null)
      setOrphanAction('keep')
      await refresh()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not update framework scope')
    } finally {
      setBusy(false)
    }
  }

  const resetScope = async () => {
    if (!isAdmin) return
    if (window.prompt('Type REMOVE ALL to clear frameworks and effective scope') !== 'REMOVE ALL') return
    setBusy(true)
    try {
      const result = await resetAllScope(organizationId)
      toast.success(result.message)
      await refresh()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not reset scope')
    } finally {
      setBusy(false)
    }
  }

  if (summary.isLoading) {
    return <div className="framework-scoping-page">Loading framework scope…</div>
  }
  if (summary.isError) {
    return <div className="framework-scoping-page">Framework scope could not be loaded.</div>
  }

  return (
    <div className="framework-scoping-page">
      <header className="framework-scoping-header">
        <div>
          <p>
            Selected frameworks establish your baseline. Individual control inclusions
            and exclusions remain explicit overrides.
          </p>
        </div>
        <span>{summary.data?.selected_count ?? 0} selected</span>
      </header>

      <section>
        <div className="framework-section-heading">
          <div>
            <h2>Frameworks in scope</h2>
            <p>Coverage reflects the same effective scope shown in Control Library.</p>
          </div>
        </div>
        {selected.length === 0 ? (
          <div className="framework-empty">No frameworks selected. Add one from the browser below.</div>
        ) : (
          <div className="framework-selected-grid">
            {selected.map((framework) => (
              <article key={framework.id} className="framework-selected-card">
                <FrameworkLogo frameworkName={framework.name} size={48} />
                <div className="framework-selected-card-main">
                  <div className="framework-selected-card-title">
                    <h3>{framework.name}</h3>
                    <span className={framework.partial ? 'status-warning' : 'status-success'}>
                      {framework.partial ? 'Partial' : 'Covered'}
                    </span>
                  </div>
                  <div className="framework-coverage-meter">
                    <div style={{ width: `${framework.coverage_percentage}%` }} />
                  </div>
                  <strong>{framework.coverage_percentage}% coverage</strong>
                  <span>
                    {framework.in_scope_count}/{framework.mapped_control_count} controls in scope ·{' '}
                    {framework.missing_count} missing or excluded
                  </span>
                  <small>
                    Selected {framework.selected_at ? new Date(framework.selected_at).toLocaleDateString() : '—'}
                    {' · '}{framework.source ?? 'unknown source'}
                    {framework.selected_by ? ` · actor ${framework.selected_by.slice(0, 8)}` : ''}
                  </small>
                  <div className="framework-card-actions">
                    <button type="button" className="btn-secondary btn-small" onClick={() => onReviewControls?.(framework.id)}>
                      Review controls
                    </button>
                    {canEdit && (
                      <button type="button" className="btn-danger btn-small" onClick={() => void openPreview(framework, 'remove')}>
                        Remove framework
                      </button>
                    )}
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="framework-browser">
        <div className="framework-section-heading">
          <div>
            <h2>Browse frameworks</h2>
            <p>Internal SCF risk, threat, summary, and errata mappings are excluded.</p>
          </div>
        </div>
        <div className="framework-browser-toolbar">
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape' && search) setSearch('')
            }}
            placeholder="Search frameworks…"
            aria-label="Search frameworks"
          />
          <div className="framework-browser-filter" role="group" aria-label="Show frameworks">
            {SELECTION_FILTERS.map((option) => (
              <button
                key={option.value}
                type="button"
                aria-pressed={selectionFilter === option.value}
                className={selectionFilter === option.value ? 'is-active' : undefined}
                onClick={() => setSelectionFilter(option.value)}
              >
                {option.label}
                <span className="framework-browser-filter-count">{filterCounts[option.value]}</span>
              </button>
            ))}
          </div>
          <ul className="framework-coverage-legend" aria-label="Bar colours">
            {STATUS_SEGMENTS.map((segment) => (
              <li key={segment.key}>
                <i className={`framework-coverage-segment--${segment.key}`} />
                {segment.label}
              </li>
            ))}
            <li>
              <i className="framework-coverage-legend-gap" />
              Not in scope
            </li>
          </ul>
          {!narrowed && groups.length > 1 && (
            <button type="button" className="framework-browser-expand-all" onClick={toggleAllFamilies}>
              {allOpen ? 'Collapse all' : 'Expand all'}
            </button>
          )}
        </div>
        {groups.length === 0 ? (
          <div className="framework-empty">
            {needle ? `No framework matches “${search.trim()}”.` : 'No frameworks to show.'}
          </div>
        ) : (
          groups.map((group) => {
            const open = isOpen(group.family)
            const panelId = `framework-group-${group.family}`
            return (
              <div key={group.family} className={`framework-browser-group${open ? ' is-open' : ''}`}>
                <h3>
                  <button
                    type="button"
                    className="framework-browser-group-toggle"
                    aria-expanded={open}
                    aria-controls={panelId}
                    disabled={narrowed}
                    onClick={() => toggleFamily(group.family)}
                  >
                    <svg className="framework-browser-chevron" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                      <path d="M4 2.5 7.5 6 4 9.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                    <span className="framework-browser-group-icon" aria-hidden="true">
                      {FAMILY_ICONS[group.family] ?? OTHER_GROUP.emoji}
                    </span>
                    <span className="framework-browser-group-label">{FAMILY_LABELS[group.family] ?? group.family}</span>
                    <span className="framework-browser-group-count">
                      {narrowed && group.rows.length !== group.all ? `${group.rows.length} of ${group.all}` : group.all}
                    </span>
                    {group.selected > 0 && (
                      <span className="framework-browser-group-selected">{group.selected} selected</span>
                    )}
                  </button>
                </h3>
                {open && (
                  <div id={panelId} className="framework-browser-table" role="table" aria-label={FAMILY_LABELS[group.family] ?? group.family}>
                    <div className="framework-browser-row framework-browser-row--header" role="row">
                      <span role="columnheader">Framework</span>
                      <span role="columnheader">Controls in scope</span>
                      <span role="columnheader" className="framework-browser-num">Expected additions</span>
                      <span role="columnheader">Selection</span>
                      <span role="columnheader" className="framework-browser-action">Action</span>
                    </div>
                    {group.rows.map((framework) => {
                      const status = selectionStatus(framework)
                      return (
                        <div
                          key={framework.id}
                          className={`framework-browser-row${framework.active ? ' is-selected' : ''}`}
                          role="row"
                        >
                          <span role="cell" className="framework-browser-name" title={framework.id}>
                            {framework.name}
                          </span>
                          <span role="cell">
                            <CoverageBar framework={framework} />
                          </span>
                          <span role="cell" className="framework-browser-num">{framework.expected_additions}</span>
                          <span role="cell">
                            <span className={`framework-status-pill framework-status-pill--${status.tone}`}>{status.label}</span>
                          </span>
                          <span role="cell" className="framework-browser-action">
                            {framework.active ? (
                              <button type="button" className="btn-secondary btn-small" onClick={() => onReviewControls?.(framework.id)}>
                                Review
                              </button>
                            ) : canEdit ? (
                              <button type="button" className="btn-primary btn-small" onClick={() => void openPreview(framework, 'add')}>
                                Add
                              </button>
                            ) : (
                              <span className="framework-browser-view-only">View only</span>
                            )}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })
        )}
      </section>

      {isAdmin && (
        <section className="framework-danger-zone">
          <h2>Danger zone</h2>
          <p>Reset effective control scope and deactivate every selected framework. Implementation metadata and history are retained.</p>
          <button type="button" className="btn-danger" disabled={busy} onClick={() => void resetScope()}>
            Reset scope
          </button>
        </section>
      )}

      {pending && (
        <aside className="framework-preview-panel" aria-label="Framework change preview">
          <div className="framework-preview-header">
            <div>
              <span>Exact change preview</span>
              <h2>{pending.operation === 'add' ? 'Add' : 'Remove'} {pending.framework.name}</h2>
            </div>
            <button type="button" aria-label="Close preview" onClick={() => setPending(null)}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
          {!preview ? (
            <p>Calculating exact effects…</p>
          ) : (
            <>
              {previewRows(pending.operation, preview).map((row) => (
                <PreviewGroup key={row.title} title={row.title} ids={row.ids} />
              ))}
              {pending.operation === 'remove' && (preview.orphaned_evidence?.length ?? 0) > 0 && (
                <fieldset className="framework-preview-orphans">
                  <legend>Tracked evidence no longer required</legend>
                  <label>
                    <input
                      type="radio"
                      name="orphan-evidence-action"
                      value="keep"
                      checked={orphanAction === 'keep'}
                      onChange={() => setOrphanAction('keep')}
                    />
                    Keep tracking — collectors and open tasks carry on
                  </label>
                  <label>
                    <input
                      type="radio"
                      name="orphan-evidence-action"
                      value="untrack"
                      checked={orphanAction === 'untrack'}
                      onChange={() => setOrphanAction('untrack')}
                    />
                    Stop tracking — close its open tasks as won't do; files are kept
                  </label>
                </fieldset>
              )}
              <label>
                Change rationale
                <textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={3} />
              </label>
              <div className="framework-preview-actions">
                <button type="button" className="btn-secondary" onClick={() => setPending(null)}>Cancel</button>
                <button
                  type="button"
                  className={pending.operation === 'remove' ? 'btn-danger' : 'btn-primary'}
                  disabled={busy}
                  onClick={() => void apply()}
                >
                  {busy ? 'Applying…' : 'Confirm exact change'}
                </button>
              </div>
            </>
          )}
        </aside>
      )}
    </div>
  )
}

/* One bar per framework: the coloured part is its controls already in scope,
   split by implementation status (the Dashboard's four buckets); the empty
   track is the gap. For an unselected framework the gap is roughly what adding
   it brings in (explicit exclusions stay out, hence the separate number). */
const STATUS_SEGMENTS = [
  { key: 'implemented', label: 'Implemented' },
  { key: 'in_progress', label: 'In progress' },
  { key: 'at_risk', label: 'At risk' },
  { key: 'not_started', label: 'Not started' },
] as const

function CoverageBar({ framework }: { framework: FrameworkScopeSummaryItem }): JSX.Element {
  const mapped = framework.mapped_control_count
  const inScope = framework.in_scope_count
  const gap = mapped - inScope
  // Older backends send no breakdown: show the in-scope part as not started.
  const counts = framework.status_counts ?? {
    implemented: 0,
    in_progress: 0,
    at_risk: 0,
    not_started: inScope,
  }
  const parts = STATUS_SEGMENTS.filter((segment) => counts[segment.key] > 0).map(
    (segment) => `${counts[segment.key]} ${segment.label.toLowerCase()}`,
  )
  const label = [
    `${inScope} of ${mapped} mapped controls in scope`,
    ...(parts.length ? [parts.join(', ')] : []),
    ...(gap ? [`${gap} not in scope`] : []),
  ].join(' · ')
  return (
    <span className="framework-coverage-cell" title={label}>
      <span
        className="framework-coverage-bar"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={mapped}
        aria-valuenow={inScope}
      >
        {STATUS_SEGMENTS.map((segment) =>
          counts[segment.key] > 0 && mapped > 0 ? (
            <span
              key={segment.key}
              className={`framework-coverage-segment framework-coverage-segment--${segment.key}`}
              style={{ width: `${(counts[segment.key] / mapped) * 100}%` }}
            />
          ) : null,
        )}
      </span>
      <span className="framework-coverage-figures">
        {inScope}/{mapped}
      </span>
    </span>
  )
}

/* The preview buckets a control into exactly one list, and which lists an operation can
   populate depends on the operation: an add never produces `controls_leaving_scope`, a
   remove never produces `new_controls`. Rendering all six regardless printed rows that
   were structurally incapable of being non-zero.
   `already_covered` also carries a different predicate per operation — on an add it is
   "in scope, but no other active framework maps it", on a remove it is "was not in scope
   anyway" — so it gets a different label rather than one that reads true only half the
   time. `headline` rows stay visible at zero because zero is the answer; the rest appear
   only when they have something to say. */
type PreviewRow = { title: string; ids: string[] }

function previewRows(
  operation: 'add' | 'remove',
  preview: FrameworkScopePreview,
): PreviewRow[] {
  const candidates: Array<PreviewRow & { headline?: boolean }> =
    operation === 'add'
      ? [
          { title: 'New controls entering scope', ids: preview.new_controls, headline: true },
          {
            title: 'Already in scope via another active framework',
            ids: preview.shared_with_active_frameworks,
            headline: true,
          },
          { title: 'Already in scope — individually included', ids: preview.individual_inclusions },
          { title: 'Already in scope — no other active framework maps it', ids: preview.already_covered },
          { title: 'Blocked by an explicit exclusion', ids: preview.explicitly_excluded },
        ]
      : [
          { title: 'Controls leaving scope', ids: preview.controls_leaving_scope, headline: true },
          {
            title: 'Retained — still mapped by an active framework',
            ids: preview.shared_with_active_frameworks,
            headline: true,
          },
          { title: 'Retained — individually included', ids: preview.individual_inclusions },
          { title: 'Not in scope anyway', ids: preview.already_covered },
          { title: 'Blocked by an explicit exclusion', ids: preview.explicitly_excluded },
          {
            title:
              'Tracked evidence no longer required by any in-scope control' +
              (preview.open_tasks_affected
                ? ` (${preview.open_tasks_affected} open task${preview.open_tasks_affected === 1 ? '' : 's'})`
                : ''),
            ids: preview.orphaned_evidence ?? [],
          },
        ]

  return candidates.filter((row) => row.headline || row.ids.length > 0)
}

/* A framework change routinely touches hundreds of controls — adding SOC 2 to this scope
   lists 176 new and 236 already covered. Printing every id expanded a single row to ~700px
   and pushed the confirm button below the fold, so open only short lists by default and
   cap what is printed; the count in the summary is always the authoritative figure. */
const PREVIEW_AUTO_OPEN_MAX = 12
const PREVIEW_ID_LIMIT = 40

function PreviewGroup({ title, ids }: { title: string; ids: string[] }): JSX.Element {
  const shown = ids.slice(0, PREVIEW_ID_LIMIT)
  const remaining = ids.length - shown.length
  return (
    <details
      className="framework-preview-group"
      open={ids.length > 0 && ids.length <= PREVIEW_AUTO_OPEN_MAX}
    >
      <summary><span>{title}</span><strong>{ids.length}</strong></summary>
      {ids.length > 0 ? (
        <p>
          {shown.join(', ')}
          {remaining > 0 ? ` … and ${remaining} more` : ''}
        </p>
      ) : (
        <p>None</p>
      )}
    </details>
  )
}
