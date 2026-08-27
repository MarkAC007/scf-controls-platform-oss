import { useState, useEffect, useMemo } from 'react'
import ListToolbar from './explorer/ListToolbar'
import {
  listEngagements,
  createEngagement,
  deleteEngagement,
  getEngagementScope,
  getEngagementPresentation,
  listEngagementAuditors,
  grantEngagementAuditor,
  revokeEngagementAuditor,
  listEngagementQueries,
  createEngagementQuery,
  getEngagementQuery,
  respondToEngagementQuery,
  updateEngagementQueryStatus,
  type AuditEngagement,
  type AuditEngagementCreate,
  type EngagementScopeItem,
  type EngagementScopeStatus,
  type FrameworkPresentation,
  type EngagementAuditor,
  type EngagementQuery,
  type EngagementQueryStatus,
} from '../data/apiClient'
import { fetchFrameworks, type FrameworkInfo } from '../data/catalogApi'
import DeprecatedBadge, { getCatalogLifecycle } from './DeprecatedBadge'

interface EngagementsPageProps {
  organizationId: string
}

type FrameworkNamer = (id: string) => string

const STATUS_LABELS: Record<string, string> = {
  draft: 'Draft',
  active: 'Active',
  under_review: 'Under Review',
  closed: 'Closed',
}

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  draft:        { bg: 'var(--secondary)', color: 'var(--muted)' },
  active:       { bg: 'var(--success-bg)', color: 'var(--success)' },
  under_review: { bg: 'var(--warning-bg)', color: 'var(--warning)' },
  closed:       { bg: 'var(--error-bg)', color: 'var(--text)' },
}

// Scope status presentation. Order drives the grouped display: in-scope first,
// then the exclusions an auditor needs to review, then mapped-but-untracked gaps.
const SCOPE_STATUS_META: Record<EngagementScopeStatus, { label: string; bg: string; color: string }> = {
  in_scope:    { label: 'In scope',    bg: 'var(--success-bg)', color: 'var(--success)' },
  excluded:    { label: 'Excluded',    bg: 'var(--warning-bg)', color: 'var(--warning)' },
  not_tracked: { label: 'Not tracked', bg: 'var(--secondary)', color: 'var(--muted)' },
}
const SCOPE_STATUS_ORDER: EngagementScopeStatus[] = ['in_scope', 'excluded', 'not_tracked']

const AUDITOR_STATUS_META: Record<string, { label: string; bg: string; color: string }> = {
  active:   { label: 'Active',   bg: 'var(--success-bg)', color: 'var(--success)' },
  invited:  { label: 'Invited',  bg: 'var(--warning-bg)', color: 'var(--warning)' },
  revoked:  { label: 'Revoked',  bg: 'var(--secondary)', color: 'var(--muted)' },
}

const QUERY_STATUS_META: Record<EngagementQueryStatus, { label: string; bg: string; color: string }> = {
  open:     { label: 'Open',     bg: 'var(--warning-bg)', color: 'var(--warning)' },
  answered: { label: 'Answered', bg: 'var(--accent-muted)', color: 'var(--primary)' },
  closed:   { label: 'Closed',   bg: 'var(--success-bg)', color: 'var(--success)' },
}

// ---------------------------------------------------------------------------
// Shared building blocks
// ---------------------------------------------------------------------------

const inputStyle: React.CSSProperties = {
  padding: '9px 12px', border: '1px solid var(--border)', borderRadius: 8,
  fontSize: 14, outline: 'none', width: '100%', boxSizing: 'border-box',
  background: 'var(--panel)', color: 'var(--text)',
}

/** A quiet, consistent contextual-help block. */
function HelpNote({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      display: 'flex', gap: 10, alignItems: 'flex-start',
      padding: '10px 14px', borderRadius: 8,
      background: 'var(--accent-muted)', color: 'var(--text)', fontSize: 12.5, lineHeight: 1.5,
    }}>
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--primary)" strokeWidth="2"
           style={{ flexShrink: 0, marginTop: 1 }}>
        <circle cx="12" cy="12" r="10" />
        <line x1="12" y1="16" x2="12" y2="12" />
        <line x1="12" y1="8" x2="12.01" y2="8" />
      </svg>
      <div>{children}</div>
    </div>
  )
}

/** Right-anchored slide-over shell shared by every engagement drawer. */
function Drawer({ width = 560, onClose, children }: { width?: number; onClose: () => void; children: React.ReactNode }) {
  return (
    <div
      style={{ position: 'fixed', inset: 0, zIndex: 50, display: 'flex', justifyContent: 'flex-end', background: 'rgba(0,0,0,0.35)' }}
      onClick={onClose}
    >
      <div
        style={{
          width, maxWidth: '100vw', height: '100%',
          background: 'var(--card)', boxShadow: '-4px 0 24px rgba(0,0,0,0.25)',
          display: 'flex', flexDirection: 'column', overflowY: 'auto',
        }}
        onClick={e => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  )
}

function DrawerHeader({ title, subtitle, onClose }: { title: string; subtitle?: React.ReactNode; onClose: () => void }) {
  return (
    <div style={{
      padding: '20px 24px', borderBottom: '1px solid var(--border)',
      display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12,
      position: 'sticky', top: 0, background: 'var(--card)', zIndex: 1,
    }}>
      <div style={{ minWidth: 0 }}>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600, color: 'var(--text)' }}>{title}</h2>
        {subtitle && <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--muted)', lineHeight: 1.5 }}>{subtitle}</p>}
      </div>
      <button onClick={onClose} aria-label="Close"
        style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 22, lineHeight: 1, color: 'var(--muted)' }}>&times;</button>
    </div>
  )
}

