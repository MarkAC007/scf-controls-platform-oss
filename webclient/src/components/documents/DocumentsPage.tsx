/**
 * Documents workspace — the library, the generate panel, and the editor.
 *
 * Documents are grouped by type rather than listed flat. An ISMS is a set of
 * related artefacts, and a reader looking for "the access control policy"
 * navigates by kind first.
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  LIFECYCLE_LABELS,
  getDocGenSettings,
  listDocuments,
  type DocumentSummary,
  type LifecycleStatus,
} from '../../data/documentsApi'
import DocumentEditor from './DocumentEditor'
import DocumentReader from './DocumentReader'
import GeneratePanel from './GeneratePanel'

interface Props {
  organizationId: string
  onOpenSettings?: () => void
}

const TYPE_LABELS: Record<string, string> = {
  soa: 'Statement of Applicability',
  report: 'Reports and Registers',
  policy: 'Policies',
  procedure: 'Procedures',
  standard: 'Standards',
}

const STATUS_FILTERS: Array<{ value: string; label: string }> = [
  { value: '', label: 'All' },
  { value: 'draft', label: 'Draft' },
  { value: 'in_review', label: 'In Review' },
  { value: 'approved', label: 'Approved' },
  { value: 'published', label: 'Published' },
]

export const DOC_GEN_USER_DOC_URL =
  'https://docs.scfcontrolsplatform.app/user-guide/document-generation/'

/** Where the workspace is, as the URL records it. */
interface DocLocation {
  /** The open document's id, or null for the library list. */
  doc: string | null
  /** True when the URL asks for the editor rather than the reader. */
  editing: boolean
}

/**
 * Read the workspace's position out of the query string.
 *
 * `mode` is honoured only alongside a `doc`. "Editor, no document" is not a
 * place in this app, and treating it as one would open the editor on nothing.
 * Anything other than `mode=editor` — including a missing or misspelt value —
 * means the reader, which is the deliberate default everywhere else here.
 */
function readDocLocation(): DocLocation {
  const params = new URLSearchParams(window.location.search)
  const doc = params.get('doc')
  return { doc, editing: doc !== null && params.get('mode') === 'editor' }
}

