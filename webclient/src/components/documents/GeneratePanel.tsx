/**
 * Generation panel — pick generators, pick what they cover, queue the run.
 *
 * What a policy, standard or procedure covers depends on the organisation's
 * document profile: one SCF domain, a bundle of domains, or an operational
 * topic. The scopes endpoint lists, per generator, only the scopes with
 * controls in scope. Generators that share a kind of scope share one picker,
 * so under the granular profile you still choose domains once for all three.
 *
 * AI-augmented generators are shown even when the module is switched off,
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
  listDocumentScopes,
  listGenerators,
  type GenerationRequestItem,
  type ScopeKind,
  type ScopeOption,
} from '../../data/documentsApi'

const CORE_TYPES = ['isms', 'register', 'plan']

const SCOPE_PICKERS: Record<ScopeKind, { heading: string; sub: string; empty: string }> = {
  domain: {
    heading: 'Domains',
    sub: 'One document per selected domain. Only domains with controls in scope are listed.',
    empty: 'No domains have controls in scope yet.',
  },
  bundle: {
    heading: 'Policy areas',
    sub: 'One document per selected area. Each area groups related SCF domains; only areas with controls in scope are listed.',
    empty: 'No policy areas have controls in scope yet.',
  },
  topic: {
    heading: 'Procedure topics',
    sub: 'One procedure per selected operational topic. Only topics with controls in scope are listed.',
    empty: 'No procedure topics have controls in scope yet.',
  },
}

interface Props {
  organizationId: string
  onClose: () => void
}

export default function GeneratePanel({ organizationId, onClose }: Props) {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [selectedScopes, setSelectedScopes] = useState<Set<string>>(new Set())
  const [force, setForce] = useState(false)

  const { data: generators = [] } = useQuery({
    queryKey: ['docgen-generators', organizationId],
    queryFn: () => listGenerators(organizationId),
  })
  const { data: settings } = useQuery({
    queryKey: ['docgen-settings', organizationId],
    queryFn: () => getDocGenSettings(organizationId),
  })
  // Fetched here rather than passed in, like the panel's other reference
  // data. Keyed on the profile so switching structure refetches.
  const { data: scopeData } = useQuery({
    queryKey: ['docgen-scopes', organizationId, settings?.document_profile],
    queryFn: () => listDocumentScopes(organizationId),
  })

  // Shares the run card's query, which polls and shows progress on the page
  // (GenerationRunCard). Read here only to stop a second run being queued.
  const { data: status } = useQuery({
    queryKey: ['docgen-status', organizationId],
    queryFn: () => getGenerationStatus(organizationId),
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

  // The ISO/IEC 27001 core pack is listed apart from the reports: it is
  // written from the ISMS Profile, and a user looking for "the scope
  // statement" should not have to find it among control status reports.
  const isCore = (g: { document_type: string }) => CORE_TYPES.includes(g.document_type)
  const core = generators.filter((g) => !g.is_derivative && isCore(g))
  const tier1 = generators.filter((g) => !g.is_derivative && !isCore(g))
  const tier2 = generators.filter((g) => g.is_derivative)
  const derivativeAllowed = Boolean(settings?.derivative_generators_enabled)

  const modeFor = (name: string) => scopeData?.generators[name]?.mode

  // One picker per kind of scope the selected generators need, listing the
  // union of their scopes in first-seen order.
  const pickers = useMemo(() => {
    const byKind = new Map<ScopeKind, ScopeOption[]>()
    for (const g of generators) {
      if (!selected.has(g.name) || !g.domain_scoped) continue
      for (const scope of scopeData?.generators[g.name]?.scopes ?? []) {
        const list = byKind.get(scope.kind) ?? []
        if (!list.some((s) => s.key === scope.key)) list.push(scope)
        byKind.set(scope.kind, list)
      }
      const mode = scopeData?.generators[g.name]?.mode
      if (mode && mode !== 'none' && !byKind.has(mode)) byKind.set(mode, [])
    }
    return Array.from(byKind.entries())
  }, [generators, selected, scopeData])

  const requests = useMemo<GenerationRequestItem[]>(() => {
    const out: GenerationRequestItem[] = []
    for (const g of generators) {
      if (!selected.has(g.name)) continue
      if (g.domain_scoped) {
        for (const scope of scopeData?.generators[g.name]?.scopes ?? []) {
          if (selectedScopes.has(scope.key)) out.push({ generator: g.name, domain_id: scope.key })
        }
      } else {
        out.push({ generator: g.name })
      }
    }
    return out
  }, [generators, selected, selectedScopes, scopeData])

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

      {/* ── ISO/IEC 27001 core pack ──────────────────────────────────────── */}
      {core.length > 0 && (
        <section className="doc-gen-group">
          <h3>ISO/IEC 27001 Core</h3>
          <p className="doc-gen-group-sub">
            Clause documents, registers and plans, written from your ISMS Profile and
            registers. Anything not yet recorded is marked as to be completed. No
            language model.
          </p>
          {core.map((g) => (
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
      )}

      {/* ── Tier 2 ──────────────────────────────────────────────────────── */}
      <section className="doc-gen-group">
        <h3>Policies and Procedures</h3>
        {!derivativeAllowed && (
          <p className="doc-gen-group-sub doc-gen-locked">
            AI-augmented generation is switched off. An administrator can turn
            it on in Org Settings.
          </p>
        )}
        {tier2.map((g) => {
          // A generator the profile folds into the policy (standards under
          // Compact and Essentials) is shown disabled with the reason, rather
          // than hidden, so nobody wonders where standards went.
          const folded = modeFor(g.name) === 'none'
          return (
            <label
              key={g.name}
              className={`doc-gen-option ${!derivativeAllowed || folded ? 'is-locked' : ''}`}
            >
              <input
                type="checkbox"
                checked={selected.has(g.name) && !folded}
                disabled={!derivativeAllowed || folded}
                onChange={() => toggle(selected, g.name, setSelected)}
              />
              <span className="doc-gen-option-body">
                <strong>{g.display_name}</strong>
                <span>
                  {folded
                    ? 'Not a separate document in your document structure: each policy carries its own requirements.'
                    : g.description}
                </span>
              </span>
            </label>
          )
        })}
      </section>

      {/* ── Scopes ──────────────────────────────────────────────────────── */}
      {pickers.map(([kind, options]) => (
        <section key={kind} className="doc-gen-group">
          <h3>{SCOPE_PICKERS[kind].heading}</h3>
          <p className="doc-gen-group-sub">{SCOPE_PICKERS[kind].sub}</p>
          <div className="doc-domain-grid">
            {options.map((scope) => (
              <label
                key={scope.key}
                className="doc-domain-chip"
                title={kind === 'domain' ? undefined : scope.domains.join(', ')}
              >
                <input
                  type="checkbox"
                  checked={selectedScopes.has(scope.key)}
                  onChange={() => toggle(selectedScopes, scope.key, setSelectedScopes)}
                />
                <span>
                  {kind === 'domain' && <strong>{scope.key}</strong>} {scope.title}
                  <em>{scope.control_count}</em>
                </span>
              </label>
            ))}
          </div>
          {options.length === 0 && (
            <p className="doc-gen-group-sub">{SCOPE_PICKERS[kind].empty}</p>
          )}
        </section>
      ))}

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
