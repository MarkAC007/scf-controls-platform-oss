import { useState, type JSX } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import { setControlMateriality } from '../../data/apiClient'
import { invalidateCompensatingControls } from '../../hooks/useCompensatingControls'
import { flattenScopedControlPages, useScopedControlsQuery } from '../../hooks/useScopedControlsQuery'
import { useIsOrgAdmin } from '../../hooks/useIsOrgAdmin'
import MaterialityBadge from './MaterialityBadge'

interface Props {
  organizationId: string
  scfId: string
  /** Called after a save, so the page can reload what it shows. */
  onChange?: () => void
}

/** Catalogue weighting at which a control is material by default (backend MATERIAL_WEIGHTING). */
const MATERIAL_WEIGHTING = 10

/* PIG R6: whether this control is material — its own override, else its
   catalogue weighting (10 is material). A material control leaves scope, or is
   marked not applicable, only with an approving admin. The server resolves it
   on the paginated list, so this shows that. Admins may override it with a
   reason, or clear the override so the weighting decides again; a clear that
   makes the control not material is the same decision, so it needs a reason too. */
export default function MaterialityPanel({ organizationId, scfId, onChange }: Props): JSX.Element {
  const queryClient = useQueryClient()
  const isAdmin = useIsOrgAdmin(organizationId)
  const lookup = useScopedControlsQuery({ search: scfId, scope_status: 'all' }, organizationId)
  const row = flattenScopedControlPages(lookup.data?.pages).controls.find((control) => control.scf_id === scfId)
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState<'' | 'true' | 'false'>('')
  const [reason, setReason] = useState('')
  // What the form opened on. Save stays inert until it changes: saving the
  // opening "None" would erase the decision the backend keeps after a clear.
  const [initial, setInitial] = useState<{ value: '' | 'true' | 'false'; reason: string }>({ value: '', reason: '' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const overridden = row?.material_source === 'control'
  const weighting = row?.control_weighting ?? null
  const materialByWeighting = weighting !== null && weighting >= MATERIAL_WEIGHTING
  // Clearing an override of "material" on a lighter control makes it not material.
  const clearDematerialises = overridden && !!row?.material && !materialByWeighting
  const needsReason = value !== '' || clearDematerialises

  const unchanged = value === initial.value && reason.trim() === initial.reason.trim()

  const startEdit = () => {
    const opening = {
      value: (overridden ? (row?.material ? 'true' : 'false') : '') as '' | 'true' | 'false',
      reason: overridden ? row?.material_reason ?? '' : '',
    }
    setInitial(opening)
    setValue(opening.value)
    setReason(opening.reason)
    setError(null)
    setEditing(true)
  }

  const save = async () => {
    if (needsReason && !reason.trim()) {
      setError(value
        ? 'A materiality override needs a reason.'
        : 'Clearing this override makes the control not material, so it needs a reason.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await setControlMateriality(
        organizationId,
        scfId,
        value === '' ? null : value === 'true',
        needsReason ? reason.trim() : null,
      )
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['scoped-controls', organizationId] }),
        // Whether a compensating control is justified turns on its control being material.
        invalidateCompensatingControls(queryClient, organizationId),
      ])
      toast.success(value
        ? `${scfId} marked ${value === 'true' ? 'material' : 'not material'}`
        : `${scfId} materiality override cleared`)
      setEditing(false)
      onChange?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the materiality')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="gov-panel" aria-label="Materiality">
      <header className="gov-panel-header">
        <h3>Materiality</h3>
        {isAdmin && !editing && row?.is_scoped && !lookup.isError && (
          <button type="button" className="btn-secondary btn-small" onClick={startEdit}>
            {overridden ? 'Change override' : 'Override materiality'}
          </button>
        )}
      </header>
      {lookup.isLoading ? (
        <p className="gov-muted">Loading…</p>
      ) : lookup.isError ? (
        <div role="alert" className="gov-error">
          {lookup.error instanceof Error ? lookup.error.message : 'Could not load the materiality'}
        </div>
      ) : !row ? (
        <p className="gov-muted">This control is not in the catalogue, so it has no materiality.</p>
      ) : (
        <div className="gov-panel-body">
          <MaterialityBadge material={!!row.material} />
          <p className="gov-muted">
            {overridden
              ? `Set on this control: ${row.material_reason ?? 'no reason recorded'}`
              : weighting === null
                ? 'The catalogue gives this control no weighting, so it is not material by default.'
                : row.material
                  ? `Material by default: its catalogue weighting is ${weighting}.`
                  : `Not material by default: its catalogue weighting is ${weighting}, and only ${MATERIAL_WEIGHTING} is material.`}
          </p>
          {row.material && (
            <p className="gov-muted">Only an organisation admin can exclude it from scope or mark it not applicable.</p>
          )}
          {!row.is_scoped && (
            <p className="gov-muted">This control has no scope record yet, so its materiality cannot be overridden.</p>
          )}
        </div>
      )}
      {editing && (
        <div className="gov-form">
          <label>
            Override
            <select
              className="form-control"
              value={value}
              onChange={(event) => {
                const next = event.target.value as '' | 'true' | 'false'
                setValue(next)
                // The old reason argued for the old value; never carry it over.
                setReason(next === initial.value ? initial.reason : '')
              }}
            >
              <option value="">None: follow the catalogue weighting</option>
              <option value="true">Material</option>
              <option value="false">Not material</option>
            </select>
          </label>
          {needsReason && (
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
            <button type="button" className="btn-primary btn-small" onClick={() => void save()} disabled={busy || unchanged}>
              {busy ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      )}
    </section>
  )
}
