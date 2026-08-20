/**
 * OrgApplyProgress — in-flight state of an org reconciliation run while the
 * wizard polls the run detail (applying or rolling back).
 */
import type { OrgRunStatus } from '../../types/catalogUpgrade'

interface OrgApplyProgressProps {
  status: OrgRunStatus
  toVersion?: string | null
}

export default function OrgApplyProgress({ status, toVersion }: OrgApplyProgressProps) {
  const message =
    status === 'rolling_back'
      ? `Rolling back — restoring snapshotted rows to their pre-${toVersion || 'upgrade'} state…`
      : `Applying catalog ${toVersion || 'upgrade'} — re-materialising scope and executing planned actions…`
  return (
    <div style={{ textAlign: 'center', padding: '2rem' }}>
      <div className="loading-spinner" />
      <p style={{ color: 'var(--muted)', marginTop: '0.75rem' }}>{message}</p>
    </div>
  )
}
