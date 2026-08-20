/**
 * CatalogChangelogPage — read-only org changelog of applied catalog changes
 * (GET /api/organizations/{org_id}/catalog-changelog, viewer-visible).
 *
 * Lists what each reconciled catalog version changed for this organisation,
 * plus a short explainer of the deprecated badge that appears on retired
 * controls throughout the app.
 */
import { useCallback, useEffect, useState } from 'react'
import { getOrgCatalogChangelog } from '../data/catalogUpgradeApi'
import type { ChangelogEntry } from '../types/catalogUpgrade'
import DeprecatedBadge from './DeprecatedBadge'
import { ORG_RECONCILIATION_DOC_URL } from './CatalogVersionCard'

export const PLATFORM_UPGRADE_DOC_URL =
  'https://github.com/MarkAC007/scf-controls-platform/blob/main/docs/user/platform-catalog-upgrade.md'

const PAGE_SIZE = 50

const CHANGE_CLASS_LABELS: Record<string, { label: string; className: string }> = {
  added: { label: 'Added', className: 'badge badge-success' },
  changed: { label: 'Changed', className: 'badge badge-good' },
  deprecated: { label: 'Deprecated', className: 'badge badge-warning' },
  resurrected: { label: 'Reactivated', className: 'badge badge-good' },
  unchanged: { label: 'Unchanged', className: 'badge' },
}

const ENTITY_LABELS: Record<string, string> = {
  controls: 'Control',
  domains: 'Domain',
  evidence: 'Evidence',
  assessment_objectives: 'Assessment objective',
  capability_themes: 'Capability theme',
  framework_mappings: 'Framework mapping',
}

interface CatalogChangelogPageProps {
  organizationId: string
}

export default function CatalogChangelogPage({ organizationId }: CatalogChangelogPageProps) {
  const [entries, setEntries] = useState<ChangelogEntry[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadPage = useCallback(
    async (offset: number) => {
      const data = await getOrgCatalogChangelog(organizationId, { limit: PAGE_SIZE, offset })
      setTotal(data.total)
      setEntries((prev) => (offset === 0 ? data.entries : [...prev, ...data.entries]))
    },
    [organizationId]
  )

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setEntries([])
    loadPage(0)
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load changelog')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [loadPage])

  const handleLoadMore = async () => {
    setLoadingMore(true)
    try {
      await loadPage(entries.length)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load changelog')
    } finally {
      setLoadingMore(false)
    }
  }

  return (
    <div className="surface-bench" style={{ padding: '2rem' }}>
      <h2>Catalog Changelog</h2>
      <p style={{ color: 'var(--muted)', maxWidth: 720 }}>
        Changes applied to your organisation&apos;s control catalog by each reconciled
        SCF catalog version. This log is read-only; reconciliation is performed by
        platform administrators.
      </p>

      {/* Deprecated-badge explainer (plan §4.6) */}
      <div
        className="card"
        style={{ margin: '1.25rem 0', padding: '1rem 1.25rem', maxWidth: 720 }}
        data-testid="deprecated-badge-explainer"
      >
        <h3 style={{ marginTop: 0 }}>
          What does{' '}
          <DeprecatedBadge catalog_status="deprecated" />{' '}
          mean?
        </h3>
        <p style={{ fontSize: '0.9rem', marginBottom: '0.5rem' }}>
          A deprecated badge marks a control that has been retired from the SCF
          catalog. Your existing data on it — scoping decisions, assessments,
          evidence, engagement scope — is never deleted and keeps rendering
          everywhere it did before. New scoping of a retired control is refused;
          where the SCF names a successor control, the badge shows it.
        </p>
        <p style={{ fontSize: '0.85rem', color: 'var(--muted)', marginBottom: 0 }}>
          Details:{' '}
          <a href={ORG_RECONCILIATION_DOC_URL} target="_blank" rel="noreferrer">
            organisation catalog reconciliation guide
          </a>
          {' · '}
          <a href={PLATFORM_UPGRADE_DOC_URL} target="_blank" rel="noreferrer">
            platform catalog upgrade runbook
          </a>
          {' '}(for platform administrators).
        </p>
      </div>

      {loading ? (
        <p style={{ color: 'var(--muted)' }}>Loading changelog…</p>
      ) : error ? (
        <div className="error-message">{error}</div>
      ) : entries.length === 0 ? (
        <p style={{ color: 'var(--muted)' }} data-testid="changelog-empty">
          No catalog changes have been applied to your organisation yet.
        </p>
      ) : (
        <>
          <div style={{ overflowX: 'auto' }}>
            <table className="cp-controls-table" data-testid="changelog-table">
              <thead>
                <tr>
                  <th>Version</th>
                  <th>Applied</th>
                  <th>Type</th>
                  <th>Change</th>
                  <th>Identifier</th>
                  <th>Name</th>
                  <th>Summary</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry, idx) => {
                  const change = CHANGE_CLASS_LABELS[entry.change_class] ?? {
                    label: entry.change_class,
                    className: 'badge',
                  }
                  return (
                    <tr key={`${entry.version}-${entry.entity}-${entry.key}-${idx}`}>
                      <td style={{ whiteSpace: 'nowrap', fontWeight: 600 }}>{entry.version}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        {entry.applied_at ? new Date(entry.applied_at).toLocaleDateString() : '—'}
                      </td>
                      <td>{ENTITY_LABELS[entry.entity] ?? entry.entity}</td>
                      <td>
                        <span className={change.className}>{change.label}</span>
                      </td>
                      <td style={{ fontFamily: 'var(--font-mono, monospace)', whiteSpace: 'nowrap' }}>
                        {entry.key}
                      </td>
                      <td>{entry.name ?? '—'}</td>
                      <td>{entry.summary ?? '—'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <div style={{ marginTop: '0.75rem', display: 'flex', alignItems: 'center', gap: '1rem' }}>
            <span style={{ fontSize: '0.85rem', color: 'var(--muted)' }}>
              Showing {entries.length} of {total}
            </span>
            {entries.length < total && (
              <button className="btn" onClick={handleLoadMore} disabled={loadingMore}>
                {loadingMore ? 'Loading…' : 'Load more'}
              </button>
            )}
          </div>
        </>
      )}
    </div>
  )
}
