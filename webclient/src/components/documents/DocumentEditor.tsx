/**
 * Document editor — outline, section editing, lifecycle, export.
 *
 * The outline is the important half. It is the only place the three-layer
 * merge becomes visible: which sections the generator changed, which carry
 * your edits, and which need a decision because both moved. Without it a
 * regenerated document is just a wall of text you have to re-read.
 */
import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import {
  LIFECYCLE_LABELS,
  SECTION_STATUS_LABELS,
  downloadDocument,
  getDocument,
  getDocumentHistory,
  saveSection,
  transitionDocument,
  type DocumentSection,
  type LifecycleStatus,
} from '../../data/documentsApi'
import MarkdownEditor from './MarkdownEditor'

interface Props {
  organizationId: string
  documentId: string
  onBack: () => void
}

/** Section bodies, keyed by section id, sliced out of the merged markdown. */
function sliceSections(
  markdown: string,
  sections: DocumentSection[]
): Record<string, string> {
  const lines = markdown.split('\n')
  const headingIndexes: number[] = []
  let inFence = false

  lines.forEach((line, i) => {
    if (/^(`{3,}|~{3,})/.test(line)) inFence = !inFence
    if (!inFence && /^#{1,6}\s+/.test(line)) headingIndexes.push(i)
  })

  const ordered = [...sections].sort((a, b) => a.ordinal - b.ordinal)
  const out: Record<string, string> = {}
  ordered.forEach((section, i) => {
    const start = headingIndexes[i]
    if (start === undefined) return
    const end = headingIndexes[i + 1] ?? lines.length
    out[section.section_id] = lines.slice(start + 1, end).join('\n').trim()
  })
  return out
}

export default function DocumentEditor({ organizationId, documentId, onBack }: Props) {
  const queryClient = useQueryClient()
  const [activeSection, setActiveSection] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [dirty, setDirty] = useState(false)
  const [showHistory, setShowHistory] = useState(false)

  const { data: doc, isLoading } = useQuery({
    queryKey: ['document', organizationId, documentId],
    queryFn: () => getDocument(organizationId, documentId),
  })

  const { data: history } = useQuery({
    queryKey: ['document-history', organizationId, documentId],
    queryFn: () => getDocumentHistory(organizationId, documentId),
    enabled: showHistory,
  })

  const bodies = useMemo(
    () => (doc ? sliceSections(doc.merged_content, doc.sections) : {}),
    [doc]
  )

  // Open the first section that needs a decision — that is what the reader
  // came for after a regeneration.
  useEffect(() => {
    if (!doc || activeSection) return
    const conflict = doc.sections.find((s) => s.status === 'conflict')
    const first = conflict ?? [...doc.sections].sort((a, b) => a.ordinal - b.ordinal)[0]
    if (first) {
      setActiveSection(first.section_id)
      setDraft(bodies[first.section_id] ?? '')
      setDirty(false)
    }
  }, [doc, activeSection, bodies])

  const saveMutation = useMutation({
    mutationFn: (payload: { sectionId: string; content: string }) =>
      saveSection(organizationId, documentId, payload.sectionId, payload.content),
    onSuccess: (result) => {
      setDirty(false)
      queryClient.invalidateQueries({ queryKey: ['document', organizationId, documentId] })
      queryClient.invalidateQueries({ queryKey: ['documents', organizationId] })
      toast.success(
        result.lifecycle_status === 'in_review'
          ? 'Saved — the document returned to review'
          : 'Section saved'
      )
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const transitionMutation = useMutation({
    mutationFn: (to: LifecycleStatus) => transitionDocument(organizationId, documentId, to),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['document', organizationId, documentId] })
      queryClient.invalidateQueries({ queryKey: ['documents', organizationId] })
      toast.success(`Moved to ${LIFECYCLE_LABELS[result.to_status as LifecycleStatus]}`)
    },
    onError: (error: Error) => toast.error(error.message),
  })

  function selectSection(section: DocumentSection) {
    if (dirty && !window.confirm('Discard unsaved changes to this section?')) return
    setActiveSection(section.section_id)
    setDraft(bodies[section.section_id] ?? '')
    setDirty(false)
  }

  if (isLoading || !doc) {
    return <div className="doc-editor-loading-page">Loading document…</div>
  }

  const ordered = [...doc.sections].sort((a, b) => a.ordinal - b.ordinal)
  const conflicts = ordered.filter((s) => s.status === 'conflict')
  const current = ordered.find((s) => s.section_id === activeSection) ?? null
  const readOnly = doc.lifecycle_status === 'published'

  return (
    <div className="doc-editor">
      <div className="doc-editor-topbar">
        <button type="button" className="btn-secondary" onClick={onBack}>
          ← All documents
        </button>
        <div className="doc-editor-title">
          <h1>{doc.title}</h1>
          <span className="doc-editor-meta">
            v{doc.generation_version}
            {doc.catalog_version && ` · SCF ${doc.catalog_version}`}
            {doc.is_derivative && <span className="doc-derivative-tag">Derivative</span>}
          </span>
        </div>
        <div className="doc-editor-actions">
          <span className={`doc-lifecycle-pill status-${doc.lifecycle_status}`}>
            {LIFECYCLE_LABELS[doc.lifecycle_status]}
          </span>
          {doc.available_transitions.map((t) => (
            <button
              key={t.to_status}
              type="button"
              className="btn-secondary"
              disabled={transitionMutation.isPending}
              onClick={() => transitionMutation.mutate(t.to_status)}
            >
              {t.label}
            </button>
          ))}
          <button
            type="button"
            className="btn-secondary"
            onClick={() =>
              downloadDocument(organizationId, documentId, 'md', doc.title).catch(
                (e: Error) => toast.error(e.message)
              )
            }
          >
            Markdown
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() =>
              downloadDocument(organizationId, documentId, 'pdf', doc.title).catch(
                (e: Error) => toast.error(e.message)
              )
            }
          >
            PDF
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setShowHistory((v) => !v)}
          >
            History
          </button>
        </div>
      </div>

      {conflicts.length > 0 && (
        <div className="doc-notice doc-notice-warning doc-conflict-banner">
          <strong>
            {conflicts.length} section{conflicts.length === 1 ? '' : 's'} need
            your decision.
          </strong>{' '}
          Your edit was kept in each. The generated alternative is in this
          document's version history — nothing was overwritten.
        </div>
      )}

      {readOnly && (
        <div className="doc-notice">
          This document is published and is read-only. Return it to review to
          make changes.
        </div>
      )}

      {showHistory && history && (
        <div className="doc-history-panel">
          <h3>Lifecycle</h3>
          <ul className="doc-history-list">
            {history.transitions.map((t, i) => (
              <li key={i}>
                <span className="doc-history-when">
                  {t.created_at ? new Date(t.created_at).toLocaleString('en-GB') : '—'}
                </span>
                <span>
                  {t.from_status ? `${t.from_status} → ` : ''}
                  <strong>{t.to_status}</strong>
                  {t.actor_email && ` by ${t.actor_email}`}
                  {t.trigger !== 'manual' && ` (${t.trigger})`}
                </span>
                {t.reason && <em>{t.reason}</em>}
              </li>
            ))}
          </ul>
          <h3>Generated versions</h3>
          <ul className="doc-history-list">
            {history.versions.map((v) => (
              <li key={v.version}>
                <span className="doc-history-when">
                  {v.created_at ? new Date(v.created_at).toLocaleString('en-GB') : '—'}
                </span>
                <span>
                  v{v.version}
                  {v.model_id && ` · ${v.model_id}`}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="doc-editor-body">
        {/* ── Outline ─────────────────────────────────────────────────── */}
        <aside className="doc-outline">
          <div className="doc-outline-head">
            <h3>Sections</h3>
            <span>{ordered.length}</span>
          </div>
          <ul className="doc-outline-list">
            {ordered.map((s) => (
              <li key={s.section_id}>
                <button
                  type="button"
                  className={`doc-outline-item level-${s.heading_level} ${
                    s.section_id === activeSection ? 'is-active' : ''
                  } status-${s.status}`}
                  onClick={() => selectSection(s)}
                >
                  <span className="doc-outline-label">{s.heading_text}</span>
                  {s.status !== 'unchanged' && (
                    <span className={`doc-section-badge status-${s.status}`}>
                      {SECTION_STATUS_LABELS[s.status]}
                    </span>
                  )}
                  {s.control_ids.length > 0 && (
                    <span className="doc-outline-controls">
                      {s.control_ids.length} control
                      {s.control_ids.length === 1 ? '' : 's'}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </aside>

        {/* ── Section editor ──────────────────────────────────────────── */}
        <main className="doc-section-editor">
          {current ? (
            <>
              <div className="doc-section-head">
                <div>
                  <h2>{current.heading_text}</h2>
                  <span className="doc-section-sub">
                    {SECTION_STATUS_LABELS[current.status]}
                    {current.edited_at &&
                      ` · edited ${new Date(current.edited_at).toLocaleDateString('en-GB')}`}
                  </span>
                </div>
                <button
                  type="button"
                  className="btn-primary"
                  disabled={!dirty || saveMutation.isPending || readOnly}
                  onClick={() =>
                    saveMutation.mutate({
                      sectionId: current.section_id,
                      content: draft,
                    })
                  }
                >
                  {saveMutation.isPending ? 'Saving…' : 'Save section'}
                </button>
              </div>

              {current.control_ids.length > 0 && (
                <div className="doc-section-controls">
                  {current.control_ids.map((id) => (
                    <span key={id} className="doc-control-chip">
                      {id}
                    </span>
                  ))}
                </div>
              )}

              <MarkdownEditor
                value={draft}
                readOnly={readOnly}
                onChange={(next) => {
                  setDraft(next)
                  setDirty(true)
                }}
              />
            </>
          ) : (
            <p className="doc-empty">Select a section to edit.</p>
          )}
        </main>
      </div>
    </div>
  )
}
