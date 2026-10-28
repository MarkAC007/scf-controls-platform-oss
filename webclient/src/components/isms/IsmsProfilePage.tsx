/**
 * ISMS Profile — the facts the ISO 27001 clause documents are written from.
 *
 * Scope, context, interested parties, objectives, communication and the audit
 * programme are things only the organisation knows. The clause document
 * generators render these registers; whatever is left empty shows in the
 * document as "to be completed" rather than being made up.
 *
 * Each section saves on its own. Edits are a local draft until saved, and the
 * draft resets to the server copy whenever the server copy changes and the
 * section has no unsaved edits.
 */
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'

import {
  getIsmsProfile,
  getIsmsStarters,
  updateIsmsSection,
  type IsmsAuditEntry,
  type IsmsCommunication,
  type IsmsContextIssue,
  type IsmsExclusion,
  type IsmsInterestedParty,
  type IsmsObjective,
  type IsmsProfile,
  type IsmsScope,
  type IsmsSection,
} from '../../data/documentsApi'
import { useIsOrgEditor } from '../../hooks/useHasOrgRole'
import RegisterRows, { type RegisterField } from './RegisterRows'

interface Props {
  organizationId: string
}

interface SectionMeta {
  key: IsmsSection
  label: string
  clause: string
  intro: string
}

const SECTIONS: SectionMeta[] = [
  {
    key: 'scope', label: 'Scope', clause: '4.3',
    intro: 'Where the ISMS starts and stops. In-scope systems and suppliers are added from the Systems Registry and Vendor Inventory when the scope statement is generated.',
  },
  {
    key: 'context_issues', label: 'Context', clause: '4.1',
    intro: 'Internal and external issues that affect what the ISMS has to achieve, including whether climate change is relevant to your organisation.',
  },
  {
    key: 'interested_parties', label: 'Interested parties', clause: '4.2',
    intro: 'Who has a stake in your information security, what they require, and which of those requirements the ISMS addresses.',
  },
  {
    key: 'objectives', label: 'Objectives', clause: '6.2',
    intro: 'Measurable information security objectives: what will be done, by whom, with what resources, by when, and how results are evaluated.',
  },
  {
    key: 'communication_plan', label: 'Communication', clause: '7.4',
    intro: 'What the ISMS communicates, when, to whom, who communicates it, and how.',
  },
  {
    key: 'audit_programme', label: 'Audit programme', clause: '9.2',
    intro: 'The internal audits planned across the ISMS clauses and your applicable controls, and their outcome.',
  },
]

const ISSUE_FIELDS: RegisterField<IsmsContextIssue>[] = [
  { key: 'kind', label: 'Internal or external', type: 'select', options: [
    { value: 'external', label: 'External' }, { value: 'internal', label: 'Internal' }] },
  { key: 'category', label: 'Category', type: 'select', options: [
    'political', 'economic', 'social', 'technological', 'legal', 'environmental', 'organisational',
  ].map((v) => ({ value: v, label: v.charAt(0).toUpperCase() + v.slice(1) })) },
  { key: 'climate_related', label: 'Climate-related', type: 'checkbox' },
  { key: 'issue', label: 'Issue', type: 'textarea', wide: true },
  { key: 'effect', label: 'Effect on the ISMS', type: 'textarea' },
  { key: 'response', label: 'How it is addressed', type: 'textarea' },
]

const PARTY_FIELDS: RegisterField<IsmsInterestedParty>[] = [
  { key: 'party', label: 'Interested party', type: 'text' },
  { key: 'kind', label: 'Internal or external', type: 'select', options: [
    { value: 'external', label: 'External' }, { value: 'internal', label: 'Internal' }] },
  { key: 'addressed_through_isms', label: 'Addressed through the ISMS', type: 'checkbox' },
  { key: 'requirements', label: 'Requirements', type: 'textarea', wide: true },
  { key: 'how_addressed', label: 'How they are addressed', type: 'textarea', wide: true },
]

