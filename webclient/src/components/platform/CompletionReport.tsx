/**
 * CompletionReport — post-apply summary for an applied run.
 *
 * Shows the per-entity change counts from the stored diff summary, plus the
 * artifact-re-extraction list (plan §4.6): controls whose text changed in this
 * upgrade, whose extracted document artifacts should be re-extracted against
 * the new wording.
 */
import { useEffect, useState } from 'react'
import { toast } from 'react-hot-toast'
import { getCatalogUpgradeDiff } from '../../data/catalogUpgradeApi'
import type {
  CatalogEntityType,
  DiffItem,
  PlatformImportRunDetail,
} from '../../types/catalogUpgrade'
import { CATALOG_ENTITY_TYPES } from '../../types/catalogUpgrade'

const ENTITY_LABELS: Record<CatalogEntityType, string> = {
  controls: 'Controls',
  domains: 'Domains',
  evidence: 'Evidence',
  assessment_objectives: 'Assessment Objectives',
  capability_themes: 'Capability Themes',
  framework_mappings: 'Framework Mappings',
}

interface CompletionReportProps {
  run: PlatformImportRunDetail
}

export default function CompletionReport({ run }: CompletionReportProps) {
  // Controls with field-level changes — the artifact-re-extraction candidates.
  const [changedControls, setChangedControls] = useState<DiffItem[] | null>(null)

  useEffect(() => {
    let cancelled = false
    getCatalogUpgradeDiff(run.id, { entity: 'controls', change_class: 'changed', page_size: 200 })
      .then(response => {
        if (!cancelled) setChangedControls(response.items)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          toast.error(err instanceof Error ? err.message : 'Failed to load changed controls')
          setChangedControls([])
        }
      })
    return () => {
      cancelled = true
    }
  }, [run.id])

  const summary = run.diff_summary

  return (
    <div>
      <p>
        Catalog upgraded from <strong>{run.from_version || 'unversioned'}</strong> to{' '}
        <strong>{run.to_version}</strong>
        {run.applied_at && <> on {new Date(run.applied_at).toLocaleString()}</>}. Tenant
        organisations can now reconcile to {run.to_version} from the Tenants board.
      </p>

      {summary && (
        <div className="api-keys-table-container" style={{ marginTop: '0.75rem' }}>
          <table className="api-key-table">
            <thead>
              <tr>
                <th>Entity</th>
                <th>Added</th>
                <th>Changed</th>
                <th>Deprecated</th>
                <th>Resurrected</th>
                <th>Unchanged</th>
              </tr>
            </thead>
            <tbody>
              {CATALOG_ENTITY_TYPES.map(entityType => {
                const counts = summary.entities?.[entityType]
                if (!counts) return null
                return (
                  <tr key={entityType}>
                    <td style={{ fontWeight: 500 }}>{ENTITY_LABELS[entityType]}</td>
                    <td>{counts.added}</td>
                    <td>{counts.changed}</td>
                    <td>{counts.deprecated}</td>
                    <td>{counts.resurrected}</td>
                    <td>{counts.unchanged}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <div style={{ marginTop: '1.25rem' }}>
        <h4 style={{ marginBottom: '0.25rem' }}>Artifact re-extraction</h4>
        {changedControls === null ? (
          <div style={{ textAlign: 'center', padding: '1rem' }}>
            <div className="loading-spinner" />
          </div>
        ) : changedControls.length === 0 ? (
          <p style={{ color: 'var(--muted)' }}>
            No control text changed in this upgrade — no document artifacts need re-extraction.
          </p>
        ) : (
          <>
            <p style={{ color: 'var(--muted)', fontSize: '0.875rem' }}>
              {changedControls.length} control{changedControls.length === 1 ? '' : 's'} changed in
              this upgrade. Document artifacts extracted against the old wording of these
              controls should be re-extracted:
            </p>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', marginTop: '0.5rem' }}>
              {changedControls.map(item => (
                <code
                  key={item.key}
                  title={item.name || item.key}
                  style={{
                    background: 'var(--bg-tertiary)',
                    padding: '2px 6px',
                    borderRadius: '4px',
                    fontSize: '0.8rem',
                  }}
                >
                  {item.key}
                </code>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
