/**
 * Document reader — the default view of a generated document.
 *
 * A policy is written once and read many times: by a reviewer deciding whether
 * to approve it, by an auditor asking who approved it and against which
 * catalog version, by whoever has to live with it. Until now the only way to
 * see one in the app was the section editor, which shows a single section at a
 * time inside a code editor. That serves the author and nobody else — a
 * thirty-section policy could not be *read* in the tool that produced it.
 *
 * So reading is the default and editing is a mode you enter deliberately.
 *
 * Two things the reader must not lose from the editor:
 *
 *   - The merge state. The generator's changes, your preserved edits and the
 *     sections whose controls have left scope are the whole point of the
 *     three-layer merge. The backend wraps each section with its status, so
 *     here they appear in the document flow rather than only in an outline.
 *   - The lifecycle. For a compliance artifact the provenance *is* the
 *     credibility, so the stepper and the approval facts sit in the header
 *     rather than behind a History toggle.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import DOMPurify from 'dompurify'
import { toast } from 'react-hot-toast'
import {
  LIFECYCLE_LABELS,
  SECTION_STATUS_LABELS,
  downloadDocument,
  getDocument,
  getDocumentHistory,
  previewDocument,
  type DocumentSection,
  type LifecycleStatus,
} from '../../data/documentsApi'

interface Props {
  organizationId: string
  documentId: string
  onBack: () => void
  /** Enter the editor. The section id anchors it to what was being read. */
  onEdit: (sectionId: string | null) => void
}

/**
 * What the reader is allowed to render.
 *
 * The backend already refuses to emit raw HTML from document content — see
 * `_neutralise_raw_html` in renderer.py — and this is the second layer, because
 * one regression in a Markdown extension should not become a stored XSS in an
 * authenticated page. The list is exactly what our own fragment builder emits
 * plus what Markdown itself produces; anything else is a bug or an attack.
 *
 * `data-section-id` and `data-level` are not decoration: the scroll-spy and the
 * contents rail both query for them, so stripping data attributes wholesale —
 * as the two sibling sinks in this app do — would silently kill navigation.
 */
const READER_SANITIZE_CONFIG = {
  ALLOWED_TAGS: [
    'section', 'div', 'p', 'span', 'br', 'hr',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li', 'dl', 'dt', 'dd',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'strong', 'em', 'b', 'i', 'code', 'pre', 'blockquote', 'a',
  ],
  ALLOWED_ATTR: ['class', 'id', 'href', 'title', 'data-section-id', 'data-level'],
  ALLOW_DATA_ATTR: false,
}

/** The lifecycle in order. The stepper needs the sequence, not just the state. */
const LIFECYCLE_ORDER: LifecycleStatus[] = [
  'draft',
  'in_review',
  'approved',
  'published',
]

