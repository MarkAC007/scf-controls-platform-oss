/**
 * VersionCard — current platform catalog version (GET /api/catalog/status).
 *
 * The version authority is the import-run ledger: ``catalog_version`` is null
 * until the first upgrade run is applied (pre-feature seeds have no ledger row).
 */
import type {
  CatalogStatusExtended,
  FrameworkRegistryStatus,
} from '../../types/catalogUpgrade'

interface VersionCardProps {
  status: CatalogStatusExtended | null
  loading?: boolean
  /**
   * Framework registry stored for the live catalog version. Null when the
   * request failed — the line is then omitted rather than guessed at.
   */
  registry?: FrameworkRegistryStatus | null
}

/**
 * One line telling the admin whether the churn gate has the framework list
 * (focal-document identifiers) it needs for the live catalog version.
 */
function registryLine(registry: FrameworkRegistryStatus, fallbackVersion?: string | null): string {
  if (registry.present) {
    return (
      `Framework registry: ${registry.entries} frameworks, ` +
      `${registry.with_focal_document_id} with focal-document identifiers ` +
      `(${registry.source ?? 'unknown source'})`
    )
  }
  const version = registry.catalog_version || fallbackVersion || 'the live catalog'
  return (
    `Framework registry: not stored for ${version} — the next upgrade will try to ` +
    'recover it, or register the workbook from a blocked run'
  )
}

export default function VersionCard({ status, loading = false, registry }: VersionCardProps) {
  return (
    <div
      className="surface-bench"
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: '2rem',
        padding: '1.25rem 1.5rem',
        marginBottom: '1.5rem',
        flexWrap: 'wrap',
      }}
    >
      <div>
        <div className="platform-stat-label">
          Catalog version
        </div>
        <div className="platform-version-num">
          {loading ? '…' : status?.catalog_version || 'Unversioned'}
        </div>
        {!loading && !status?.catalog_version && (
          <div style={{ fontSize: '0.8rem', color: 'var(--muted)' }}>
            No upgrade has been applied yet — the seeded catalog predates version tracking.
          </div>
        )}
        {!loading && registry && (
          <div style={{ fontSize: '0.8rem', color: 'var(--muted)', marginTop: '0.35rem' }}>
            {registryLine(registry, status?.catalog_version)}
          </div>
        )}
      </div>
      <div>
        <div className="platform-stat-label">
          Controls
        </div>
        <div className="platform-version-num">
          {loading ? '…' : (status ? status.controls.toLocaleString() : '—')}
        </div>
      </div>
      <div>
        <div className="platform-stat-label">
          Seeded
        </div>
        <div style={{ marginTop: '0.35rem' }}>
          {loading ? (
            '…'
          ) : status?.seeded ? (
            <span className="badge badge-active">Yes</span>
          ) : (
            <span className="badge badge-revoked">No</span>
          )}
        </div>
      </div>
    </div>
  )
}
