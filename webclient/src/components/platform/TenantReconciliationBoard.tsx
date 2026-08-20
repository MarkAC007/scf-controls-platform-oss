/**
 * TenantReconciliationBoard — Platform → Tenants (plan §4.6).
 *
 * The reconciliation board from GET /api/admin/catalog/tenants: one row per
 * organisation with its reconciled catalog version, eligibility against the
 * platform version, and any active reconciliation run. Clicking a row opens
 * the per-org preview/apply/rollback wizard. This page doubles as the org
 * picker for platform admins (the existing org switcher is consultant-only).
 *
 * Access: gated on is_platform_admin — App only routes here for admins, and
 * the page re-checks so a direct render shows nothing useful.
 */
import { useCallback, useEffect, useState } from 'react'
import { toast } from 'react-hot-toast'
import { useAuth } from '../../contexts/AuthContext'
import { getTenantsBoard } from '../../data/catalogUpgradeApi'
import type { OrgRunStatus, TenantBoardRow, TenantsBoardResponse } from '../../types/catalogUpgrade'
import OrgReconciliationWizard from './OrgReconciliationWizard'

/** Status → existing badge classes so both themes render correctly. */
export function OrgRunStatusBadge({ status }: { status: OrgRunStatus }) {
  switch (status) {
    case 'applied':
      return <span className="badge badge-active">Applied</span>
    case 'previewed':
      return <span className="badge badge-good">Previewed</span>
    case 'applying':
    case 'rolling_back':
      return (
        <span className="badge badge-viewer">
          {status === 'applying' ? 'Applying…' : 'Rolling back…'}
        </span>
      )
    case 'failed':
      return <span className="badge badge-revoked">Failed</span>
    case 'rolled_back':
      return <span className="badge badge-warning">Rolled back</span>
    case 'cancelled':
      return <span className="badge badge-viewer">Cancelled</span>
    default:
      return <span className="badge badge-viewer">{status}</span>
  }
}

function TenantBoard() {
  const [board, setBoard] = useState<TenantsBoardResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [selectedOrg, setSelectedOrg] = useState<TenantBoardRow | null>(null)

  const loadBoard = useCallback(async () => {
    try {
      setBoard(await getTenantsBoard())
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to load the tenants board')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadBoard()
  }, [loadBoard])

  return (
    <div>
      <h2 style={{ marginBottom: '1rem' }}>Tenants</h2>
      <div className="surface-bench" style={{ padding: '1.25rem 1.5rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '0.75rem' }}>
          <h3 className="bench-header" style={{ margin: 0 }}>
            <span className="container-title">Reconciliation board</span>
          </h3>
          {board?.platform_catalog_version && (
            <span style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>
              Platform catalog: <strong>{board.platform_catalog_version}</strong>
            </span>
          )}
        </div>

        {loading ? (
          <div style={{ textAlign: 'center', padding: '2rem' }}>
            <div className="loading-spinner" />
          </div>
        ) : !board || board.tenants.length === 0 ? (
          <p style={{ color: 'var(--muted)' }}>No tenant organisations found.</p>
        ) : (
          <div className="api-keys-table-container">
            <table className="api-key-table">
              <thead>
                <tr>
                  <th>Organisation</th>
                  <th>Reconciled version</th>
                  <th>Last reconciled</th>
                  <th>Eligibility</th>
                  <th>Active run</th>
                </tr>
              </thead>
              <tbody>
                {board.tenants.map(tenant => (
                  <tr
                    key={tenant.organization_id}
                    onClick={() => setSelectedOrg(tenant)}
                    style={{
                      cursor: 'pointer',
                      background:
                        tenant.organization_id === selectedOrg?.organization_id
                          ? 'var(--bg-tertiary)'
                          : undefined,
                    }}
                  >
                    <td style={{ fontWeight: 500 }}>{tenant.organization_name}</td>
                    <td>
                      {tenant.reconciled_catalog_version || (
                        <span style={{ color: 'var(--muted)' }}>—</span>
                      )}
                    </td>
                    <td>
                      {tenant.last_reconciled_at ? (
                        new Date(tenant.last_reconciled_at).toLocaleString()
                      ) : (
                        <span style={{ color: 'var(--muted)' }}>Never</span>
                      )}
                    </td>
                    <td>
                      {tenant.eligible ? (
                        <span className="badge badge-warning">Upgrade available</span>
                      ) : (
                        <span className="badge badge-active">Up to date</span>
                      )}
                    </td>
                    <td>
                      {tenant.active_run_status ? (
                        <OrgRunStatusBadge status={tenant.active_run_status} />
                      ) : (
                        <span style={{ color: 'var(--muted)' }}>—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {board && board.total > board.tenants.length && (
          <p style={{ color: 'var(--muted)', fontSize: '0.85rem', marginTop: '0.5rem' }}>
            Showing {board.tenants.length} of {board.total} organisations.
          </p>
        )}
      </div>

      {selectedOrg && (
        <OrgReconciliationWizard
          key={selectedOrg.organization_id}
          organizationId={selectedOrg.organization_id}
          organizationName={selectedOrg.organization_name}
          onRunSettled={loadBoard}
          onClose={() => setSelectedOrg(null)}
        />
      )}
    </div>
  )
}

export default function TenantReconciliationBoard() {
  const { isPlatformAdmin } = useAuth()

  if (!isPlatformAdmin) {
    return (
      <div className="surface-bench" style={{ padding: '2rem' }}>
        <h2>Access denied</h2>
        <p style={{ color: 'var(--muted)' }}>
          This page is only available to platform administrators.
        </p>
      </div>
    )
  }
  return <TenantBoard />
}
