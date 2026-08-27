/**
 * VersionCard — current platform catalog version (GET /api/catalog/status).
 *
 * The version authority is the import-run ledger: ``catalog_version`` is null
 * until the first upgrade run is applied (pre-feature seeds have no ledger row).
 */
import type { CatalogStatusExtended } from '../../types/catalogUpgrade'

interface VersionCardProps {
  status: CatalogStatusExtended | null
  loading?: boolean
}

export default function VersionCard({ status, loading = false }: VersionCardProps) {
  return (
    <div
      className="surface-bench"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '2rem',
        padding: '1.25rem 1.5rem',
        marginBottom: '1.5rem',
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
