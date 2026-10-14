import { useState, type JSX } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import { setControlTargetMaturity } from '../../data/apiClient'
import { MATURITY_LEVEL_OPTIONS, domainOf } from '../../data/targetMaturity'
import { flattenScopedControlPages, useScopedControlsQuery } from '../../hooks/useScopedControlsQuery'
import { useIsOrgAdmin } from '../../hooks/useIsOrgAdmin'
import type { MaturityLevel } from '../../types'

interface Props {
  organizationId: string
  scfId: string
  /** Called after a save, so the page can reload the record its roadmap reads. */
  onChange?: () => void
}

const levelLabel = (level: string) => MATURITY_LEVEL_OPTIONS.find((option) => option.value === level)?.label ?? level

/* PIG R5: the level this control is meant to reach — its own override, else its
   domain's target from Org Settings. The server resolves it on the paginated
   list (the same query the obligation basis panel reads), so this shows that
   rather than resolving it again. Admins may override it with a reason, or
   clear the override so the control inherits its domain's target again. */
export default function TargetMaturityPanel({ organizationId, scfId, onChange }: Props): JSX.Element {
  const queryClient = useQueryClient()
  const isAdmin = useIsOrgAdmin(organizationId)
  const lookup = useScopedControlsQuery({ search: scfId, scope_status: 'all' }, organizationId)
  const row = flattenScopedControlPages(lookup.data?.pages).controls.find((control) => control.scf_id === scfId)
  const [editing, setEditing] = useState(false)
  const [level, setLevel] = useState<'' | MaturityLevel>('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const domain = domainOf(scfId)
  const overridden = row?.target_source === 'control'

  const startEdit = () => {
    setLevel(overridden ? row?.target_maturity ?? '' : '')
    setReason(overridden ? row?.target_maturity_reason ?? '' : '')
    setError(null)
    setEditing(true)
  }

  const save = async () => {
    if (level && !reason.trim()) {
      setError('A target override needs a reason.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await setControlTargetMaturity(organizationId, scfId, level || null, level ? reason.trim() : null)
      await queryClient.invalidateQueries({ queryKey: ['scoped-controls', organizationId] })
      toast.success(level ? `${scfId} target set to ${level}` : `${scfId} target override cleared`)
      setEditing(false)
      onChange?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the target')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="gov-panel" aria-label="Target maturity">
      <header className="gov-panel-header">
        <h3>Target maturity</h3>
        {isAdmin && !editing && row?.is_scoped && !lookup.isError && (
          <button type="button" className="btn-secondary btn-small" onClick={startEdit}>
            {overridden ? 'Change override' : 'Override target'}
          </button>
        )}
      </header>
      {lookup.isLoading ? (
        <p className="gov-muted">Loading…</p>
      ) : lookup.isError ? (
        <div role="alert" className="gov-error">
          {lookup.error instanceof Error ? lookup.error.message : 'Could not load the target maturity'}
        </div>
      ) : !row?.is_scoped ? (
        <p className="gov-muted">This control has no scope record yet, so it has no target maturity.</p>
      ) : (
        <div className="gov-panel-body">
          <span className="cadence-badge">{row.target_maturity ? levelLabel(row.target_maturity) : 'No target'}</span>
          <p className="gov-muted">
            {overridden
              ? `Set on this control: ${row.target_maturity_reason ?? 'no reason recorded'}`
              : row.target_maturity
                ? `Inherited from the ${domain} domain target set in Org Settings.`
                : `Neither this control nor the ${domain} domain has a target. Set domain targets in Org Settings under Maturity targets.`}
          </p>
        </div>
      )}
      {editing && (
        <div className="gov-form">
          <label>
            Target
            <select
              className="form-control"
              value={level}
              onChange={(event) => setLevel(event.target.value as '' | MaturityLevel)}
            >
              <option value="">Inherit the {domain} domain target (no override)</option>
              {MATURITY_LEVEL_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          {level && (
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
