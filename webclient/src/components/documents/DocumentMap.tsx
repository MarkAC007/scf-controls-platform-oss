/**
 * Which document each in-scope control lands in, under the organisation's
 * document structure (Org Settings → document profile).
 *
 * The structure decides how domains consolidate into documents, so "where is
 * IAC-21 written down?" has no obvious answer from the library alone. Search
 * answers it per control; the table below shows the whole mapping. Membership
 * comes from the backend rather than being inferred from domain prefixes,
 * because topic procedures are defined by named controls, not domains.
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  LIFECYCLE_LABELS,
  getDocumentMap,
  listDocuments,
  type DocumentSummary,
  type LifecycleStatus,
  type MapScope,
} from '../../data/documentsApi'

interface Props {
  organizationId: string
  onOpenDocument: (documentId: string) => void
  onOpenSettings?: () => void
}

/** Policy, standard, procedure — the order a reader climbs down. */
const GENERATOR_ORDER = ['policy', 'standard', 'procedure']

/** Group headings, in the library's words. */
const GROUP_LABELS: Record<string, string> = {
  policy: 'Policies',
  standard: 'Standards',
  procedure: 'Procedures',
}

/** Search results shown at once; a domain code alone can match hundreds. */
const RESULT_LIMIT = 40

interface Placement {
  generator: string
  scope: MapScope
}