const OBJECTIVE_FIELDS: RegisterField<IsmsObjective>[] = [
  { key: 'objective', label: 'Objective', type: 'textarea', wide: true },
  { key: 'measure', label: 'Measure', type: 'textarea' },
  { key: 'target', label: 'Target', type: 'textarea' },
  { key: 'owner', label: 'Owner', type: 'text' },
  { key: 'due_date', label: 'Due date', type: 'date' },
  { key: 'status', label: 'Status', type: 'select', options: [
    { value: 'planned', label: 'Planned' }, { value: 'in_progress', label: 'In progress' },
    { value: 'achieved', label: 'Achieved' }, { value: 'missed', label: 'Missed' }] },
  { key: 'resources', label: 'Resources', type: 'textarea' },
  { key: 'evaluation', label: 'How results are evaluated', type: 'textarea' },
]

const COMMUNICATION_FIELDS: RegisterField<IsmsCommunication>[] = [
  { key: 'what', label: 'What', type: 'textarea', wide: true },
  { key: 'when', label: 'When', type: 'textarea' },
  { key: 'audience', label: 'With whom', type: 'textarea' },
  { key: 'communicator', label: 'Who communicates', type: 'textarea' },
  { key: 'method', label: 'How', type: 'textarea' },
]

const AUDIT_FIELDS: RegisterField<IsmsAuditEntry>[] = [
  { key: 'area', label: 'Area audited', type: 'text', placeholder: 'e.g. Clauses 4–6, or Access Control' },
  { key: 'planned_period', label: 'Planned period', type: 'text', placeholder: 'e.g. 2027 Q1' },
  { key: 'auditor', label: 'Auditor', type: 'text' },
  { key: 'status', label: 'Status', type: 'select', options: [
    { value: 'planned', label: 'Planned' }, { value: 'in_progress', label: 'In progress' },
    { value: 'completed', label: 'Completed' }, { value: 'cancelled', label: 'Cancelled' }] },
  { key: 'criteria', label: 'Audit criteria', type: 'textarea', wide: true },
  { key: 'findings', label: 'Findings', type: 'textarea', wide: true },
]

const BLANKS = {
  context_issues: (): IsmsContextIssue => ({
    kind: 'external', category: 'organisational', issue: '', effect: '', response: '',
    climate_related: false,
  }),
  interested_parties: (): IsmsInterestedParty => ({
    party: '', kind: 'external', requirements: '', addressed_through_isms: true, how_addressed: '',
  }),
  objectives: (): IsmsObjective => ({
    objective: '', measure: '', target: '', owner: '', resources: '', due_date: null,
    evaluation: '', status: 'planned',
  }),
  communication_plan: (): IsmsCommunication => ({
    what: '', when: '', audience: '', communicator: '', method: '',
  }),
  audit_programme: (): IsmsAuditEntry => ({
    area: '', criteria: '', planned_period: '', auditor: '', status: 'planned', findings: '',
  }),
}

const lines = (text: string) => text.split('\n').map((l) => l.trim()).filter(Boolean)

