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
import { useConfirm } from '../../contexts/ConfirmContext'
import {
  getDocGenSettings,
  listDocuments,
  updateDocGenSettings,
  type DocumentProfile,
} from '../../data/documentsApi'

/** The generators a document structure decides; everything else is unaffected by it. */
const PROFILED_GENERATORS = ['policy', 'standard', 'procedure']

/**
 * What a profile produces, in words. Counted from the profile itself rather
 * than from the organisation's scope, so the line reads the same before and
 * after controls are scoped; the Generate panel shows the scoped counts.
 */
export function describeProfileOutput(profile: DocumentProfile): string {
  const bundles = profile.bundles.length
  const parts: string[] = []
  const policy = profile.generators.policy
  if (policy === 'domain') parts.push('a policy per scoped domain')
  else if (policy === 'bundle')
    parts.push(bundles === 1 ? 'one policy manual' : `${bundles} policies`)
  const standard = profile.generators.standard
  if (standard === 'domain') parts.push('a standard per domain')
  else if (standard === 'bundle') parts.push(`${bundles} standards`)
  const procedure = profile.generators.procedure
  if (procedure === 'domain') parts.push('a procedure per domain')
  else if (procedure === 'topic') parts.push('procedures by operational topic')
  const text = parts.join(', ')
  return profile.fold_standards ? `${text}; requirements are built into each policy` : text
}

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

  const confirm = useConfirm()

  const { data: settings, isLoading } = useQuery({
    queryKey: ['docgen-settings', organizationId],
    queryFn: () => getDocGenSettings(organizationId),
  })

  // The library, unfiltered, so a change of structure can say what it supersedes.
  const { data: documents = [] } = useQuery({
    queryKey: ['documents', organizationId, ''],
    queryFn: () => listDocuments(organizationId, {}),
    enabled: Boolean(settings?.profiles?.length),
  })

  const mutation = useMutation({
    mutationFn: (payload: Parameters<typeof updateDocGenSettings>[1]) =>
      updateDocGenSettings(organizationId, payload),
    onSuccess: (next) => {
      queryClient.setQueryData(['docgen-settings', organizationId], next)
      queryClient.invalidateQueries({ queryKey: ['documents', organizationId] })
      queryClient.invalidateQueries({ queryKey: ['document-map', organizationId] })
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

  /**
   * Switch structure, confirming first when current policies, standards or
   * procedures would be superseded by it. Nothing is deleted, but an approved
   * policy set stops being the current one, and that is worth a second look.
   */
  async function changeProfile(next: DocumentProfile) {
    const affected = documents.filter(
      (d) => !d.superseded && PROFILED_GENERATORS.includes(d.generator_name),
    )
    if (affected.length > 0) {
      const signedOff = affected.filter(
        (d) => d.lifecycle_status === 'approved' || d.lifecycle_status === 'published',
      ).length
      const currentName =
        settings!.profiles!.find((p) => p.name === (settings!.document_profile ?? 'granular'))
          ?.display_name ?? 'the current structure'
      const n = affected.length
      const ok = await confirm({
        title: `Switch to ${next.display_name}?`,
        message:
          `${n} ${n === 1 ? 'policy, standard or procedure was' : 'policies, standards and procedures were'} ` +
          `generated under ${currentName}` +
          (signedOff ? `, ${signedOff} of them approved or published` : '') +
          `. Switching marks ${n === 1 ? 'it' : 'them'} superseded: kept in the library and still ` +
          `readable and exportable, but no longer current and no longer regenerated.\n\n` +
          `${next.display_name} starts with none of its documents generated` +
          (signedOff ? ' or approved' : '') +
          `. Nothing is deleted, and switching back to ${currentName} makes the superseded ` +
          `documents current again.`,
        confirmLabel: `Switch to ${next.display_name}`,
        destructive: true,
      })
      if (!ok) return
    }
    mutation.mutate({ document_profile: next.name })
  }

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

      {/* ── Document profile ─────────────────────────────────────────────── */}
      {settings.profiles && settings.profiles.length > 0 && (
        <fieldset className="doc-profile-block" disabled={busy || !canAdminister}>
          <legend>Document structure</legend>
          <p className="doc-licence-text">
            How policies, standards and procedures are grouped. Smaller
            organisations usually want fewer, broader documents. Changing this
            deletes nothing: documents from the previous structure stay in your
            library, marked superseded.
          </p>
          <div className="doc-profile-options">
            {settings.profiles.map((profile) => {
              const current = (settings.document_profile ?? 'granular') === profile.name
              return (
                <label
                  key={profile.name}
                  className={`doc-profile-option ${current ? 'is-current' : ''}`}
                >
                  <input
                    type="radio"
                    name="doc-profile"
                    id={`doc-profile-${profile.name}`}
                    value={profile.name}
                    checked={current}
                    onChange={() => void changeProfile(profile)}
                  />
                  <span className="doc-profile-copy">
                    <strong>{profile.display_name}</strong>
                    <span>{profile.description}</span>
                    <span className="doc-profile-meta">
                      {profile.recommended_for} · {describeProfileOutput(profile)}
                    </span>
                  </span>
                </label>
              )
            })}
          </div>
        </fieldset>
      )}

      {settings.enabled && (
        <p className="doc-settings-foot">
          Disabling document generation does not delete documents you have
          already produced, and does not clear this acknowledgement.
        </p>
      )}
    </div>
  )
}
