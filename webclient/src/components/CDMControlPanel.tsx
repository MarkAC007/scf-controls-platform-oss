import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'react-hot-toast'
import {
  listCdmMappings,
  queryCdm,
  reviewCdmMapping,
  type CDMMapping,
  type CDMQueryHit,
} from '../data/apiClient'

interface CDMControlPanelProps {
  organizationId: string
  scopedControlId?: string
  controlName?: string
  controlDescription?: string
}

const EXCERPT_TRUNCATE_CHARS = 600
const STOP_WORDS = new Set([
  'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'any', 'can',
  'has', 'had', 'have', 'with', 'that', 'this', 'from', 'will', 'shall',
  'must', 'should', 'their', 'they', 'them', 'these', 'those', 'such',
  'into', 'over', 'each', 'when', 'where', 'which', 'while', 'been',
  'were', 'also', 'than', 'then', 'upon', 'within', 'using', 'used',
  'use', 'one', 'two', 'three', 'four', 'five', 'about', 'across',
  'against', 'after', 'before', 'between',
])

function statusBadgeClass(status: string): string {
  if (status === 'accepted') return 'cdm-badge cdm-badge-success'
  if (status === 'stale') return 'cdm-badge cdm-badge-progress'
  return 'cdm-badge'
}

function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .split(/[^a-z]+/)
    .filter((t) => t.length >= 3 && !STOP_WORDS.has(t))
}

function reviewBadge(lastReviewedAt: string | null): {
  label: string
  className: string
} {
  if (!lastReviewedAt) {
    return { label: 'Never reviewed', className: 'cdm-badge cdm-badge-warning' }
  }
  const then = new Date(lastReviewedAt).getTime()
  if (Number.isNaN(then)) {
    return { label: 'Reviewed', className: 'cdm-badge' }
  }
  const days = Math.floor((Date.now() - then) / (1000 * 60 * 60 * 24))
  if (days <= 0) return { label: 'Reviewed today', className: 'cdm-badge cdm-badge-success' }
  if (days === 1) return { label: 'Reviewed 1 day ago', className: 'cdm-badge' }
  return { label: `Reviewed ${days} days ago`, className: 'cdm-badge' }
}

interface MappingRowProps {
  mapping: CDMMapping
  organizationId: string
  controlName?: string
  controlDescription?: string
  expanded: boolean
  onToggle: () => void
  onReviewed: (updated: Partial<CDMMapping>) => void
}

