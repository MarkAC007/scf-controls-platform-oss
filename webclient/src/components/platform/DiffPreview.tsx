/**
 * DiffPreview — paginated global diff for a staged run
 * (GET /api/admin/catalog/upgrade/runs/{id}/diff).
 *
 * Entity tabs across the six catalog entity types, change-class filters,
 * and field-level old → new rendering for changed/resurrected rows.
 */
import { useEffect, useState } from 'react'
import { toast } from 'react-hot-toast'
import { getCatalogUpgradeDiff } from '../../data/catalogUpgradeApi'
import type {
  CatalogEntityType,
  ChangeClass,
  DiffItem,
  DiffPageResponse,
  DiffSummary,
} from '../../types/catalogUpgrade'
import { CATALOG_ENTITY_TYPES, CHANGE_CLASSES } from '../../types/catalogUpgrade'

const ENTITY_LABELS: Record<CatalogEntityType, string> = {
  controls: 'Controls',
  domains: 'Domains',
  evidence: 'Evidence',
  assessment_objectives: 'Assessment Objectives',
  capability_themes: 'Capability Themes',
  framework_mappings: 'Framework Mappings',
}

const CLASS_LABELS: Record<ChangeClass, string> = {
  added: 'Added',
  changed: 'Changed',
  deprecated: 'Deprecated',
  resurrected: 'Resurrected',
  unchanged: 'Unchanged',
}

const PAGE_SIZE = 50

/** Compact single-line rendering of a diff value (old or new side). */
function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  const text = typeof value === 'string' ? value : JSON.stringify(value)
  return text.length > 80 ? `${text.slice(0, 77)}…` : text
}

function ChangeClassBadge({ changeClass }: { changeClass: ChangeClass }) {
  switch (changeClass) {
    case 'added':
      return <span className="badge badge-active">Added</span>
    case 'changed':
      return <span className="badge badge-warning">Changed</span>
    case 'deprecated':
      return <span className="badge badge-revoked">Deprecated</span>
    case 'resurrected':
      return <span className="badge badge-good">Resurrected</span>
    default:
      return <span className="badge badge-viewer">Unchanged</span>
  }
}

function DiffItemDetail({ item }: { item: DiffItem }) {
  if (item.change_class === 'changed' || item.change_class === 'resurrected') {
    const fieldNames = Object.keys(item.fields)
    if (fieldNames.length === 0) {
      return <span style={{ color: 'var(--muted)' }}>Re-activated, no field changes</span>
    }
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        {fieldNames.map(fieldName => (
          <div key={fieldName} style={{ fontSize: '0.82rem' }}>
            <strong>{fieldName}:</strong>{' '}
            <span style={{ color: 'var(--muted)', textDecoration: 'line-through' }}>
              {formatValue(item.fields[fieldName].old)}
            </span>
            {' → '}
            <span>{formatValue(item.fields[fieldName].new)}</span>
          </div>
        ))}
      </div>
    )
  }
  if (item.change_class === 'added') {
    const fieldCount = Object.keys(item.data).length
    return <span style={{ color: 'var(--muted)' }}>New entry ({fieldCount} fields)</span>
  }
  if (item.change_class === 'deprecated') {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
        {item.superseded_by ? (
          <span className="badge badge-good">Superseded by {item.superseded_by}</span>
        ) : (
          <span style={{ color: 'var(--muted)' }}>No successor paired</span>
        )}
        {item.suggestions.map(suggestion => (
          <span
            key={suggestion.scf_id}
            className="badge badge-viewer"
            title={suggestion.name || suggestion.scf_id}
          >
            {suggestion.scf_id} · {Math.round(suggestion.score * 100)}%
          </span>
        ))}
      </div>
    )
  }
  return <span style={{ color: 'var(--muted)' }}>—</span>
}

interface DiffPreviewProps {
  runId: string
  diffSummary?: DiffSummary | null
}

