/**
 * Mine versus generated, side by side.
 *
 * The banners told the reader the generated alternative was "in this document's
 * version history". History listed version numbers, timestamps and a model id —
 * it could not show the alternative it was pointing at. This is the missing
 * half: the text of both, aligned, with the decision attached to it. Putting
 * "Keep mine" / "Take generated" here rather than only in the document flow is
 * the point — this is the screen on which the reader actually decides.
 *
 * `available: false` is a real answer, not an empty result. It means the
 * generator no longer emits this section, which is exactly why the section is
 * retiring, and it always holds for a pending-retirement section. Rendering an
 * empty right-hand pane would claim the generator wrote nothing, which is a
 * different and wrong statement, so it is said in words instead.
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  getSectionGenerated,
  type DocumentHistory,
  type DocumentSection,
  type SectionResolveChoice,
} from '../../data/documentsApi'
import { lineDiff } from './lineDiff'
import SectionDecision from './SectionDecision'

interface Props {
  organizationId: string
  documentId: string
  section: DocumentSection
  /** Snapshots to offer in the picker. Undefined while history is loading. */
  versions: DocumentHistory['versions'] | undefined
  /** Snapshot being compared against; undefined means "the latest". */
  version: number | undefined
  onVersionChange: (version: number | undefined) => void
  onClose: () => void
  onResolve: (choice: SectionResolveChoice) => void
  pending: boolean
  disabled?: boolean
}

export default function SectionDiff({
  organizationId,
  documentId,
  section,
  versions,
  version,
  onVersionChange,
  onClose,
  onResolve,
  pending,
  disabled = false,
}: Props) {
  const { data, isLoading, error } = useQuery({
    queryKey: [
      'section-generated',
      organizationId,
      documentId,
      section.section_id,
      version ?? 'latest',
    ],
    queryFn: () => getSectionGenerated(organizationId, documentId, section.section_id, version),
  })

  const diff = useMemo(() => {
    if (!data || !data.available || data.content === null) return null
    return lineDiff(data.current_content ?? '', data.content)
  }, [data])

  // `heading_text` is null for an unavailable (retiring) section, so neither
  // the label nor the accessible name can assume a string.
  const headingLabel = section.heading_text || 'this section'

  return (
    <section className="doc-diff" aria-label={`Compare ${headingLabel}`}>
      <header className="doc-diff-head">
        <div className="doc-diff-head-text">
          <h3>{headingLabel}</h3>
          {diff && (
            <span className="doc-diff-summary">
              {diff.added} line{diff.added === 1 ? '' : 's'} the generator adds ·{' '}
              {diff.removed} line{diff.removed === 1 ? '' : 's'} only yours has
            </span>
          )}
        </div>
        <div className="doc-diff-head-actions">
          {versions && versions.length > 0 && (
            <label className="doc-diff-versions">
              <span>Compare against</span>
              <select
                value={version === undefined ? '' : String(version)}
                onChange={(e) =>
                  onVersionChange(e.target.value === '' ? undefined : Number(e.target.value))
                }
              >
                <option value="">Latest generation</option>
                {versions.map((v) => (
                  <option key={v.version} value={v.version}>
                    v{v.version}
                    {v.is_current ? ' (current)' : ''}
                    {v.created_at
                      ? ` · ${new Date(v.created_at).toLocaleDateString('en-GB')}`
                      : ''}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button type="button" className="btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </header>

      {isLoading && <p className="doc-empty">Loading the generated text…</p>}
      {error && <p className="doc-diff-unavailable">{(error as Error).message}</p>}

      {data && !data.available && (
        <p className="doc-diff-unavailable">
          The generator no longer produces this section — that is why it is
          retiring. There is nothing to compare against: the choice is whether the
          text you have stays in the document or leaves it.
        </p>
      )}

      {data && data.available && diff && (
        <div className="doc-diff-panes">
          <div className="doc-diff-pane">
            <h4 className="doc-diff-pane-head">Yours — in the document now</h4>
            <ol className="doc-diff-lines">
              {diff.rows.map((row, i) => (
                <li
                  key={`l${i}`}
                  className={`doc-diff-line is-${row.left === null ? 'blank' : row.op}`}
                >
                  <span className="doc-diff-gutter">{row.leftNo ?? ''}</span>
                  <code>{row.left ?? ''}</code>
                </li>
              ))}
            </ol>
          </div>
          <div className="doc-diff-pane">
            <h4 className="doc-diff-pane-head">
              Generated{version === undefined ? '' : ` — v${version}`}
            </h4>
            <ol className="doc-diff-lines">
              {diff.rows.map((row, i) => (
                <li
                  key={`r${i}`}
                  className={`doc-diff-line is-${row.right === null ? 'blank' : row.op}`}
                >
                  <span className="doc-diff-gutter">{row.rightNo ?? ''}</span>
                  <code>{row.right ?? ''}</code>
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}

      {/*
        The decision always applies to the LATEST generation: `/resolve` takes
        no version and the endpoint reads the newest snapshot. Leaving the bar
        live while the reader is comparing against v3 would let them read one
        version and adopt another without being told — the exact class of quiet
        lie this panel exists to end. So while an older generation is selected
        the bar is replaced by the reason it is not there.
      */}
      {version === undefined ? (
        <SectionDecision
          section={section}
          pending={pending}
          onResolve={onResolve}
          disabled={disabled}
          variant="bar"
        />
      ) : (
        <p className="doc-diff-note">
          You are comparing against v{version}. Decisions apply to the latest
          generation, so switch back to <strong>Latest generation</strong> to
          settle this section.
        </p>
      )}
    </section>
  )
}