export default function DocumentReader({
  organizationId,
  documentId,
  onBack,
  onEdit,
}: Props) {
  const [activeSection, setActiveSection] = useState<string | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const bodyRef = useRef<HTMLDivElement>(null)

  const { data: doc } = useQuery({
    queryKey: ['document', organizationId, documentId],
    queryFn: () => getDocument(organizationId, documentId),
  })

  const { data: preview, isLoading: previewLoading } = useQuery({
    queryKey: ['document-preview', organizationId, documentId],
    queryFn: () => previewDocument(organizationId, documentId),
  })

  const { data: history } = useQuery({
    queryKey: ['document-history', organizationId, documentId],
    queryFn: () => getDocumentHistory(organizationId, documentId),
    enabled: showHistory,
  })

  const ordered = useMemo(
    () => (doc ? [...doc.sections].sort((a, b) => a.ordinal - b.ordinal) : []),
    [doc]
  )

  // Scroll-spy. The rail is the only navigation for a document this long, so
  // it has to say where you are, not just where you could go. rootMargin
  // pulls the trigger line to the upper third: a heading counts as "current"
  // once it reaches reading position, not when it first peeks into view.
  useEffect(() => {
    const root = bodyRef.current
    if (!root || !preview) return
    const nodes = root.querySelectorAll<HTMLElement>('[data-section-id]')
    if (!nodes.length) return

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0]
        if (visible) {
          setActiveSection(visible.target.getAttribute('data-section-id'))
        }
      },
      // The page pane is the scroller, not the window — see the reader's
      // layout note in styles.css. Observing against the window here would
      // report every section as permanently visible.
      { root, rootMargin: '-12% 0px -72% 0px', threshold: 0 }
    )
    nodes.forEach((n) => observer.observe(n))
    return () => observer.disconnect()
  }, [preview])

  function jumpTo(section: DocumentSection) {
    const target = bodyRef.current?.querySelector<HTMLElement>(
      `[data-section-id="${CSS.escape(section.section_id)}"]`
    )
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    setActiveSection(section.section_id)
  }

  function download(format: 'md' | 'pdf') {
    downloadDocument(organizationId, documentId, format, doc?.title ?? 'document').catch(
      (e: Error) => toast.error(e.message)
    )
  }

  if (!doc) {
    return <div className="doc-editor-loading-page">Loading document…</div>
  }

  const stageIndex = LIFECYCLE_ORDER.indexOf(doc.lifecycle_status)
  // A published document is the organisation's live policy. The editor already
  // refuses to write to one, but offering an Edit button that leads to a
  // read-only editor is a worse answer than saying why up front.
  const published = doc.lifecycle_status === 'published'
  const conflicts = ordered.filter((s) => s.status === 'conflict')
  const retiring = ordered.filter((s) => s.status === 'pending_retirement')
  // The transition that moved it to its current state — who signed it off and
  // when. Exactly the question an auditor opens with.
  const signoff = history?.transitions.find(
    (t) => t.to_status === doc.lifecycle_status
  )

  return (
    <div className="doc-reader">
      {/* ── Masthead ─────────────────────────────────────────────────── */}
      <header className="doc-reader-head">
        <div className="doc-reader-head-top">
          <button type="button" className="btn-secondary" onClick={onBack}>
            ← All documents
          </button>
          <div className="doc-reader-actions">
            <button
              type="button"
              className="btn-primary"
              disabled={published}
              title={
                published
                  ? 'Published documents are read-only. Return this one to review to edit it.'
                  : undefined
              }
              onClick={() => onEdit(activeSection)}
            >
              Edit
            </button>
            <button type="button" className="btn-secondary" onClick={() => download('pdf')}>
              PDF
            </button>
            <button type="button" className="btn-secondary" onClick={() => download('md')}>
              Markdown
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

        <h1 className="doc-reader-title">{doc.title}</h1>

        <div className="doc-reader-facts">
          <span>Version {doc.generation_version}</span>
          {doc.catalog_version && <span>SCF {doc.catalog_version}</span>}
          <span>{doc.section_count} sections</span>
          {signoff?.actor_email && (
            <span>
              {LIFECYCLE_LABELS[doc.lifecycle_status]} by {signoff.actor_email}
              {signoff.created_at &&
                ` · ${new Date(signoff.created_at).toLocaleDateString('en-GB')}`}
            </span>
          )}
        </div>

        {/* The lifecycle, stated rather than hidden. */}
        <ol className="doc-lifecycle-steps">
          {LIFECYCLE_ORDER.map((stage, i) => (
            <li
              key={stage}
              className={`doc-lifecycle-step ${
                i < stageIndex ? 'is-done' : ''
              } ${i === stageIndex ? 'is-current' : ''}`}
            >
              <span className="doc-lifecycle-dot" aria-hidden="true" />
              <span className="doc-lifecycle-label">{LIFECYCLE_LABELS[stage]}</span>
            </li>
          ))}
        </ol>
      </header>

      {published && (
        <div className="doc-notice">
          This document is published and is read-only. Return it to review to
          make changes.
        </div>
      )}

      {conflicts.length > 0 && (
        <div className="doc-notice doc-notice-warning">
          <strong>
            {conflicts.length} section{conflicts.length === 1 ? '' : 's'} need your
            decision.
          </strong>{' '}
          Your edit was kept in each. The generated alternative is in this
          document's version history — nothing was overwritten.
        </div>
      )}

      {retiring.length > 0 && (
        <div className="doc-notice">
          <strong>
            {retiring.length} section{retiring.length === 1 ? '' : 's'} are
            pending retirement.
          </strong>{' '}
          The controls behind them have left scope. They are marked in the
          document below and nothing has been deleted.
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

      <div className="doc-reader-body">
        {/* ── Contents rail ───────────────────────────────────────────── */}
        <aside className="doc-reader-toc">
          <div className="doc-outline-head">
            <h3>Contents</h3>
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
                  onClick={() => jumpTo(s)}
                >
                  <span className="doc-outline-label">{s.heading_text}</span>
                  {s.status !== 'unchanged' && (
                    <span className={`doc-section-badge status-${s.status}`}>
                      {SECTION_STATUS_LABELS[s.status]}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </aside>

        {/* ── The document ────────────────────────────────────────────── */}
        <main className="doc-reader-page" ref={bodyRef}>
          {previewLoading && <p className="doc-empty">Rendering document…</p>}
          {preview && (
            <article
              className="doc-reader-prose"
              dangerouslySetInnerHTML={{
                // Document content is Markdown written by a generator and then
                // edited by people, so it is untrusted on both counts. The
                // backend neutralises raw HTML at the Markdown boundary; this
                // allowlist is the second layer. See READER_SANITIZE_CONFIG.
                __html: DOMPurify.sanitize(preview.html, READER_SANITIZE_CONFIG),
              }}
            />
          )}
        </main>
      </div>
    </div>
  )
}