export default function DiffPreview({ runId, diffSummary }: DiffPreviewProps) {
  const [entity, setEntity] = useState<CatalogEntityType>('controls')
  const [changeClass, setChangeClass] = useState<ChangeClass | 'all'>('all')
  const [page, setPage] = useState(1)
  const [pageData, setPageData] = useState<DiffPageResponse | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getCatalogUpgradeDiff(runId, {
      entity,
      change_class: changeClass === 'all' ? undefined : changeClass,
      page,
      page_size: PAGE_SIZE,
    })
      .then(data => {
        if (!cancelled) setPageData(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          toast.error(err instanceof Error ? err.message : 'Failed to load diff')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [runId, entity, changeClass, page])

  const entityCounts = (entityType: CatalogEntityType): number | null => {
    const counts = diffSummary?.entities?.[entityType]
    if (!counts) return null
    return counts.added + counts.changed + counts.deprecated + counts.resurrected
  }

  const totalPages = pageData ? Math.max(1, Math.ceil(pageData.total / PAGE_SIZE)) : 1

  return (
    <div>
      {/* Entity tabs */}
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '0.75rem' }} role="tablist" aria-label="Diff entity">
        {CATALOG_ENTITY_TYPES.map(entityType => {
          const count = entityCounts(entityType)
          return (
            <button
              key={entityType}
              role="tab"
              aria-selected={entity === entityType}
              className={entity === entityType ? 'btn btn-primary btn-sm' : 'btn btn-secondary btn-sm'}
              onClick={() => {
                setEntity(entityType)
                setPage(1)
              }}
            >
              {ENTITY_LABELS[entityType]}
              {count !== null && ` (${count})`}
            </button>
          )
        })}
      </div>

      {/* Change-class filters */}
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '0.75rem' }}>
        <button
          className={changeClass === 'all' ? 'btn btn-primary btn-sm' : 'btn btn-secondary btn-sm'}
          onClick={() => {
            setChangeClass('all')
            setPage(1)
          }}
        >
          All changes
        </button>
        {CHANGE_CLASSES.map(cls => (
          <button
            key={cls}
            className={changeClass === cls ? 'btn btn-primary btn-sm' : 'btn btn-secondary btn-sm'}
            onClick={() => {
              setChangeClass(cls)
              setPage(1)
            }}
          >
            {CLASS_LABELS[cls]}
          </button>
        ))}
      </div>

      {loading && !pageData ? (
        <div style={{ textAlign: 'center', padding: '2rem' }}>
          <div className="loading-spinner" />
        </div>
      ) : pageData && pageData.items.length === 0 ? (
        <p style={{ color: 'var(--muted)', padding: '1rem 0' }}>
          No {changeClass === 'all' ? '' : `${CLASS_LABELS[changeClass].toLowerCase()} `}entries for{' '}
          {ENTITY_LABELS[entity].toLowerCase()}.
        </p>
      ) : pageData ? (
        <div className="api-keys-table-container" style={{ opacity: loading ? 0.6 : 1 }}>
          <table className="api-key-table">
            <thead>
              <tr>
                <th>Key</th>
                <th>Name</th>
                <th>Change</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {pageData.items.map(item => (
                <tr key={`${item.entity}-${item.change_class}-${item.key}`}>
                  <td style={{ whiteSpace: 'nowrap', fontWeight: 500 }}>{item.key}</td>
                  <td>{item.name || <span style={{ color: 'var(--muted)' }}>—</span>}</td>
                  <td><ChangeClassBadge changeClass={item.change_class} /></td>
                  <td><DiffItemDetail item={item} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {/* Pagination */}
      {pageData && pageData.total > PAGE_SIZE && (
        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', marginTop: '0.75rem' }}>
          <button
            className="btn btn-secondary btn-sm"
            disabled={page <= 1 || loading}
            onClick={() => setPage(p => p - 1)}
          >
            Previous
          </button>
          <span style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>
            Page {page} of {totalPages} · {pageData.total} entries
          </span>
          <button
            className="btn btn-secondary btn-sm"
            disabled={page >= totalPages || loading}
            onClick={() => setPage(p => p + 1)}
          >
            Next
          </button>
        </div>
      )}
    </div>
  )
}
