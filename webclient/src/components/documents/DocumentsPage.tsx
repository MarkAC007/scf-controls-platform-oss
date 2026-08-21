/**
 * Documents workspace — the library, the generate panel, and the editor.
 *
 * Documents are grouped by type rather than listed flat. An ISMS is a set of
 * related artefacts, and a reader looking for "the access control policy"
 * navigates by kind first.
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  LIFECYCLE_LABELS,
  getDocGenSettings,
  listDocuments,
  type DocumentSummary,
  type LifecycleStatus,
} from '../../data/documentsApi'
import DocumentEditor from './DocumentEditor'
import GeneratePanel from './GeneratePanel'

interface DomainOption {
  identifier: string
  name: string
  controlCount: number
}

interface Props {
  organizationId: string
  /** Domains that have controls in scope, for the generate panel. */
  domains?: DomainOption[]
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

export default function DocumentsPage({
  organizationId,
  domains = [],
  onOpenSettings,
}: Props) {
  const [openDocument, setOpenDocument] = useState<string | null>(null)
  const [showGenerate, setShowGenerate] = useState(false)
  const [statusFilter, setStatusFilter] = useState('')

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
    return (
      <DocumentEditor
        organizationId={organizationId}
        documentId={openDocument}
        onBack={() => setOpenDocument(null)}
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
          <strong>
            {totalConflicts} section{totalConflicts === 1 ? '' : 's'} across your
            documents need a decision.
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
          domains={domains}
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
                    <span>{d.section_count} sections</span>
                    {d.catalog_version && <span>SCF {d.catalog_version}</span>}
                  </div>
                  <div className="document-card-flags">
                    {d.conflict_count > 0 && (
                      <span className="doc-section-badge status-conflict">
                        {d.conflict_count} need{d.conflict_count === 1 ? 's' : ''} a decision
                      </span>
                    )}
                    {d.edited_count > 0 && (
                      <span className="doc-section-badge status-human_preserved">
                        {d.edited_count} edited
                      </span>
                    )}
                    {d.is_derivative && (
                      <span className="doc-derivative-tag">Derivative</span>
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