export default function DocumentsPage({ organizationId, onOpenSettings }: Props) {
  // Seeded from the URL so a reload, a bookmark or a pasted link lands back on
  // the same document in the same mode instead of dumping the reader at the
  // top of the library. See the sync effect below for why this is parameter
  // sync and not a router.
  const [openDocument, setOpenDocument] = useState<string | null>(
    () => readDocLocation().doc,
  )
  const [editing, setEditing] = useState(() => readDocLocation().editing)
  const [editSection, setEditSection] = useState<string | null>(null)
  const [showGenerate, setShowGenerate] = useState(false)
  const [statusFilter, setStatusFilter] = useState('')

  /**
   * Keep `?doc=…&mode=…` in step with what is open.
   *
   * `replaceState`, not `pushState`: opening a document is a change of view
   * within this screen, not a destination. (Since #785 the evidence workspace
   * is URL-aware too, and EvidenceReview does push — one entry per evidence
   * item the user selects. Both screens write only their own parameters; see
   * `data/appUrl.ts` for who owns what.)
   */
  useEffect(() => {
    const url = new URL(window.location.href)
    if (openDocument) {
      url.searchParams.set('doc', openDocument)
      url.searchParams.set('mode', editing ? 'editor' : 'reader')
    } else {
      url.searchParams.delete('doc')
      url.searchParams.delete('mode')
    }
    // Guarded because replaceState on an unchanged URL still churns history
    // state, and this effect runs on every mount.
    if (url.toString() === window.location.href) return
    window.history.replaceState(window.history.state, '', url.toString())
  }, [openDocument, editing])

  /**
   * Browser Back and Forward.
   *
   * Fires when the user returns from elsewhere in their history, and — since
   * #785 added pushed entries for evidence-item selections — when they Back
   * through those. Either way the screen has to match the URL they arrived at
   * rather than whatever was mounted when they left. Reads only `doc`/`mode`,
   * which nothing outside this file writes.
   */
  useEffect(() => {
    const onPopState = () => {
      const next = readDocLocation()
      setOpenDocument(next.doc)
      setEditing(next.editing)
      setEditSection(null)
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  const { data: settings } = useQuery({
    queryKey: ['docgen-settings', organizationId],
    queryFn: () => getDocGenSettings(organizationId),
  })

  const { data: documents = [], isLoading } = useQuery({
    queryKey: ['documents', organizationId, statusFilter],
    queryFn: () => listDocuments(organizationId, { status: statusFilter || undefined }),
  })

  const grouped = useMemo(() => {
    const out = new Map<string, DocumentSummary[]>()
    for (const d of documents) {
      const list = out.get(d.document_type) ?? []
      list.push(d)
      out.set(d.document_type, list)
    }
    return [...out.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [documents])

  const totalConflicts = documents.reduce((sum, d) => sum + d.conflict_count, 0)

  if (openDocument) {
    // Reading is the default; editing is entered deliberately and returns
    // here rather than dumping you back out to the list.
    return editing ? (
      <DocumentEditor
        organizationId={organizationId}
        documentId={openDocument}
        initialSectionId={editSection}
        onBack={() => setEditing(false)}
      />
    ) : (
      <DocumentReader
        organizationId={organizationId}
        documentId={openDocument}
        onBack={() => {
          setOpenDocument(null)
          setEditing(false)
          setEditSection(null)
        }}
        onEdit={(sectionId) => {
          setEditSection(sectionId)
          setEditing(true)
        }}
      />
    )
  }

  return (
    <div className="documents-page">
      <div className="documents-header">
        <div>
          <h1>Documents</h1>
          <p className="documents-sub">
            ISMS documents generated from your scoped controls. Edit them here —
            regeneration keeps what you have written. The{' '}
            <a href={DOC_GEN_USER_DOC_URL} target="_blank" rel="noreferrer">
              generated documents guide
            </a>{' '}
            explains how edits survive a regeneration.
          </p>
        </div>
        <div className="documents-header-actions">
          <button
            type="button"
            className="btn-primary"
            disabled={!settings?.enabled}
            onClick={() => setShowGenerate(true)}
          >
            Generate documents
          </button>
        </div>
      </div>

      {settings && !settings.enabled && (
        <div className="doc-notice doc-notice-warning">
          <strong>Document generation is not enabled.</strong> An administrator
          can enable it in Org Settings, where the SCF licence position is
          confirmed.
          {onOpenSettings && (
            <button type="button" className="btn-link" onClick={onOpenSettings}>
              Open settings
            </button>
          )}
        </div>
      )}

      {totalConflicts > 0 && (
        <div className="doc-notice doc-notice-warning">
          {/* Verb agrees with the count, and the words are the reader's:
              DocumentReader says "1 section needs your decision." A library
              that says "1 of your documents need a decision" about the same
              fact is two defects, not one. */}
          <strong>
            {totalConflicts} section{totalConflicts === 1 ? '' : 's'} across your
            documents {totalConflicts === 1 ? 'needs' : 'need'} your decision.
          </strong>{' '}
          These are places where you edited a section and a regeneration also
          changed it. Your text is what the document currently says.
        </div>
      )}

      <div className="documents-filters">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            className={`doc-filter-chip ${statusFilter === f.value ? 'is-active' : ''}`}
            onClick={() => setStatusFilter(f.value)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {showGenerate && (
        <GeneratePanel
          organizationId={organizationId}
          onClose={() => setShowGenerate(false)}
        />
      )}

      {isLoading ? (
        <p className="doc-empty">Loading documents…</p>
      ) : documents.length === 0 ? (
        <div className="documents-empty">
          <h2>No documents yet</h2>
          <p>
            {settings?.enabled
              ? 'Generate a Statement of Applicability to start — it needs no AI and no additional licence position.'
              : 'Enable document generation in Org Settings to begin.'}
          </p>
        </div>
      ) : (
        grouped.map(([type, docs]) => (
          <section key={type} className="documents-group">
            <h2>{TYPE_LABELS[type] ?? type}</h2>
            <div className="documents-grid">
              {docs.map((d) => (
                <button
                  key={d.id}
                  type="button"
                  className="document-card"
                  onClick={() => setOpenDocument(d.id)}
                >
                  <div className="document-card-head">
                    <h3>{d.title}</h3>
                    <span className={`doc-lifecycle-pill status-${d.lifecycle_status}`}>
                      {LIFECYCLE_LABELS[d.lifecycle_status as LifecycleStatus]}
                    </span>
                  </div>
                  <div className="document-card-meta">
                    <span>v{d.generation_version}</span>
                    {d.domain_id && <span>{d.domain_id}</span>}
                    {/* Operative sections only — `section_count` excludes
                        pending_retirement rows (backend `_section_stats`). The
                        retiring tally is a separate badge below rather than a
                        number folded in here, because a Statement of
                        Applicability with 39 live sections and 30 retiring ones
                        is a 39-section document, not a 69-section one. */}
                    <span>
                      {d.section_count} section{d.section_count === 1 ? '' : 's'}
                    </span>
                    {d.catalog_version && <span>SCF {d.catalog_version}</span>}
                  </div>
                  <div className="document-card-flags">
                    {d.conflict_count > 0 && (
                      <span className="doc-section-badge status-conflict">
                        {d.conflict_count} section
                        {d.conflict_count === 1 ? '' : 's'}{' '}
                        {d.conflict_count === 1 ? 'needs' : 'need'} a decision
                      </span>
                    )}
                    {/* Same wording as OutlineCount's "+30 retiring", and the
                        same `undefined` guard for the same reason: the field
                        landed with the operative-only section_count, so an
                        older backend that omits it must read as "not reported"
                        rather than as zero. */}
                    {d.pending_retirement_count !== undefined &&
                      d.pending_retirement_count > 0 && (
                        <span
                          className="doc-section-badge status-pending_retirement"
                          title={
                            `${d.pending_retirement_count} section` +
                            `${d.pending_retirement_count === 1 ? ' is' : 's are'} ` +
                            'pending retirement, still in the document until you decide.'
                          }
                        >
                          +{d.pending_retirement_count} retiring
                        </span>
                      )}
                    {d.edited_count > 0 && (
                      <span className="doc-section-badge status-human_preserved">
                        {d.edited_count} edited
                      </span>
                    )}
                  </div>
                  {d.updated_at && (
                    <span className="document-card-when">
                      Updated {new Date(d.updated_at).toLocaleDateString('en-GB')}
                    </span>
                  )}
                </button>
              ))}
            </div>
          </section>
        ))
      )}
    </div>
  )
}
