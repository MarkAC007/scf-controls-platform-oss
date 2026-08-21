/**
 * Document generation settings — the feature toggle and the SCF licence
 * acknowledgement.
 *
 * Two switches, not one. Tier 1 documents tabulate SCF identifiers beside the
 * organisation's own status, which is arguably a compilation. Tier 2 has a
 * language model write prose from SCF content, which is unambiguously a
 * derivative work. Collapsing both into a single toggle would make an
 * organisation that only wants a Statement of Applicability accept a notice
 * that does not describe what it is doing.
 *
 * This card is a courtesy, not a control. The gate is enforced in the API and
 * by a database check constraint; a direct PUT bypasses everything here and
 * still gets refused.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import { getDocGenSettings, updateDocGenSettings } from '../../data/documentsApi'

interface Props {
  organizationId: string
  /** Admin-only actions are hidden for non-admins; the API refuses them too. */
  canAdminister?: boolean
}

export const DOC_GEN_ADMIN_DOC_URL =
  'https://docs.scfcontrolsplatform.app/admin-guide/document-generation/'

export default function DocGenSettingsCard({ organizationId, canAdminister = true }: Props) {
  const queryClient = useQueryClient()
  const [acknowledging, setAcknowledging] = useState(false)

  const { data: settings, isLoading } = useQuery({
    queryKey: ['docgen-settings', organizationId],
    queryFn: () => getDocGenSettings(organizationId),
  })

  const mutation = useMutation({
    mutationFn: (payload: Parameters<typeof updateDocGenSettings>[1]) =>
      updateDocGenSettings(organizationId, payload),
    onSuccess: (next) => {
      queryClient.setQueryData(['docgen-settings', organizationId], next)
      queryClient.invalidateQueries({ queryKey: ['documents', organizationId] })
      setAcknowledging(false)
      toast.success('Document generation settings updated')
    },
    onError: (error: Error) => toast.error(error.message),
  })

  if (isLoading || !settings) {
    return (
      <div className="settings-card">
        <h2>Document Generation</h2>
        <p className="settings-card-sub">Loading…</p>
      </div>
    )
  }

  const busy = mutation.isPending

  return (
    <div className="settings-card doc-settings-card">
      <div className="doc-settings-head">
        <div>
          <h2>Document Generation</h2>
          <p className="settings-card-sub">
            Generate ISMS documents from your scoped controls. Documents remain
            editable, and regeneration preserves your edits.
          </p>
        </div>
        <span className={`doc-status-pill ${settings.enabled ? 'is-on' : 'is-off'}`}>
          {settings.enabled ? 'Enabled' : 'Disabled'}
        </span>
      </div>

      {settings.platform_disabled && (
        <div className="doc-notice doc-notice-warning">
          <strong>Disabled platform-wide.</strong> An operator has switched
          document generation off for this deployment. Your organisation
          settings are preserved but no generation will run.
        </div>
      )}

      {/* ── Licence acknowledgement ─────────────────────────────────────── */}
      <div className="doc-licence-block">
        <h3>SCF Licence Position</h3>
        <p className="doc-licence-text">{settings.acknowledgement_text}</p>
        <p className="doc-licence-text">
          The reasoning behind the two switches, and what each one covers, is in the{' '}
          <a href={DOC_GEN_ADMIN_DOC_URL} target="_blank" rel="noreferrer">
            document generation administrator guide
          </a>.
        </p>

        {settings.licence_acknowledged ? (
          <div className="doc-notice doc-notice-ok">
            Acknowledged
            {settings.licence_acknowledged_by_email && (
              <> by <strong>{settings.licence_acknowledged_by_email}</strong></>
            )}
            {settings.licence_acknowledged_at && (
              <> on {new Date(settings.licence_acknowledged_at).toLocaleDateString('en-GB')}</>
            )}
            {settings.licence_text_version && <> ({settings.licence_text_version})</>}
          </div>
        ) : (
          canAdminister && (
            <label className="doc-ack-checkbox">
              <input
                type="checkbox"
                checked={acknowledging}
                disabled={busy}
                onChange={(e) => setAcknowledging(e.target.checked)}
              />
              <span>
                I confirm my organisation has reviewed its SCF licence position.
              </span>
            </label>
          )
        )}
      </div>

      {/* ── Toggles ─────────────────────────────────────────────────────── */}
      <div className="doc-toggle-row">
        <div className="doc-toggle-copy">
          <strong>Enable document generation</strong>
          <span>
            Tabular documents — Statement of Applicability, control status,
            evidence schedule. No AI, no derivative content.
          </span>
        </div>
        <button
          type="button"
          className={`toggle-switch ${settings.enabled ? 'is-on' : ''}`}
          disabled={
            busy ||
            !canAdminister ||
            (!settings.enabled && !settings.licence_acknowledged && !acknowledging)
          }
          onClick={() =>
            mutation.mutate({
              enabled: !settings.enabled,
              acknowledge_licence: acknowledging || undefined,
            })
          }
          aria-pressed={settings.enabled}
          aria-label="Enable document generation"
        >
          <span className="toggle-knob" />
        </button>
      </div>

      <div className="doc-toggle-row">
        <div className="doc-toggle-copy">
          <strong>Enable AI-augmented generators</strong>
          <span>
            Policies, procedures and standards written by a language model from
            SCF control content. <em>This produces derivative works.</em>
          </span>
        </div>
        <button
          type="button"
          className={`toggle-switch ${settings.derivative_generators_enabled ? 'is-on' : ''}`}
          disabled={busy || !canAdminister || !settings.enabled}
          onClick={() =>
            mutation.mutate({
              derivative_generators_enabled: !settings.derivative_generators_enabled,
            })
          }
          aria-pressed={settings.derivative_generators_enabled}
          aria-label="Enable AI-augmented generators"
        >
          <span className="toggle-knob" />
        </button>
      </div>

      {settings.enabled && (
        <p className="doc-settings-foot">
          Disabling document generation does not delete documents you have
          already produced, and does not clear this acknowledgement.
        </p>
      )}
    </div>
  )
}
