/**
 * ImportRunHistory — table of platform catalog import runs
 * (GET /api/admin/catalog/upgrade/runs), newest first per backend ordering.
 */
import type { PlatformImportRunSummary, PlatformRunStatus } from '../../types/catalogUpgrade'

/** Status → existing badge classes so both themes render correctly. */
export function RunStatusBadge({ status }: { status: PlatformRunStatus }) {
  switch (status) {
    case 'applied':
      return <span className="badge badge-active">Applied</span>
    case 'staged':
      return <span className="badge badge-good">Staged</span>
    case 'staging':
    case 'applying':
      return <span className="badge badge-viewer">{status === 'staging' ? 'Staging…' : 'Applying…'}</span>
    case 'blocked':
      return <span className="badge badge-warning">Blocked</span>
    case 'failed':
      return <span className="badge badge-revoked">Failed</span>
    case 'cancelled':
      return <span className="badge badge-viewer">Cancelled</span>
    case 'reverted':
      return <span className="badge badge-warning">Reverted</span>
    default:
      return <span className="badge badge-viewer">{status}</span>
  }
}

function formatDateTime(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString()
}

interface ImportRunHistoryProps {
  runs: PlatformImportRunSummary[]
  total: number
  activeRunId?: string | null
  onSelect: (runId: string) => void
}

export default function ImportRunHistory({ runs, total, activeRunId, onSelect }: ImportRunHistoryProps) {
  return (
    <div className="surface-bench" style={{ padding: '1.25rem 1.5rem', marginTop: '1.5rem' }}>
      <div className="platform-section-label">
        Import history{total > 0 ? ` — ${total} run${total === 1 ? '' : 's'}` : ''}
      </div>
      {runs.length === 0 ? (
        <p style={{ color: 'var(--muted)' }}>No catalog upgrade runs yet.</p>
      ) : (
        <div className="api-keys-table-container">
          <table className="api-key-table">
            <thead>
              <tr>
                <th>From</th>
                <th>To</th>
                <th>Status</th>
                <th>Started by</th>
                <th>Created</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {runs.map(run => (
                <tr
                  key={run.id}
                  onClick={() => onSelect(run.id)}
                  className={run.id === activeRunId ? 'platform-board-row-selected' : undefined}
                  style={{ cursor: 'pointer' }}
                >
                  <td className="platform-mono-id">{run.from_version || '—'}</td>
                  <td className="platform-mono-id strong">{run.to_version || '—'}</td>
                  <td><RunStatusBadge status={run.status} /></td>
                  <td>{run.created_by || '—'}</td>
                  <td>{formatDateTime(run.created_at)}</td>
                  <td>{formatDateTime(run.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {total > runs.length && (
        <p style={{ color: 'var(--muted)', fontSize: '0.85rem', marginTop: '0.5rem' }}>
          Showing {runs.length} of {total} runs.
        </p>
      )}
    </div>
  )
}
