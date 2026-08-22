/**
 * Document editor — outline, section editing, lifecycle, export.
 *
 * The outline is the important half. It is the only place the three-layer
 * merge becomes visible: which sections the generator changed, which carry
 * your edits, and which need a decision because both moved. Without it a
 * regenerated document is just a wall of text you have to re-read.
 *
 * Seeing that a section needs a decision is not the same as being able to make
 * one, so the open section carries the same Keep mine / Take generated /
 * Retire / Keep controls the reader does, beside Save section.
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
import OutlineCount from './OutlineCount'
import SectionDecision from './SectionDecision'
import { sliceSections } from './sectionText'
import { useResolveSection } from './useResolveSection'

interface Props {
  organizationId: string
  documentId: string
  onBack: () => void
  /** Section to open first — whatever was being read when Edit was pressed. */
  initialSectionId?: string | null
}

export default function DocumentEditor({
  organizationId,
  documentId,
  onBack,
  initialSectionId = null,
}: Props) {
  const queryClient = useQueryClient()
  const [activeSection, setActiveSection] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [dirty, setDirty] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  /** Section the user asked for while the open one has unsaved changes. */
  const [pendingSelection, setPendingSelection] = useState<DocumentSection | null>(null)

  const { data: doc, isLoading, dataUpdatedAt } = useQuery({
    queryKey: ['document', organizationId, documentId],
    queryFn: () => getDocument(organizationId, documentId),
  })

  /**
   * A decision taken while this section is open, awaiting the refetch it
   * triggered.
   *
   * `take_generated` rewrites the body server-side, but `draft` still holds
   * the text the decision just discarded. Left alone the pane shows the
   * rejected words, and the next keystroke makes them dirty and therefore
   * saveable -- silently undoing the decision the user had already been told
   * succeeded. `at` is the moment the decision was sent: the reseed waits for
   * document data newer than that, because `bodies` is still the pre-decision
   * text when the mutation resolves. Reseeding off whatever is in the cache at
   * that instant would put the stale body back.
   */
  const [reseed, setReseed] = useState<{ sectionId: string; at: number } | null>(null)

  const { data: history } = useQuery({
    queryKey: ['document-history', organizationId, documentId],
    queryFn: () => getDocumentHistory(organizationId, documentId),
    enabled: showHistory,
  })

  const slices = useMemo(
    () =>
      doc
        ? sliceSections(doc.merged_content, doc.sections)
        : { bodies: {} as Record<string, string>, unmatched: [] as string[] },
    [doc]
  )
  const bodies = slices.bodies
  const resolve = useResolveSection(organizationId, documentId)

  /**
   * Choose the section to open.
   *
   * The reader's anchor first — that is what the user was looking at when they
   * pressed Edit. Then the first section needing a decision, which is what a
   * reader comes to the editor for after a regeneration.
   *
   * The `hasBody` test on the anchor is the part that matters. The top of a
   * generated document is its H1 — "Statement of Applicability" — a heading
   * whose entire content is its subsections, so it has no body of its own.
   * Anchoring there is correct for the reader and useless for the editor: it
   * opens an empty box on a document with seventy-one sections of text. An
   * anchor with nothing to edit is not an anchor, so it falls through. A
   * genuinely empty section can still be opened deliberately from the outline.
   */
  useEffect(() => {
    if (!doc || activeSection) return
    const hasBody = (s: DocumentSection) => (bodies[s.section_id] ?? '').trim().length > 0
    const ordered = [...doc.sections].sort((a, b) => a.ordinal - b.ordinal)
    const anchored = initialSectionId
      ? doc.sections.find((s) => s.section_id === initialSectionId)
      : undefined
    const conflict = ordered.find((s) => s.status === 'conflict')
    const firstWithBody = ordered.find(hasBody)
    const first =
      (anchored && hasBody(anchored) ? anchored : undefined) ??
      conflict ??
      firstWithBody ??
      anchored ??
      ordered[0]
    if (first) {
      setActiveSection(first.section_id)
      setDraft(bodies[first.section_id] ?? '')
      setDirty(false)
    }
  }, [doc, activeSection, bodies, initialSectionId])

  useEffect(() => {
    // Only once the document query has produced data newer than the decision.
    if (!reseed || dataUpdatedAt <= reseed.at) return
    const next = bodies[reseed.sectionId]
    // `undefined` means the section is gone -- `retire` removes the row. There
    // is no body to show and nothing to reseed; the outline moves on.
    if (next !== undefined && reseed.sectionId === activeSection) {
      setDraft(next)
      setDirty(false)
    }
    setReseed(null)
  }, [reseed, dataUpdatedAt, bodies, activeSection])

  const saveMutation = useMutation({
    mutationFn: (payload: { sectionId: string; content: string }) =>
      saveSection(organizationId, documentId, payload.sectionId, payload.content),
    onSuccess: (result) => {
      setDirty(false)
      queryClient.invalidateQueries({ queryKey: ['document', organizationId, documentId] })
      // The reader renders from the preview, not from `merged_content`, so a
      // save that skipped this key left the reader showing the old text and,
      // with it, the old status callout for the section just edited.
      queryClient.invalidateQueries({
        queryKey: ['document-preview', organizationId, documentId],
      })
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

  /**
   * Move to another section, asking first if the current one has unsaved work.
   *
   * The confirmation is in the page rather than `window.confirm`. A native
   * dialog blocks the whole renderer while it is up — it cannot be styled or
   * themed, it is unreachable to the browser automation this feature is
   * verified with, and it gives the user no way to look at what they are about
   * to discard. Here the pending target is held in state and the outline says
   * what will be lost until the user answers.
   */
  function selectSection(section: DocumentSection) {
    if (section.section_id === activeSection) return
    if (dirty) {
      setPendingSelection(section)
      return
    }
    openSection(section)
  }

  function openSection(section: DocumentSection) {
    setPendingSelection(null)
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
          ← Back to document
        </button>
        <div className="doc-editor-title">
          <h1>{doc.title}</h1>
          <span className="doc-editor-meta">
            v{doc.generation_version}
            {doc.catalog_version && ` · SCF ${doc.catalog_version}`}
          </span>
        </div>
        <div className="doc-editor-actions">
          <span className={`doc-lifecycle-pill status-${doc.lifecycle_status}`}>
            {LIFECYCLE_LABELS[doc.lifecycle_status]}
          </span>
          {/* The backend lists the advancing transition first in every state
              (lifecycle.VALID_TRANSITIONS), so index 0 is the forward move --
              Submit for Review, Approve, Publish -- and earns the primary
              treatment. Backward moves (Return to Draft, Request Changes) stay
              secondary, matching every other action bar in the app. */}
          {doc.available_transitions.map((t, i) => (
            <button
              key={t.to_status}
              type="button"
              className={i === 0 ? 'btn-primary' : 'btn-secondary'}
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
            {conflicts.length} section{conflicts.length === 1 ? '' : 's'}{' '}
            {conflicts.length === 1 ? 'needs' : 'need'} your decision.
          </strong>{' '}
          Your text was kept in each, but it was written against an earlier
          scope. Open one to keep it or take the generated version — nothing was
          overwritten either way.
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

      {pendingSelection && (
        <div className="doc-notice doc-notice-warning doc-decision-confirm">
          <span>
            “{current?.heading_text ?? 'This section'}” has unsaved changes.
            Discard them and open “{pendingSelection.heading_text}”?
          </span>
          <div className="doc-decision-actions">
            <button
              type="button"
              className="btn-danger"
              onClick={() => openSection(pendingSelection)}
            >
              Discard and switch
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setPendingSelection(null)}
            >
              Keep editing
            </button>
          </div>
        </div>
      )}

      <div className="doc-editor-body">
        {/* ── Outline ─────────────────────────────────────────────────── */}
        <aside className="doc-outline">
          <div className="doc-outline-head">
            <h3>Sections</h3>
            {/* Same count, same words as the reader's rail and the masthead —
                the three are one system. See OutlineCount. */}
            <OutlineCount
              sectionCount={doc.section_count}
              retiringCount={doc.pending_retirement_count}
            />
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
                <div className="doc-section-head-actions">
                  {/* A conflicted or retiring section can be settled from here
                      rather than by retyping your own words over themselves,
                      which was the only exit this screen used to offer. */}
                  <SectionDecision
                    section={current}
                    pending={resolve.isPending}
                    disabled={readOnly}
                    variant="bar"
                    onResolve={(choice) =>
                      resolve.mutate(
                        { sectionId: current.section_id, choice },
                        {
                          onSuccess: () =>
                            setReseed({
                              sectionId: current.section_id,
                              at: Date.now(),
                            }),
                        }
                      )
                    }
                  />
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
              </div>

              {/* The body could not be located in the document. That is a
                  mapping failure, not an empty section, and showing a blank box
                  would invite the user to "fix" it by typing — which would then
                  overwrite whatever the section really holds. */}
              {slices.unmatched.includes(current.section_id) && (
                <div className="doc-notice doc-notice-warning doc-editor-guard">
                  <strong>This section's text could not be located.</strong> Its
                  heading is not in the document as stored, so there is nothing
                  safe to load here. Saving would replace the section's real
                  content — regenerate the document or edit it as Markdown
                  instead.
                </div>
              )}

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
                readOnly={readOnly || slices.unmatched.includes(current.section_id)}
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
