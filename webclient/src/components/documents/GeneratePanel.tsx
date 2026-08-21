/**
 * Generation panel — pick generators, pick domains, queue the run.
 *
 * Derivative generators are shown even when they are switched off, marked and
 * disabled rather than hidden. Hiding them would leave an administrator
 * wondering why the platform cannot write a policy; showing them disabled
 * answers the question and points at the switch.
 */
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import {
  generateDocuments,
  getDocGenSettings,
  getGenerationStatus,
  listGenerators,
  type GenerationRequestItem,
} from '../../data/documentsApi'

interface DomainOption {
  identifier: string
  name: string
  controlCount: number
}

interface Props {
  organizationId: string
  domains: DomainOption[]
  onClose: () => void
}

export default function GeneratePanel({ organizationId, domains, onClose }: Props) {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [selectedDomains, setSelectedDomains] = useState<Set<string>>(new Set())
  const [force, setForce] = useState(false)

  const { data: generators = [] } = useQuery({
    queryKey: ['docgen-generators', organizationId],
    queryFn: () => listGenerators(organizationId),
  })
  const { data: settings } = useQuery({
    queryKey: ['docgen-settings', organizationId],
    queryFn: () => getDocGenSettings(organizationId),
  })

  // Poll while a run is in flight. The status key is per organisation, so this
  // also shows a run someone else started.
  const { data: status } = useQuery({
    queryKey: ['docgen-status', organizationId],
    queryFn: () => getGenerationStatus(organizationId),
    refetchInterval: (query) => {
      const s = query.state.data?.status
      return s === 'running' || s === 'queued' ? 1500 : false
    },
  })

  const running = status?.status === 'running' || status?.status === 'queued'

  const mutation = useMutation({
    mutationFn: (requests: GenerationRequestItem[]) =>
      generateDocuments(organizationId, requests, force),
    onSuccess: (result) => {
      toast.success(`Queued ${result.queued} document${result.queued === 1 ? '' : 's'}`)
      queryClient.invalidateQueries({ queryKey: ['docgen-status', organizationId] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const tier1 = generators.filter((g) => !g.is_derivative)
  const tier2 = generators.filter((g) => g.is_derivative)
  const derivativeAllowed = Boolean(settings?.derivative_generators_enabled)

  const needsDomain = useMemo(
    () => generators.some((g) => selected.has(g.name) && g.domain_scoped),
    [generators, selected]
  )

  const requests = useMemo<GenerationRequestItem[]>(() => {
    const out: GenerationRequestItem[] = []
    for (const g of generators) {
      if (!selected.has(g.name)) continue
      if (g.domain_scoped) {
        for (const d of selectedDomains) out.push({ generator: g.name, domain_id: d })
      } else {
        out.push({ generator: g.name })
      }
    }
    return out
  }, [generators, selected, selectedDomains])

  function toggle(set: Set<string>, key: string, apply: (s: Set<string>) => void) {
    const next = new Set(set)
    next.has(key) ? next.delete(key) : next.add(key)
    apply(next)
  }

  return (
    <div className="doc-generate-panel">
      <div className="doc-panel-head">
        <h2>Generate Documents</h2>
        <button type="button" className="btn-icon" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      {running && (
        <div className="doc-progress">
          <div className="doc-progress-bar">
            <div
              className="doc-progress-fill"
              style={{
                width: `${status?.total ? ((status.completed || 0) / status.total) * 100 : 15}%`,
              }}
            />
          </div>
          <p className="doc-progress-label">
            {status?.message || 'Starting…'}
            {status?.total ? ` (${status.completed || 0} of ${status.total})` : ''}
          </p>
        </div>
      )}

      {status?.status === 'completed' || status?.status === 'completed_with_errors' ? (
        <div className="doc-run-summary">
          <strong>Last run:</strong> {status.generated || 0} generated,{' '}
          {status.skipped || 0} unchanged
          {status.failed ? `, ${status.failed} refused` : ''}
          {status.results?.some((r) => r.conflict_count) && (
            <span className="doc-conflict-note">
              {' '}— some sections need your decision.
            </span>
          )}
        </div>
      ) : null}

      {/* ── Tier 1 ──────────────────────────────────────────────────────── */}
      <section className="doc-gen-group">
        <h3>Reports and Registers</h3>
        <p className="doc-gen-group-sub">
          Built from your control data. Deterministic — no language model.
        </p>
        {tier1.map((g) => (
          <label key={g.name} className="doc-gen-option">
            <input
              type="checkbox"
              checked={selected.has(g.name)}
              disabled={!settings?.enabled}
              onChange={() => toggle(selected, g.name, setSelected)}
            />
            <span className="doc-gen-option-body">
              <strong>{g.display_name}</strong>
              <span>{g.description}</span>
            </span>
          </label>
        ))}
      </section>

      {/* ── Tier 2 ──────────────────────────────────────────────────────── */}
      <section className="doc-gen-group">
        <h3>
          Policies and Procedures
          <span className="doc-derivative-tag">Derivative work</span>
        </h3>
        {!derivativeAllowed && (
          <p className="doc-gen-group-sub doc-gen-locked">
            AI-augmented generation is switched off. An administrator can enable
            it in Org Settings after reviewing the SCF licence position.
          </p>
        )}
        {tier2.map((g) => (
          <label
            key={g.name}
            className={`doc-gen-option ${!derivativeAllowed ? 'is-locked' : ''}`}
          >
            <input
              type="checkbox"
              checked={selected.has(g.name)}
              disabled={!derivativeAllowed}
              onChange={() => toggle(selected, g.name, setSelected)}
            />
            <span className="doc-gen-option-body">
              <strong>{g.display_name}</strong>
              <span>{g.description}</span>
            </span>
          </label>
        ))}
      </section>

      {/* ── Domains ─────────────────────────────────────────────────────── */}
      {needsDomain && (
        <section className="doc-gen-group">
          <h3>Domains</h3>
          <p className="doc-gen-group-sub">
            One document per selected domain. Only domains with controls in
            scope are listed.
          </p>
          <div className="doc-domain-grid">
            {domains.map((d) => (
              <label key={d.identifier} className="doc-domain-chip">
                <input
                  type="checkbox"
                  checked={selectedDomains.has(d.identifier)}
                  onChange={() => toggle(selectedDomains, d.identifier, setSelectedDomains)}
                />
                <span>
                  <strong>{d.identifier}</strong> {d.name}
                  <em>{d.controlCount}</em>
                </span>
              </label>
            ))}
          </div>
          {domains.length === 0 && (
            <p className="doc-gen-group-sub">
              No domains have controls in scope yet.
            </p>
          )}
        </section>
      )}

      <div className="doc-gen-footer">
        <label className="doc-force-check">
          <input
            type="checkbox"
            checked={force}
            onChange={(e) => setForce(e.target.checked)}
          />
          <span>
            Regenerate even if nothing has changed
            <em>
              Without this, documents whose inputs are identical are left alone.
            </em>
          </span>
        </label>

        <div className="doc-gen-actions">
          <span className="doc-gen-count">
            {requests.length} document{requests.length === 1 ? '' : 's'} selected
          </span>
          <button type="button" className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={requests.length === 0 || mutation.isPending || running}
            onClick={() => mutation.mutate(requests)}
          >
            {running ? 'Generation running…' : 'Generate'}
          </button>
        </div>
      </div>
    </div>
  )
}