const fmtDate = (d: string | null | undefined) => (d ? new Date(d).toLocaleDateString() : '—')

// ---------------------------------------------------------------------------
// Create Engagement Drawer
// ---------------------------------------------------------------------------

interface CreateDrawerProps {
  organizationId: string
  frameworks: FrameworkInfo[]
  onClose: () => void
  onCreated: () => void
}

function CreateEngagementDrawer({ organizationId, frameworks, onClose, onCreated }: CreateDrawerProps) {
  const [name, setName] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [search, setSearch] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const nameById = useMemo(
    () => Object.fromEntries(frameworks.map(f => [f.id, f.name])),
    [frameworks]
  )

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    const list = q
      ? frameworks.filter(f => f.name.toLowerCase().includes(q) || f.id.toLowerCase().includes(q))
      : frameworks
    return [...list].sort((a, b) => a.name.localeCompare(b.name))
  }, [frameworks, search])

  const toggle = (id: string) =>
    setSelected(prev => (prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]))

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) { setError('Give the engagement a name.'); return }
    if (selected.length === 0) { setError('Select at least one framework to scope.'); return }
    if (startDate && endDate && endDate < startDate) { setError("The end date can't be before the start date."); return }

    setSubmitting(true)
    setError(null)
    try {
      const payload: AuditEngagementCreate = {
        name: name.trim(),
        frameworks: selected,
        start_date: startDate || null,
        end_date: endDate || null,
      }
      await createEngagement(organizationId, payload)
      onCreated()
    } catch (err: any) {
      setError(err?.message ?? 'Could not create the engagement.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Drawer width={520} onClose={onClose}>
      <DrawerHeader
        title="New engagement"
        subtitle="Scope an audit to one or more frameworks. We pull in the SCF controls that map to them, so you can present and evidence the audit from the framework's perspective."
        onClose={onClose}
      />

      <form onSubmit={handleSubmit} style={{ padding: 24, flex: 1, display: 'flex', flexDirection: 'column', gap: 24 }}>
        {error && (
          <div role="alert" style={{ padding: '10px 14px', background: 'var(--error-bg)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text)', fontSize: 13 }}>
            {error}
          </div>
        )}

        {/* Name */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <label htmlFor="eng-name" style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
            Engagement name <span style={{ color: 'var(--error, #ef4444)' }}>*</span>
          </label>
          <input
            id="eng-name" type="text" value={name} onChange={e => setName(e.target.value)}
            placeholder="e.g. ISO 27001:2022 Certification — FY26"
            style={inputStyle} autoFocus
          />
        </div>

        {/* Frameworks */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 }}>
            <label style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
              Frameworks <span style={{ color: 'var(--error, #ef4444)' }}>*</span>
            </label>
            <span style={{ fontSize: 12, color: 'var(--muted)' }}>
              {selected.length > 0 ? `${selected.length} selected` : `${frameworks.length} available`}
            </span>
          </div>

          {/* Selected chips */}
          {selected.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {selected.map(id => (
                <span key={id} style={{
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                  padding: '3px 6px 3px 10px', borderRadius: 16,
                  background: 'var(--accent-muted)', color: 'var(--primary)', fontSize: 12, fontWeight: 500,
                }}>
                  {nameById[id] ?? id}
                  <button type="button" onClick={() => toggle(id)} aria-label={`Remove ${nameById[id] ?? id}`}
                    style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--primary)', fontSize: 15, lineHeight: 1, padding: 0 }}>&times;</button>
                </span>
              ))}
            </div>
          )}

          {/* Search */}
          <input
            type="search" value={search} onChange={e => setSearch(e.target.value)}
            placeholder="Search frameworks (e.g. ISO, SOC 2, NIST)…"
            style={{ ...inputStyle, fontSize: 13 }}
          />

          {/* Selectable list */}
          <div role="listbox" aria-label="Frameworks" style={{
            maxHeight: 260, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 8,
            background: 'var(--panel)',
          }}>
            {frameworks.length === 0 ? (
              <div style={{ padding: 14, color: 'var(--muted)', fontSize: 13 }}>Loading frameworks…</div>
            ) : filtered.length === 0 ? (
              <div style={{ padding: 14, color: 'var(--muted)', fontSize: 13 }}>No frameworks match “{search}”.</div>
            ) : (
              filtered.map(fw => {
                const isSel = selected.includes(fw.id)
                return (
                  <label key={fw.id} style={{
                    display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px',
                    cursor: 'pointer', borderBottom: '1px solid var(--border)',
                    background: isSel ? 'var(--accent-muted)' : 'transparent',
                  }}>
                    <input type="checkbox" checked={isSel} onChange={() => toggle(fw.id)} style={{ cursor: 'pointer' }} />
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <span style={{ display: 'block', fontSize: 13, color: 'var(--text)', fontWeight: isSel ? 600 : 400 }}>{fw.name}</span>
                      <span style={{ display: 'block', fontSize: 11, color: 'var(--muted)', fontFamily: 'var(--font-mono, monospace)' }}>{fw.id}</span>
                    </span>
                    <span style={{ fontSize: 11, color: 'var(--muted)', whiteSpace: 'nowrap' }}>{fw.control_count} controls</span>
                  </label>
                )
              })
            )}
          </div>
        </div>

        {/* Audit period */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <label style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>Audit period</label>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <span style={{ fontSize: 12, color: 'var(--muted)' }}>Start</span>
              <input type="date" value={startDate} max={endDate || undefined} onChange={e => setStartDate(e.target.value)} style={inputStyle} />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <span style={{ fontSize: 12, color: 'var(--muted)' }}>End</span>
              <input type="date" value={endDate} min={startDate || undefined} onChange={e => setEndDate(e.target.value)} style={inputStyle} />
            </div>
          </div>
          <HelpNote>
            Evidence is matched to this window. For a SOC&nbsp;2 Type&nbsp;2 review use the full observation period;
            for ISO&nbsp;27001 use the surveillance year. You can leave these blank and set them later.
          </HelpNote>
        </div>

        <div style={{ marginTop: 'auto', display: 'flex', gap: 10, paddingTop: 8 }}>
          <button type="submit" disabled={submitting} style={{
            flex: 1, padding: '11px 0', borderRadius: 8, border: 'none',
            background: submitting ? 'var(--muted-bg)' : 'var(--primary)', color: 'var(--primary-foreground)',
            fontSize: 14, fontWeight: 600, cursor: submitting ? 'not-allowed' : 'pointer',
          }}>
            {submitting ? 'Creating…' : 'Create engagement'}
          </button>
          <button type="button" onClick={onClose} style={{
            padding: '11px 20px', borderRadius: 8, border: '1px solid var(--border)',
            background: 'var(--secondary)', color: 'var(--text)', fontSize: 14, cursor: 'pointer',
          }}>Cancel</button>
        </div>
      </form>
    </Drawer>
  )
}