function MappingRow({
  mapping,
  organizationId,
  controlName,
  controlDescription,
  expanded,
  onToggle,
  onReviewed,
}: MappingRowProps) {
  const [draftNotes, setDraftNotes] = useState(mapping.review_notes ?? '')
  const [saving, setSaving] = useState(false)
  const [showFullExcerpt, setShowFullExcerpt] = useState(false)

  useEffect(() => {
    setDraftNotes(mapping.review_notes ?? '')
  }, [mapping.review_notes])

  const excerpt = mapping.excerpt ?? ''
  const excerptOverflows = excerpt.length > EXCERPT_TRUNCATE_CHARS
  const visibleExcerpt =
    excerptOverflows && !showFullExcerpt
      ? `${excerpt.slice(0, EXCERPT_TRUNCATE_CHARS)}…`
      : excerpt

  const scfText = `${controlName ?? ''} ${controlDescription ?? ''}`.trim()
  const divergentTokens = useMemo(() => {
    if (!scfText || !excerpt) return [] as string[]
    const excerptTokens = new Set(tokenize(excerpt))
    const seen = new Set<string>()
    const out: string[] = []
    for (const t of tokenize(scfText)) {
      if (!excerptTokens.has(t) && !seen.has(t)) {
        seen.add(t)
        out.push(t)
      }
    }
    return out.slice(0, 30)
  }, [scfText, excerpt])

  const badge = reviewBadge(mapping.last_reviewed_at)

  const notesDirty = (draftNotes ?? '') !== (mapping.review_notes ?? '')

  const handleSave = useCallback(
    async (markReviewed: boolean) => {
      setSaving(true)
      try {
        const body: { notes?: string; mark_reviewed?: boolean } = {}
        if (notesDirty) body.notes = draftNotes
        if (markReviewed) body.mark_reviewed = true
        if (!body.notes && !body.mark_reviewed) {
          setSaving(false)
          return
        }
        const resp = await reviewCdmMapping(organizationId, mapping.id, body)
        onReviewed({
          review_notes: resp.review_notes,
          last_reviewed_at: resp.last_reviewed_at,
          last_reviewed_by_user_id: resp.last_reviewed_by_user_id,
        })
        toast.success(markReviewed ? 'Review noted' : 'Notes saved')
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Save failed'
        toast.error(message)
      } finally {
        setSaving(false)
      }
    },
    [draftNotes, mapping.id, notesDirty, onReviewed, organizationId],
  )

  const previewText = excerpt ? excerpt.slice(0, 150) : ''

  return (
    <li className="cdm-mapping-row">
      <div className="cdm-mapping-row-main">
        <button
          type="button"
          className="cdm-mapping-toggle"
          onClick={onToggle}
          aria-expanded={expanded}
        >
          <span className="cdm-mapping-toggle-icon" aria-hidden>
            {expanded ? '▾' : '▸'}
          </span>
          <span className="cdm-mapping-row-text">
            <span className="cdm-mapping-row-heading">
              <span className="cdm-filename">{mapping.original_filename ?? '—'}</span>
              <span className="cdm-mapping-section">{mapping.section ?? '—'}</span>
            </span>
            {previewText ? (
              <span className="cdm-mapping-preview" title={previewText}>
                {previewText}
                {excerpt.length > 150 ? '…' : ''}
              </span>
            ) : (
              <span className="cdm-mapping-preview cdm-mapping-preview-empty">
                No excerpt yet
              </span>
            )}
          </span>
        </button>
        <div className="cdm-mapping-row-side">
          <span className={statusBadgeClass(mapping.status)}>{mapping.status}</span>
          <span className={badge.className}>{badge.label}</span>
        </div>
      </div>

      {expanded ? (
        <div className="cdm-mapping-row-expanded">
          <div className="cdm-review-block">
            <h4 className="cdm-review-block-title">Document excerpt</h4>
            {excerpt ? (
              <>
                <pre className="cdm-excerpt">{visibleExcerpt}</pre>
                {excerptOverflows ? (
                  <button
                    type="button"
                    className="cdm-link-button"
                    onClick={() => setShowFullExcerpt((v) => !v)}
                  >
                    {showFullExcerpt ? 'Show less' : 'Show more'}
                  </button>
                ) : null}
              </>
            ) : (
              <p className="cdm-row-meta cdm-excerpt-empty">
                No excerpt yet — re-run mapping to populate.
              </p>
            )}
          </div>

          <div className="cdm-review-block">
            <h4 className="cdm-review-block-title">SCF control language</h4>
            {scfText ? (
              <>
                {controlName ? (
                  <div className="cdm-scf-name">{controlName}</div>
                ) : null}
                {controlDescription ? (
                  <p className="cdm-scf-description">{controlDescription}</p>
                ) : null}
              </>
            ) : (
              <p className="cdm-row-meta">Control text unavailable.</p>
            )}
          </div>

          {divergentTokens.length > 0 ? (
            <div className="cdm-review-block">
              <h4 className="cdm-review-block-title">Terminology hints</h4>
              <p className="cdm-row-meta">
                Terms in the SCF control that don't appear in the excerpt — consider
                aligning the document's wording on the next revision.
              </p>
              <div className="cdm-terminology-hints">
                {divergentTokens.map((t) => (
                  <span key={t} className="cdm-terminology-token">
                    {t}
                  </span>
                ))}
              </div>
            </div>
          ) : null}

          <div className="cdm-review-block">
            <h4 className="cdm-review-block-title">Review notes</h4>
            <textarea
              className="cdm-review-notes"
              value={draftNotes}
              onChange={(e) => setDraftNotes(e.target.value)}
              placeholder="e.g. doc says 'minimum access' — SCF uses 'least privilege'."
              disabled={saving}
              rows={3}
            />
            <div className="cdm-review-actions">
              <button
                type="button"
                className="btn-secondary"
                disabled={saving || !notesDirty}
                onClick={() => void handleSave(false)}
              >
                {saving ? 'Saving…' : 'Save notes'}
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={saving}
                onClick={() => void handleSave(true)}
              >
                {saving ? 'Saving…' : 'Mark reviewed today'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </li>
  )
}

export default function CDMControlPanel({
  organizationId,
  scopedControlId,
  controlName,
  controlDescription,
}: CDMControlPanelProps) {
  const [mappings, setMappings] = useState<CDMMapping[]>([])
  const [loading, setLoading] = useState(true)
  const [queryText, setQueryText] = useState('')
  const [querying, setQuerying] = useState(false)
  const [hits, setHits] = useState<CDMQueryHit[] | null>(null)
  const [kbRevision, setKbRevision] = useState<string | null>(null)
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>({})

  const fetchMappings = useCallback(async () => {
    if (!scopedControlId) {
      setMappings([])
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      const response = await listCdmMappings(organizationId, {
        controlId: scopedControlId,
        limit: 100,
      })
      setMappings(
        response.mappings.filter(
          (m) => m.status === 'accepted' || m.status === 'stale',
        ),
      )
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load mappings'
      toast.error(message)
    } finally {
      setLoading(false)
    }
  }, [organizationId, scopedControlId])

  useEffect(() => {
    void fetchMappings()
  }, [fetchMappings])

  const staleMappings = useMemo(
    () => mappings.filter((m) => m.status === 'stale'),
    [mappings],
  )

  const handleQuery = useCallback(async () => {
    if (!scopedControlId) return
    setQuerying(true)
    try {
      const trimmed = queryText.trim()
      const response = await queryCdm(organizationId, {
        control_id: scopedControlId,
        query_text: trimmed ? trimmed : null,
        limit: 10,
      })
      setHits(response.hits)
      setKbRevision(response.kb_revision)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Query failed'
      toast.error(message)
    } finally {
      setQuerying(false)
    }
  }, [organizationId, scopedControlId, queryText])

  const handleReviewed = useCallback(
    (mappingId: string, patch: Partial<CDMMapping>) => {
      setMappings((prev) =>
        prev.map((m) => (m.id === mappingId ? { ...m, ...patch } : m)),
      )
    },
    [],
  )

  if (!scopedControlId) {
    return (
      <div className="cdm-control-panel">
        <div className="cdm-empty">
          <p>This control is not in your scope.</p>
          <p className="cdm-empty-hint">
            Scope this control first, then upload documents and accept proposed
            mappings to see the knowledge-base view here.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="cdm-control-panel">
      {staleMappings.length > 0 ? (
        <div className="cdm-stale-banner" role="alert">
          <strong>Knowledge base changed.</strong>{' '}
          {staleMappings.length === 1
            ? '1 accepted mapping'
            : `${staleMappings.length} accepted mappings`}{' '}
          for this control are now <em>stale</em> because the underlying
          document was re-indexed. Re-review them in the Review queue to
          re-accept or dismiss.
        </div>
      ) : null}

      <section className="cdm-control-section">
        <h3 className="cdm-control-section-title">Accepted evidence</h3>
        {loading ? (
          <div className="cdm-loading">Loading mappings…</div>
        ) : mappings.length === 0 ? (
          <div className="cdm-empty">
            <p>No knowledge-base mappings yet.</p>
            <p className="cdm-empty-hint">
              Upload more documents in Control Documents, or accept proposed
              mappings from the Review queue.
            </p>
          </div>
        ) : (
          <ul className="cdm-mapping-list">
            {mappings.map((m) => (
              <MappingRow
                key={m.id}
                mapping={m}
                organizationId={organizationId}
                controlName={controlName}
                controlDescription={controlDescription}
                expanded={!!expandedIds[m.id]}
                onToggle={() =>
                  setExpandedIds((prev) => ({ ...prev, [m.id]: !prev[m.id] }))
                }
                onReviewed={(patch) => handleReviewed(m.id, patch)}
              />
            ))}
          </ul>
        )}
      </section>

      <section className="cdm-control-section">
        <h3 className="cdm-control-section-title">Ask the knowledge base</h3>
        <p className="cdm-row-meta">
          Search your indexed documents for this control. Leave blank to use
          the control name + description as the query.
        </p>
        <div className="cdm-query-row">
          <input
            type="text"
            className="cdm-query-input"
            placeholder="e.g. how often do we review privileged access?"
            value={queryText}
            onChange={(e) => setQueryText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void handleQuery()
            }}
            disabled={querying}
          />
          <button
            type="button"
            className="btn-primary"
            disabled={querying}
            onClick={() => void handleQuery()}
          >
            {querying ? 'Searching…' : 'Search'}
          </button>
        </div>

        {hits !== null ? (
          <div className="cdm-query-results">
            <div className="cdm-row-meta">
              {hits.length} hit{hits.length === 1 ? '' : 's'}
              {kbRevision ? ` · KB ${kbRevision}` : ''}
            </div>
            {hits.length === 0 ? (
              <div className="cdm-empty">
                <p>No matches.</p>
              </div>
            ) : (
              <ul className="cdm-hit-list">
                {hits.map((hit, idx) => (
                  <li key={hit.chunk_id ?? `${idx}`} className="cdm-hit-row">
                    <div className="cdm-hit-content">{hit.content}</div>
                    <div className="cdm-row-meta">
                      {hit.file_source ?? hit.file_path ?? 'unknown source'}
                      {hit.reference_id ? ` · ref ${hit.reference_id}` : ''}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : null}
      </section>
    </div>
  )
}