export default function IsmsProfilePage({ organizationId }: Props) {
  const canEdit = useIsOrgEditor(organizationId)
  const queryClient = useQueryClient()
  const [active, setActive] = useState<IsmsSection>('scope')

  const { data: profile, isLoading, error } = useQuery({
    queryKey: ['isms-profile', organizationId],
    queryFn: () => getIsmsProfile(organizationId),
  })

  const [draft, setDraft] = useState<IsmsProfile | null>(null)
  const [dirty, setDirty] = useState<Set<IsmsSection>>(new Set())

  // Adopt the server copy for every section without unsaved edits.
  useEffect(() => {
    if (!profile) return
    setDraft((current) => {
      if (!current) return profile
      const next = { ...profile }
      for (const section of dirty) {
        ;(next as Record<string, unknown>)[section] = current[section]
      }
      return next
    })
    // `dirty` is read, not tracked: only a new server copy should trigger this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile])

  const save = useMutation({
    mutationFn: (section: IsmsSection) =>
      updateIsmsSection(organizationId, section, draft![section] as never),
    onSuccess: (next, section) => {
      setDirty((d) => {
        const copy = new Set(d)
        copy.delete(section)
        return copy
      })
      setDraft((current) => (current ? { ...current, [section]: next[section] } : next))
      queryClient.setQueryData(['isms-profile', organizationId], next)
      toast.success(`${SECTIONS.find((s) => s.key === section)?.label} saved`)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const edit = <S extends IsmsSection>(section: S, value: IsmsProfile[S]) => {
    setDraft((current) => (current ? { ...current, [section]: value } : current))
    setDirty((d) => new Set(d).add(section))
  }

  const discard = (section: IsmsSection) => {
    if (profile) setDraft((current) => (current ? { ...current, [section]: profile[section] } : profile))
    setDirty((d) => {
      const copy = new Set(d)
      copy.delete(section)
      return copy
    })
  }

  const addStarters = async (section: 'interested_parties' | 'communication_plan') => {
    try {
      const rows = await getIsmsStarters(organizationId, section)
      const existing = (draft?.[section] ?? []) as Array<IsmsInterestedParty | IsmsCommunication>
      edit(section, [...existing, ...rows] as never)
      toast.success(`Added ${rows.length} starter rows. Edit them, then save.`)
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  if (isLoading || (!draft && !error)) {
    return <div className="isms-page"><p className="isms-empty">Loading your ISMS profile…</p></div>
  }
  if (error || !draft) {
    return (
      <div className="isms-page">
        <p className="isms-empty">Your ISMS profile could not be loaded: {(error as Error)?.message}</p>
      </div>
    )
  }

  const meta = SECTIONS.find((s) => s.key === active)!
  const disabled = !canEdit

  return (
    <div className="isms-page">
      <header className="isms-head">
        <p className="isms-lede">
          The facts your ISO/IEC 27001 clause documents are written from. Anything left empty
          appears in the generated document as <em>to be completed</em>, never as invented text.
        </p>
        {!canEdit && (
          <p className="doc-notice doc-notice-warning">You can view this profile. Editing it needs the editor role.</p>
        )}
      </header>

      <nav className="isms-tabs" aria-label="ISMS profile sections">
        {SECTIONS.map((s) => (
          <button
            key={s.key}
            type="button"
            className={`isms-tab ${active === s.key ? 'is-active' : ''}`}
            aria-current={active === s.key ? 'page' : undefined}
            onClick={() => setActive(s.key)}
          >
            {s.label}
            <span className="isms-tab-clause">{s.clause}</span>
            {dirty.has(s.key) && <span className="isms-tab-dirty" aria-label="unsaved changes">•</span>}
          </button>
        ))}
      </nav>

      <section className="isms-section" aria-labelledby="isms-section-title">
        <div className="isms-section-head">
          <h2 id="isms-section-title">
            {meta.label} <span className="isms-clause">Clause {meta.clause}</span>
          </h2>
          <p>{meta.intro}</p>
        </div>

        {active === 'scope' && (
          <ScopeForm scope={draft.scope} disabled={disabled} onChange={(v) => edit('scope', v)} />
        )}
        {active === 'context_issues' && (
          <RegisterRows
            rows={draft.context_issues} fields={ISSUE_FIELDS} blank={BLANKS.context_issues}
            onChange={(r) => edit('context_issues', r)} disabled={disabled} noun="issue"
            empty="No issues recorded yet."
          />
        )}
        {active === 'interested_parties' && (
          <>
            {!disabled && (
              <button type="button" className="btn-link isms-starters" onClick={() => addStarters('interested_parties')}>
                Add common interested parties as a starting point
              </button>
            )}
            <RegisterRows
              rows={draft.interested_parties} fields={PARTY_FIELDS} blank={BLANKS.interested_parties}
              onChange={(r) => edit('interested_parties', r)} disabled={disabled} noun="party"
              empty="No interested parties recorded yet."
            />
          </>
        )}
        {active === 'objectives' && (
          <RegisterRows
            rows={draft.objectives} fields={OBJECTIVE_FIELDS} blank={BLANKS.objectives}
            onChange={(r) => edit('objectives', r)} disabled={disabled} noun="objective"
            empty="No objectives recorded yet."
          />
        )}
        {active === 'communication_plan' && (
          <>
            {!disabled && (
              <button type="button" className="btn-link isms-starters" onClick={() => addStarters('communication_plan')}>
                Add common communications as a starting point
              </button>
            )}
            <RegisterRows
              rows={draft.communication_plan} fields={COMMUNICATION_FIELDS} blank={BLANKS.communication_plan}
              onChange={(r) => edit('communication_plan', r)} disabled={disabled} noun="communication"
              empty="No communications recorded yet."
            />
          </>
        )}
        {active === 'audit_programme' && (
          <RegisterRows
            rows={draft.audit_programme} fields={AUDIT_FIELDS} blank={BLANKS.audit_programme}
            onChange={(r) => edit('audit_programme', r)} disabled={disabled} noun="audit"
            empty="No audits planned yet."
          />
        )}

        {canEdit && (
          <div className="isms-actions">
            <span className="isms-actions-note">
              {dirty.has(active) ? 'Unsaved changes' : 'Saved'}
            </span>
            <button
              type="button" className="btn-secondary" disabled={!dirty.has(active) || save.isPending}
              onClick={() => discard(active)}
            >
              Discard
            </button>
            <button
              type="button" className="btn-primary" disabled={!dirty.has(active) || save.isPending}
              onClick={() => save.mutate(active)}
            >
              {save.isPending ? 'Saving…' : `Save ${meta.label.toLowerCase()}`}
            </button>
          </div>
        )}
      </section>
    </div>
  )
}

function ScopeForm({
  scope, disabled, onChange,
}: { scope: IsmsScope; disabled: boolean; onChange: (s: IsmsScope) => void }) {
  // Lists are edited as one entry per line; keep the raw text so a half-typed
  // blank line is not swallowed while typing.
  const [locations, setLocations] = useState(scope.locations.join('\n'))
  const [services, setServices] = useState(scope.services.join('\n'))
  useEffect(() => setLocations(scope.locations.join('\n')), [scope.locations])
  useEffect(() => setServices(scope.services.join('\n')), [scope.services])

  const set = <K extends keyof IsmsScope>(key: K, value: IsmsScope[K]) => onChange({ ...scope, [key]: value })

  return (
    <fieldset className="isms-scope" disabled={disabled}>
      <label className="isms-field is-wide" htmlFor="isms-scope-description">
        <span>The organisation</span>
        <textarea id="isms-scope-description" rows={3} value={scope.organisation_description}
          placeholder="What the organisation does, in a few sentences."
          onChange={(e) => set('organisation_description', e.target.value)} />
      </label>
      <label className="isms-field is-wide" htmlFor="isms-scope-boundaries">
        <span>Boundaries</span>
        <textarea id="isms-scope-boundaries" rows={3} value={scope.boundaries}
          placeholder="Which business units, functions, people and information the ISMS covers."
          onChange={(e) => set('boundaries', e.target.value)} />
      </label>
      <label className="isms-field" htmlFor="isms-scope-locations">
        <span>Locations (one per line)</span>
        <textarea id="isms-scope-locations" rows={3} value={locations}
          onChange={(e) => { setLocations(e.target.value); set('locations', lines(e.target.value)) }} />
      </label>
      <label className="isms-field" htmlFor="isms-scope-services">
        <span>Products and services (one per line)</span>
        <textarea id="isms-scope-services" rows={3} value={services}
          onChange={(e) => { setServices(e.target.value); set('services', lines(e.target.value)) }} />
      </label>
      <label className="isms-field is-wide" htmlFor="isms-scope-interfaces">
        <span>Interfaces and dependencies</span>
        <textarea id="isms-scope-interfaces" rows={3} value={scope.interfaces}
          placeholder="Where activities inside the scope depend on, or connect to, those outside it."
          onChange={(e) => set('interfaces', e.target.value)} />
      </label>
      <div className="isms-field is-wide">
        <span>Exclusions</span>
        <RegisterRows<IsmsExclusion>
          rows={scope.exclusions}
          fields={[
            { key: 'item', label: 'Excluded', type: 'text' },
            { key: 'justification', label: 'Justification', type: 'textarea', wide: true },
          ]}
          blank={() => ({ item: '', justification: '' })}
          onChange={(rows) => set('exclusions', rows)}
          disabled={disabled}
          noun="exclusion"
          empty="Nothing excluded."
        />
      </div>
    </fieldset>
  )
}
