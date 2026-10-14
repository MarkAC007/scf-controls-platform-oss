import { useState, type JSX } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import { setControlBasisOverride, type ObligationBasis } from '../../data/apiClient'
import { flattenScopedControlPages, useScopedControlsQuery } from '../../hooks/useScopedControlsQuery'
import { useIsOrgAdmin } from '../../hooks/useIsOrgAdmin'
import ObligationBasisBadge from './ObligationBasisBadge'

interface Props {
  organizationId: string
  scfId: string
}

/* PIG R2: the control's obligation basis — its own override, else the strongest
   basis among the classified frameworks in scope that map it. The effective
   value is only served by the paginated list, so it is read from there (the
   same query the library's detail view already makes). Admins may override it
   with a reason, or clear the override to derive it again. */
export default function ObligationBasisPanel({ organizationId, scfId }: Props): JSX.Element {
  const queryClient = useQueryClient()
  const isAdmin = useIsOrgAdmin(organizationId)
  const lookup = useScopedControlsQuery({ search: scfId, scope_status: 'all' }, organizationId)
  const row = flattenScopedControlPages(lookup.data?.pages).controls.find((control) => control.scf_id === scfId)
  const [editing, setEditing] = useState(false)
  const [basis, setBasis] = useState<'' | ObligationBasis>('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const overridden = row?.basis_source === 'override'

  const startEdit = () => {
    setBasis(overridden ? row?.obligation_basis ?? '' : '')
    setReason(overridden ? row?.basis_override_reason ?? '' : '')
    setError(null)
    setEditing(true)
  }

  const save = async () => {
    if (basis && !reason.trim()) {
      setError('A basis override needs a reason.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await setControlBasisOverride(organizationId, scfId, basis || null, basis ? reason.trim() : null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['scoped-controls', organizationId] }),
        queryClient.invalidateQueries({ queryKey: ['framework-scoping', organizationId] }),
      ])
      toast.success(basis ? `${scfId} basis set to ${basis.toUpperCase()}` : `${scfId} basis override cleared`)
      setEditing(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the basis')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="gov-panel" aria-label="Obligation basis">
      <header className="gov-panel-header">
        <h3>Obligation basis</h3>
        {isAdmin && !editing && row && !lookup.isError && (
          <button type="button" className="btn-secondary btn-small" onClick={startEdit}>
            {overridden ? 'Change override' : 'Override basis'}
          </button>
        )}
      </header>
      {lookup.isLoading ? (
        <p className="gov-muted">Loading…</p>
      ) : lookup.isError ? (
        <div role="alert" className="gov-error">
          {lookup.error instanceof Error ? lookup.error.message : 'Could not load the obligation basis'}
        </div>
      ) : !row ? (
        <p className="gov-muted">This control has no scope record yet, so it has no obligation basis.</p>
      ) : (
        <div className="gov-panel-body">
          <ObligationBasisBadge basis={row?.obligation_basis} long />
          <p className="gov-muted">
            {overridden
              ? `Set on this control: ${row?.basis_override_reason ?? 'no reason recorded'}`
              : row?.obligation_basis
                ? 'Derived from the classified frameworks in scope that map this control.'
                : 'No classified framework in scope maps this control. Classify frameworks under Framework Scoping.'}
          </p>
        </div>
      )}
      {editing && (
        <div className="gov-form">
          <label>
            Basis
            <select
              className="form-control"
              value={basis}
              onChange={(event) => setBasis(event.target.value as '' | ObligationBasis)}
            >
              <option value="">Derive from frameworks (no override)</option>
              <option value="mcr">Mandatory (MCR)</option>
              <option value="dsr">Discretionary (DSR)</option>
            </select>
          </label>
          {basis && (
            <label>
              Reason
              <textarea
                className="form-control"
                rows={2}
                maxLength={2000}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </label>
          )}
          {error && <div role="alert" className="gov-error">{error}</div>}
          <div className="gov-form-actions">
            <button type="button" className="btn-secondary btn-small" onClick={() => setEditing(false)} disabled={busy}>
              Cancel
            </button>
            <button type="button" className="btn-primary btn-small" onClick={() => void save()} disabled={busy}>
              {busy ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      )}
    </section>
  )
}
