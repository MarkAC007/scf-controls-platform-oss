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

export default function FrameworkScopingPage({
  organizationId,
  onReviewControls,
  onChanged,
}: Props): JSX.Element {
  const queryClient = useQueryClient()
  const canEdit = useIsOrgEditor(organizationId)
  const isAdmin = useIsOrgAdmin(organizationId)
  const [search, setSearch] = useState('')
  const [pending, setPending] = useState<{
    framework: FrameworkScopeSummaryItem
    operation: 'add' | 'remove'
  } | null>(null)
  const [preview, setPreview] = useState<FrameworkScopePreview | null>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)

  const summary = useQuery({
    queryKey: ['framework-scoping', organizationId],
    queryFn: () => fetchFrameworkScopeSummary(organizationId),
  })

  const selected = useMemo(
    () => (summary.data?.frameworks ?? []).filter((framework) => framework.active),
    [summary.data],
  )
  const grouped = useMemo(() => {
    const needle = search.trim().toLowerCase()
    const rows = (summary.data?.frameworks ?? []).filter(
      (framework) =>
        !needle ||
        framework.name.toLowerCase().includes(needle) ||
        framework.id.toLowerCase().includes(needle),
    )
    return rows.reduce<Record<string, FrameworkScopeSummaryItem[]>>((result, framework) => {
      ;(result[framework.family] ??= []).push(framework)
      return result
    }, {})
  }, [summary.data, search])

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
          { frameworks: [pending.framework.id], removal_reason: reason || undefined },
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
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search frameworks…"
            aria-label="Search frameworks"
          />
        </div>
        {Object.entries(grouped).map(([family, frameworks]) => (
          <div key={family} className="framework-browser-group">
            <h3>{FAMILY_LABELS[family] ?? family}</h3>
            <div className="framework-browser-table" role="table">
              <div className="framework-browser-row framework-browser-row--header" role="row">
                <span>Framework</span>
                <span>Mapped controls</span>
                <span>Selection</span>
                <span>Expected additions</span>
                <span>Action</span>
              </div>
              {frameworks.map((framework) => (
                <div key={framework.id} className="framework-browser-row" role="row">
                  <span><strong>{framework.name}</strong><small>{framework.id}</small></span>
                  <span>{framework.mapped_control_count}</span>
                  <span>{framework.active ? (framework.partial ? 'Partial' : 'Selected') : 'Not selected'}</span>
                  <span>{framework.expected_additions}</span>
                  <span>
                    {framework.active ? (
                      <button type="button" className="btn-secondary btn-small" onClick={() => onReviewControls?.(framework.id)}>
                        Review
                      </button>
                    ) : canEdit ? (
                      <button type="button" className="btn-primary btn-small" onClick={() => void openPreview(framework, 'add')}>
                        Add
                      </button>
                    ) : (
                      <span>View only</span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ))}
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
