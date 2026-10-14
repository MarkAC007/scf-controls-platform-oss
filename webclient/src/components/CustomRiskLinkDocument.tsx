import { useState, type JSX } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { setCustomRiskControlDocument } from '../data/apiClient'
import { LIFECYCLE_LABELS, type DocumentSummary, type LifecycleStatus } from '../data/documentsApi'
import { useConfirm } from '../contexts/ConfirmContext'
import { useIsOrgEditor } from '../hooks/useHasOrgRole'
import { customRiskLinksKey, useOrgDocuments } from '../hooks/useCustomRiskLinks'

const statusLabel = (status: string) => LIFECYCLE_LABELS[status as LifecycleStatus] ?? status
const optionLabel = (document: DocumentSummary) =>
  `${document.title} (${document.document_type}, ${statusLabel(document.lifecycle_status)})`

interface SelectProps {
  organizationId: string
  value: string
  onChange: (documentId: string) => void
  label?: string
  disabled?: boolean
}

/** A picker over the organisation's documents; '' means no document. */
export function LinkDocumentSelect({
  organizationId,
  value,
  onChange,
  label = 'Supporting document (optional)',
  disabled = false,
}: SelectProps): JSX.Element {
  const documents = useOrgDocuments(organizationId)
  if (documents.isError) {
    return (
      <div role="alert" className="gov-error">
        {documents.error instanceof Error ? documents.error.message : 'Could not load the documents'}
      </div>
    )
  }
  return (
    <label className="risk-link-document-field">
      {label}
      <select
        className="form-control"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled || documents.isLoading}
      >
        <option value="">No supporting document</option>
        {(documents.data ?? []).map((document) => (
          <option key={document.id} value={document.id}>
            {optionLabel(document)}
          </option>
        ))}
      </select>
    </label>
  )
}

interface Props {
  organizationId: string
  riskCode: string
  scfId: string
  /** The document on this link now, from the custom-risk links list. */
  documentId: string | null
  /** Called after a change, so the page can reload what it shows. */
  onChanged?: () => void
  /**
   * Asked before saving when the change withdraws an approval in force
   * (the server clears it on any link change); null when nothing is at stake.
   */
  confirmMessage?: string | null
}

/* PIG R7: a custom risk's control link can carry the document that sets the
   standard the risk is an exception against. Editors set, change or clear it.
   Changing it on an accepted exception withdraws the approval server-side. */
export default function CustomRiskLinkDocument({
  organizationId,
  riskCode,
  scfId,
  documentId,
  onChanged,
  confirmMessage = null,
}: Props): JSX.Element {
  const queryClient = useQueryClient()
  const confirm = useConfirm()
  const canEdit = useIsOrgEditor(organizationId)
  const [editing, setEditing] = useState(false)
  const [choice, setChoice] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const documents = useOrgDocuments(organizationId, editing || !!documentId)
  const linked = documentId ? documents.data?.find((document) => document.id === documentId) : undefined

  const startEdit = () => {
    setChoice(documentId ?? '')
    setError(null)
    setEditing(true)
  }

  const save = async () => {
    if (
      confirmMessage &&
      !(await confirm({ title: 'Change the document link', message: confirmMessage, confirmLabel: 'Change link', destructive: true }))
    )
      return
    setBusy(true)
    setError(null)
    try {
      await setCustomRiskControlDocument(riskCode, scfId, choice || null, organizationId)
      await queryClient.invalidateQueries({ queryKey: customRiskLinksKey(organizationId, riskCode) })
      setEditing(false)
      onChanged?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the document link')
    } finally {
      setBusy(false)
    }
  }

  if (editing) {
    return (
      <div className="gov-form risk-link-document-form">
        <LinkDocumentSelect
          organizationId={organizationId}
          value={choice}
          onChange={setChoice}
          label={`Supporting document for ${scfId}`}
          disabled={busy}
        />
        {error && <div role="alert" className="gov-error">{error}</div>}
        <div className="gov-form-actions">
          <button type="button" className="btn-secondary btn-small" onClick={() => setEditing(false)} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary btn-small"
            onClick={() => void save()}
            disabled={busy || documents.isLoading || documents.isError || choice === (documentId ?? '')}
          >
            {busy ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="risk-link-document">
      <span className="risk-link-document-label">
        {documentId ? (
          <>
            Supporting document:{' '}
            <strong>{linked?.title ?? (documents.isLoading ? 'Loading…' : 'Linked document')}</strong>
          </>
        ) : (
          'No supporting document'
        )}
      </span>
      {canEdit && (
        <button
          type="button"
          className="btn-secondary btn-small"
          onClick={startEdit}
          aria-label={`${documentId ? 'Change' : 'Add'} document for ${scfId}`}
        >
          {documentId ? 'Change' : 'Add document'}
        </button>
      )}
    </div>
  )
}