// ---------------------------------------------------------------------------
// Scope Drawer — the tagged mapped-control comparison for an engagement
// ---------------------------------------------------------------------------

interface ScopeDrawerProps {
  organizationId: string
  engagement: AuditEngagement
  frameworkName: FrameworkNamer
  onClose: () => void
}

function ScopeDrawer({ organizationId, engagement, frameworkName, onClose }: ScopeDrawerProps) {
  const [items, setItems] = useState<EngagementScopeItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getEngagementScope(organizationId, engagement.id)
      .then(data => { if (!cancelled) setItems(data) })
      .catch(err => { if (!cancelled) setError(err?.message ?? 'Could not load the scope.') })
    return () => { cancelled = true }
  }, [organizationId, engagement.id])

  const counts: Record<EngagementScopeStatus, number> = { in_scope: 0, excluded: 0, not_tracked: 0 }
  for (const it of items ?? []) counts[it.scope_status] = (counts[it.scope_status] ?? 0) + 1

  return (
    <Drawer width={620} onClose={onClose}>
      <DrawerHeader
        title="Scope comparison"
        subtitle={<>{engagement.name} — {engagement.frameworks.map(frameworkName).join(', ')}</>}
        onClose={onClose}
      />

      <div style={{ padding: '16px 24px 0' }}>
        <HelpNote>
          Every control mapped to the engagement's frameworks, tagged by how your organisation scoped it.
          <strong> Excluded</strong> controls carry the justification an auditor will review;
          <strong> not tracked</strong> means the control isn't in your control set yet.
        </HelpNote>
      </div>

      {items && items.length > 0 && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', padding: '14px 24px', borderBottom: '1px solid var(--border)' }}>
          {SCOPE_STATUS_ORDER.map(status => {
            const meta = SCOPE_STATUS_META[status]
            return (
              <span key={status} style={{ padding: '4px 12px', borderRadius: 20, fontSize: 12, fontWeight: 600, background: meta.bg, color: meta.color }}>
                {counts[status]} {meta.label.toLowerCase()}
              </span>
            )
          })}
        </div>
      )}

      <div style={{ padding: 24, flex: 1 }}>
        {error ? (
          <div style={{ padding: '10px 14px', background: 'var(--error-bg)', borderRadius: 8, color: 'var(--text)', fontSize: 13 }}>{error}</div>
        ) : items === null ? (
          <div style={{ color: 'var(--muted)', fontSize: 14 }}>Loading scope…</div>
        ) : items.length === 0 ? (
          <div style={{ color: 'var(--muted)', fontSize: 14 }}>No controls map to the engagement's frameworks yet.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
            {SCOPE_STATUS_ORDER.filter(s => counts[s] > 0).map(status => {
              const meta = SCOPE_STATUS_META[status]
              const group = items.filter(it => it.scope_status === status)
              return (
                <div key={status}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                    <span style={{ padding: '2px 10px', borderRadius: 10, fontSize: 12, fontWeight: 600, background: meta.bg, color: meta.color }}>{meta.label}</span>
                    <span style={{ fontSize: 12, color: 'var(--muted)' }}>{group.length}</span>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {group.map(it => (
                      <div key={it.id} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '10px 14px', background: 'var(--panel)' }}>
                        <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
                          <strong style={{ fontSize: 13, color: 'var(--text)' }}>{it.scf_id}</strong>
                          <DeprecatedBadge compact {...getCatalogLifecycle(it)} />
                          {it.control_name && <span style={{ fontSize: 13, color: 'var(--muted)' }}>{it.control_name}</span>}
                        </div>
                        {status === 'excluded' && (
                          <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text)', borderLeft: '2px solid var(--warning)', paddingLeft: 10 }}>
                            {it.out_of_scope_justification
                              ? it.out_of_scope_justification
                              : <em style={{ color: 'var(--muted)' }}>No exclusion justification recorded.</em>}
                          </div>
                        )}
                        {status === 'not_tracked' && (
                          <div style={{ marginTop: 6, fontSize: 12, color: 'var(--muted)' }}>
                            Mapped to the framework but not present in your control set.
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </Drawer>
  )
}

// ---------------------------------------------------------------------------
// Presentation Drawer — controls re-sequenced from a framework's perspective
// ---------------------------------------------------------------------------

interface PresentationDrawerProps {
  organizationId: string
  engagement: AuditEngagement
  frameworkName: FrameworkNamer
  onClose: () => void
}

function PresentationDrawer({ organizationId, engagement, frameworkName, onClose }: PresentationDrawerProps) {
  const [framework, setFramework] = useState<string>(engagement.frameworks[0] ?? '')
  const [data, setData] = useState<FrameworkPresentation | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!framework) return
    let cancelled = false
    setLoading(true)
    setError(null)
    getEngagementPresentation(organizationId, engagement.id, framework)
      .then(d => { if (!cancelled) setData(d) })
      .catch(err => { if (!cancelled) setError(err?.message ?? 'Could not load the presentation.') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [organizationId, engagement.id, framework])

  const controlCount = (data?.clauses ?? []).reduce((n, c) => n + c.controls.length, 0)

  return (
    <Drawer width={720} onClose={onClose}>
      <DrawerHeader
        title="Framework presentation"
        subtitle={<>{engagement.name} — controls sequenced under {frameworkName(framework)}'s own clauses.
          {(data?.start_date || data?.end_date) && <> Evidence window {fmtDate(data?.start_date)} → {fmtDate(data?.end_date)}.</>}</>}
        onClose={onClose}
      />

      {/* Framework selector */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', padding: '14px 24px', borderBottom: '1px solid var(--border)' }}>
        {engagement.frameworks.map(fw => (
          <button key={fw} onClick={() => setFramework(fw)} style={{
            padding: '5px 14px', borderRadius: 20, fontSize: 12, fontWeight: 600, cursor: 'pointer',
            border: '1px solid var(--border)',
            background: fw === framework ? 'var(--primary)' : 'var(--card)',
            color: fw === framework ? 'var(--primary-foreground)' : 'var(--text)',
          }}>{frameworkName(fw)}</button>
        ))}
      </div>

      <div style={{ padding: 24, flex: 1 }}>
        {error ? (
          <div style={{ padding: '10px 14px', background: 'var(--error-bg)', borderRadius: 8, color: 'var(--text)', fontSize: 13 }}>{error}</div>
        ) : loading || data === null ? (
          <div style={{ color: 'var(--muted)', fontSize: 14 }}>Loading presentation…</div>
        ) : controlCount === 0 ? (
          <div style={{ color: 'var(--muted)', fontSize: 14 }}>No controls map to {frameworkName(framework)} in this engagement's scope.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            <HelpNote>
              Controls are grouped under each framework clause they satisfy. Evidence chips are
              <span style={{ color: 'var(--success)' }}> green</span> when uploaded inside the audit window and muted when outside it.
            </HelpNote>
            {data.clauses.map(clause => (
              <div key={clause.clause_id}>
                <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', marginBottom: 8, paddingBottom: 4, borderBottom: '1px solid var(--border)' }}>
                  {frameworkName(framework)} · {clause.clause_id}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {clause.controls.map(ctrl => {
                    const meta = SCOPE_STATUS_META[ctrl.scope_status]
                    return (
                      <div key={`${clause.clause_id}-${ctrl.scf_id}`} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '10px 14px', background: 'var(--panel)' }}>
                        <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                          <strong style={{ fontSize: 13, color: 'var(--text)' }}>{ctrl.scf_id}</strong>
                          <DeprecatedBadge compact {...getCatalogLifecycle(ctrl)} />
                          {ctrl.control_name && <span style={{ fontSize: 13, color: 'var(--muted)', flex: 1, minWidth: 0 }}>{ctrl.control_name}</span>}
                          <span style={{ padding: '1px 8px', borderRadius: 10, fontSize: 11, fontWeight: 600, background: meta.bg, color: meta.color }}>{meta.label}</span>
                        </div>

                        <div style={{ display: 'flex', gap: 16, marginTop: 6, fontSize: 12, color: 'var(--muted)', flexWrap: 'wrap' }}>
                          {ctrl.implementation_status && <span>Status: {ctrl.implementation_status}</span>}
                          {ctrl.maturity_level && <span>Maturity: {ctrl.maturity_level}</span>}
                          {ctrl.owner && <span>Owner: {ctrl.owner}</span>}
                        </div>

                        {ctrl.scope_status === 'excluded' && (
                          <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text)', borderLeft: '2px solid var(--warning)', paddingLeft: 10 }}>
                            {ctrl.out_of_scope_justification || <em style={{ color: 'var(--muted)' }}>No exclusion justification recorded.</em>}
                          </div>
                        )}

                        {ctrl.evidence.length > 0 ? (
                          <div style={{ marginTop: 8 }}>
                            <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>
                              Evidence — {ctrl.evidence_in_window_count} of {ctrl.evidence.length} in window
                            </div>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                              {ctrl.evidence.map(ev => (
                                <span key={ev.id ?? ev.filename} title={ev.in_window ? 'Uploaded within the audit window' : 'Uploaded outside the audit window'} style={{
                                  display: 'inline-flex', alignItems: 'center', gap: 6, padding: '2px 8px', borderRadius: 6, fontSize: 11,
                                  border: '1px solid var(--border)',
                                  background: ev.in_window ? 'var(--success-bg)' : 'var(--secondary)',
                                  color: ev.in_window ? 'var(--success)' : 'var(--muted)',
                                }}>
                                  <span style={{ width: 6, height: 6, borderRadius: '50%', background: ev.in_window ? 'var(--success)' : 'var(--muted)' }} />
                                  {ev.filename}
                                </span>
                              ))}
                            </div>
                          </div>
                        ) : (
                          <div style={{ marginTop: 8, fontSize: 11, color: 'var(--muted)' }}>No evidence collected.</div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </Drawer>
  )
}

// ---------------------------------------------------------------------------
// Auditors Drawer — manage external auditor access to an engagement
// ---------------------------------------------------------------------------

interface AuditorsDrawerProps {
  organizationId: string
  engagement: AuditEngagement
  onClose: () => void
}

function AuditorsDrawer({ organizationId, engagement, onClose }: AuditorsDrawerProps) {
  const [auditors, setAuditors] = useState<EngagementAuditor[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [userId, setUserId] = useState('')
  const [busy, setBusy] = useState(false)

  const load = () => {
    listEngagementAuditors(organizationId, engagement.id)
      .then(setAuditors)
      .catch(err => setError(err?.message ?? 'Could not load auditors.'))
  }
  useEffect(() => { load() }, [organizationId, engagement.id])

  const handleGrant = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!userId.trim()) return
    setBusy(true); setError(null)
    try {
      await grantEngagementAuditor(organizationId, engagement.id, userId.trim())
      setUserId(''); load()
    } catch (err: any) {
      setError(err?.message ?? 'Could not grant access.')
    } finally { setBusy(false) }
  }

  const handleRevoke = async (auditor: EngagementAuditor) => {
    if (!window.confirm(`Revoke ${auditor.email ?? 'this auditor'}'s access?`)) return
    setBusy(true); setError(null)
    try {
      await revokeEngagementAuditor(organizationId, engagement.id, auditor.id); load()
    } catch (err: any) {
      setError(err?.message ?? 'Could not revoke access.')
    } finally { setBusy(false) }
  }

  return (
    <Drawer width={520} onClose={onClose}>
      <DrawerHeader
        title="External auditors"
        subtitle={<>{engagement.name}</>}
        onClose={onClose}
      />

      <div style={{ padding: 24, flex: 1, display: 'flex', flexDirection: 'column', gap: 20 }}>
        <HelpNote>
          An auditor you add here gets <strong>read-only</strong> access to <strong>this engagement only</strong> — its
          controls, evidence, and queries. They can't see anything else in your organisation. They reach it from their
          own “My engagements” list after signing in.
        </HelpNote>

        {error && <div role="alert" style={{ padding: '10px 14px', background: 'var(--error-bg)', borderRadius: 8, color: 'var(--text)', fontSize: 13 }}>{error}</div>}

        <form onSubmit={handleGrant} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <label htmlFor="auditor-user" style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>Grant access</label>
          <div style={{ display: 'flex', gap: 8 }}>
            <input id="auditor-user" type="text" value={userId} onChange={e => setUserId(e.target.value)}
              placeholder="Auditor's user ID" style={{ ...inputStyle, fontSize: 13 }} />
            <button type="submit" disabled={busy || !userId.trim()} style={{
              padding: '9px 16px', borderRadius: 8, border: 'none',
              background: busy || !userId.trim() ? 'var(--muted-bg)' : 'var(--primary)',
              color: 'var(--primary-foreground)', fontSize: 13, fontWeight: 600,
              cursor: busy || !userId.trim() ? 'not-allowed' : 'pointer', whiteSpace: 'nowrap',
            }}>Grant</button>
          </div>
          <p style={{ margin: 0, fontSize: 11, color: 'var(--muted)' }}>
            The auditor must already have a platform account. Inviting brand-new auditors by email is coming later.
          </p>
        </form>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {auditors === null ? (
            <div style={{ color: 'var(--muted)', fontSize: 14 }}>Loading…</div>
          ) : auditors.length === 0 ? (
            <div style={{ color: 'var(--muted)', fontSize: 14 }}>No auditors have access yet.</div>
          ) : (
            auditors.map(a => {
              const meta = AUDITOR_STATUS_META[a.status] ?? AUDITOR_STATUS_META.revoked
              return (
                <div key={a.id} style={{ display: 'flex', alignItems: 'center', gap: 10, border: '1px solid var(--border)', borderRadius: 8, padding: '10px 14px', background: 'var(--panel)' }}>
                  <div style={{ flex: 1, minWidth: 0, fontSize: 13, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis' }}>{a.email ?? a.user_id}</div>
                  <span style={{ padding: '2px 10px', borderRadius: 10, fontSize: 11, fontWeight: 600, background: meta.bg, color: meta.color }}>{meta.label}</span>
                  {a.status === 'active' && (
                    <button onClick={() => handleRevoke(a)} disabled={busy} style={{
                      padding: '4px 12px', borderRadius: 6, border: '1px solid var(--border)',
                      background: 'var(--card)', color: 'var(--error, #ef4444)', fontSize: 12,
                      cursor: busy ? 'not-allowed' : 'pointer',
                    }}>Revoke</button>
                  )}
                </div>
              )
            })
          )}
        </div>
      </div>
    </Drawer>
  )
}

// ---------------------------------------------------------------------------
// Queries Drawer — structured auditor <-> owner query log
// ---------------------------------------------------------------------------

interface QueriesDrawerProps {
  organizationId: string
  engagement: AuditEngagement
  onClose: () => void
}

function QueriesDrawer({ organizationId, engagement, onClose }: QueriesDrawerProps) {
  const [queries, setQueries] = useState<EngagementQuery[] | null>(null)
  const [selected, setSelected] = useState<EngagementQuery | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [showNew, setShowNew] = useState(false)
  const [form, setForm] = useState({ scf_id: '', title: '', body: '' })
  const [reply, setReply] = useState('')

  const loadList = () => {
    listEngagementQueries(organizationId, engagement.id)
      .then(setQueries)
      .catch(err => setError(err?.message ?? 'Could not load queries.'))
  }
  useEffect(() => { loadList() }, [organizationId, engagement.id])

  const openQuery = async (q: EngagementQuery) => {
    setError(null)
    try { setSelected(await getEngagementQuery(organizationId, engagement.id, q.id)) }
    catch (err: any) { setError(err?.message ?? 'Could not load the query.') }
  }

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.scf_id.trim() || !form.title.trim() || !form.body.trim()) return
    setBusy(true); setError(null)
    try {
      await createEngagementQuery(organizationId, engagement.id, {
        scf_id: form.scf_id.trim(), title: form.title.trim(), body: form.body.trim(),
      })
      setForm({ scf_id: '', title: '', body: '' }); setShowNew(false); loadList()
    } catch (err: any) {
      setError(err?.message ?? 'Could not raise the query.')
    } finally { setBusy(false) }
  }

  const handleReply = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selected || !reply.trim()) return
    setBusy(true); setError(null)
    try {
      await respondToEngagementQuery(organizationId, engagement.id, selected.id, reply.trim())
      setReply(''); await openQuery(selected); loadList()
    } catch (err: any) {
      setError(err?.message ?? 'Could not post the response.')
    } finally { setBusy(false) }
  }

  const changeStatus = async (status: EngagementQueryStatus) => {
    if (!selected) return
    setBusy(true); setError(null)
    try {
      await updateEngagementQueryStatus(organizationId, engagement.id, selected.id, status)
      await openQuery(selected); loadList()
    } catch (err: any) {
      setError(err?.message ?? 'Could not change the status.')
    } finally { setBusy(false) }
  }

  return (
    <Drawer width={640} onClose={onClose}>
      <DrawerHeader
        title={selected ? 'Query' : 'Auditor queries'}
        subtitle={selected
          ? <>{selected.scf_id} · raised by {selected.raised_by_email ?? 'unknown'}</>
          : <>{engagement.name}</>}
        onClose={onClose}
      />

      <div style={{ padding: 24, flex: 1, display: 'flex', flexDirection: 'column', gap: 16 }}>
        {error && <div role="alert" style={{ padding: '10px 14px', background: 'var(--error-bg)', borderRadius: 8, color: 'var(--text)', fontSize: 13 }}>{error}</div>}

        {selected ? (
          <>
            <button onClick={() => setSelected(null)} style={{ alignSelf: 'flex-start', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--primary)', fontSize: 13, padding: 0 }}>← All queries</button>

            <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 16, background: 'var(--panel)' }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <strong style={{ fontSize: 15, color: 'var(--text)' }}>{selected.title}</strong>
                <span style={{ padding: '2px 10px', borderRadius: 10, fontSize: 11, fontWeight: 600, background: QUERY_STATUS_META[selected.status].bg, color: QUERY_STATUS_META[selected.status].color }}>{QUERY_STATUS_META[selected.status].label}</span>
              </div>
              <p style={{ margin: '8px 0 0', fontSize: 13, color: 'var(--text)', whiteSpace: 'pre-wrap' }}>{selected.body}</p>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {(selected.responses ?? []).length === 0 ? (
                <div style={{ fontSize: 13, color: 'var(--muted)' }}>No responses yet.</div>
              ) : (
                (selected.responses ?? []).map(r => (
                  <div key={r.id} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '10px 14px' }}>
                    <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 4 }}>{r.email ?? r.user_id ?? 'unknown'} · {new Date(r.created_at).toLocaleString()}</div>
                    <div style={{ fontSize: 13, color: 'var(--text)', whiteSpace: 'pre-wrap' }}>{r.content}</div>
                  </div>
                ))
              )}
            </div>

            <form onSubmit={handleReply} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <textarea value={reply} onChange={e => setReply(e.target.value)} placeholder="Write a response…" rows={3} style={{ ...inputStyle, resize: 'vertical' }} />
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button type="submit" disabled={busy || !reply.trim()} style={{
                  padding: '9px 16px', borderRadius: 8, border: 'none',
                  background: busy || !reply.trim() ? 'var(--muted-bg)' : 'var(--primary)',
                  color: 'var(--primary-foreground)', fontSize: 13, fontWeight: 600,
                  cursor: busy || !reply.trim() ? 'not-allowed' : 'pointer',
                }}>Respond</button>
                {selected.status !== 'closed' ? (
                  <button type="button" disabled={busy} onClick={() => changeStatus('closed')} style={{ padding: '9px 16px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--text)', fontSize: 13, cursor: 'pointer' }}>Close query</button>
                ) : (
                  <button type="button" disabled={busy} onClick={() => changeStatus('open')} style={{ padding: '9px 16px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--text)', fontSize: 13, cursor: 'pointer' }}>Reopen</button>
                )}
              </div>
            </form>
          </>
        ) : (
          <>
            <HelpNote>
              Queries are the audit's question log. An auditor <strong>raises</strong> a question about a control;
              the control owner <strong>responds</strong> (moving it to <em>Answered</em>); the auditor
              <strong> closes</strong> it once satisfied. Reopen any time if there's more to discuss.
            </HelpNote>

            <div>
              <button onClick={() => setShowNew(v => !v)} style={{ padding: '9px 16px', borderRadius: 8, border: 'none', background: 'var(--primary)', color: 'var(--primary-foreground)', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>
                {showNew ? 'Cancel' : '+ Raise query'}
              </button>
            </div>

            {showNew && (
              <form onSubmit={handleCreate} style={{ display: 'flex', flexDirection: 'column', gap: 8, border: '1px solid var(--border)', borderRadius: 8, padding: 16 }}>
                <input style={{ ...inputStyle, fontSize: 13 }} placeholder="Control SCF ID (e.g. GOV-01)" value={form.scf_id} onChange={e => setForm(f => ({ ...f, scf_id: e.target.value }))} />
                <input style={{ ...inputStyle, fontSize: 13 }} placeholder="Query title" value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))} />
                <textarea style={{ ...inputStyle, fontSize: 13, resize: 'vertical' }} rows={3} placeholder="What are you asking the control owner?" value={form.body} onChange={e => setForm(f => ({ ...f, body: e.target.value }))} />
                <button type="submit" disabled={busy} style={{ alignSelf: 'flex-start', padding: '9px 16px', borderRadius: 8, border: 'none', background: 'var(--primary)', color: 'var(--primary-foreground)', fontSize: 13, fontWeight: 600, cursor: busy ? 'not-allowed' : 'pointer' }}>Raise query</button>
              </form>
            )}

            {queries === null ? (
              <div style={{ color: 'var(--muted)', fontSize: 14 }}>Loading…</div>
            ) : queries.length === 0 ? (
              <div style={{ color: 'var(--muted)', fontSize: 14 }}>No queries raised yet.</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {queries.map(q => {
                  const meta = QUERY_STATUS_META[q.status]
                  return (
                    <button key={q.id} onClick={() => openQuery(q)} style={{ textAlign: 'left', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 14px', background: 'var(--panel)', cursor: 'pointer' }}>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                        <strong style={{ fontSize: 12, color: 'var(--muted)' }}>{q.scf_id}</strong>
                        <span style={{ fontSize: 13, color: 'var(--text)', flex: 1, minWidth: 0 }}>{q.title}</span>
                        <span style={{ padding: '1px 8px', borderRadius: 10, fontSize: 11, fontWeight: 600, background: meta.bg, color: meta.color }}>{meta.label}</span>
                        <span style={{ fontSize: 11, color: 'var(--muted)' }}>{q.response_count} repl{q.response_count === 1 ? 'y' : 'ies'}</span>
                      </div>
                    </button>
                  )
                })}
              </div>
            )}
          </>
        )}
      </div>
    </Drawer>
  )
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function EngagementsPage({ organizationId }: EngagementsPageProps) {
  const [engagements, setEngagements] = useState<AuditEngagement[]>([])
  const [frameworks, setFrameworks] = useState<FrameworkInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [showCreateDrawer, setShowCreateDrawer] = useState(false)
  const [scopeEngagement, setScopeEngagement] = useState<AuditEngagement | null>(null)
  const [presentEngagement, setPresentEngagement] = useState<AuditEngagement | null>(null)
  const [auditorsEngagement, setAuditorsEngagement] = useState<AuditEngagement | null>(null)
  const [queriesEngagement, setQueriesEngagement] = useState<AuditEngagement | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      setEngagements(await listEngagements(organizationId))
    } catch {
      setEngagements([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [organizationId])

  useEffect(() => {
    fetchFrameworks(false).then(setFrameworks).catch(() => {})
  }, [])

  const nameById = useMemo(() => Object.fromEntries(frameworks.map(f => [f.id, f.name])), [frameworks])
  const frameworkName: FrameworkNamer = (id) => nameById[id] ?? id

  const filteredEngagements = useMemo(() => {
    if (!searchQuery.trim()) return engagements
    const q = searchQuery.toLowerCase()
    return engagements.filter(eng =>
      eng.name.toLowerCase().includes(q) ||
      eng.frameworks.some(fw => (nameById[fw] ?? fw).toLowerCase().includes(q))
    )
  }, [engagements, searchQuery, nameById])

  const handleDelete = async (engagement: AuditEngagement) => {
    if (!window.confirm(`Delete engagement “${engagement.name}”? This cannot be undone.`)) return
    setDeleting(engagement.id)
    try {
      await deleteEngagement(organizationId, engagement.id)
      await load()
    } catch (err: any) {
      alert(err?.message ?? 'Could not delete the engagement.')
    } finally {
      setDeleting(null)
    }
  }

  const handleCreated = () => {
    setShowCreateDrawer(false)
    load()
  }

  const actionBtn: React.CSSProperties = {
    padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)',
    background: 'var(--card)', color: 'var(--text)', fontSize: 12, cursor: 'pointer',
  }

  return (
    <div style={{ padding: '24px 32px', maxWidth: 1100, margin: '0 auto' }}>
      {/* Page header */}
      <div style={{ marginBottom: 12 }}>
        <h1 style={{ margin: 0, fontSize: 24, fontWeight: 700, color: 'var(--text)' }}>Audit Engagements</h1>
        <p style={{ margin: '4px 0 0', color: 'var(--muted)', fontSize: 14, maxWidth: 640, lineHeight: 1.5 }}>
          A time-bounded audit scoped to one or more frameworks. Each engagement pulls in the mapped SCF controls,
          presents them from the framework's perspective with the evidence you've collected, and gives external
          auditors a read-only workspace to review and raise queries.
        </p>
      </div>

      {/* Explorer toolbar */}
      <ListToolbar
        search={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder="Search engagements…"
        count={`${engagements.length} engagement${engagements.length !== 1 ? 's' : ''}`}
        actions={
          <button
            onClick={() => setShowCreateDrawer(true)}
            className="eng-new-btn"
          >
            + New Engagement
          </button>
        }
      />

      {/* Content */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: '64px 0', color: 'var(--muted)', fontSize: 15 }}>Loading engagements…</div>
      ) : engagements.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '80px 0', border: '2px dashed var(--border)', borderRadius: 12 }}>
          <div style={{ fontSize: 40, marginBottom: 12 }}>📋</div>
          <h3 style={{ margin: 0, color: 'var(--muted)', fontWeight: 600 }}>No engagements yet</h3>
          <p style={{ color: 'var(--muted)', fontSize: 14, marginTop: 6 }}>Create your first engagement to start scoping an audit.</p>
          <button onClick={() => setShowCreateDrawer(true)} style={{
            marginTop: 16, padding: '9px 18px', borderRadius: 6, border: 'none',
            background: 'var(--primary)', color: 'var(--primary-foreground)', fontSize: 14, fontWeight: 600, cursor: 'pointer',
          }}>+ New Engagement</button>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {filteredEngagements.map(eng => {
            const statusStyle = STATUS_COLORS[eng.status] ?? { bg: 'var(--secondary)', color: 'var(--muted)' }
            return (
              <div key={eng.id} className="eng-explorer-row" style={{
                padding: '18px 22px', display: 'flex', alignItems: 'flex-start', gap: 16,
              }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 6 }}>
                    <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: 'var(--text)' }}>{eng.name}</h3>
                    <span style={{ padding: '2px 10px', borderRadius: 10, fontSize: 12, fontWeight: 600, background: statusStyle.bg, color: statusStyle.color }}>
                      {STATUS_LABELS[eng.status] ?? eng.status}
                    </span>
                  </div>

                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
                    {eng.frameworks.map(fw => (
                      <span key={fw} title={fw} style={{ padding: '2px 8px', borderRadius: 10, background: 'var(--accent-muted)', color: 'var(--primary)', fontSize: 12, fontWeight: 500 }}>
                        {frameworkName(fw)}
                      </span>
                    ))}
                  </div>

                  <div style={{ display: 'flex', gap: 20, fontSize: 13, color: 'var(--muted)', flexWrap: 'wrap' }}>
                    <span><strong style={{ color: 'var(--text)' }}>{eng.scope_count ?? 0}</strong> controls in scope</span>
                    {eng.start_date && <span>Start: {fmtDate(eng.start_date)}</span>}
                    {eng.end_date && <span>End: {fmtDate(eng.end_date)}</span>}
                    <span>Created: {fmtDate(eng.created_at)}</span>
                  </div>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, flexShrink: 0 }}>
                  <button onClick={() => setScopeEngagement(eng)} style={actionBtn}>View scope</button>
                  <button onClick={() => setPresentEngagement(eng)} style={actionBtn}>Present</button>
                  <button onClick={() => setAuditorsEngagement(eng)} style={actionBtn}>Auditors</button>
                  <button onClick={() => setQueriesEngagement(eng)} style={actionBtn}>Queries</button>
                  {eng.status === 'draft' && (
                    <button onClick={() => handleDelete(eng)} disabled={deleting === eng.id} style={{
                      ...actionBtn, color: 'var(--error, #ef4444)',
                      cursor: deleting === eng.id ? 'not-allowed' : 'pointer', opacity: deleting === eng.id ? 0.6 : 1,
                    }}>{deleting === eng.id ? 'Deleting…' : 'Delete'}</button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {showCreateDrawer && (
        <CreateEngagementDrawer organizationId={organizationId} frameworks={frameworks} onClose={() => setShowCreateDrawer(false)} onCreated={handleCreated} />
      )}
      {scopeEngagement && (
        <ScopeDrawer organizationId={organizationId} engagement={scopeEngagement} frameworkName={frameworkName} onClose={() => setScopeEngagement(null)} />
      )}
      {presentEngagement && (
        <PresentationDrawer organizationId={organizationId} engagement={presentEngagement} frameworkName={frameworkName} onClose={() => setPresentEngagement(null)} />
      )}
      {auditorsEngagement && (
        <AuditorsDrawer organizationId={organizationId} engagement={auditorsEngagement} onClose={() => setAuditorsEngagement(null)} />
      )}
      {queriesEngagement && (
        <QueriesDrawer organizationId={organizationId} engagement={queriesEngagement} onClose={() => setQueriesEngagement(null)} />
      )}
    </div>
  )
}
