import { useState, type JSX } from 'react'
import { useIsOrgEditor } from '../../hooks/useHasOrgRole'
import { isOpenDeficiency, isOverdue, useDeficiencies, useOrgMembersQuery } from '../../hooks/useDeficiencies'
import DeficiencyCompleteModal from '../deficiencies/DeficiencyCompleteModal'
import DeficiencyFormModal from '../deficiencies/DeficiencyFormModal'
import DeficiencyList from '../deficiencies/DeficiencyList'
import type { Deficiency } from '../../types'

interface Props {
  organizationId: string
  scfId: string
}

/* PIG R8: the deficiencies raised against this control, open and closed, and
   a way to raise another against it. Editing, completing, cancelling and
   reopening work here as on the Deficiencies page. */
export default function DeficiencyPanel({ organizationId, scfId }: Props): JSX.Element {
  const canEdit = useIsOrgEditor(organizationId)
  const deficiencies = useDeficiencies(organizationId, { scf_id: scfId })
  const members = useOrgMembersQuery(organizationId)
  const [raising, setRaising] = useState(false)
  // Owned here, not by the list, so a refetch cannot unmount a half-typed form.
  const [editing, setEditing] = useState<Deficiency | null>(null)
  const [completing, setCompleting] = useState<Deficiency | null>(null)

  const rows = deficiencies.data ?? []
  const open = rows.filter(isOpenDeficiency).length
  const overdue = rows.filter((d) => isOverdue(d)).length
  const closed = rows.length - open

  return (
    <section className="gov-panel deficiency-panel" aria-label="Deficiencies">
      <header className="gov-panel-header">
        <h3>Deficiencies</h3>
        {canEdit && (
          <button type="button" className="btn-secondary btn-small" onClick={() => setRaising(true)}>
            Raise deficiency
          </button>
        )}
      </header>
      {deficiencies.isError && deficiencies.data && (
        <div role="alert" className="gov-error">
          Could not refresh the deficiencies:{' '}
          {deficiencies.error instanceof Error ? deficiencies.error.message : String(deficiencies.error)}
        </div>
      )}
      {deficiencies.isLoading ? (
        <p className="gov-muted">Loading…</p>
      ) : !deficiencies.data ? (
        <div role="alert" className="gov-error">
          Could not load the deficiencies:{' '}
          {deficiencies.error instanceof Error ? deficiencies.error.message : String(deficiencies.error)}
        </div>
      ) : rows.length === 0 ? (
        <p className="gov-muted">No deficiencies against this control.</p>
      ) : (
        <>
          <p className="gov-muted" data-testid="control-deficiency-counts">
            {open} open · {overdue} overdue · {closed} closed
          </p>
          <DeficiencyList
            organizationId={organizationId}
            deficiencies={rows}
            users={members.data}
            canEdit={canEdit}
            showControl={false}
            onEdit={setEditing}
            onComplete={setCompleting}
          />
        </>
      )}
      {!canEdit && (
        <p className="gov-muted">Only an editor or admin can raise or change a deficiency.</p>
      )}
      {raising && (
        <DeficiencyFormModal organizationId={organizationId} fixedScfId={scfId} onClose={() => setRaising(false)} />
      )}
      {editing && (
        <DeficiencyFormModal
          key={editing.id}
          organizationId={organizationId}
          deficiency={editing}
          onClose={() => setEditing(null)}
        />
      )}
      {completing && (
        <DeficiencyCompleteModal
          key={completing.id}
          organizationId={organizationId}
          deficiency={completing}
          onClose={() => setCompleting(null)}
        />
      )}
    </section>
  )
}
