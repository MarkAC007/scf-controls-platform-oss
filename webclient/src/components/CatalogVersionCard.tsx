/**
 * CatalogVersionCard — org-visible catalog version section for Org Settings.
 *
 * Shows which catalog version the organisation is reconciled to versus the
 * current platform catalog version (GET .../catalog-reconciliation/status),
 * with an upgrade-available banner when the platform has moved ahead.
 * Reconciliation itself is run by platform admins (tenant board) — this card
 * is informational for the org.
 */
import { useEffect, useState } from 'react'
import { getOrgReconciliationStatus } from '../data/catalogUpgradeApi'
import type { OrgCatalogStatusResponse } from '../types/catalogUpgrade'

export const ORG_RECONCILIATION_DOC_URL =
  'https://github.com/MarkAC007/scf-controls-platform/blob/main/docs/user/org-catalog-reconciliation.md'

/** Numeric segment-wise compare of catalog versions like "2026.2". */
export function compareCatalogVersions(a: string, b: string): number {
  const as = a.split('.').map((s) => parseInt(s, 10))
  const bs = b.split('.').map((s) => parseInt(s, 10))
  const len = Math.max(as.length, bs.length)
  for (let i = 0; i < len; i++) {
    const av = Number.isNaN(as[i]) || as[i] === undefined ? 0 : as[i]
    const bv = Number.isNaN(bs[i]) || bs[i] === undefined ? 0 : bs[i]
    if (av !== bv) return av - bv
  }
  return 0
}

/**
 * Banner logic: an upgrade is available only when the platform catalog
 * version is ahead of the org's reconciled version. The backend ``eligible``
 * flag is the authority; the version compare covers older payloads.
 */
export function isCatalogUpgradeAvailable(status: OrgCatalogStatusResponse | null | undefined): boolean {
  if (!status || !status.platform_catalog_version) return false
  if (status.eligible) return true
  if (!status.reconciled_catalog_version) return false
  return compareCatalogVersions(status.platform_catalog_version, status.reconciled_catalog_version) > 0
}

interface CatalogVersionCardProps {
  organizationId: string
}

export default function CatalogVersionCard({ organizationId }: CatalogVersionCardProps) {
  const [status, setStatus] = useState<OrgCatalogStatusResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [unavailable, setUnavailable] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setUnavailable(false)
    getOrgReconciliationStatus(organizationId)
      .then((data) => {
        if (!cancelled) setStatus(data)
      })
      .catch(() => {
        // Endpoint not deployed / not permitted — hide the section quietly.
        if (!cancelled) setUnavailable(true)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [organizationId])

  if (unavailable) return null

  const upgradeAvailable = isCatalogUpgradeAvailable(status)

  return (
    <div className="card" data-testid="catalog-version-card">
      <h2>Catalog Version</h2>

      {upgradeAvailable && (
        <div
          data-testid="catalog-upgrade-banner"
          role="status"
          style={{
            display: 'flex',
            alignItems: 'baseline',
            gap: '0.5rem',
            flexWrap: 'wrap',
            padding: '0.75rem 1rem',
            marginBottom: '1rem',
            borderRadius: 8,
            border: '1px solid rgba(245, 158, 11, 0.4)',
            background: 'rgba(245, 158, 11, 0.1)',
          }}
        >
          <strong>
            Catalog {status?.platform_catalog_version} available
          </strong>
          <span>
            — your organisation is reconciled to{' '}
            {status?.reconciled_catalog_version ?? 'an unversioned catalog'}. A platform
            administrator can reconcile your organisation to the new version.
          </span>
        </div>
      )}

      <div style={{ display: 'flex', gap: '2rem', flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: '0.8rem', color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Your catalog version
          </div>
          <div style={{ fontSize: '1.6rem', fontWeight: 600 }}>
            {loading ? '…' : status?.reconciled_catalog_version || 'Unversioned'}
          </div>
        </div>
        <div>
          <div style={{ fontSize: '0.8rem', color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Platform catalog version
          </div>
          <div style={{ fontSize: '1.6rem', fontWeight: 600 }}>
            {loading ? '…' : status?.platform_catalog_version || 'Unversioned'}
          </div>
        </div>
        {status?.last_reconciled_at && (
          <div>
            <div style={{ fontSize: '0.8rem', color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Last reconciled
            </div>
            <div style={{ fontSize: '1rem', fontWeight: 500, marginTop: '0.4rem' }}>
              {new Date(status.last_reconciled_at).toLocaleString()}
            </div>
          </div>
        )}
      </div>

      {!loading && !upgradeAvailable && status?.platform_catalog_version && (
        <p style={{ fontSize: '0.85rem', color: 'var(--muted)', marginTop: '0.75rem' }}>
          Your organisation is up to date with the platform catalog.
        </p>
      )}

      <p style={{ fontSize: '0.85rem', color: 'var(--muted)', marginTop: '0.75rem' }}>
        What reconciliation means for your scope, and how rollback works:{' '}
        <a href={ORG_RECONCILIATION_DOC_URL} target="_blank" rel="noreferrer">
          organisation catalog reconciliation guide
        </a>
        .
      </p>
    </div>
  )
}
