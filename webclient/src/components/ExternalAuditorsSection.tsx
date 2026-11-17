// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useState } from 'react'
import { listOrgAuditors, disableOrgAuditor, type OrgAuditor } from '../data/apiClient'
import { useConfirm } from '../contexts/ConfirmContext'

const STATUS_LABEL: Record<OrgAuditor['status'], string> = {
  invited: 'Invited',
  active: 'Active',
  disabled: 'Disabled',
}

interface Props {
  organizationId: string
  /** Only admins may disable; everyone who can see Users sees the list. */
  isOrgAdmin: boolean
  /** Bumped by the parent after an invite so the list refreshes. */
  refreshKey?: number
}

/**
 * The organisation's external auditors: people invited from outside its email
 * domain who are not members. Being listed grants nothing; access comes only
 * from an engagement assignment (Engagements → Auditors).
 */
export default function ExternalAuditorsSection({ organizationId, isOrgAdmin, refreshKey = 0 }: Props) {
  const confirm = useConfirm()
  const [auditors, setAuditors] = useState<OrgAuditor[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const load = () => {
    listOrgAuditors(organizationId)
      .then(setAuditors)
      .catch(err => setError(err?.message ?? 'Could not load external auditors.'))
  }
  useEffect(() => { load() }, [organizationId, refreshKey])

  const handleDisable = async (a: OrgAuditor) => {
    const ok = await confirm({
      title: 'Disable external auditor',
      message: `Disable ${a.display_name || a.email}? Their access to every engagement in this organisation ends now. You can invite them again later.`,
      confirmLabel: 'Disable',
      destructive: true,
    })
    if (!ok) return
    setBusyId(a.id); setError(null)
    try { await disableOrgAuditor(organizationId, a.id); load() }
    catch (err: any) { setError(err?.message ?? 'Could not disable the auditor.') }
    finally { setBusyId(null) }
  }

  if (auditors !== null && auditors.length === 0 && !error) return null

  return (
    <section className="users-table-container external-auditors" aria-label="External auditors" style={{ marginTop: 24 }}>
      <h3 style={{ margin: '0 0 4px', fontSize: 15 }}>External auditors</h3>
      <p style={{ margin: '0 0 12px', fontSize: 12, color: 'var(--muted)' }}>
        Not members of this organisation and take no seat. Each sees only the engagements assigned to them.
      </p>
      {error && <div role="alert" className="error-message">{error}</div>}
      {auditors === null ? (
        <div style={{ fontSize: 13, color: 'var(--muted)' }}>Loading…</div>
      ) : (
        <table className="users-table">
          <thead>
            <tr>
              <th>Auditor</th>
              <th>Firm</th>
              <th>Status</th>
              <th>Engagements</th>
              {isOrgAdmin && <th>Actions</th>}
            </tr>
          </thead>
          <tbody>
            {auditors.map(a => (
              <tr key={a.id}>
                <td className="user-cell">
                  <div className="user-info">
                    <div className="user-name">{a.display_name || 'No name'}</div>
                    <div className="user-email">{a.email}</div>
                  </div>
                </td>
                <td>{a.firm || '—'}</td>
                <td><span className={`auditor-status is-${a.status}`}>{STATUS_LABEL[a.status]}</span></td>
                <td>{a.active_engagements}</td>
                {isOrgAdmin && (
                  <td className="actions-cell">
                    {a.status !== 'disabled' && (
                      <button className="btn-action btn-remove" disabled={busyId === a.id}
                        onClick={() => void handleDisable(a)}
                        aria-label={`Disable ${a.display_name || a.email}`} title="Disable auditor">
                        Disable
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
