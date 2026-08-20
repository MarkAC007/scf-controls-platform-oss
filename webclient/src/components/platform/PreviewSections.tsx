/**
 * PreviewSections — the non-decision sections of a per-org reconciliation
 * preview (plan §4.3): (a) scope additions per framework, (c) changed
 * controls in scope with re-assessment flags, (d) orphan report banner,
 * (e) first-reconciliation framework-selection confirmation.
 *
 * Section (b) — the deprecated-with-org-data decision table — lives in
 * ActionRadioTable and is composed between (a) and (c) by the wizard.
 */
import type {
  ChangedInScopeItem,
  FieldChange,
  FrameworkConfirmation,
  OrphanReport,
  ScopeAdditionsPreview,
} from '../../types/catalogUpgrade'

/** Compact single-line rendering of a diff value (old or new side). */
function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  const text = typeof value === 'string' ? value : JSON.stringify(value)
  return text.length > 80 ? `${text.slice(0, 77)}…` : text
}

function FieldChangeList({ fields }: { fields: Record<string, FieldChange> }) {
  const fieldNames = Object.keys(fields)
  if (fieldNames.length === 0) {
    return <span style={{ color: 'var(--muted)' }}>—</span>
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
      {fieldNames.map(fieldName => (
        <div key={fieldName} style={{ fontSize: '0.82rem' }}>
          <strong>{fieldName}:</strong>{' '}
          <span style={{ color: 'var(--muted)', textDecoration: 'line-through' }}>
            {formatValue(fields[fieldName].old)}
          </span>
          {' → '}
          <span>{formatValue(fields[fieldName].new)}</span>
        </div>
      ))}
    </div>
  )
}

/** (a) new controls that will be added to the org's scope. */
export function ScopeAdditionsSection({ additions }: { additions: ScopeAdditionsPreview }) {
  return (
    <section style={{ marginBottom: '1.25rem' }}>
      <h4 style={{ marginBottom: '0.5rem' }}>Scope additions</h4>
      {additions.in_scope.length === 0 ? (
        <p style={{ color: 'var(--muted)' }}>
          No new controls intersect this organisation's framework selections.
        </p>
      ) : (
        <>
          <p style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>
            These controls will be added to the organisation's scope.
          </p>
          <div className="api-keys-table-container">
            <table className="api-key-table">
              <thead>
                <tr>
                  <th>Control</th>
                  <th>Name</th>
                  <th>Frameworks</th>
                </tr>
              </thead>
              <tbody>
                {additions.in_scope.map(item => (
                  <tr key={item.scf_id}>
                    <td style={{ whiteSpace: 'nowrap', fontWeight: 500 }}>{item.scf_id}</td>
                    <td>{item.name || <span style={{ color: 'var(--muted)' }}>—</span>}</td>
                    <td>
                      <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                        {item.frameworks.map(framework => (
                          <span key={framework} className="badge badge-viewer">
                            {framework}
                          </span>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {additions.out_of_scope_count > 0 && (
        <p style={{ color: 'var(--muted)', fontSize: '0.85rem', marginTop: '0.5rem' }}>
          {additions.out_of_scope_count} new control
          {additions.out_of_scope_count === 1 ? '' : 's'} in frameworks this organisation has not
          selected (not added).
        </p>
      )}
    </section>
  )
}

/** (c) changed controls in the org's scope — informational only. */
export function ChangedInScopeSection({ changed }: { changed: ChangedInScopeItem[] }) {
  return (
    <section style={{ marginBottom: '1.25rem' }}>
      <h4 style={{ marginBottom: '0.5rem' }}>Changed controls in scope</h4>
      {changed.length === 0 ? (
        <p style={{ color: 'var(--muted)' }}>No selected controls changed in this upgrade.</p>
      ) : (
        <>
          <p style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>
            Informational — nothing is modified. Controls flagged below have existing assessment
            composites and should be re-assessed against the new wording.
          </p>
          <div className="api-keys-table-container">
            <table className="api-key-table">
              <thead>
                <tr>
                  <th>Control</th>
                  <th>Name</th>
                  <th>Changes</th>
                  <th>Re-assessment</th>
                </tr>
              </thead>
              <tbody>
                {changed.map(item => (
                  <tr key={item.scf_id}>
                    <td style={{ whiteSpace: 'nowrap', fontWeight: 500 }}>{item.scf_id}</td>
                    <td>{item.name || <span style={{ color: 'var(--muted)' }}>—</span>}</td>
                    <td>
                      <FieldChangeList fields={item.fields} />
                    </td>
                    <td>
                      {item.reassessment_recommended ? (
                        <span className="badge badge-warning">Re-assessment recommended</span>
                      ) : (
                        <span style={{ color: 'var(--muted)' }}>—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}

/** (d) orphan report — report-only, never blocks the reconciliation. */
export function OrphanReportSection({ orphans }: { orphans: OrphanReport }) {
  if (orphans.count === 0) return null
  return (
    <section
      role="note"
      style={{
        border: '1px solid var(--warning, #f59e0b)',
        borderRadius: '6px',
        padding: '0.75rem 1rem',
        marginBottom: '1.25rem',
      }}
    >
      <strong>Orphan report.</strong>{' '}
      <span style={{ color: 'var(--muted)' }}>
        {orphans.count} pre-existing row{orphans.count === 1 ? '' : 's'} reference invalid catalog
        keys. Report-only — this never blocks the reconciliation.
      </span>
      <ul style={{ margin: '0.5rem 0 0', paddingLeft: '1.25rem', fontSize: '0.85rem' }}>
        {orphans.items.map(item => (
          <li key={`${item.source_table}-${item.key}`}>
            <strong>{item.key}</strong> in {item.source_table}
            {item.detail && <span style={{ color: 'var(--muted)' }}> — {item.detail}</span>}
          </li>
        ))}
      </ul>
    </section>
  )
}

interface FrameworkConfirmSectionProps {
  confirmation: FrameworkConfirmation
  confirmed: boolean
  disabled?: boolean
  onConfirmedChange: (confirmed: boolean) => void
}

/** (e) first-reconciliation framework confirmation (M3 backfill is heuristic). */
export function FrameworkConfirmSection({
  confirmation,
  confirmed,
  disabled,
  onConfirmedChange,
}: FrameworkConfirmSectionProps) {
  if (!confirmation.required) return null
  return (
    <section
      style={{
        border: '1px solid var(--border, #d1d5db)',
        borderRadius: '6px',
        padding: '0.75rem 1rem',
        marginBottom: '1.25rem',
      }}
    >
      <h4 style={{ marginBottom: '0.5rem' }}>Confirm framework selections</h4>
      <p style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>
        This is the organisation's first reconciliation. Its framework list was reconstructed from
        free-text scoping reasons and must be confirmed before the reconciliation can be applied.
      </p>
      <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap', marginBottom: '0.75rem' }}>
        {confirmation.selections.map(selection => (
          <span
            key={selection.framework_id}
            className={selection.active ? 'badge badge-active' : 'badge badge-viewer'}
            title={`source: ${selection.source}`}
          >
            {selection.framework_id}
          </span>
        ))}
      </div>
      <label style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <input
          type="checkbox"
          aria-label="Confirm framework selections"
          checked={confirmed}
          disabled={disabled}
          onChange={e => onConfirmedChange(e.target.checked)}
        />
        These framework selections are correct for this organisation.
      </label>
    </section>
  )
}
