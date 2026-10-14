import { useState, type JSX } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import { setControlProcedureDocument } from '../../data/apiClient'
import { LIFECYCLE_LABELS, SIGNED_OFF_STATUSES, type LifecycleStatus } from '../../data/documentsApi'
import { flattenScopedControlPages, useScopedControlsQuery } from '../../hooks/useScopedControlsQuery'
import { useIsOrgEditor } from '../../hooks/useHasOrgRole'
import { useProcedureDocuments } from '../../hooks/useProcedureDocuments'

interface Props {
  organizationId: string
  scfId: string
  /** Called after a save, so the page can reload what it shows. */
  onChange?: () => void
}

const statusLabel = (status: string) => LIFECYCLE_LABELS[status as LifecycleStatus] ?? status
const signedOff = (status: string | null | undefined) =>
  !!status && SIGNED_OFF_STATUSES.includes(status as LifecycleStatus)

/* PIG R6, playbook step 2d: the approved procedure describing how this control
   is operated. The link and the document's status now come from the paginated
   list; the title from the organisation's procedure documents. Editors link
   one that is approved or published, or unlink it. An edit after linking sends
   the document back to review, and the link stays, so that drift is shown. */
export default function ProcedureDocumentPanel({ organizationId, scfId, onChange }: Props): JSX.Element {
  const queryClient = useQueryClient()
  const canEdit = useIsOrgEditor(organizationId)
  const lookup = useScopedControlsQuery({ search: scfId, scope_status: 'all' }, organizationId)
  const row = flattenScopedControlPages(lookup.data?.pages).controls.find((control) => control.scf_id === scfId)
  const [editing, setEditing] = useState(false)
  const linkedId = row?.procedure_document_id ?? null
  const documents = useProcedureDocuments(organizationId, editing || !!linkedId)
  const [documentId, setDocumentId] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const linked = linkedId ? documents.data?.find((document) => document.id === linkedId) : undefined
  const linkable = (documents.data ?? []).filter((document) => signedOff(document.lifecycle_status))
  // The documents list is refreshed when a document is edited; the scoped-controls
  // row can be minutes stale, so the document's own status wins when it is loaded.
  const linkedStatus = linked?.lifecycle_status ?? row?.procedure_document_status ?? null
  // A drifted link is no longer linkable, but it is still the current value.
  const currentUnlinkable = linked && !signedOff(linked.lifecycle_status) ? linked : undefined

  const startEdit = () => {
    setDocumentId(linkedId ?? '')
    setError(null)
    setEditing(true)
  }

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      await setControlProcedureDocument(organizationId, scfId, documentId || null)
      await queryClient.invalidateQueries({ queryKey: ['scoped-controls', organizationId] })
      toast.success(documentId ? `${scfId} procedure linked` : `${scfId} procedure unlinked`)
      setEditing(false)
      onChange?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the procedure link')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="gov-panel" aria-label="Procedure document">
      <header className="gov-panel-header">
        <h3>Procedure document</h3>
        {canEdit && !editing && row?.is_scoped && !lookup.isError && (
          <button type="button" className="btn-secondary btn-small" onClick={startEdit}>
            {linkedId ? 'Change procedure' : 'Link procedure'}
          </button>
        )}
      </header>
      {lookup.isLoading ? (
        <p className="gov-muted">Loading…</p>
      ) : lookup.isError ? (
        <div role="alert" className="gov-error">
          {lookup.error instanceof Error ? lookup.error.message : 'Could not load the procedure link'}
        </div>
      ) : !row?.is_scoped ? (
        <p className="gov-muted">This control has no scope record yet, so it has no procedure document.</p>
      ) : !linkedId ? (
        <p className="gov-muted">No procedure document is linked to this control.</p>
      ) : (
        <div className="gov-panel-body">
          <strong>{linked?.title ?? (documents.isLoading ? 'Loading…' : 'Linked procedure')}</strong>
          {linkedStatus && (
            <span className="cadence-badge">{statusLabel(linkedStatus)}</span>
          )}
          {!signedOff(linkedStatus) && (
            <p className="gov-note">
              This procedure has changed since it was linked and needs signing off again under Generated Documents.
            </p>
          )}
        </div>
      )}
      {editing && (
        <div className="gov-form">
          {documents.isError ? (
            <div role="alert" className="gov-error">
              {documents.error instanceof Error ? documents.error.message : 'Could not load the procedure documents'}
            </div>
          ) : documents.isLoading ? (
            <p className="gov-muted">Loading procedure documents…</p>
          ) : (
            <>
              <label>
                Document
                <select
                  className="form-control"
                  value={documentId}
                  onChange={(event) => setDocumentId(event.target.value)}
                >
                  <option value="">No procedure linked</option>
                  {currentUnlinkable && (
                    <option value={currentUnlinkable.id} disabled>
                      {currentUnlinkable.title} ({statusLabel(currentUnlinkable.lifecycle_status)}, current; needs signing off)
                    </option>
                  )}
                  {linkable.map((document) => (
                    <option key={document.id} value={document.id}>
                      {document.title} ({statusLabel(document.lifecycle_status)})
                    </option>
                  ))}
                </select>
              </label>
              {linkable.length === 0 && (
                <p className="gov-muted">
                  No approved or published procedure documents yet. Approve one under Generated Documents to link it here.
                </p>
              )}
            </>
          )}
          {error && <div role="alert" className="gov-error">{error}</div>}
          <div className="gov-form-actions">
            <button type="button" className="btn-secondary btn-small" onClick={() => setEditing(false)} disabled={busy}>
              Cancel
            </button>
            <button
              type="button"
              className="btn-primary btn-small"
              onClick={() => void save()}
              disabled={busy || documents.isLoading || documents.isError || documentId === (linkedId ?? '')}
            >
              {busy ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      )}
    </section>
  )
}