export default function DocumentMap({ organizationId, onOpenDocument, onOpenSettings }: Props) {
  const [query, setQuery] = useState('')
  const { data: map, isLoading, isError } = useQuery({
    queryKey: ['document-map', organizationId],
    queryFn: () => getDocumentMap(organizationId),
  })
  // Unfiltered, whatever the library's status filter is: the map pairs every
  // planned document with its generated copy.
  const { data: documents = [] } = useQuery({
    queryKey: ['documents', organizationId, ''],
    queryFn: () => listDocuments(organizationId, {}),
  })

  const generators = useMemo(
    () => GENERATOR_ORDER.filter((g) => map?.generators[g]).map((g) => [g, map!.generators[g]] as const),
    [map],
  )
  const domainNames = useMemo(
    () => new Map((map?.domains ?? []).map((d) => [d.identifier, d.name])),
    [map],
  )

  // The current (not superseded) generated document for a planned one.
  const generated = useMemo(() => {
    const out = new Map<string, DocumentSummary>()
    for (const d of documents) {
      if (!d.superseded && d.domain_id) out.set(`${d.generator_name}:${d.domain_id}`, d)
    }
    return out
  }, [documents])

  const placements = useMemo(() => {
    const out = new Map<string, Placement[]>()
    for (const [generator, g] of generators) {
      for (const scope of g.scopes) {
        for (const c of scope.controls) {
          const list = out.get(c) ?? []
          list.push({ generator, scope })
          out.set(c, list)
        }
      }
    }
    return out
  }, [generators])

  const needle = query.trim().toLowerCase()
  const matches = useMemo(() => {
    if (!map || !needle) return []
    return map.controls.filter(
      (c) =>
        c.scf_id.toLowerCase().startsWith(needle) ||
        c.name.toLowerCase().includes(needle) ||
        (domainNames.get(c.domain) ?? '').toLowerCase().includes(needle),
    )
  }, [map, needle, domainNames])

  if (isLoading) return <p className="docs-list-loading">Loading the document map…</p>
  if (isError || !map) return <p className="docs-list-loading">The document map could not be loaded.</p>

  const docFor = (generator: string, scope: MapScope) => generated.get(`${generator}:${scope.key}`)

  function renderChip({ generator, scope }: Placement) {
    const doc = docFor(generator, scope)
    const label = map!.generators[generator].display_name
    const body = (
      <>
        <span className="docmap-chip-kind">{label}</span>
        <span className="docmap-chip-title">{scope.title}</span>
        <span className={`docmap-chip-state ${doc ? `status-${doc.lifecycle_status}` : 'is-planned'}`}>
          {doc ? LIFECYCLE_LABELS[doc.lifecycle_status as LifecycleStatus] : 'Not generated yet'}
        </span>
      </>
    )
    return doc ? (
      <button
        key={`${generator}:${scope.key}`}
        type="button"
        className="docmap-chip is-generated"
        onClick={() => onOpenDocument(doc.id)}
      >
        {body}
      </button>
    ) : (
      <span key={`${generator}:${scope.key}`} className="docmap-chip">
        {body}
      </span>
    )
  }

  /** What to say for a generator that does not place a control anywhere. */
  function absence(generator: string): string | null {
    const g = map!.generators[generator]
    if (generator === 'standard' && map!.fold_standards) return 'Folded into the policy'
    if (g.mode === 'none') return `No ${g.display_name.toLowerCase()}s in this structure`
    if (g.mode === 'topic') return `Not covered by a topic ${g.display_name.toLowerCase()}`
    return null
  }

  const totalControls = map.controls.length
  const documentCount = generators.reduce((n, [, g]) => n + g.scopes.length, 0)

  return (
    <div className="docmap">
      <section className="docmap-structure" aria-label="Document structure">
        <div>
          <p className="docmap-eyebrow">Document structure</p>
          <h2>{map.profile_display_name}</h2>
          <p className="docmap-summary">
            {totalControls} in-scope control{totalControls === 1 ? '' : 's'} across {map.domains.length} domain
            {map.domains.length === 1 ? '' : 's'}, written into {documentCount} document
            {documentCount === 1 ? '' : 's'}.
            {map.fold_standards && ' Standards are written into the policies rather than as separate documents.'}
          </p>
        </div>
        {onOpenSettings && (
          <button type="button" className="btn-secondary" onClick={onOpenSettings}>
            Change in Org Settings
          </button>
        )}
      </section>

      <div className="docmap-search">
        <label htmlFor="docmap-search-input">Find a control</label>
        <input
          id="docmap-search-input"
          type="search"
          value={query}
          placeholder="Control ID, name or domain — e.g. IAC-21, backup, Cryptographic"
          onChange={(e) => setQuery(e.target.value)}
          autoComplete="off"
        />
      </div>

      {needle ? (
        <section aria-label="Matching controls">
          {matches.length === 0 ? (
            <p className="docmap-empty">
              No in-scope control matches “{query.trim()}”. A control outside your scope is in no document.
            </p>
          ) : (
            <>
              <p className="docmap-count">
                {matches.length > RESULT_LIMIT
                  ? `Showing ${RESULT_LIMIT} of ${matches.length} controls — narrow the search to see the rest.`
                  : `${matches.length} control${matches.length === 1 ? '' : 's'}`}
              </p>
              <ul className="docmap-results">
                {matches.slice(0, RESULT_LIMIT).map((c) => {
                  const found = placements.get(c.scf_id) ?? []
                  const missing = generators
                    .map(([g]) => g)
                    .filter((g) => !found.some((p) => p.generator === g))
                    .map((g) => ({ generator: g, text: absence(g) }))
                    .filter((m): m is { generator: string; text: string } => Boolean(m.text))
                  return (
                    <li key={c.scf_id} className="docmap-result">
                      <div className="docmap-result-head">
                        <span className="docmap-id">{c.scf_id}</span>
                        <span className="docmap-result-name">{c.name}</span>
                        <span className="docmap-domain" title={domainNames.get(c.domain)}>
                          {c.domain}
                        </span>
                      </div>
                      <div className="docmap-chips">
                        {found.map(renderChip)}
                        {missing.map((m) => (
                          <span key={m.generator} className="docmap-chip is-absent">
                            <span className="docmap-chip-kind">{map.generators[m.generator].display_name}</span>
                            <span className="docmap-chip-title">{m.text}</span>
                          </span>
                        ))}
                      </div>
                    </li>
                  )
                })}
              </ul>
            </>
          )}
        </section>
      ) : (
        generators.map(([generator, g]) => {
          const covered = new Set(g.scopes.flatMap((s) => s.controls))
          const uncovered = totalControls - covered.size
          return (
            <section key={generator} className="docmap-group" aria-label={`${g.display_name} documents`}>
              <p className="docs-list-group-label">{GROUP_LABELS[generator] ?? g.display_name}</p>
              {g.scopes.length === 0 ? (
                <p className="docmap-empty">
                  {generator === 'standard' && map.fold_standards
                    ? 'No separate standards — this structure writes them into the policies.'
                    : `No ${g.display_name.toLowerCase()}s to generate.`}
                </p>
              ) : (
                <div className="docmap-table" role="table" aria-label={`${g.display_name} mapping`}>
                  <div className="docmap-row is-head" role="row">
                    <span role="columnheader">Document</span>
                    <span role="columnheader">Domains</span>
                    <span role="columnheader" className="is-num">Controls</span>
                    <span role="columnheader">Status</span>
                  </div>
                  {g.scopes.map((scope) => {
                    const doc = docFor(generator, scope)
                    return (
                      <div key={scope.key} className="docmap-row" role="row">
                        <span role="cell" className="docmap-row-title">
                          {doc ? (
                            <button type="button" className="btn-link" onClick={() => onOpenDocument(doc.id)}>
                              {scope.title}
                            </button>
                          ) : (
                            scope.title
                          )}
                        </span>
                        <span role="cell" className="docmap-row-domains">
                          {scope.domains.length > 12 ? (
                            <span className="docmap-domain is-all" title={scope.domains.join(', ')}>
                              {scope.domains.length} domains
                            </span>
                          ) : (
                            scope.domains.map((d) => (
                              <span key={d} className="docmap-domain" title={domainNames.get(d)}>
                                {d}
                              </span>
                            ))
                          )}
                        </span>
                        <span role="cell" className="is-num">{scope.controls.length}</span>
                        <span role="cell">
                          <span className={`docmap-chip-state ${doc ? `status-${doc.lifecycle_status}` : 'is-planned'}`}>
                            {doc ? LIFECYCLE_LABELS[doc.lifecycle_status as LifecycleStatus] : 'Not generated yet'}
                          </span>
                        </span>
                      </div>
                    )
                  })}
                </div>
              )}
              {g.mode === 'topic' && uncovered > 0 && (
                <p className="docmap-note">
                  {uncovered} in-scope control{uncovered === 1 ? ' has' : 's have'} no {g.display_name.toLowerCase()} under
                  this structure — topic {g.display_name.toLowerCase()}s cover named control families only. Search a
                  control to check it.
                </p>
              )}
            </section>
          )
        })
      )}
    </div>
  )
}
